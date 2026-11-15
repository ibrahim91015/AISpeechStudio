import codecs
import io
import subprocess
import tempfile
import zipfile
from pathlib import Path

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
from striprtf.striprtf import rtf_to_text
from xml.etree.ElementTree import ParseError


MAX_BYTES = 12 * 1024 * 1024
MAX_TEXT = 150_000
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".yaml",
                   ".yml", ".log", ".srt", ".vtt", ".rst"}
IMPORT_EXTENSIONS = sorted(TEXT_EXTENSIONS | {".docx", ".odt", ".pdf", ".rtf"})
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ODF = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


def plain_text(data):
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return data.decode("utf-32")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def office_text(data, extension):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        member = archive.getinfo("word/document.xml" if extension == ".docx" else "content.xml")
        if member.file_size > MAX_BYTES:
            raise ValueError("The document’s text is too large to import.")
        xml = fromstring(archive.read(member))
    if extension == ".docx":
        breaks = {WORD + "tab": "\t", WORD + "br": "\n", WORD + "cr": "\n"}
        return "\n".join(
            "".join((node.text or "") if node.tag == WORD + "t" else breaks.get(node.tag, "")
                    for node in paragraph.iter())
            for paragraph in xml.iter(WORD + "p")
        )
    return "\n".join("".join(paragraph.itertext()) for paragraph in xml.iter()
                     if paragraph.tag in {ODF + "p", ODF + "h"})


def import_document(upload):
    extension = Path(upload.filename).suffix.lower()
    if extension not in IMPORT_EXTENSIONS:
        raise ValueError("Choose a TXT, Markdown, DOCX, ODT, RTF, PDF, or other supported text file.")
    if not upload.size or upload.size > MAX_BYTES:
        raise ValueError("Choose a nonempty document up to 12 MB.")
    data = upload.file.read(MAX_BYTES + 1)
    try:
        if extension in {".docx", ".odt"}:
            result = office_text(data, extension)
        elif extension == ".pdf":
            with tempfile.TemporaryFile() as output:
                subprocess.run(["pdftotext", "-nopgbrk", "-", "-"], input=data, stdout=output,
                               stderr=subprocess.DEVNULL, check=True, timeout=15)
                output.seek(0)
                result = output.read(MAX_TEXT * 4 + 4).decode("utf-8", errors="replace")
        elif extension == ".rtf":
            result = rtf_to_text(data.decode("latin-1"))
        else:
            result = plain_text(data)
    except (KeyError, LookupError, UnicodeError, zipfile.BadZipFile, ParseError,
            DefusedXmlException, subprocess.SubprocessError, OSError) as exc:
        raise ValueError("Could not read this document. Check that it opens and is unlocked.") from exc
    result = result.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not result:
        raise ValueError("No text found. Scan-only PDFs need a text layer.")
    if len(result) > MAX_TEXT:
        raise ValueError("The document exceeds 150,000 characters.")
    if any(ord(char) < 32 and char not in "\n\t\f" for char in result):
        raise ValueError("The file does not contain readable text.")
    return result
