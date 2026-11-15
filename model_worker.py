"""One disposable CUDA process. The web process communicates over JSON lines."""
import gc
import json
import os
import random
import sys
from pathlib import Path

from catalog import ARABIC, DIALECTS, FLAGS, INDEX_GENERATION, QWEN
from storage import AISPEECH, REFERENCES


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
    if kind == "qwen":
        from qwen_tts import Qwen3TTSModel

        path = Path(os.getenv("TTS_QWEN_MODEL", AISPEECH / "models/Qwen3-TTS-12Hz-1.7B-CustomVoice"))
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


def prepare(kind, data):
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
    if kind == "index":
        return {"reference": data["reference"], "emotion": data["emotion"]}
    dialect = settings["dialect"]
    if dialect == "auto":
        dialect = dialect_for(data["script"])
    reference = data.get("reference") or str(REFERENCES / DIALECTS[dialect][1])
    return {"dialect": dialect, "reference": reference}


def render(kind, model, data):
    import numpy as np
    import soundfile as sf
    import torch

    s = data["settings"]
    with torch.inference_mode():
        if kind == "qwen":
            generation = {k: s[k] for k in (*QWEN, *FLAGS)}
            if not s["do_sample"]:
                for key in ("temperature", "top_k", "top_p"):
                    generation.pop(key)
            if s["instruct"]:
                prompts = [model._build_instruct_text(s["instruct"]), model._build_assistant_text(data["text"])]
                prompt_tokens = sum(tokens.numel() for tokens in model._tokenize_texts(prompts))
                limit = model.model.config.talker_config.max_position_embeddings
                if prompt_tokens + s["max_new_tokens"] + 16 > limit:
                    raise ValueError(f"Delivery instruction, script chunk and reserved codec output exceed the {limit:,}-token model context. Shorten the instruction or lower the codec token limit.")
            wavs, rate = model.generate_custom_voice(
                text=data["text"], language=s["language"], speaker=s["speaker"],
                instruct=s["instruct"] or None, **generation,
            )
            wav = wavs[0]
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
            rate, wav = model.infer(data["reference"], text, None, s["language"],
                                    emo_audio_prompt=data["emotion"], emo_alpha=s["emo_alpha"], emo_vector=vector,
                                    use_emo_text=mode == "text", emo_text=s["emo_text"] or None,
                                    use_random=s["emo_random"], duration_factor=s["duration_factor"],
                                    text_normalization=s["text_normalization"], interval_silence=s["segment_pause"],
                                    max_text_tokens_per_segment=s["max_text_tokens"], do_sample=s["do_sample"], **options)
            wav = wav.astype(np.float32) / 32768
        else:
            wav = model.generate(
                data["text"], language_id=data["dialect"],
                audio_prompt_path=data["reference"] if data["first"] else None,
                **{k: s[k] for k in ARABIC},
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
    def checkpoint(step):
        print(json.dumps({"loaded_step": step}), file=protocol)

    model = load(kind, checkpoint)
    checkpoint(2)
    import torch
    torch.cuda.synchronize()
    print(json.dumps({"ready": True}), file=protocol)
    for raw in sys.stdin:
        message = json.loads(raw)
        try:
            if message["op"] == "prepare":
                result = prepare(kind, message)
            else:
                result = render(kind, model, message)
            print(json.dumps({"ok": result}), file=protocol)
        except Exception as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=protocol)


if __name__ == "__main__":
    main()
