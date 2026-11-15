from dataclasses import dataclass


MODELS = {"qwen": "Qwen3-TTS CustomVoice", "lahgtna": "Lahgtna Chatterbox (Arabic)", "index": "IndexTTS 2.5"}
DIALECTS = {
    "auto": ("Auto-detect dialect", "", ""),
    "eg": ("Egyptian · مصري", "ar_prompts2.flac", "إزيك؟ النهارده هنسجل حكاية جديدة."),
    "sa": ("Saudi · سعودي", "saudi-ref.wav", "هلا والله، اليوم بنسجل قصة قصيرة."),
    "mo": ("Moroccan · مغربي", "mor-ref.wav", "لاباس؟ اليوم غادي نسجلو واحد الحكاية قصيرة."),
    "iq": ("Iraqi · عراقي", "iraqi-ref.wav", "شلونك؟ هسه راح نسجل قصة قصيرة."),
    "lb": ("Lebanese · لبناني", "leb-ref.wav", "مرحبا، اليوم بدنا نسجّل قصة قصيرة."),
    "sd": ("Sudanese · سوداني", "fdddf1f2a317e626a0db94d6d7d92e772039121f120787c80be2250c09999ec1.wav", "يا زول، الليلة حنسجل لينا حكاية حلوة."),
    "ly": ("Libyan · ليبي", "lib-ref.wav", "شن حالك؟ اليوم بنسجلوا حكاية قصيرة."),
    "sy": ("Syrian · سوري", "syrian-ref.wav", "أهلين، اليوم رح نسجل حكاية قصيرة."),
    "tn": ("Tunisian · تونسي", "tun-ref.wav", "عسلامة، اليوم باش نسجلو حكاية قصيرة."),
    "ps": ("Palestinian · فلسطيني", "pal-ref.wav", "يعطيك العافية، اليوم بدنا نسجل حكاية."),
    "ar": ("Modern Standard Arabic · فصحى", "ar_prompts2.flac", "مرحبًا بكم، سنقرأ اليوم حكايةً قصيرةً."),
}
BUILTIN_VOICES = {key: (value[0], value[1]) for key, value in DIALECTS.items() if value[1]}
BUILTIN_VOICES["eg-alt"] = ("Egyptian · Alternate recording", "egypt-ref.wav")
QWEN_LANGUAGES = ("English", "Auto", "Chinese", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian")
SPEAKERS = {"Ryan": "English · male", "Aiden": "English · male", "Vivian": "Chinese · female", "Serena": "Chinese · female", "Dylan": "Beijing Mandarin · male", "Eric": "Sichuan Mandarin · male", "Uncle_Fu": "Chinese · male", "Ono_Anna": "Japanese · female", "Sohee": "Korean · female"}
INDEX_LANGUAGES = {"en": "English", "ar": "Arabic", "zh": "Chinese", "ja": "Japanese", "es": "Spanish", "zhen": "Chinese / English"}
EMOTIONS = ("Happy", "Angry", "Sad", "Afraid", "Disgusted", "Melancholic", "Surprised", "Calm")
TASHKIL = {"Fatha": "َ", "Damma": "ُ", "Kasra": "ِ", "Sukun": "ْ", "Shadda": "ّ", "Tanween Fath": "ً", "Tanween Damm": "ٌ", "Tanween Kasr": "ٍ"}


@dataclass(frozen=True)
class Control:
    label: str
    value: int | float
    minimum: int | float
    maximum: int | float
    step: int | float


ARABIC = {
    "exaggeration": Control("Exaggeration", .5, .25, 2, .01),
    "cfg_weight": Control("CFG weight", .5, 0, 1, .01),
    "repetition_penalty": Control("Repetition penalty", 1.75, 1, 2, .05),
    "temperature": Control("Temperature", .8, .05, 5, .01),
    "min_p": Control("Min-p", .05, 0, 1, .01),
    "top_p": Control("Top-p", 1., .01, 1, .01),
}
QWEN = {
    "temperature": Control("Temperature", .9, .05, 5, .01),
    "top_k": Control("Top-k", 50, 0, 1000, 1),
    "top_p": Control("Top-p", 1., .01, 1, .01),
    "repetition_penalty": Control("Repetition penalty", 1.05, 1, 3, .01),
    "max_new_tokens": Control("Max codec tokens per chunk", 1536, 64, 4096, 1),
    "subtalker_top_k": Control("Sub-talker top-k", 50, 0, 1000, 1),
    "subtalker_top_p": Control("Sub-talker top-p", 1., .01, 1, .01),
    "subtalker_temperature": Control("Sub-talker temperature", .9, .05, 5, .01),
}
INDEX_GENERATION = {
    "temperature": Control("Temperature", .8, .1, 2, .1),
    "top_k": Control("Top-k", 30, 0, 100, 1),
    "top_p": Control("Top-p", .8, 0, 1, .01),
    "num_beams": Control("Beam count", 3, 1, 10, 1),
    "repetition_penalty": Control("Repetition penalty", 10., .1, 20, .1),
    "length_penalty": Control("Length penalty", 0., -2, 2, .1),
    "max_mel_tokens": Control("Max mel tokens", 1500, 50, 1815, 1),
}
INDEX = {**INDEX_GENERATION, "max_text_tokens": Control("Max text tokens per segment", 120, 20, 600, 1),
         "segment_pause": Control("Pause inside a chunk (ms)", 200, 0, 3000, 1),
         "duration_factor": Control("Duration multiplier", 1., .5, 2, .01),
         "emo_alpha": Control("Emotion strength", .65, 0, 1, .01)}
COMMON = {"seed": Control("Seed (0 varies)", 42, 0, 4294967295, 1),
          "pause_ms": Control("Pause between chunks (ms)", 450, 0, 3000, 10)}
FLAGS = {"do_sample": "Sample main talker", "subtalker_dosample": "Sample sub-talker"}


def controls(model):
    chunk = Control("Characters per chunk", 220, 80, 280, 1) if model == "lahgtna" else Control("Characters per chunk", 320, 80, 900, 1)
    return {**{"lahgtna": ARABIC, "qwen": QWEN, "index": INDEX}[model], **COMMON, "chunk_size": chunk}


def defaults(model):
    values = {k: c.value for k, c in controls(model).items()}
    values.update(format="wav", normalize=False, keep_tags=True)
    if model == "lahgtna":
        return dict(values, dialect="eg", voice_id="")
    if model == "index":
        return dict(values, language="en", voice_id="", emo_mode="speaker", emo_voice="", emo_text="",
                    emo_vector=[0.] * len(EMOTIONS), emo_random=False, do_sample=True, text_normalization=True,
                    glossary="")
    return dict(values, language="English", speaker="Ryan", instruct="", **dict.fromkeys(FLAGS, True))


def settings_from(form, model):
    settings = defaults(model)
    for key, c in controls(model).items():
        try:
            val = type(c.value)(form.get(key, c.value))
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"Enter a valid value for {c.label}.") from None
        if not c.minimum <= val <= c.maximum:
            raise ValueError(f"{c.label} must be between {c.minimum} and {c.maximum}.")
        settings[key] = val
    settings["normalize"] = form.get("normalize") == "1"
    settings["format"] = form.get("format", "wav")
    if settings["format"] not in ("wav", "mp3", "flac"):
        raise ValueError("Choose WAV, MP3, or FLAC.")
    if model == "lahgtna":
        settings.update(dialect=form.get("dialect", "eg"), voice_id=form.get("voice_id", ""), keep_tags=form.get("keep_tags") == "1")
        if settings["dialect"] not in DIALECTS:
            raise ValueError("Choose an Arabic dialect.")
    elif model == "index":
        settings.update(language=form.get("language", "en"), voice_id=form.get("voice_id", ""),
                        emo_mode=form.get("emo_mode", "speaker"), emo_voice=form.get("emo_voice", ""),
                        emo_text=form.get("emo_text", "").strip(), emo_random=form.get("emo_random") == "1",
                        do_sample=form.get("do_sample") == "1", text_normalization=form.get("text_normalization") == "1",
                        glossary=form.get("glossary", "").strip())
        if settings["language"] not in INDEX_LANGUAGES or settings["emo_mode"] not in {"speaker", "audio", "vector", "text"}:
            raise ValueError("Choose a supported language and emotion mode.")
        try:
            vector = [float(form.get(f"emotion_{i}", 0)) for i in range(len(EMOTIONS))]
        except (ValueError, TypeError):
            raise ValueError("Use emotion strengths from 0 to 1.") from None
        if not all(0 <= value <= 1 for value in vector):
            raise ValueError("Use emotion strengths from 0 to 1.")
        settings["emo_vector"] = vector
        for line in settings["glossary"].splitlines():
            term, _, reading = line.partition("=")
            if line.strip() and (not term.strip() or not reading.strip()):
                raise ValueError("Write one glossary entry per line as term = spoken form.")
        if settings["emo_mode"] != "audio":
            settings["emo_voice"] = ""
    else:
        settings.update(language=form.get("language", "English"), speaker=form.get("speaker", "Ryan"), instruct=form.get("instruct", "").strip())
        if settings["language"] not in QWEN_LANGUAGES or settings["speaker"] not in SPEAKERS:
            raise ValueError("Choose a supported Qwen language and voice.")
        settings.update({k: form.get(k) == "1" for k in FLAGS})
    return settings
