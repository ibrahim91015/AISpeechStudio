#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."

command -v uv >/dev/null || { echo "Install uv and add it to PATH first." >&2; exit 1; }
case "${1:-web}" in
  web|qwen|lahgtna) mode="${1:-web}" ;;
  *) echo "Usage: $0 [web|qwen|lahgtna]" >&2; exit 2 ;;
esac

uv python install 3.11
if [[ ! -x .runtime/web/bin/python ]]; then
  uv venv --python 3.11 .runtime/web
fi
uv pip install --python .runtime/web/bin/python -r requirements.txt
.runtime/web/bin/python scripts/install_references.py

if [[ "$mode" == web ]]; then
  exit 0
fi

venv=".runtime/$mode"
if [[ ! -x "$venv/bin/python" ]]; then
  uv venv --python 3.11 "$venv"
fi
uv pip install --python "$venv/bin/python" \
  --index-url https://download.pytorch.org/whl/cu124 torch==2.6.0 torchaudio==2.6.0
uv pip install --python "$venv/bin/python" -r "requirements-$mode.txt"
if [[ "$mode" == lahgtna && ! -e ../lahgtna-chatterbox ]]; then
  git clone https://github.com/Oddadmix/lahgtna-chatterbox.git ../lahgtna-chatterbox
  git -C ../lahgtna-chatterbox checkout --detach 433cb74
fi
uv run --no-project --python 3.11 --with huggingface_hub scripts/download_models.py "$mode"
