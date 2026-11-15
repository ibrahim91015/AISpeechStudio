"""Fetch optional model weights into the layout used by the Studio adapters."""

import argparse
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT.parent
MODELS = {
    "qwen": ("Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", "Qwen3-TTS-12Hz-1.7B-CustomVoice", None, None),
    "qwen_base": (
        "Qwen/Qwen3-TTS-12Hz-1.7B-Base", "Qwen3-TTS-12Hz-1.7B-Base",
        "fd4b254389122332181a7c3db7f27e918eec64e3", None,
    ),
    "index": ("IndexTeam/IndexTTS-2.5", "IndexTTS-2.5", None, None),
    "dialect_router": (
        "oddadmix/dialect-router-v0.1", "dialect-router-v0.1",
        "2318b628f86bad2a62acae396030f941a986fcbd", None,
    ),
    "chatterbox": (
        "ResembleAI/chatterbox", "Chatterbox-Multilingual-V3",
        "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
        ["t3_mtl23ls_v3.safetensors", "ve.pt", "s3gen_v3.pt",
         "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json"],
    ),
    "higgs_bf16": (
        "bosonai/higgs-tts-3-4b", "Higgs-TTS-3-4B-BF16",
        "239f63fb7b02b1aa085f98d9efae5e35cc5523e8", None,
    ),
    "higgs_q8": (
        "audio-cpp/audio.cpp-gguf", "Higgs-TTS-3-4B-Q8",
        "ca0b203eb1bb87f333a7cfbad2d5f6672b4ccba7",
        ["Higgs-Audio-v3-TTS-4B-GGUF/higgs-audio-v3-tts-4b-q8_0.gguf"],
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+", choices=[*MODELS, "lahgtna"])
    for key in parser.parse_args().models:
        if key == "lahgtna":
            path = snapshot_download(
                "oddadmix/lahgtna-chatterbox-v1",
                cache_dir=ASSETS / "huggingface" / "hub",
                revision="main",
            )
        else:
            repo, folder, revision, patterns = MODELS[key]
            path = snapshot_download(
                repo, local_dir=ASSETS / "models" / folder,
                revision=revision, allow_patterns=patterns,
            )
        print(f"{key}: {path}", flush=True)


if __name__ == "__main__":
    main()
