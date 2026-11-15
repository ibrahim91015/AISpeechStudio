import re
import unicodedata


RUN_CHAR_LIMIT = 600_000
RUN_BOUNDARY = re.compile(r"\n\s*\n|[.!?؟。！？][\"'”’»)]*(?=\s)|\s+")


class SpeechLimitError(RuntimeError):
    """The model exhausted its output-token budget before the speech was complete."""


def split_runs(text):
    start = 0
    remaining = (len(text) + RUN_CHAR_LIMIT - 1) // RUN_CHAR_LIMIT
    while remaining > 1:
        floor = max(RUN_CHAR_LIMIT - 8000, len(text) - RUN_CHAR_LIMIT * (remaining - 1))
        cut = RUN_CHAR_LIMIT
        for match in RUN_BOUNDARY.finditer(text, floor, RUN_CHAR_LIMIT):
            cut = match.end()
        while cut > floor and unicodedata.combining(text[cut]):
            cut -= 1
        piece = text[:cut].rstrip()
        yield start, start + len(piece), piece
        rest = text[cut:].lstrip()
        start += len(text) - len(rest)
        text = rest
        remaining -= 1
    if text:
        yield start, start + len(text), text
