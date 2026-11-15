"""Higgs Audio v3 with the original BF16 checkpoint and a CUDA GGUF runtime."""
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from storage import AISPEECH, ROOT


LOCAL_HTTP = build_opener(ProxyHandler({}))
RUNTIME = ROOT / ".runtime"
MIB = 1024 ** 2


class HiggsBF16:
    def __init__(self, free_vram):
        import torch
        from system_stats import host_memory

        sys.path.insert(0, str(RUNTIME / "higgs-python"))
        path = RUNTIME / "higgs-native/native.py"
        spec = importlib.util.spec_from_file_location("higgs_native", path)
        native = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = native
        spec.loader.exec_module(native)
        native.tqdm = None
        self.native = native

        checkpoint = AISPEECH / "models/Higgs-TTS-3-4B-BF16"
        cuda = torch.device("cuda:0")
        available = host_memory()
        # Keep the neural codec on CPU when the autoregressive model needs the VRAM.
        codec_device = cuda if free_vram >= 12288 * MIB else torch.device("cpu")
        if codec_device.type == "cpu" and available and available["free"] < 4096 * MIB:
            raise RuntimeError("Higgs BF16 needs at least 4 GiB of available system RAM to offload its audio codec.")
        print(f"Higgs BF16 transformer on {cuda}, audio codec on {codec_device}", file=sys.stderr)
        self.bundle = SimpleNamespace(
            tokenizer=native.load_tokenizer(checkpoint), attention="sdpa",
            model=native.build_native_model(native.read_config(checkpoint), torch.bfloat16, "sdpa"),
        )
        native.load_native_weights(self.bundle.model, checkpoint, cuda, torch.bfloat16)
        self.bundle.codec = native.HiggsAudioCodec.from_pretrained(
            checkpoint, device=codec_device, dtype=torch.float32 if codec_device.type == "cpu" else torch.bfloat16,
        )
        self.reference = None

    def prepare(self, reference):
        self.reference = None
        if reference:
            n = self.native
            wav, rate = n.load_audio_path(reference)
            wav = n.trim_silence_edges(wav, rate)[:30 * rate]
            self.reference = n.apply_delay_pattern(self.bundle.codec.encode_reference(wav, rate))

    def render(self, text, transcript, settings):
        n, bundle = self.native, self.bundle
        prompt = bundle.tokenizer.build_prompt(
            text, num_ref_tokens=len(self.reference) if self.reference is not None else 0,
            reference_text=transcript or None,
        )
        delayed = bundle.model.generate_codes(
            prompt, self.reference, max_new_tokens=settings["max_new_tokens"],
            temperature=settings["temperature"], top_p=settings["top_p"], top_k=settings["top_k"],
        )
        wave = bundle.codec.decode(n.reverse_delay_pattern(delayed))
        return wave.numpy(), n.SAMPLE_RATE


class HiggsQ8:
    def __init__(self):
        root = RUNTIME / "higgs-cpp"
        torch_root = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        env = {**os.environ, "LD_LIBRARY_PATH": f"{root}:{torch_root / 'nvidia/nccl/lib'}"}
        with socket.socket() as socket_check:
            socket_check.bind(("127.0.0.1", 0))
            port = socket_check.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        model = AISPEECH / "models/Higgs-TTS-3-4B-Q8/Higgs-Audio-v3-TTS-4B-GGUF/higgs-audio-v3-tts-4b-q8_0.gguf"
        if not model.is_file():
            raise RuntimeError("The Higgs Q8 weights are missing.")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as config:
            json.dump({
                "host": "127.0.0.1", "port": port, "backend": "cuda", "device": 0,
                "threads": 2, "lazy_load": False, "max_loaded_models": 1, "ui": False,
                "models": [{"id": "higgs-q8", "family": "higgs_audio_tts", "task": "tts",
                            "mode": "offline", "path": str(model),
                            "session_options": {"higgs_audio_tts.reference_prefill_mode": "exact"}}],
            }, config)
            config.flush()
            self.process = subprocess.Popen(
                [str(root / "audiocpp_server"), "--config", config.name, "--no-ui",
                 "--min-free-memory-mb", "512"], cwd=root, env=env,
                stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr,
            )
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("The Higgs Q8 CUDA backend exited during model load. See worker.log.")
                try:
                    with LOCAL_HTTP.open(self.url + "/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except (OSError, URLError):
                    time.sleep(.35)
            else:
                raise RuntimeError("The Higgs Q8 CUDA backend did not become ready.")
        self.reference = None

    def prepare(self, reference):
        self.reference = reference

    def render(self, text, transcript, settings):
        import io
        import soundfile as sf

        options = {key: settings[key] for key in ("temperature", "top_k", "top_p")}
        options["max_tokens"] = settings["max_new_tokens"]
        if settings["seed"]:
            # Pick a reproducible seed per chunk, so identical requests sound the same.
            options["seed"] = int.from_bytes(hashlib.blake2s(text.encode(), digest_size=4).digest(), "little") ^ settings["seed"]
        request = {"model": "higgs-q8", "input": text, "response_format": "wav", "options": options}
        if self.reference:
            request.update(voice_ref=self.reference, reference_text=transcript)
        req = Request(self.url + "/v1/audio/speech", data=json.dumps(request).encode(),
                      headers={"Content-Type": "application/json"})
        try:
            with LOCAL_HTTP.open(req, timeout=160) as response:
                audio = response.read(16 * MIB + 1)
        except HTTPError as exc:
            detail = exc.read(1024).decode(errors="replace")
            raise RuntimeError(f"Higgs Q8 could not generate this chunk: {detail}") from exc
        if len(audio) > 16 * MIB:
            raise RuntimeError("Higgs Q8 returned more audio than one chunk can hold.")
        return sf.read(io.BytesIO(audio), dtype="float32")
