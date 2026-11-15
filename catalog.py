from dataclasses import dataclass


MODELS = {
    "qwen": "Qwen3-TTS CustomVoice", "lahgtna": "Lahgtna Chatterbox (Arabic)", "index": "IndexTTS 2.5",
    "qwen_base": "Qwen3-TTS 12Hz 1.7B Base", "higgs_bf16": "Higgs TTS 3 4B (BF16)",
    "higgs_q8": "Higgs TTS 3 4B (Q8)", "chatterbox": "Chatterbox Multilingual V3",
}
HIGGS_MODELS = {"higgs_bf16", "higgs_q8"}
REFERENCE_MODELS = set(MODELS) - {"qwen"}
REQUIRED_REFERENCE = {"index", "qwen_base"}
REFERENCE_TRANSCRIPTS = {
    "dialect:en-us-joe": "If being able to work from home is important to you, it's good to get a sense of a company's flexibility before getting too far along in the interview process.",
    "dialect:en-us-mike": "There are beautiful vistas, secluded villages, and a range of exhilarating walks en route. You'd be surprised at just how outrageous or outlandish some of the claims are.",
}
CHATTERBOX_LANGUAGES = dict(zip(
    "ar da de el en es fi fr he hi it ja ko ms nl no pl pt ru sv sw tr zh".split(),
    "Arabic Danish German Greek English Spanish Finnish French Hebrew Hindi Italian Japanese Korean Malay Dutch Norwegian Polish Portuguese Russian Swedish Swahili Turkish Chinese".split(),
))
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
INDEX_ENGLISH_VOICES = {
    "en-us-joe": ("Joe · male", "index-en-us-joe.flac"),
    "en-us-mike": ("Mike · male", "index-en-us-mike.flac"),
    "en-us-kathleen": ("Kathleen · female", "index-en-us-kathleen.flac"),
}
REFERENCE_VOICES = BUILTIN_VOICES | INDEX_ENGLISH_VOICES
ARABIC_SAMPLES = {
    "eg": ("صباح الفل، تعالى نحكي عن أول يوم في الرحلة.", "خلي القهوة هنا، أنا راجع بعد شوية."),
    "sa": ("خذ راحتك، السالفة توها بادية.", "تعال نقعد شوي ونشرب قهوة."),
    "mo": ("واش نخرجو ندورو شوية فهاد الزنقة؟", "هاد الصباح مشيت للسوق وجبت الخبز."),
    "iq": ("هسه نطلع نشرب چاي ونرجع للبيت.", "اليوم الجو حلو، شرايك نتمشى شوي؟"),
    "lb": ("شو رأيك نقعد شوي هون ونشرب قهوة؟", "اليوم الجو حلو كتير، خلّينا نتمشّى."),
    "sd": ("يا زول، خلينا نمشي السوق قبل المغرب.", "الجو سمح الليلة، نقعد شوية برة؟"),
    "ly": ("شن رايك نمشوا للبحر قبل الغروب؟", "توا نرجعوا للحوش ونشربوا شاهي."),
    "sy": ("شو رأيك نطلع نتمشّى قبل ما تغيب الشمس؟", "هلّق بعمل قهوة ومنقعد نحكي شوي."),
    "tn": ("شنوّة رايك نمشيو للبحر قبل ما تغرب الشمس؟", "توّة نرجع للدار ونحضّر قهوة."),
    "ps": ("يلا نمشي شوي قبل ما تروح الشمس.", "هلّأ بعمل قهوة ومنقعد نحكي."),
    "ar": ("وصل القطار قبل الغروب بقليل.", "رحّب الضيوف بالمضيف وجلسوا في الفناء."),
}
ARABIC_SAMPLES = {key: (DIALECTS[key][2], *lines) for key, lines in ARABIC_SAMPLES.items()}
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
HIGGS = {
    "temperature": Control("Temperature", .8, .05, 2, .01),
    "top_k": Control("Top-k", 30, 1, 100, 1),
    "top_p": Control("Top-p", .8, .01, 1, .01),
    "max_new_tokens": Control("Max audio tokens per chunk", 1024, 64, 2048, 1),
}
CHATTERBOX = {key: ARABIC[key] for key in ("exaggeration", "cfg_weight", "temperature")}
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
         "emo_alpha": Control("Emotion strength", .50, 0, 1, .01)}
COMMON = {"seed": Control("Seed (0 varies)", 42, 0, 4294967295, 1),
          "pause_ms": Control("Pause between chunks (ms)", 450, 0, 3000, 10)}
FLAGS = {"do_sample": "Sample main talker", "subtalker_dosample": "Sample sub-talker"}


def controls(model):
    chunk = (Control("Characters per chunk", 220, 80, 280, 1) if model in {"lahgtna", "chatterbox"}
             else Control("Characters per chunk", 320, 80, 900, 1))
    specific = {"lahgtna": ARABIC, "qwen": QWEN, "qwen_base": QWEN, "index": INDEX,
                "higgs_bf16": HIGGS, "higgs_q8": HIGGS, "chatterbox": CHATTERBOX}
    result = {**specific[model], **COMMON, "chunk_size": chunk}
    if model == "index":
        result["pause_ms"] = Control("Pause between chunks (ms)", 260, 0, 3000, 10)
    return result


def defaults(model):
    values = {k: c.value for k, c in controls(model).items()}
    values.update(format="wav", normalize=False, keep_tags=True)
    if model == "lahgtna":
        return dict(values, dialect="eg", voice_id="")
    if model == "index":
        return dict(values, language="en", voice_id="", emo_mode="speaker", emo_voice="", emo_text="",
                    emo_vector=[0.] * len(EMOTIONS), emo_random=False, do_sample=True, text_normalization=True,
                    glossary="")
    if model == "qwen_base":
        return dict(values, language="English", voice_id="", reference_text="", x_vector_only=False, **dict.fromkeys(FLAGS, True))
    if model in HIGGS_MODELS:
        return dict(values, voice_id="", reference_text="")
    if model == "chatterbox":
        return dict(values, language="en", voice_id="")
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
    elif model in HIGGS_MODELS or model in {"qwen_base", "chatterbox"}:
        settings["voice_id"] = form.get("voice_id", "")
        if model != "chatterbox":
            settings["reference_text"] = form.get("reference_text", "").strip()
            if len(settings["reference_text"]) > 2048:
                raise ValueError("Keep the reference transcript within 2,048 characters.")
        if model == "qwen_base":
            settings["language"] = form.get("language", "English")
            settings["x_vector_only"] = form.get("x_vector_only") == "1"
            settings.update({k: form.get(k) == "1" for k in FLAGS})
            if settings["language"] not in QWEN_LANGUAGES:
                raise ValueError("Choose a supported Qwen language.")
        if model == "chatterbox":
            settings["language"] = form.get("language", "en")
            if settings["language"] not in CHATTERBOX_LANGUAGES:
                raise ValueError("Choose a supported Chatterbox language.")
    else:
        settings.update(language=form.get("language", "English"), speaker=form.get("speaker", "Ryan"), instruct=form.get("instruct", "").strip())
        if settings["language"] not in QWEN_LANGUAGES or settings["speaker"] not in SPEAKERS:
            raise ValueError("Choose a supported Qwen language and voice.")
        settings.update({k: form.get(k) == "1" for k in FLAGS})
    return settings
