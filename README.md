# AI Speech Studio

A local GPU speech studio for Lahgtna Chatterbox and Qwen3-TTS 12 Hz 1.7B CustomVoice. It uses FastAPI, Jinja, vendored HTMX, and a SQLite queue and library.

## Service

The app runs from `/opt/aispeech/tts-studio` on port 7860. Use `systemctl status aispeech-studio` and `journalctl -u aispeech-studio` for service logs. Model errors go to `data/worker.log`.

Anyone who can reach the port can use the app. Profiles organize the library. Microphone recording requires HTTPS or localhost. Audio uploads work over LAN HTTP.

## Models

- Lahgtna uses the sibling `lahgtna-chatterbox` checkout and the original weights in `../huggingface/hub`.
- Qwen loads `../models/Qwen3-TTS-12Hz-1.7B-CustomVoice`. Set `TTS_QWEN_MODEL` to select another compatible local checkpoint.
- IndexTTS 2.5 loads its [official checkpoint](https://huggingface.co/IndexTeam/IndexTTS-2.5) from `../models/IndexTTS-2.5`. Its isolated `.runtime/index` environment uses the upstream lockfile at `d9e41aac89fd00b3d71497fddb287b7f24613712`. Auxiliary weights are cached beside the checkpoint.
- Lahgtna auto-detection uses `../models/dialect-router-v0.1` on CPU. Its built-in reference clips come from revision `25e5e82` of the [reference Space](https://huggingface.co/spaces/oddadmix/lahgtna-chatterbox-demo/tree/25e5e82).
- Python 3.11 environments live in `.runtime/web`, `.runtime/lahgtna`, and `.runtime/qwen`. Use the corresponding requirements files. The model environments use `torch==2.6.0+cu124` and `torchaudio==2.6.0+cu124` from the PyTorch CUDA 12.4 index. FFmpeg is required.

## Queue and storage

SQLite and a worker lock enforce one running job. The worker exits its current model process before switching TTS models. It unloads a warm model after two idle minutes by default. Set `TTS_MODEL_IDLE_SECONDS` to change that period. LM Studio has its own independent lifecycle.

Long scripts run in sequential, punctuation-aware chunks. PCM is written to disk as it is produced. FFmpeg exports MP3 or FLAC and applies optional loudness normalization. Back up `data/` to preserve the library, voices, and audio. Use **Reopen** in job history to restore a script and its generation settings.

The Voices page keeps original uploads for playback and download. Lahgtna uses the first 30 seconds as its conditioning clip. Studio uploads and microphone clips also enter Voices. Drop text, Markdown, DOCX, ODT, RTF, and text-based PDFs into the script editor. PDF import uses Poppler's `pdftotext`.

Voice uploads begin on selection. A tab-local draft preserves text and per-model settings until the page is manually reloaded. Library MP3 share copies expire after one day, with hourly file cleanup.
