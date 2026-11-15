# Linux setup

The web app uses Linux and Python 3.11. GPU inference needs an NVIDIA CUDA environment. The web app starts with an empty database and can be used to test imports and navigation before installing any model. Profiles organize data but are not an authentication boundary. Put an authenticated reverse proxy in front of a deployment that is reachable from the internet.

```sh
mkdir -p ~/aispeech
git clone https://github.com/ibrahim91015/AISpeechStudio.git ~/aispeech/tts-studio
cd ~/aispeech/tts-studio
python3.11 -m venv .runtime/web
.runtime/web/bin/python -m pip install -r requirements.txt
.runtime/web/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7860 --workers 1
```

Install FFmpeg, Poppler utilities, libmobi tools, and Tesseract with English and Arabic language data to use the corresponding audio and document features. On Ubuntu the package names are `ffmpeg poppler-utils libmobi-tools tesseract-ocr-eng tesseract-ocr-ara`.

For a user systemd service, copy [aispeech-studio.service](aispeech-studio.service) to `~/.config/systemd/user/`, adapt the paths if needed, then run `systemctl --user daemon-reload` and `systemctl --user enable --now aispeech-studio`. To start it without an interactive login, enable systemd lingering for the service account. The sample binds to loopback. For a reverse proxy, set your HTTPS origin in `TTS_TRUSTED_ORIGINS` and a VAPID contact URI in `TTS_PUSH_SUBJECT` in `~/.config/aispeech-studio.env`. The app creates its own push keys and SQLite database on first run.

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

Install `huggingface_hub` in a separate tool environment, then use [download_models.py](../scripts/download_models.py) to fetch only the model weights you need. For example, run `python scripts/download_models.py qwen` for CustomVoice. Run `python scripts/download_models.py --help` for all model choices. Review upstream terms before downloading. The worker runs offline and expects the assets to be present before synthesis.

This repository ships no recordings. Upload your own via the Studio, or provision separately sourced reference clips under `data/references` (or the `TTS_DATA_DIR` equivalent). To register optional reference collections, populate `references/demo-clips.json` with entries containing `id`, `model`, `name`, `file`, `language`, and optionally `source`, `transcript` and `duration`. Use the model group names in `demo_voices.py`. Paths, libraries, and models for advanced adapters are installed separately.
