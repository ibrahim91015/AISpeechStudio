import json
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


def convert(path, dest, *args, timeout=30):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *INPUT_OPTIONS, "-i", str(path),
                    "-map", "0:a:0", *args, str(dest)], check=True, timeout=timeout,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def store_voice(uid, upload, name=None):
    if not upload.size or upload.size > 24 * 1024 * 1024:
        raise ValueError("Upload a reference clip up to 24 MB.")
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
            "ffprobe", "-v", "error", *INPUT_OPTIONS, "-select_streams", "a:0", "-show_entries",
            "format=format_name,duration:stream=codec_name", "-of", "json", str(original),
        ], timeout=20, stderr=subprocess.DEVNULL))
        if not info.get("streams"):
            raise ValueError("Choose a file with an audio track.")
        container = info["format"]["format_name"]
        codec = info["streams"][0]["codec_name"]
        media_type = next((mime for fmt, (mime, codecs) in PLAYABLE.items()
                           if fmt in container.split(",") and codec in codecs), None)
        duration = info["format"].get("duration")
        convert(original, conditioning, "-t", "30", "-ac", "1", "-ar", "24000")
        if conditioning.stat().st_size < 4800:
            raise ValueError("Choose a longer reference clip.")
        if media_type is None:
            convert(original, playback, "-vn", "-c:a", "libmp3lame", "-q:a", "4", timeout=120)
        with connect() as db:
            db.execute("INSERT INTO voices(id,user_id,name,original_name,media_type,size,duration) VALUES (?,?,?,?,?,?,?)",
                       (key, uid, name, original_name, media_type, upload.size, float(duration) if duration else None))
    except BaseException as exc:
        for path in (original, conditioning, playback):
            path.unlink(missing_ok=True)
        if isinstance(exc, (subprocess.SubprocessError, OSError, KeyError)):
            raise ValueError("This audio could not be decoded. Try WAV, MP3, FLAC, or WebM.") from None
        raise
    return key
