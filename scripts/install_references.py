"""Install bundled references and optionally fetch public model-demo clips."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets" / "references"
MAX_DOWNLOAD = 32 * 1024 * 1024


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def merge_catalog(path, bundled, by_id=False):
    current = load_json(path, [] if by_id else {})
    if by_id:
        merged = {entry["id"]: entry for entry in bundled}
        merged.update({entry["id"]: entry for entry in current})
        data = list(merged.values())
    else:
        data = {**bundled, **current}
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as staged:
        json.dump(data, staged, ensure_ascii=False, indent=2)
        staged.write("\n")
    Path(staged.name).replace(path)


def fetch(url, destination):
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        source = Path(temp) / "source"
        with urlopen(url, timeout=30) as response, source.open("wb") as output:
            remaining = MAX_DOWNLOAD
            while chunk := response.read(min(1024 * 1024, remaining + 1)):
                remaining -= len(chunk)
                if remaining < 0:
                    raise ValueError("reference download exceeds 32 MiB")
                output.write(chunk)
        target = Path(temp) / destination.name
        if destination.suffix == ".wav":
            subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "stream=codec_name", "-of", "csv=p=0", str(source)],
                           check=True, stdout=subprocess.PIPE, timeout=30)
            source.replace(target)
        else:
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), "-map", "0:a:0",
                            "-ac", "1", "-c:a", "flac", str(target)], check=True, timeout=45)
        target.replace(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("TTS_DATA_DIR", ROOT / "data")))
    parser.add_argument("--upstream", action="store_true", help="also fetch separately hosted demo audio")
    args = parser.parse_args()
    target = args.data_dir / "references"
    target.mkdir(parents=True, exist_ok=True)
    clips = load_json(ASSETS / "demo-clips.json", [])
    transcripts = load_json(ASSETS / "reference-transcripts.json", {})
    for source in sorted(ASSETS.glob("*.flac")):
        if not (target / source.name).exists():
            shutil.copyfile(source, target / source.name)
    merge_catalog(target / "demo-clips.json", clips, by_id=True)
    merge_catalog(target / "reference-transcripts.json", transcripts)
    print(f"Reference catalog installed in {target}", flush=True)
    if not args.upstream:
        return
    upstream = load_json(ASSETS / "upstream.json", {})
    failures = []
    for filename, url in upstream["downloads"].items():
        path = target / filename
        if path.exists():
            continue
        try:
            fetch(url, path)
            print(f"Downloaded {filename}", flush=True)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"Could not download {filename}: {exc}", flush=True)
            failures.append(filename)
    for filename, details in upstream["manual"].items():
        if not (target / filename).exists():
            print(f"Manual import required for {filename}. See {details['source']}", flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
