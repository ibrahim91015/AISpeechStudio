import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from bisect import bisect_right
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from defusedxml.ElementTree import fromstring


BLOCKS = {"address", "article", "aside", "blockquote", "caption", "dd", "div", "dl", "dt",
          "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "main",
          "p", "pre", "section", "table", "tr", "ul", "ol"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
HIDDEN = {"head", "title", "nav", "script", "style", "noscript", "svg", "math", "rt", "rp",
          "figcaption", "object", "audio", "video"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr", "mbp:pagebreak"}
PAGE_CLASSES = {"pagebreak", "page-number", "pagenum", "page-num", "page_number", "page-number-marker"}
METADATA_TYPES = {"cover", "titlepage", "halftitlepage", "copyright-page", "toc", "landmarks",
                  "page-list", "acknowledgments", "imprint", "colophon"}
STORY_LABEL = re.compile(r"^(?:prelude\b|prologue\b|chapter\b|part\b|\d+(?:[.:\s]|$))", re.I)
SECTION_LABEL = re.compile(r"^(?:part\b|book\s+(?:one|two|three|four|five|[ivxlcdm\d]+)\b|"
                           r"appendix\b|glossary\b|ars arcanum\b|endnote\b)", re.I)
METADATA_LABEL = re.compile(r"^(?:table of contents|contents|copyright|title page|dedication|"
                            r"preface and acknowledg(?:e)?ments?|illustrations|list of (?:illustrations|figures|maps)|"
                            r"acknowledg(?:e)?ments?|about the author|also by)(?:\W|$)", re.I)
ILLUSTRATION_LABEL = re.compile(r"^(?:(?:prime )?map(?: of|$)|(?:detail|diagram|sketch|illustration) of\b)", re.I)
END_LABEL = re.compile(r"^(?:about the author|also by|copyright|contents|table of contents)\b|"
                       r"\bBOOK TWO OF\b", re.I)
BY_AUTHOR = re.compile(r"^By (?:[^\W\d_]+ +){1,3}[^\W\d_]+$", re.I)
TAIL_CONTENTS = re.compile(r"(?:^|\n\n)Table of Contents(?=\n\n|$)", re.I)
TAIL_BIOGRAPHY = re.compile(r"(?:^|\n\n)About the Author(?=[ \n:])")
TAIL_COPYRIGHT = re.compile(r"(?:^|\n\n)Copyright(?=[ \n©:])", re.I)


class BookHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks = []
        self.length = 0
        self.gap = ""
        self.stack = []
        self.anchors = {}
        self.links = []
        self.pages = []
        self.headings = []
        self.heading = None
        self.link = None
        self.quote_start = None
        self.quoted_length = 0

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        semantics = set((attrs.get("epub:type") or "").lower().split())
        classes = set((attrs.get("class") or "").lower().split())
        roles = set((attrs.get("role") or "").lower().split())
        hidden = bool(self.stack and self.stack[-1][1])
        if not hidden:
            page = (tag == "mbp:pagebreak" or "pagebreak" in semantics or
                    bool(classes & PAGE_CLASSES) or "doc-pagebreak" in roles)
            if page:
                label = attrs.get("title") or attrs.get("aria-label") or ""
                if tag != "mbp:pagebreak":
                    self.pages.append((self.length, label))
            for anchor in (attrs.get("id"), attrs.get("name") if tag == "a" else None):
                if anchor:
                    self.anchors.setdefault(anchor, self.length)
            hidden = (page or tag in HIDDEN or bool(semantics & METADATA_TYPES) or
                      "hidden" in attrs or attrs.get("aria-hidden") == "true" or
                      bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", attrs.get("style") or "", re.I)))
            if not hidden:
                if tag in BLOCKS:
                    self.gap = "\n\n"
                elif tag == "br" and len(self.gap) < 2:
                    self.gap = "\n"
                elif tag in {"td", "th"} and not self.gap:
                    self.gap = " "
                if tag in HEADINGS:
                    self.heading = (tag, self.length, len(self.chunks))
                if tag == "a" and attrs.get("href"):
                    self.link = (self.length, len(self.chunks), attrs["href"])
                if tag == "blockquote" and self.quote_start is None:
                    self.quote_start = self.length
        if tag not in VOID:
            self.stack.append((tag, hidden))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                _, hidden = self.stack[index]
                del self.stack[index:]
                if not hidden and tag in BLOCKS:
                    self.gap = "\n\n"
                if not hidden and self.heading and self.heading[0] == tag:
                    _, start, chunk = self.heading
                    label = "".join(self.chunks[chunk:]).strip()
                    if label:
                        self.headings.append((start, label[:150]))
                    self.heading = None
                if not hidden and tag == "a" and self.link:
                    start, chunk, href = self.link
                    label = "".join(self.chunks[chunk:]).strip()
                    if label and len(label) <= 150:
                        self.links.append((start, href, label))
                    self.link = None
                return

    def handle_data(self, data):
        if self.stack and self.stack[-1][1]:
            return
        data = re.sub(r"\s+", " ", data)
        value = data.strip()
        if value:
            if any(tag == "blockquote" for tag, _ in self.stack):
                self.quoted_length += len(value)
            if not self.gap and data[0] == " ":
                self.gap = " "
            chunk = (self.gap if self.chunks else "") + value
            self.chunks.append(chunk)
            self.length += len(chunk)
            self.gap = ""
        if data.endswith(" ") and not self.gap:
            self.gap = " "

    @property
    def text(self):
        return "".join(self.chunks)


def read_html(text):
    parser = BookHTML()
    parser.feed(text)
    parser.close()
    return parser


def html_text(text):
    return read_html(text).text


def archive_member(archive, path, max_bytes):
    member = archive.getinfo(path)
    if member.file_size > max_bytes:
        raise ValueError("An ebook section is too large to import.")
    return archive.read(member)


def book_link(parent, value):
    link = urlsplit(value)
    if link.scheme or link.netloc:
        return "", ""
    return (posixpath.normpath(posixpath.join(posixpath.dirname(parent), unquote(link.path))) if link.path else parent,
            unquote(link.fragment))


def read_navigation(archive, package_path, manifest, spine, limit):
    chapters, pages = [], []
    for item in manifest.values():
        modern = "nav" in item.get("properties", "").split()
        if not modern and item.get("id") != spine.get("toc"):
            continue
        path, _ = book_link(package_path, item.attrib["href"])
        xml = fromstring(archive_member(archive, path, limit))
        if modern:
            for nav in xml.findall(".//{*}nav"):
                types = nav.get("{http://www.idpf.org/2007/ops}type", "").split()
                target = chapters if "toc" in types else pages if "page-list" in types else None
                if target is not None:
                    target.extend((book_link(path, a.get("href")), "".join(a.itertext()).strip())
                                  for a in nav.findall(".//{*}a") if a.get("href"))
        else:
            for kind, target in (("navPoint", chapters), ("pageTarget", pages)):
                for item in xml.findall(f".//{{*}}{kind}"):
                    source = item.find("./{*}content")
                    label = item.findtext("./{*}navLabel/{*}text") or item.get("value", "")
                    if source is not None and source.get("src"):
                        target.append((book_link(path, source.get("src")), label.strip()))
        if modern and chapters:
            break
    return chapters, pages


def ordered_marks(entries):
    unique = {}
    for position, label in entries:
        if position not in unique or not unique[position]:
            unique[position] = label
    return sorted(unique.items())


def linked_contents(links, locations):
    # Some old MOBI files contain a working printed TOC but have an empty NCX.
    forward = [(origin, locations[target], label) for origin, target, label in links
               if target[1] and target in locations and origin < locations[target]]
    main = [(origin, target) for origin, target, label in forward if STORY_LABEL.match(label)]
    if not main:
        return []
    first_text = min(target for _, target in main)
    if len({target for origin, target in main if origin < first_text}) < 3:
        return []
    return [(target, label) for origin, target, label in forward
            if origin < first_text <= target]


def approximate_pages(text, start, end):
    marks = []
    while start < end:
        marks.append((start, str(len(marks) + 1)))
        target = start + 3000
        if target >= end:
            break
        floor = start + 2250
        stop = text.rfind("\n\n", floor, target)
        if stop < floor:
            stop = text.rfind(" ", floor, target)
        start = stop if stop >= floor else target
    return marks


def omit_spans(text, groups, removed, preserve_marks=False):
    ends, prefix = [], [0]
    pieces, previous = [], 0
    for lo, hi in removed:
        pieces.append(text[previous:lo])
        previous = hi
        ends.append(hi)
        prefix.append(prefix[-1] + hi - lo)
    def remap(marks):
        result = []
        for offset, label in marks:
            index = bisect_right(ends, offset)
            inside = index < len(removed) and removed[index][0] <= offset
            if inside and not (preserve_marks or SECTION_LABEL.match(label)):
                continue
            partial = offset - removed[index][0] if inside else 0
            result.append((offset - prefix[index] - partial, label))
        return result
    return "".join(pieces) + text[previous:], [remap(marks) for marks in groups]


def section_ranges(text, chapters, headings, pages, files):
    source_marks = sorted(chapters or headings or files, key=lambda mark: mark[0])
    # The copyright page may have no spoken text, leaving its anchor just beyond
    # the last text block. Still use it to recognize a preceding bibliography.
    for index, (offset, label) in enumerate(source_marks[:-1]):
        if BY_AUTHOR.match(label) and offset > len(text) * .8 and source_marks[index + 1][1].lower().startswith("copyright"):
            text = text[:offset]
            break
    chapters = [(offset, label) for offset, label in source_marks if 0 <= offset < len(text)]
    if not chapters:
        chapters = [(0, "Entire book")]
    first_story = next((offset for offset, label in chapters if STORY_LABEL.match(label)), 0)
    possible_titles = {label for offset, label in chapters if offset < first_story and not METADATA_LABEL.match(label)}
    repeated = []
    for offset, label in chapters:
        if not STORY_LABEL.match(label):
            continue
        match = re.match(r"\s*([^\n]+)\n\s*", text[offset:offset + 400])
        if match and match[1] in possible_titles:
            repeated.append((offset, offset + match.end(), match[1]))
    counts = Counter(label for _, _, label in repeated)
    title_spans = [(lo, hi) for lo, hi, label in repeated if counts[label] >= 3]
    if title_spans:
        text, (chapters, headings, pages, files) = omit_spans(text, (chapters, headings, pages, files), title_spans, True)
    # Remove the actual text spans too, not just their labels in the picker.
    labels_at = {}
    for offset, label in chapters:
        labels_at.setdefault(offset, []).append(label)
    if len(labels_at) > 10_000:
        raise ValueError("This ebook has more than 10,000 navigation entries. Split it before importing.")
    offsets = sorted(set(labels_at) | {len(text)})
    removed = []
    first_story = next((offset for offset, label in chapters if STORY_LABEL.match(label)), len(text))
    for offset, stop in zip(offsets, offsets[1:]):
        labels = labels_at[offset]
        image_divider = (stop - offset < 1000 and any(ILLUSTRATION_LABEL.match(label) for label in labels)
                         and all(ILLUSTRATION_LABEL.match(label) or SECTION_LABEL.match(label) for label in labels))
        if image_divider or (labels and all(METADATA_LABEL.match(label) for label in labels)):
            if offset > first_story and any(END_LABEL.search(label) for label in labels):
                removed.append((offset, len(text)))
                break
            removed.append((offset, stop))
    if removed:
        text, (chapters, headings, pages, files) = omit_spans(text, (chapters, headings, pages, files), removed)
        if not chapters:
            chapters = [(0, "Entire book")]
    display_marks = [(offset, label) for offset, label in chapters if not METADATA_LABEL.match(label)]
    # Do not discard a named introduction or Arabic opening merely because a later
    # heading happens to match the English word "Chapter".
    start = display_marks[0][0] if display_marks else 0
    first_story = next((offset for offset, label in display_marks if STORY_LABEL.match(label)), None)
    if first_story is not None and first_story > start:
        opening = text[start:first_story]
        introduction = any(re.match(r"^(?:introduction|preface|foreword|مقدمة)\b", label, re.I)
                           for offset, label in display_marks if offset < first_story)
        front_matter = re.search(r"(?im)^(?:by [^\n]{2,80}|a tom doherty|published by|copyright|ISBN)\b", opening)
        if not introduction and front_matter:
            start = first_story
    end = len(text)
    for index, (offset, label) in enumerate(chapters):
        if offset <= start:
            continue
        credits = (BY_AUTHOR.match(label) and index + 1 < len(chapters)
                   and chapters[index + 1][1].lower().startswith("copyright"))
        if credits or END_LABEL.search(label):
            end = offset
            break
    # Older Kindle books sometimes include a printed TOC with no navigation entry of its own.
    last_chapter = max((offset for offset, _ in display_marks if offset < end), default=start)
    contents = TAIL_CONTENTS.search(text, max(last_chapter, end - 12_000), end)
    if contents:
        biography = TAIL_BIOGRAPHY.search(text, max(last_chapter, contents.start() - 5_000), contents.start())
        end = biography.start() if biography else contents.start()
    copyright_notice = TAIL_COPYRIGHT.search(text, max(last_chapter, end - 12_000), end)
    if copyright_notice and re.search(r"\bISBN\b|all rights reserved", text[copyright_notice.start():end], re.I):
        end = copyright_notice.start()
    fiction_notice = re.search(r"(?:^|\n\n)This is a work of fiction\.", text[max(last_chapter, end - 2000):end])
    if fiction_notice:
        end = max(last_chapter, end - 2000) + fiction_notice.start()
    preview = re.search(r"(?:^|\n\n)(?:Turn the page for a sneak peek|Read on for an? (?:excerpt|preview)|An? (?:excerpt|preview) from)\b",
                        text[last_chapter:end], re.I)
    if preview:
        end = last_chapter + preview.start()
    proof = re.search(r"(?:^|\n\n)v\d[\d.]*\s+proof(?:ed|read) by\b", text[max(last_chapter, end - 500):end], re.I)
    if proof:
        end = max(last_chapter, end - 500) + proof.start()
    book = text[start:end]
    if not book.strip():
        raise ValueError("No readable text found in the ebook’s reading order.")

    # A part divider consisting of artwork can have the same offset as the next chapter.
    chapters = ordered_marks((offset, label) for offset, label in display_marks
                             if start <= offset < end and not SECTION_LABEL.match(label))
    sections = ordered_marks((offset, label) for offset, label in display_marks
                             if start <= offset < end and SECTION_LABEL.match(label))
    if not chapters:
        chapters = sections.copy()
    if chapters and chapters[0][0] > start:
        chapters.insert(0, (start, sections[0][1] if sections else "Beginning"))
    if not chapters:
        chapters = [(start, "Entire book")]
    if not sections:
        sections = [(offset, f"{label} – {chapters[min(index + 4, len(chapters) - 1)][1]}")
                    if index + 1 < len(chapters) else (offset, label)
                    for index, (offset, label) in enumerate(chapters) if index % 5 == 0]
    elif sections[0][0] > start:
        sections.insert(0, (start, "Beginning"))

    pages = [(offset, label) for offset, label in ordered_marks(pages) if start <= offset < end]
    estimated = not pages
    if estimated:
        pages = approximate_pages(text, start, end)
    elif pages[0][0] > start:
        pages.insert(0, (start, "Before first numbered page"))

    indices = {start: 0}
    previous = start
    for offset in sorted({offset for marks in (chapters, sections, pages) for offset, _ in marks} | {end}):
        indices[offset] = indices[previous] + len(text[previous:offset].encode("utf-16-le")) // 2
        previous = offset

    def ranges(marks, prefix, boundaries_between=()):
        boundaries = [offset for offset, _ in marks] + [end]
        result = []
        for index, (offset, label) in enumerate(marks):
            stop = boundaries[index + 1]
            stop = next((mark for mark, _ in boundaries_between if offset < mark < stop), stop)
            if text[offset:stop].strip():
                result.append({"label": label or f"{prefix} {index + 1}",
                               "start": indices[offset], "end": indices[stop]})
        return result

    return {"text": book, "estimatedPages": estimated, "chapters": ranges(chapters, "Chapter", sections),
            "sections": ranges(sections, "Section"), "pages": ranges(pages, "Page")}


def epub_content(data, decode, limit, max_text):
    with zipfile.ZipFile(data) as archive:
        if len(archive.infolist()) > 20_000:
            raise ValueError("This ebook contains too many files to import.")
        container = fromstring(archive_member(archive, "META-INF/container.xml", limit))
        package_path = container.find(".//{*}rootfile").attrib["full-path"]
        package = fromstring(archive_member(archive, package_path, limit))
        manifest = {item.attrib["id"]: item for item in package.findall("./{*}manifest/{*}item")}
        spine = package.find("./{*}spine")
        try:
            toc, page_list = read_navigation(archive, package_path, manifest, spine, limit)
        except (KeyError, ValueError, SyntaxError):
            toc, page_list = [], []
        extras = {book_link(package_path, ref.attrib["href"])[0]
                  for ref in package.findall("./{*}guide/{*}reference")
                  if ref.get("type") in METADATA_TYPES and ref.get("href")}
        locations, headings, pages, files, blocks, links = {}, [], [], [], [], []
        reading_order = []
        position = archive_size = characters = 0
        for ref in spine.findall("./{*}itemref"):
            item = manifest.get(ref.get("idref"))
            if item is None or ref.get("linear") == "no" or "nav" in item.get("properties", "").split():
                continue
            if item.get("media-type") not in {"application/xhtml+xml", "text/html"}:
                continue
            path, _ = book_link(package_path, item.attrib["href"])
            if path in extras or not path:
                continue
            markup = archive_member(archive, path, limit)
            archive_size += len(markup)
            if archive_size > 4 * limit:
                raise ValueError("The ebook’s markup is too large to import. Split it before importing.")
            parser = read_html(decode(markup))
            locations[(path, "")] = position
            text = parser.text
            if text and len(text) < 12_000:
                first_line = text.split("\n", 1)[0].strip()
                if re.fullmatch(r"copyright(?: page)?", first_line, re.I) or (
                    re.search(r"\bISBN\b", text) and re.search(r"all rights reserved|copyright", text, re.I)
                ):
                    continue
            for name, offset in parser.anchors.items():
                locations[(path, name)] = position + offset
            links.extend((position + offset, book_link(path, href), label) for offset, href, label in parser.links)
            headings.extend((position + offset, label) for offset, label in parser.headings)
            pages.extend((position + offset, label) for offset, label in parser.pages)
            if text:
                files.append((position, f"Section {len(files) + 1}"))
                epigraph = (position + parser.quote_start if parser.quote_start is not None and
                            len(text) < 8000 and parser.quoted_length > len(text) * .6 else None)
                reading_order.append((path, position, epigraph))
                position += len(text) + 2
                characters += len(text.encode("utf-16-le")) // 2 + 2
                if characters > max_text + 2:
                    raise ValueError("The ebook exceeds 5 million characters. Split it before importing.")
                blocks.append(text)
        chapters = [(locations[link], label) for link, label in toc if link in locations]
        # Calibre/legacy books may store the epigraph in an unlisted spine item
        # immediately before the chapter's navigation target.
        navigated = {link[0] for link, _ in toc}
        preceding_quotes = {position: reading_order[index - 1][2]
                            for index, (path, position, _) in enumerate(reading_order) if index and
                            path in navigated and reading_order[index - 1][0] not in navigated and
                            reading_order[index - 1][2] is not None}
        chapters = [(preceding_quotes.get(offset, offset), label) for offset, label in chapters]
        if not chapters:
            chapters = linked_contents(links, locations)
        for link, label in page_list:
            if link in locations:
                pages.insert(0, (locations[link], label))
    result = section_ranges("\n\n".join(blocks), chapters, headings, pages, files)
    if any(ord(char) < 32 and char not in "\n\t\f" for char in result["text"]):
        raise ValueError("The ebook contains invalid text characters.")
    title = (package.findtext("./{*}metadata/{*}title") or "").strip()[:100]
    if re.fullmatch(r"[\d_ -]{8,}(?:\.(?:pdf|mobi|epub))?", title, re.I):
        title = next((label for _, label in chapters[:1]
                      if not (STORY_LABEL.match(label) or METADATA_LABEL.match(label))), "")[:100]
    result["title"] = title
    return result


def ebook_content(upload, extension, decode, limit, max_text):
    if extension == ".epub":
        return epub_content(upload, decode, limit, max_text)
    header = upload.read(86)
    if header[60:68] not in {b"BOOKMOBI", b"TEXtREAd"}:
        raise ValueError("This is not a supported MOBI or Kindle document.")
    record = int.from_bytes(header[78:82], "big")
    if not 86 <= record <= upload.seek(0, 2) - 16:
        raise ValueError("The Kindle document is incomplete or damaged.")
    upload.seek(record + 12)
    if int.from_bytes(upload.read(2), "big"):
        raise ValueError("This Kindle document is DRM-protected. Choose an unprotected copy.")
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "book.mobi"
        upload.seek(0)
        with path.open("wb") as target:
            shutil.copyfileobj(upload, target)
        subprocess.run(["mobitool", "-e", "-o", work, str(path)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True, timeout=20)
        return epub_content(Path(work) / "book.epub", decode, limit, max_text)
