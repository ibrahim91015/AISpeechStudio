# Product

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
FastAPI, Jinja2, HTMX, plain CSS, SQLite, PyTorch CUDA, and FFmpeg.

## Purpose
Generate Arabic and English narration on the owner's Kubuntu GPU server. Keep the original source text, reference voices, generation settings, and exported recordings together.

## Operating Context
Users with network access can open the app and choose a local profile. The primary workflow is writing or importing text, choosing a model and voice, rendering audio, and returning to past work in the library.

## Constraints
One active inference job and one resident TTS model. The original Lahgtna Space defines the Arabic controls. Use the published Qwen 1.7B CustomVoice checkpoint for English.
