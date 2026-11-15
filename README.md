# AI Speech Studio

A local GPU speech studio for Lahgtna Chatterbox, Qwen3-TTS 12 Hz 1.7B CustomVoice, and IndexTTS 2.5. It uses FastAPI, Jinja, vendored HTMX, and a SQLite queue and library.

## Service

The app runs from `/opt/aispeech/tts-studio` on port 7860. Use `systemctl status aispeech-studio` and `journalctl -u aispeech-studio` for service logs. Model errors go to `data/worker.log`.

Anyone who can reach the port can use the app. Profiles organize the library. Microphone recording requires HTTPS or localhost. Audio uploads work over LAN HTTP.

## Models

- Lahgtna uses the sibling `lahgtna-chatterbox` checkout and the original weights in `../huggingface/hub`.
- Qwen loads `../models/Qwen3-TTS-12Hz-1.7B-CustomVoice`. Set `TTS_QWEN_MODEL` to select another compatible local checkpoint.
- IndexTTS 2.5 loads its [official checkpoint](https://huggingface.co/IndexTeam/IndexTTS-2.5) from `../models/IndexTTS-2.5`. Its isolated `.runtime/index` environment uses the upstream lockfile at `d9e41aac89fd00b3d71497fddb287b7f24613712`. Auxiliary weights are cached beside the checkpoint.
- Lahgtna auto-detection uses `../models/dialect-router-v0.1` on CPU. Its built-in reference clips come from revision `25e5e82` of the [reference Space](https://huggingface.co/spaces/oddadmix/lahgtna-chatterbox-demo/tree/25e5e82).
- Python 3.11 environments live in `.runtime/web`, `.runtime/lahgtna`, and `.runtime/qwen`. Use the corresponding requirements files. The model environments use `torch==2.6.0+cu124` and `torchaudio==2.6.0+cu124` from the PyTorch CUDA 12.4 index. FFmpeg is required.

### IndexTTS American English references

The clips in `data/references` are original single-speaker recordings from speech synthesis datasets released under CC0 1.0. They were trimmed at the edges, converted to mono 24 kHz lossless FLAC, and peak-adjusted. The two-utterance references have a brief pause at the join.

| Reference | Dataset and original clip IDs | Duration |
| --- | --- | ---: |
| Joe | [Open Home Foundation Joe](https://mozilladatacollective.com/datasets/cmid3cyuy00gnnv07mo54373n), `0000000105` | 9.2 s |
| Mike | [Open Home Foundation voice datasets](https://github.com/OHF-Voice/voice-datasets), `0000000198` and `0000000190` | 8.6 s |
| Kathleen | [Rhasspy Voice Dataset](https://github.com/rhasspy/dataset-voice-kathleen/tree/c51cb971036a127359b43c621f26ca5081eab13e), `arctic_a0022_1592748054` and `arctic_a0023_1592748016` | 8.9 s |

IndexTTS uses the first 15 seconds of the speaker reference. **Speaker voice** uses that recording for both timbre and delivery. Text or audio emotion guidance can be enabled separately. The [maintainers recommend](https://github.com/index-tts/index-tts#-inference) keeping text emotion strength around 0.6 or lower for natural speech.

The Studio defaults use a 260 ms pause between IndexTTS chunks and 0.50 emotion strength. The decoder and 320-character chunks follow the previous defaults. A wider sampling range and lower repetition penalty improved speaker similarity on a short English passage, but the previous decoder held the voice and estimated naturalness better on two longer passages. The shorter pause preserved those results in the follow-up test. The comparisons used SpeechBrain ECAPA, Whisper, and UTMOS.

## Queue and storage

SQLite and a worker lock enforce one running job. The worker exits its current model process before switching TTS models. It unloads a warm model after two idle minutes by default. Set `TTS_MODEL_IDLE_SECONDS` to change that period. LM Studio has its own independent lifecycle.

Long scripts run in sequential, punctuation-aware chunks. PCM is written to disk as it is produced. FFmpeg exports MP3 or FLAC and applies optional loudness normalization. Back up `data/` to preserve the library, voices, and audio. Use **Reopen** in job history to restore a script and its generation settings.

The Voices page keeps original uploads for playback and download. Lahgtna uses the first 30 seconds as its conditioning clip. Studio uploads and microphone clips also enter Voices.

Drop documents into the script editor or use **Import text**. EPUB and unprotected Kindle books open a range picker. Pages are estimated when the book has no page markers. The generation limit is 150,000 characters per script. On Ubuntu, install poppler-utils, libmobi-tools, tesseract-ocr-eng, and tesseract-ocr-ara for PDF, Kindle, and scan import.

Voice uploads begin on selection. A tab-local draft preserves text and per-model settings until the page is manually reloaded. Library MP3 share copies expire after one day, with hourly file cleanup.

For a reverse proxy that rewrites the upstream Host header, set `TTS_TRUSTED_ORIGINS` to the comma-separated public origins, including the HTTPS scheme. Visitors can click **Upload** to retry a selected voice file or submit it with JavaScript disabled.
