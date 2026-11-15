import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path


PARAGRAPH_TAGS = {"P", "H", "H1", "H2", "H3", "H4", "H5", "H6", "LI", "TD", "TH",
                  "Caption", "BlockQuote", "Code", "Note"}


def command_text(command, timeout, limit):
    env = dict(os.environ, LC_ALL="C.UTF-8", OMP_THREAD_LIMIT="1")
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        result = subprocess.run(command, stdout=output, stderr=error, timeout=timeout, env=env)
        if result.returncode:
            error.seek(0)
            raise subprocess.CalledProcessError(result.returncode, command, stderr=error.read(4096))
        output.seek(0)
        data = output.read(limit + 1)
        if len(data) > limit:
            raise ValueError("The extracted text is too large to import. Split the PDF before importing.")
        return data.decode("utf-8", errors="replace")


def tagged_paragraphs(text):
    blocks = []
    stack = []
    for line in text.splitlines():
        value = line.strip()
        if not value:
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        active = stack[-1][1] if stack else None
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            if active is not None and active is not False:
                active.append(value[1:-1])
            continue
        tag = value.split(maxsplit=1)[0]
        if tag in {"Artifact", "Figure", "Caption"}:
            active = False
        elif tag in PARAGRAPH_TAGS and active is not False:
            active = []
            blocks.append(active)
        stack.append((indent, active))
    return "\n\n".join(text for block in blocks if (text := " ".join(" ".join(block).split())))


def reflow_pdf(page):
    # A soft hyphen denotes a line-wrap opportunity. A printed hyphen may be part of a word.
    page = re.sub(r"(?<=\w)\u00ad[ \t]*\n[ \t]*(?=\w)", "", page)
    paragraphs = re.split(r"\n[ \t]*\n+", page.strip())
    return "\n\n".join(" ".join(line.strip() for line in block.splitlines() if line.strip())
                       for block in paragraphs)


def sentence_end(text):
    return text.rstrip('”’"»)]').endswith((".", "?", "!", "…", "؟", "。"))


def print_paragraphs(layout):
    lines = layout.strip("\n").splitlines()
    widths = [len(line.strip()) for line in lines if line.strip()]
    if len(widths) < 4:
        return reflow_pdf(layout)
    margin = min(len(line) - len(line.lstrip()) for line in lines if line.strip())
    width = sorted(widths)[len(widths) * 3 // 4]
    if width < 35:
        return reflow_pdf(layout)
    if not any(2 <= len(line) - len(line.lstrip()) - margin <= 12 for line in lines if line.strip()):
        return reflow_pdf(layout)
    paragraphs, current = [], []
    for line in lines:
        text = line.strip()
        indent = len(line) - len(line.lstrip()) - margin
        new_paragraph = (
            not text or (2 <= indent <= 12 and current and
                         sentence_end(current[-1]) and len(current[-1]) < width * .93)
        )
        if new_paragraph and current:
            paragraphs.append(" ".join(current))
            current = []
        if text:
            current.append(text)
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


def join_print_pages(pages, layout):
    parts = []
    for page, shape in zip(pages, layout):
        if not page.strip():
            continue
        same_text = shape and " ".join(page.split()) == " ".join(shape.split())
        result = print_paragraphs(shape) if same_text else reflow_pdf(page)
        continued = parts and result[0].islower() and not sentence_end(parts[-1])
        parts.extend((" " if continued else "\n\n", result))
    return "".join(parts).strip()


def scan_pages(path, pages, missing, work, limit):
    warnings = []
    deadline = time.monotonic() + 35
    attempted = 0
    for number in missing[:12]:
        image = work / "page"
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            command_text(["pdftoppm", "-f", str(number), "-l", str(number), "-singlefile",
                          "-scale-to", "2200", "-gray", "-png", str(path), str(image)], min(8, remaining), limit)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            pages[number - 1] = command_text(["tesseract", str(image.with_suffix(".png")), "stdout",
                                               "-l", "eng+ara"], min(12, remaining), limit)
            attempted += 1
        except (subprocess.SubprocessError, OSError):
            continue
    if any(pages[number - 1].strip() for number in missing):
        warnings.append("Some pages were read with OCR. Check the extracted text for errors.")
    if attempted < len(missing):
        warnings.append("Some pages could not be scanned during import. Try a smaller PDF with those pages.")
    return warnings


def clean_print_pages(pages, layout):
    """Conservative print furniture removal; never guess at unlabeled narrative."""
    lines = [[line.strip() for line in page.splitlines() if line.strip()] for page in pages]
    for edge in (0, -1):
        counts = Counter(page[edge] for page in lines if len(page) >= 4)
        repeated = {line for line, count in counts.items() if count >= max(3, len(pages) * .2)
                    and len(line) < 120 and not sentence_end(line)}
        numbers = {i: int(page[edge]) for i, page in enumerate(lines)
                   if len(page) >= 4 and re.fullmatch(r"\d{1,5}", page[edge])}
        page_numbers = {i for i, n in numbers.items() if any(
            all(numbers.get(j) == n + j - i for j in range(start, start + 3))
            for start in range(max(0, i - 2), i + 1))}
        for index, page in enumerate(lines):
            if len(page) < 4 or not (page[edge] in repeated or index in page_numbers):
                continue
            for collection in (pages, layout):
                values = collection[index].splitlines()
                nonempty = [i for i, value in enumerate(values) if value.strip()]
                if nonempty and values[nonempty[edge]].strip() == page[edge]:
                    values.pop(nonempty[edge])
                    collection[index] = "\n".join(values)
    start, end = 0, len(pages)
    # Restrict front/back matter inference to long books with explicit publisher/TOC cues.
    if len(pages) >= 40:
        front = "\n".join(pages[:min(40, len(pages) // 5)])
        if re.search(r"(?im)^\s*(?:table of contents|contents|copyright|ISBN)\b", front):
            for i, page in enumerate(pages[:40]):
                heading = page.strip().splitlines()[0] if page.strip() else ""
                if (re.match(r"^(?:prelude|prologue|introduction|preface|foreword|chapter\s+(?:one|1|i)\b)\b", heading, re.I)
                        and len(page) > 500 and len(re.findall(r"[.!?][”’\"]?(?:\s|$)", page)) >= 4):
                    start = i
                    break
        for i in range(max(start + 1, len(pages) * 9 // 10), len(pages)):
            heading = pages[i].strip().splitlines()[0] if pages[i].strip() else ""
            copyright_page = re.match(r"^(?:this is a work of fiction|copyright)\b", heading, re.I) and re.search(r"ISBN|all rights reserved|copyright", pages[i], re.I)
            if copyright_page or re.fullmatch(r"(?:table of )?contents", heading, re.I):
                end = i
                break
    return pages[start:end], layout[start:end]


def pdf_text(upload, max_text):
    limit = max_text * 6
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        path = work / "source.pdf"
        with path.open("wb") as target:
            shutil.copyfileobj(upload, target)
        try:
            native = command_text(["pdftotext", "-enc", "UTF-8", str(path), "-"], 15, limit)
        except subprocess.CalledProcessError as exc:
            if b"password" in exc.stderr.lower():
                raise ValueError("This PDF is password-protected. Unlock it before importing.") from exc
            raise
        pages = native.split("\f")
        if len(pages) > 1 and not pages[-1].strip():
            pages.pop()
        missing = [number for number, page in enumerate(pages, 1) if not page.strip()]
        if not missing:
            try:
                structure = command_text(["pdfinfo", "-struct-text", "-enc", "UTF-8", str(path)], 10, limit * 2)
                tagged = tagged_paragraphs(structure)
            except (subprocess.SubprocessError, ValueError):
                tagged = ""
            # Some PDFs tag only part of their visible content.
            if tagged and sum(map(str.isalnum, tagged)) >= .98 * sum(map(str.isalnum, native)):
                return tagged, []
        # A few image-only pages in a long born-digital PDF are often cover art or illustrations.
        sparse_art = len(pages) >= 100 and len(missing) * 30 < len(pages)
        if missing and sparse_art:
            warnings = [f"{len(missing)} pages had no text layer and were skipped."]
        else:
            warnings = scan_pages(path, pages, missing, work, limit) if missing else []
        try:
            layout = command_text(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"], 15, limit * 2).split("\f")
        except (subprocess.SubprocessError, ValueError):
            layout = []
        if not layout or len(layout) < len(pages):
            layout = [""] * len(pages)
        pages, layout = clean_print_pages(pages, layout)
        return join_print_pages(pages, layout), warnings
