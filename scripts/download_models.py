"""Fetch optional model weights into the layout used by the Studio adapters."""

import argparse
import shutil
from pathlib import Path

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


def index_dependencies(root, snapshot, download):
    cache = root / "hf_cache"
    for repo, folder, patterns in (
        ("facebook/w2v-bert-2.0", "w2v-bert-2.0", ["config.json", "preprocessor_config.json", "model.safetensors"]),
        ("nvidia/bigvgan_v2_22khz_80band_256x", "bigvgan", ["config.json", "bigvgan_generator.pt"]),
    ):
        snapshot(repo, local_dir=cache / folder, allow_patterns=patterns)
    for repo, filename, destination in (
        ("amphion/MaskGCT", "semantic_codec/model.safetensors", "semantic_codec_model.safetensors"),
        ("funasr/campplus", "campplus_cn_common.bin", "campplus_cn_common.bin"),
    ):
        target = cache / destination
        if not target.is_file():
            cache.mkdir(parents=True, exist_ok=True)
            downloaded = download(repo, filename)
            staged = target.with_suffix(".part")
            shutil.copyfile(downloaded, staged)
            staged.replace(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+", metavar="MODEL",
                        help=", ".join([*MODELS, "lahgtna", "all"]))
    parser.add_argument("--plan", action="store_true", help="print destinations without downloading")
    args = parser.parse_args()
    available = {*MODELS, "lahgtna", "all"}
    unknown = set(args.models) - available
    if unknown:
        parser.error("unknown model(s): " + ", ".join(sorted(unknown)))
    selected = [*MODELS, "lahgtna"] if "all" in args.models else list(dict.fromkeys(args.models))
    if not args.plan:
        try:
            from huggingface_hub import hf_hub_download, snapshot_download
        except ImportError:
            parser.error("install huggingface_hub, or run with uv run --with huggingface_hub")
    for key in selected:
        if key == "lahgtna":
            repo = "oddadmix/lahgtna-chatterbox-v1"
            path = ASSETS / "huggingface" / "hub"
            print(f"{key}: {repo} → {path}/models--oddadmix--lahgtna-chatterbox-v1", flush=True)
            if not args.plan:
                snapshot_download(repo, cache_dir=path, revision="main")
            continue
        repo, folder, revision, patterns = MODELS[key]
        path = ASSETS / "models" / folder
        print(f"{key}: {repo} → {path}", flush=True)
        if key == "index":
            print(f"  Auxiliary IndexTTS weights → {path / 'hf_cache'}", flush=True)
        if not args.plan:
            snapshot_download(repo, local_dir=path, revision=revision, allow_patterns=patterns)
            if key == "index":
                index_dependencies(path, snapshot_download, hf_hub_download)


if __name__ == "__main__":
    main()
