"""Shared reference recordings and site-wide display settings."""

import json
from pathlib import Path

from catalog import BUILTIN_VOICES, INDEX_ENGLISH_VOICES, REFERENCE_TRANSCRIPTS
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


def original_clips(voices, language):
    for key, (name, filename) in voices.items():
        if (REFERENCES / filename).is_file():
            yield {"key": f"dialect:{key}", "name": name, "language": language,
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
    return [
        dict(key=key, label=label, source=source, visible=key not in hidden,
             dropdown_label="American English reference voices" if key == "english" else f"{label} reference clips",
             studio_model="index" if key in {"english", "vctk"} else key,
             clips=[{**clip, "name": names.get(clip["key"], clip["name"])} for clip in COLLECTION_CLIPS[key]])
        for key, (label, source) in COLLECTIONS.items() if COLLECTION_CLIPS[key]
    ]


def transcript_for(voice_key):
    if voice_key and voice_key.startswith("demo:"):
        return CLIPS.get(voice_key.removeprefix("demo:"), {}).get("transcript", "")
    return REFERENCE_TRANSCRIPTS.get(voice_key, "")
