"""Shared, read-only recordings published as model demo references."""

import json
from pathlib import Path

from catalog import BUILTIN_VOICES, INDEX_ENGLISH_VOICES, REFERENCE_TRANSCRIPTS
from storage import REFERENCES


COLLECTIONS = {
    "qwen_base": ("Qwen3-TTS", "https://github.com/QwenLM/Qwen3-TTS/tree/main/examples"),
    "higgs_q8": ("Higgs Audio", "https://sgl-project.github.io/sglang-omni/cookbook/higgs_tts.html"),
    "chatterbox": ("Chatterbox Multilingual V3", "https://huggingface.co/spaces/ResembleAI/Chatterbox-Multilingual-TTS-V3"),
    "lahgtna": ("Lahgtna Chatterbox", "https://huggingface.co/spaces/oddadmix/lahgtna-chatterbox-demo"),
    "index": ("IndexTTS 2.5", "https://huggingface.co/spaces/IndexTeam/IndexTTS-2.5-Demo"),
    "vctk": ("Edinburgh VCTK English accents", "https://datashare.ed.ac.uk/handle/10283/3443"),
    "english": ("American English reference library", "https://github.com/rhasspy/piper/blob/master/VOICES.md"),
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


def voice_collections(with_originals=False, preferred=None):
    groups = {key: [] for key in COLLECTIONS}
    for clip in CLIPS.values():
        groups[clip["model"]].append(clip)
    if with_originals:
        groups["lahgtna"] = list(original_clips(BUILTIN_VOICES, "Arabic"))
        groups["english"] = list(original_clips(INDEX_ENGLISH_VOICES, "English · United States"))
    preferred = "higgs_q8" if preferred == "higgs_bf16" else preferred
    return [dict(key=key, label=COLLECTIONS[key][0], source=COLLECTIONS[key][1],
                 studio_model="index" if key in {"english", "vctk"} else key, clips=groups[key])
            for key in sorted(groups, key=lambda key: key != preferred) if groups[key]]


def transcript_for(voice_key):
    if voice_key and voice_key.startswith("demo:"):
        return CLIPS.get(voice_key.removeprefix("demo:"), {}).get("transcript", "")
    return REFERENCE_TRANSCRIPTS.get(voice_key, "")
