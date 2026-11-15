"""One disposable CUDA process. The web process communicates over JSON lines."""
import copy
import gc
import json
import os
import random
import sys
import warnings
from contextlib import contextmanager
from pathlib import Path

from catalog import ARABIC, CHATTERBOX, DIALECTS, FLAGS, HIGGS_MODELS, INDEX_GENERATION, QWEN
from demo_voices import transcript_for
from generation_limits import SpeechLimitError
from storage import AISPEECH, REFERENCES, ROOT


VRAM_FLOOR_MIB = {"qwen_base": 5400, "higgs_bf16": 9850, "higgs_q8": 6800, "chatterbox": 4200}


def dialect_for(text):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    path = AISPEECH / "models/dialect-router-v0.1"
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
    classifier = AutoModelForSequenceClassification.from_pretrained(path, local_files_only=True).eval()
    with torch.inference_mode():
        tokens = tokenizer(text[:8000], return_tensors="pt", truncation=True, max_length=512)
        label = classifier.config.id2label[classifier(**tokens).logits.argmax(dim=-1).item()]
    del classifier, tokenizer, tokens
    gc.collect()
    return label if label in DIALECTS and label != "auto" else "eg"


def load(kind, report_load):
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in this model environment.")
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.cuda.init()
    report_load(1)
    free_vram, _ = torch.cuda.mem_get_info()
    required = VRAM_FLOOR_MIB.get(kind, 0)
    if free_vram < required * 1024 ** 2:
        raise RuntimeError(f"The model needs at least {required:,} MiB of free VRAM. Wait for GPU activity to finish and try again.")
    if kind in HIGGS_MODELS:
        from higgs_adapter import HiggsBF16, HiggsQ8

        return HiggsBF16(free_vram) if kind == "higgs_bf16" else HiggsQ8()
    if kind == "chatterbox":
        source = ROOT / ".runtime/chatterbox-v3-src/space/chatterbox/src"
        sys.path.insert(0, str(source))
        sys.path.insert(0, str(ROOT / ".runtime/chatterbox-deps"))
        from chatterbox.tts import ChatterboxTTS

        model = ChatterboxTTS.from_local(AISPEECH / "models/Chatterbox-Multilingual-V3", "cuda:0")
        model.studio_default_conds = model.conds
        return model
    if kind in {"qwen", "qwen_base"}:
        from qwen_tts import Qwen3TTSModel

        path = (AISPEECH / "models/Qwen3-TTS-12Hz-1.7B-Base" if kind == "qwen_base" else
                Path(os.getenv("TTS_QWEN_MODEL", AISPEECH / "models/Qwen3-TTS-12Hz-1.7B-CustomVoice")))
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        model = Qwen3TTSModel.from_pretrained(
            str(path), device_map="cuda:0", dtype=dtype, attn_implementation="sdpa",
            local_files_only=True, low_cpu_mem_usage=True,
        )
        model.model.eval()
        return model
    if kind == "index":
        from indextts.infer_v2_5 import IndexTTS2

        path = Path(os.getenv("TTS_INDEX_MODEL", AISPEECH / "models/IndexTTS-2.5"))
        return IndexTTS2(cfg_path=str(path / "config.yaml"), model_dir=str(path), device="cuda:0",
                         use_bf16=torch.cuda.is_bf16_supported(), use_cuda_kernel=False, use_qwen_emo=True)

    sys.path.insert(0, str(AISPEECH / "lahgtna-chatterbox"))
    from src.chatterbox.mtl_tts import ChatterboxMultilingualTTS

    repo = AISPEECH / "huggingface/hub/models--oddadmix--lahgtna-chatterbox-v1"
    checkpoint = repo / "snapshots" / (repo / "refs/main").read_text().strip()
    return ChatterboxMultilingualTTS.from_checkpoint(str(checkpoint) + "/", "cuda")


def prepare(kind, model, data, state):
    import numpy as np
    import torch

    settings = data["settings"]
    seed = settings["seed"]
    if seed:
        torch.manual_seed(seed)
        random.seed(seed)
        np.random.seed(seed)
    if kind == "qwen":
        return {}
    if kind == "qwen_base":
        state.clear()
        transcript = reference_transcript(settings)
        with torch.inference_mode():
            state["voice_clone_prompt"] = model.create_voice_clone_prompt(
                ref_audio=data["reference"], ref_text=transcript or None, x_vector_only_mode=settings["x_vector_only"],
            )
        return {}
    if kind in HIGGS_MODELS:
        reference = data["reference"]
        if reference:
            import soundfile as sf

            # Built-in references can be FLAC, even though job snapshots use this filename.
            path = Path(reference)
            with sf.SoundFile(path) as audio:
                wav = audio.read(frames=30 * audio.samplerate, dtype="float32", always_2d=True)
                rate = audio.samplerate
            reference = str(path.with_name("higgs-reference.wav"))
            sf.write(reference, wav.mean(axis=1), rate, subtype="PCM_16")
        with torch.inference_mode():
            model.prepare(reference)
        return {}
    if kind == "chatterbox":
        model.conds = copy.copy(model.studio_default_conds)
        return {"reference": data["reference"]}
    if kind == "index":
        return {"reference": data["reference"], "emotion": data["emotion"]}
    dialect = settings["dialect"]
    if dialect == "auto":
        dialect = dialect_for(data["script"])
    reference = data.get("reference") or str(REFERENCES / DIALECTS[dialect][1])
    return {"dialect": dialect, "reference": reference}


def reference_transcript(settings):
    return settings.get("reference_text") or transcript_for(settings.get("voice_id"))


@contextmanager
def check_end_token(module, method, eos, qwen=False):
    # Observe the codec stream before the adapter removes its end marker.
    original = getattr(module, method)

    def checked(*args, **kwargs):
        result = original(*args, **kwargs)
        codes = result.sequences if qwen else result
        limit = kwargs["max_new_tokens"]
        if codes.shape[-1] >= limit and not (codes[..., -1] == eos).all().item():
            raise SpeechLimitError("The model exhausted its audio tokens before the end of the chunk.")
        return result

    setattr(module, method, checked)
    try:
        yield
    finally:
        setattr(module, method, original)


def render(kind, model, data, state):
    import numpy as np
    import soundfile as sf
    import torch

    s = data["settings"]
    with torch.inference_mode():
        if kind in {"qwen", "qwen_base"}:
            generation = {k: s[k] for k in (*QWEN, *FLAGS)}
            if not s["do_sample"]:
                for key in ("temperature", "top_k", "top_p"):
                    generation.pop(key)
            if s.get("instruct"):
                prompts = [model._build_instruct_text(s["instruct"]), model._build_assistant_text(data["text"])]
                prompt_tokens = sum(tokens.numel() for tokens in model._tokenize_texts(prompts))
                limit = model.model.config.talker_config.max_position_embeddings
                if prompt_tokens + s["max_new_tokens"] + 16 > limit:
                    raise ValueError(f"Delivery instruction, script chunk and reserved codec output exceed the {limit:,}-token model context. Shorten the instruction or lower the codec token limit.")
            talker = model.model.talker
            with check_end_token(talker, "generate", talker.config.codec_eos_token_id, qwen=True):
                if kind == "qwen_base":
                    wavs, rate = model.generate_voice_clone(
                        text=data["text"], language=s["language"], voice_clone_prompt=state["voice_clone_prompt"], **generation,
                    )
                else:
                    wavs, rate = model.generate_custom_voice(
                        text=data["text"], language=s["language"], speaker=s["speaker"],
                        instruct=s["instruct"] or None, **generation,
                    )
            wav = wavs[0]
        elif kind in HIGGS_MODELS:
            wav, rate = model.render(data["text"], reference_transcript(s), s)
        elif kind == "index":
            mode = s["emo_mode"]
            vector = model.normalize_emo_vec(s["emo_vector"], apply_bias=True) if mode == "vector" else None
            options = {key: s[key] for key in INDEX_GENERATION}
            if not s["do_sample"]:
                for key in ("temperature", "top_k", "top_p"):
                    options.pop(key)
            elif options["top_k"] == 0:
                options["top_k"] = None
            text = data["text"]
            normalizer = model.text_process
            base_glossary = normalizer.term_glossary
            additions = {}
            for line in s.get("glossary", "").splitlines():
                if line.strip():
                    term, reading = (item.strip() for item in line.split("=", 1))
                    additions[term] = {"en": reading, "zh": reading}
            normalizer.term_glossary = {**base_glossary, **additions}
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings("error", message=r"WARN: generation stopped due to exceeding")
                    rate, wav = model.infer(data["reference"], text, None, s["language"],
                                            emo_audio_prompt=data["emotion"], emo_alpha=s["emo_alpha"], emo_vector=vector,
                                            use_emo_text=mode == "text", emo_text=s["emo_text"] or None,
                                            use_random=s["emo_random"], duration_factor=s["duration_factor"],
                                            text_normalization=s["text_normalization"], interval_silence=s["segment_pause"],
                                            max_text_tokens_per_segment=s["max_text_tokens"], do_sample=s["do_sample"], **options)
            except Warning as exc:
                if not str(exc).startswith("WARN: generation stopped due to exceeding `max_mel_tokens`"):
                    raise
                raise SpeechLimitError("IndexTTS exhausted its mel tokens before the end of the segment.") from exc
            finally:
                normalizer.term_glossary = base_glossary
            wav = wav.astype(np.float32) / 32768
        else:
            language = s["language"] if kind == "chatterbox" else data["dialect"]
            controls = CHATTERBOX if kind == "chatterbox" else ARABIC
            with check_end_token(model.t3, "inference", model.t3.hp.stop_speech_token):
                wav = model.generate(
                    data["text"], language_id=language,
                    audio_prompt_path=data["reference"] if data["first"] else None,
                    **{key: s[key] for key in controls},
                ).detach().cpu().float().numpy()
            rate = model.sr
    wav = np.asarray(wav, dtype=np.float32).reshape(-1)
    if not wav.size or not np.isfinite(wav).all():
        raise RuntimeError("The model did not return playable audio.")
    sf.write(data["output"], wav, rate, subtype="PCM_16")
    return {"samples": len(wav), "rate": rate}


def main():
    # Some model libraries print progress. Keep it off the protocol stream.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    kind = sys.argv[1]
    # Under host memory pressure, let the kernel reclaim this disposable process first.
    try:
        Path("/proc/self/oom_score_adj").write_text("500", encoding="ascii")
    except OSError:
        pass

    def checkpoint(step):
        print(json.dumps({"loaded_step": step}), file=protocol)

    import torch

    try:
        model = load(kind, checkpoint)
        checkpoint(2)
        torch.cuda.synchronize()
    except torch.OutOfMemoryError:
        print(json.dumps({"error": "CUDA ran out of memory while loading this model. Let other GPU activity finish and retry."}), file=protocol)
        raise
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=protocol)
        raise
    print(json.dumps({"ready": True}), file=protocol)
    state = {}
    for raw in sys.stdin:
        message = json.loads(raw)
        try:
            if message["op"] == "prepare":
                result = prepare(kind, model, message, state)
            else:
                result = render(kind, model, message, state)
            print(json.dumps({"ok": result}), file=protocol)
        except SpeechLimitError as exc:
            print(json.dumps({"speech_limit": str(exc)}), file=protocol)
        except torch.OutOfMemoryError:
            print(json.dumps({"error": "CUDA ran out of memory during generation. Try a shorter chunk or lower the audio token limit."}), file=protocol)
        except Exception as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=protocol)


if __name__ == "__main__":
    main()
