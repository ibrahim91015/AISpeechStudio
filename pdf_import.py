import os
import re
import subprocess
import tempfile
import time
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
        if tag == "Artifact":
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


def pdf_text(data, max_text):
    limit = max_text * 6
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        path = work / "source.pdf"
        path.write_bytes(data)
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
        warnings = scan_pages(path, pages, missing, work, limit) if missing else []
        return "\n\n".join(reflow_pdf(page) for page in pages if page.strip()), warnings
