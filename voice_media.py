import html
import json
import re
import shutil
import subprocess
import uuid

from storage import VOICES, connect


FORMATS = "wav,mp3,flac,ogg,matroska,webm,mov,mp4,m4a,3gp,3g2,mj2,aac,aiff,asf,ape,amr,caf,ac3,eac3,tta,wv"
PLAYABLE = {
    "wav": ("audio/wav", {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_u8", "pcm_f32le"}),
    "mp3": ("audio/mpeg", {"mp3"}),
    "flac": ("audio/flac", {"flac"}),
    "ogg": ("audio/ogg", {"vorbis", "opus", "flac"}),
    "webm": ("audio/webm", {"vorbis", "opus"}),
    "mov": ("audio/mp4", {"aac"}),
}
INPUT_OPTIONS = ["-protocol_whitelist", "file", "-format_whitelist", FORMATS]
MAX_VOICE_BYTES = 25 * 1024 * 1024
REFERENCE_SECONDS = 30
MAX_TRANSCRIPT = 2048
TEXT_SUBTITLE_CODECS = {"subrip", "mov_text", "webvtt", "ass", "ssa", "text", "ttml"}
SUBTITLE_TIMING = re.compile(r"(?m)^\d+\r?\n(\d+):(\d+):(\d+),\d+ --> [^\r\n]+\r?\n")
LYRIC_TIMING = re.compile(r"(?m)^(?:\[\d+:\d+(?:[.:]\d+)?\])+\s*")


def transcript_text(text):
    text = LYRIC_TIMING.sub("", text)
    text = html.unescape(re.sub(r"</?(?:i|b|u|font)\b[^>]*>", "", text, flags=re.IGNORECASE))
    return " ".join(text.split())[:MAX_TRANSCRIPT].rstrip()


def subtitle_transcript(path, stream_index):
    try:
        result = subprocess.check_output([
            "ffmpeg", "-nostdin", "-v", "error", *INPUT_OPTIONS, "-i", str(path),
            "-map", f"0:{stream_index}", "-t", str(REFERENCE_SECONDS), "-c:s", "srt",
            "-fs", "65536", "-f", "srt", "pipe:1",
        ], timeout=10, stderr=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError):
        return ""
    srt = result.decode("utf-8", errors="replace")
    markers = list(SUBTITLE_TIMING.finditer(srt))
    cues = []
    for index, marker in enumerate(markers):
        hours, minutes, seconds = map(int, marker.groups())
        if hours * 3600 + minutes * 60 + seconds >= REFERENCE_SECONDS:
            break
        end = markers[index + 1].start() if index + 1 < len(markers) else len(srt)
        cue = transcript_text(srt[marker.end():end])
        if cue and (not cues or cues[-1] != cue):
            cues.append(cue)
    return transcript_text(" ".join(cues))


def embedded_transcript(path, info, audio):
    subtitles = sorted(
        (stream for stream in info["streams"]
         if stream.get("codec_type") == "subtitle" and stream.get("codec_name") in TEXT_SUBTITLE_CODECS),
        key=lambda stream: (bool(stream.get("disposition", {}).get("forced")),
                            not stream.get("disposition", {}).get("default")),
    )
    # Prefer timed text so the transcript follows the conditioning audio's cutoff.
    for stream in subtitles[:3]:
        text = subtitle_transcript(path, stream["index"])
        if text:
            return text
    tags = [source.get("tags", {}) for source in (audio, info["format"])]
    for field in ("transcript", "transcription", "captions", "subtitles", "lyrics", "unsyncedlyrics", "unsynced_lyrics"):
        for scope in tags:
            for key, value in scope.items():
                if key.lower().split("-", 1)[0] == field and (text := transcript_text(value)):
                    return text
    return None


def convert(path, dest, *args, timeout=30):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *INPUT_OPTIONS, "-i", str(path),
                    "-map", "0:a:0", *args, str(dest)], check=True, timeout=timeout,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def store_voice(uid, upload, name=None):
    if not upload.size or upload.size > MAX_VOICE_BYTES:
        raise ValueError("Upload a reference clip up to 25 MB.")
    key = uuid.uuid4().hex
    original_name = upload.filename.replace("\\", "/").rsplit("/", 1)[-1][:255] or "Recording"
    name = name or original_name.rsplit(".", 1)[0][:100] or "Recording"
    original = VOICES / f"{key}.source"
    conditioning = VOICES / f"{key}.wav"
    playback = VOICES / f"{key}.mp3"
    try:
        with original.open("wb") as file:
            shutil.copyfileobj(upload.file, file)
        info = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", *INPUT_OPTIONS, "-show_entries",
            "format=format_name,duration:format_tags:stream=index,codec_type,codec_name:stream_tags:stream_disposition=default,forced",
            "-of", "json", str(original),
        ], timeout=20, stderr=subprocess.DEVNULL))
        audio = next((stream for stream in info.get("streams", ()) if stream.get("codec_type") == "audio"), None)
        if audio is None:
            raise ValueError("Choose a file with an audio track.")
        container = info["format"]["format_name"]
        codec = audio["codec_name"]
        media_type = next((mime for fmt, (mime, codecs) in PLAYABLE.items()
                           if fmt in container.split(",") and codec in codecs), None)
        duration = info["format"].get("duration")
        convert(original, conditioning, "-t", str(REFERENCE_SECONDS), "-ac", "1", "-ar", "24000")
        if conditioning.stat().st_size < 4800:
            raise ValueError("Choose a longer reference clip.")
        if media_type is None:
            convert(original, playback, "-vn", "-c:a", "libmp3lame", "-q:a", "4", timeout=120)
        transcript = embedded_transcript(original, info, audio)
        with connect() as db:
            db.execute("INSERT INTO voices(id,user_id,name,original_name,media_type,size,duration,transcript) VALUES (?,?,?,?,?,?,?,?)",
                       (key, uid, name, original_name, media_type, upload.size, float(duration) if duration else None, transcript))
    except BaseException as exc:
        for path in (original, conditioning, playback):
            path.unlink(missing_ok=True)
        if isinstance(exc, (subprocess.SubprocessError, OSError, KeyError)):
            raise ValueError("This audio could not be decoded. Try WAV, MP3, FLAC, or WebM.") from None
        raise
    return key
