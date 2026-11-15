import codecs
import re
import subprocess
import zipfile
from pathlib import Path

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
from striprtf.striprtf import rtf_to_text
from xml.etree.ElementTree import ParseError

from ebooks import ebook_content, html_text
from pdf_import import pdf_text


MAX_BYTES = 65 * 1024 * 1024
MAX_MEMBER_BYTES = 24 * 1024 * 1024
MAX_IMPORT_TEXT = 5_000_000
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".yaml",
                   ".yml", ".log", ".srt", ".vtt", ".rst"}
EBOOK_EXTENSIONS = {".epub", ".mobi"}
IMPORT_EXTENSIONS = sorted(TEXT_EXTENSIONS | EBOOK_EXTENSIONS | {".docx", ".odt", ".pdf", ".rtf", ".html", ".htm", ".xhtml"})
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WORD_STRICT = "{http://purl.oclc.org/ooxml/wordprocessingml/main}"
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
    with zipfile.ZipFile(data) as archive:
        member = archive.getinfo("word/document.xml" if extension == ".docx" else "content.xml")
        if member.file_size > MAX_MEMBER_BYTES:
            raise ValueError("The document’s text is too large to import.")
        xml = fromstring(archive.read(member))
    if extension == ".docx":
        ns = WORD_STRICT if xml.tag.startswith(WORD_STRICT) else WORD
        body = xml.find(ns + "body")
        if body is None:
            raise ValueError("No document body found.")
        return "\n\n".join(text for p in body.iter(ns + "p") if (text := word_paragraph(p, ns).strip()))
    return "\n\n".join(text for p in xml.iter() if p.tag in {ODF + "p", ODF + "h"}
                       and (text := odf_paragraph(p).strip()))


def word_paragraph(node, ns):
    if node.tag in {ns + "del", ns + "moveFrom", ns + "pPr", ns + "rPr", ns + "txbxContent"}:
        return ""
    if node.tag == ns + "t":
        return node.text or ""
    if node.tag == ns + "tab":
        return "\t"
    if node.tag in {ns + "br", ns + "cr"}:
        return "\n"
    if node.tag == ns + "noBreakHyphen":
        return "\u2011"
    return "".join(word_paragraph(child, ns) for child in node)


def odf_paragraph(node):
    if node.tag == ODF + "s":
        return " " * max(0, min(100, int(node.get(ODF + "c", "1"))))
    if node.tag == ODF + "tab":
        return "\t"
    if node.tag == ODF + "line-break":
        return "\n"
    return (node.text or "") + "".join(odf_paragraph(child) + (child.tail or "") for child in node)


def caption_text(data):
    cues = []
    text = plain_text(data).replace("\r\n", "\n").replace("\r", "\n")
    for block in re.split(r"\n[ \t]*\n", text):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            if re.match(r"^\s*(?:\d+:)?\d{2}:\d{2}[.,]\d{3}\s*-->", line):
                cue = html_text(" ".join(lines[index + 1:])).strip()
                if cue and (not cues or cues[-1] != cue):
                    cues.append(cue)
                break
    return "\n".join(cues)


def import_document(upload):
    extension = Path(upload.filename).suffix.lower()
    if extension not in IMPORT_EXTENSIONS:
        raise ValueError("Choose a supported file such as PDF, DOCX, EPUB, MOBI, or TXT.")
    if not upload.size or upload.size > MAX_BYTES:
        raise ValueError("Choose a nonempty document up to 65 MB.")
    warnings = []
    try:
        if extension in EBOOK_EXTENSIONS:
            return ebook_content(upload.file, extension, plain_text, MAX_MEMBER_BYTES, MAX_IMPORT_TEXT)
        if extension in {".docx", ".odt"}:
            result = office_text(upload.file, extension)
        elif extension == ".pdf":
            result, warnings = pdf_text(upload.file, MAX_IMPORT_TEXT)
        else:
            data = upload.file.read(MAX_BYTES + 1)
            if extension in {".html", ".htm", ".xhtml"}:
                result = html_text(plain_text(data))
            elif extension in {".srt", ".vtt"}:
                result = caption_text(data)
            elif extension == ".rtf":
                result = rtf_to_text(data.decode("latin-1"))
            else:
                result = plain_text(data)
    except (AttributeError, KeyError, LookupError, RecursionError, UnicodeError, zipfile.BadZipFile, ParseError,
            DefusedXmlException, subprocess.SubprocessError, OSError) as exc:
        raise ValueError("Could not read this document. Check that it opens and is unlocked.") from exc
    result = result.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not result:
        raise ValueError("No readable text found. For scanned PDFs, try a clearer scan or an export with a text layer.")
    if len(result.encode("utf-16-le")) > MAX_IMPORT_TEXT * 2:
        raise ValueError("The document exceeds 5 million characters. Split it before importing.")
    if any(ord(char) < 32 and char not in "\n\t\f" for char in result):
        raise ValueError("The file does not contain readable text.")
    return {"text": result, "warnings": warnings}
