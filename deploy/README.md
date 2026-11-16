# Linux setup

Follow the [Ubuntu quick start](../README.md#start-on-ubuntu) to install the web app and make a first recording. Keep the checkout under `~/aispeech/tts-studio` to use the example unit as supplied.

## Host the app

The [systemd user service](aispeech-studio.service) reads `~/.config/aispeech-studio.env`. Configure an authenticated HTTPS reverse proxy that forwards to `127.0.0.1:7860`, with upload capacity above 65 MiB for multipart overhead. Add your public origin and a contact URI for Web Push.

```ini
TTS_TRUSTED_ORIGINS=https://speech.example.com
TTS_PUSH_SUBJECT=mailto:admin@example.com
```

Restart with `systemctl --user restart aispeech-studio` and view logs with `journalctl --user -u aispeech-studio`. Run `sudo loginctl enable-linger "$(id -un)"` to start the user service at boot without a login.

## Model runtimes

Install each Python environment separately. Use the CUDA PyTorch wheels that fit your installed driver. The original Qwen and Lahgtna environments used PyTorch and torchaudio 2.6.0 with CUDA 12.4. The worker uses the Python executable under each runtime's `bin/` directory.

| Model | Python environment | Additional upstream components |
| --- | --- | --- |
| Qwen3-TTS CustomVoice and Base | `.runtime/qwen`, [requirements-qwen.txt](../requirements-qwen.txt) | Model weights under the sibling `models/` directory |
| Lahgtna Chatterbox | `.runtime/lahgtna`, [requirements-lahgtna.txt](../requirements-lahgtna.txt) | Clone [Oddadmix/lahgtna-chatterbox](https://github.com/Oddadmix/lahgtna-chatterbox) beside the Studio at revision `433cb74`. Speakers use reference audio downloaded separately from the [Lahgtna demo](https://huggingface.co/spaces/oddadmix/lahgtna-chatterbox-demo/tree/25e5e82). The filenames are defined in `catalog.py`. |
| Chatterbox Multilingual V3 | `.runtime/lahgtna`, with its V3-specific packages on `.runtime/chatterbox-deps` | Clone the [upstream demo Space](https://huggingface.co/spaces/ResembleAI/Chatterbox-Multilingual-TTS-V3) at revision `b21d9d062b4eda102f21975333276919935d2060` into `.runtime/chatterbox-v3-src/space`. Use its package requirements for V3-only dependencies. |
| IndexTTS 2.5 | `.runtime/index` | Install [index-tts/index-tts](https://github.com/index-tts/index-tts) at `d9e41aac89fd00b3d71497fddb287b7f24613712` using its upstream lockfile. Download its required auxiliary assets before running offline. |
| Higgs TTS BF16 | `.runtime/index`, with Transformers 5.5.0 and its dependencies on `.runtime/higgs-python` | Put the native backend from [Higgs_v3-TTS-ComfyUI](https://github.com/Saganaki22/Higgs_v3-TTS-ComfyUI) v0.1.9 in `.runtime/higgs-native`. |
| Higgs TTS Q8 | `.runtime/index` | Put the CUDA audio.cpp server and required libraries in `.runtime/higgs-cpp`. The adapter expects the executable to be named `audiocpp_server`. |

## Weight sources and destinations

Paths are relative to the Studio checkout. Run `python3 scripts/download_models.py all --plan` to display them. To fetch model files, run `uv run --no-project --python 3.11 --with huggingface_hub scripts/download_models.py all`, or replace `all` with one or more keys.

| Key | Source | Destination |
| --- | --- | --- |
| `qwen` | [Qwen CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) | `../models/Qwen3-TTS-12Hz-1.7B-CustomVoice` |
| `qwen_base` | [Qwen Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base) | `../models/Qwen3-TTS-12Hz-1.7B-Base` |
| `lahgtna` | [Lahgtna Chatterbox](https://huggingface.co/oddadmix/lahgtna-chatterbox-v1) | `../huggingface/hub/models--oddadmix--lahgtna-chatterbox-v1` in the Hub cache layout |
| `dialect_router` | [Lahgtna dialect router](https://huggingface.co/oddadmix/dialect-router-v0.1) | `../models/dialect-router-v0.1` |
| `chatterbox` | [ResembleAI/chatterbox](https://huggingface.co/ResembleAI/chatterbox) | `../models/Chatterbox-Multilingual-V3` |
| `index` | [IndexTTS 2.5](https://huggingface.co/IndexTeam/IndexTTS-2.5) | `../models/IndexTTS-2.5`, with w2v-BERT, MaskGCT, CAMPPlus and BigVGAN under `hf_cache/` |
| `higgs_bf16` | [Boson AI Higgs TTS 3](https://huggingface.co/bosonai/higgs-tts-3-4b) | `../models/Higgs-TTS-3-4B-BF16` |
| `higgs_q8` | [audio.cpp Q8 GGUF](https://huggingface.co/audio-cpp/audio.cpp-gguf/tree/main/Higgs-Audio-v3-TTS-4B-GGUF) | `../models/Higgs-TTS-3-4B-Q8/Higgs-Audio-v3-TTS-4B-GGUF/` |

The downloader installs the weights. Set up each model's Python environment and native runtime using the table above. For Higgs Q8, obtain the compatible CUDA build from the [audio.cpp release page](https://github.com/0xShug0/audio.cpp/releases/tag/v0.9.5). Inference runs offline after setup.
