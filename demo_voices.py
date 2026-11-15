"""Shared reference recordings and site-wide display settings."""

import json
from pathlib import Path

from catalog import BUILTIN_VOICES, INDEX_ENGLISH_VOICES, REFERENCE_TRANSCRIPTS, REFERENCE_VOICES
from storage import REFERENCES, connect


COLLECTIONS = {
    "english": ("American English reference library", "https://github.com/rhasspy/piper/blob/master/VOICES.md"),
    "qwen_base": ("Qwen3-TTS", "https://github.com/QwenLM/Qwen3-TTS/tree/main/examples"),
    "lahgtna": ("Lahgtna Chatterbox", "https://huggingface.co/spaces/oddadmix/lahgtna-chatterbox-demo"),
    "chatterbox": ("Chatterbox Multilingual V3", "https://huggingface.co/spaces/ResembleAI/Chatterbox-Multilingual-TTS-V3"),
    "higgs_q8": ("Higgs Audio", "https://sgl-project.github.io/sglang-omni/cookbook/higgs_tts.html"),
    "index": ("IndexTTS 2.5", "https://huggingface.co/spaces/IndexTeam/IndexTTS-2.5-Demo"),
    "vctk": ("Edinburgh VCTK English accents", "https://datashare.ed.ac.uk/handle/10283/3443"),
}


def load_clips():
    manifest = REFERENCES / "demo-clips.json"
    if not manifest.is_file():
        return {}
    data = json.loads(manifest.read_text(encoding="utf-8"))
    clips = {}
    for clip in data:
        filename = clip["file"]
        if Path(filename).name != filename or clip["model"] not in COLLECTIONS:
            raise ValueError("Invalid file or model in the reference clip catalog.")
        path = REFERENCES / filename
        if path.is_file():
            clips[clip["id"]] = {**clip, "key": f"demo:{clip['id']}", "path": path,
                                  "audio_url": f"/demo-references/{clip['id']}/audio"}
    return clips


CLIPS = load_clips()
TRANSCRIPT_FILE = REFERENCES / "reference-transcripts.json"
EXTRA_TRANSCRIPTS = json.loads(TRANSCRIPT_FILE.read_text(encoding="utf-8")) if TRANSCRIPT_FILE.is_file() else {}


def reference_filename(voice_key):
    if voice_key.startswith("demo:"):
        return CLIPS.get(voice_key.removeprefix("demo:"), {}).get("file")
    if voice_key.startswith("dialect:"):
        voice = REFERENCE_VOICES.get(voice_key.removeprefix("dialect:"))
        if voice and (REFERENCES / voice[1]).is_file():
            return voice[1]
    return None


def transcript_record(voice_key, filename=None):
    if not voice_key:
        return {}
    if voice_key.startswith("demo:"):
        clip = CLIPS.get(voice_key.removeprefix("demo:"), {})
        if clip.get("transcript"):
            return {"text": clip["transcript"], "kind": "published", "source": clip.get("source")}
    existing = REFERENCE_TRANSCRIPTS.get(voice_key)
    if existing:
        return {"text": existing, "kind": "published", "source": COLLECTIONS["english"][1]}
    return EXTRA_TRANSCRIPTS.get(filename or reference_filename(voice_key), {})


def edited_transcript(text):
    return {"text": text or "", "kind": "edited" if text else "none"}


def original_clips(voices, language):
    for key, (name, filename) in voices.items():
        if (REFERENCES / filename).is_file():
            yield {"key": f"dialect:{key}", "name": name, "language": language, "file": filename,
                   "audio_url": f"/references/{key}", "source": COLLECTIONS["lahgtna"][1] if language == "Arabic" else None}


def clips_by_collection():
    groups = {key: [] for key in COLLECTIONS}
    for clip in CLIPS.values():
        groups[clip["model"]].append(clip)
    groups["lahgtna"].extend(original_clips(BUILTIN_VOICES, "Arabic"))
    groups["english"].extend(original_clips(INDEX_ENGLISH_VOICES, "English · United States"))
    return groups


COLLECTION_CLIPS = clips_by_collection()


def voice_collections():
    with connect() as db:
        names = dict(db.execute("SELECT clip_key,name FROM reference_clip_names").fetchall())
        hidden = {row[0] for row in db.execute("SELECT collection_key FROM hidden_reference_collections")}
        edits = dict(db.execute("SELECT filename,text FROM reference_transcript_edits").fetchall())

    def details(clip):
        key = clip["key"]
        filename = clip["file"]
        record = edited_transcript(edits[filename]) if filename in edits else transcript_record(key, filename)
        return {**clip, "name": names.get(key, clip["name"]), "transcript_asset": "ref:" + filename, "transcript_record": record}

    return [
        dict(key=key, label=label, source=source, visible=key not in hidden,
             dropdown_label="American English reference voices" if key == "english" else f"{label} reference clips",
             studio_model="index" if key in {"english", "vctk"} else key,
             clips=[details(clip) for clip in COLLECTION_CLIPS[key]])
        for key, (label, source) in COLLECTIONS.items() if COLLECTION_CLIPS[key]
    ]


def transcript_for(voice_key, *, first_30s=False):
    record = transcript_record(voice_key)
    if first_30s and "first_30s" in record:
        return record["first_30s"]
    return record.get("text", "")
