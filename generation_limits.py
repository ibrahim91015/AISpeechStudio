import re
import unicodedata


RUN_CHAR_LIMIT = 600_000
RUN_BOUNDARY = re.compile(r"\n\s*\n|[.!?؟。！？][\"'”’»)]*(?=\s)|\s+")


class SpeechLimitError(RuntimeError):
    """The model exhausted its output-token budget before the speech was complete."""


def split_runs(text):
    start = 0
    while len(text) > RUN_CHAR_LIMIT:
        remaining = (len(text) + RUN_CHAR_LIMIT - 1) // RUN_CHAR_LIMIT
        floor = max(RUN_CHAR_LIMIT - 8000, len(text) - RUN_CHAR_LIMIT * (remaining - 1))
        cut = RUN_CHAR_LIMIT
        for match in RUN_BOUNDARY.finditer(text, floor, RUN_CHAR_LIMIT):
            cut = match.end()
        # Keep expression and pronunciation annotations together across run boundaries.
        for marker in re.finditer(r"<\|[^<>\n]+\|>|<[^<>\n]+\|[^<>\n]+>|\[[^\]\n]{1,40}\]", text[max(0, cut - 256):cut + 256]):
            lo, hi = max(0, cut - 256) + marker.start(), max(0, cut - 256) + marker.end()
            if lo < cut < hi:
                cut = lo
                break
        before_marks = cut
        while cut > 1 and unicodedata.category(text[cut]).startswith("M"):
            cut -= 1
            if before_marks - cut > 256:
                raise ValueError("The script contains an unusually long sequence of combining marks. Clean that text before generating.")
        piece = text[:cut].rstrip()
        if piece:
            yield start, start + len(piece), piece
        rest = text[cut:].lstrip()
        start += len(text) - len(rest)
        text = rest
        remaining -= 1
    if text:
        yield start, start + len(text), text
