from dataclasses import dataclass


MODELS = {"qwen": "Qwen3-TTS CustomVoice", "lahgtna": "Lahgtna Chatterbox (Arabic)"}
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
EGYPTIAN_EXAMPLES = (
    "صباح الفل، تعالى نحكي عن أول يوم في الرحلة.",
    "إزيك؟ أنا جبتلك خبر حلو النهارده.",
    "خلي القهوة هنا، أنا راجع بعد شوية.",
    "الحكاية بدأت لما القطر وصل المحطة.",
)
QWEN_LANGUAGES = ("English", "Auto", "Chinese", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian")
SPEAKERS = {"Ryan": "English · male", "Aiden": "English · male", "Vivian": "Chinese · female", "Serena": "Chinese · female", "Dylan": "Beijing Mandarin · male", "Eric": "Sichuan Mandarin · male", "Uncle_Fu": "Chinese · male", "Ono_Anna": "Japanese · female", "Sohee": "Korean · female"}
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
COMMON = {"seed": Control("Seed (0 varies)", 42, 0, 4294967295, 1),
          "pause_ms": Control("Pause between chunks (ms)", 450, 0, 3000, 10)}
FLAGS = {"do_sample": "Sample main talker", "subtalker_dosample": "Sample sub-talker"}


def controls(model):
    chunk = Control("Characters per chunk", 220, 80, 280, 1) if model == "lahgtna" else Control("Characters per chunk", 320, 80, 900, 1)
    return {**(ARABIC if model == "lahgtna" else QWEN), **COMMON, "chunk_size": chunk}


def defaults(model):
    values = {k: c.value for k, c in controls(model).items()}
    values.update(format="wav", normalize=False, keep_tags=True)
    if model == "lahgtna":
        return dict(values, dialect="eg", voice_id="")
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
    else:
        settings.update(language=form.get("language", "English"), speaker=form.get("speaker", "Ryan"), instruct=form.get("instruct", "").strip())
        if settings["language"] not in QWEN_LANGUAGES or settings["speaker"] not in SPEAKERS:
            raise ValueError("Choose a supported Qwen language and voice.")
        if len(settings["instruct"]) > 600:
            raise ValueError("Keep the delivery instruction under 600 characters.")
        settings.update({k: form.get(k) == "1" for k in FLAGS})
    return settings
