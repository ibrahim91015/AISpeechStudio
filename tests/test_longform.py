"""Run on Linux with the web environment, httpx, FFmpeg and document CLI tools.

Uses an isolated temporary database. Never starts a GPU worker or reads production data.
"""
import concurrent.futures
import io
import json
import os
import struct
import tempfile
import time
import threading
import unittest
import wave
import zipfile
import zlib
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

DATA = tempfile.TemporaryDirectory(prefix="aispeech-test-")
os.environ["TTS_DATA_DIR"] = DATA.name
os.environ["TTS_TRUSTED_ORIGINS"] = "https://speech.example.com"

from fastapi.testclient import TestClient
import app as web
import documents
from catalog import MODELS, defaults
from ebooks import html_text, section_ranges
from generation_limits import RUN_CHAR_LIMIT, SpeechLimitError, split_runs
from import_tasks import ImportTasks
from pdf_import import clean_print_pages
from queue_worker import Worker, WorkerStopping, Cancelled, ModelConnectionError, chunk_text, claim, update
from speech_assembly import SpeechAssembly
from storage import JOBS, connect, one, setup
from storage import VOICES


def archive(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as z:
        for name, value in files.items():
            z.writestr(name, value)
    return data.getvalue()


def upload(name, data):
    return SimpleNamespace(filename=name, file=io.BytesIO(data), size=len(data))


def epub(markup, nav=""):
    return archive({
        "META-INF/container.xml": '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>',
        "book.opf": '<package><metadata><title>Test book</title></metadata><manifest><item id="body" href="body.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" properties="nav"/></manifest><spine><itemref idref="body"/></spine></package>',
        "body.xhtml": markup,
        "nav.xhtml": '<html xmlns:epub="http://www.idpf.org/2007/ops"><nav epub:type="toc">' + nav + '</nav></html>',
    })


def pcm(path, frames=2400, rate=24000, channels=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 12000) * frames * channels)


def pdf_fixture(image=None):
    stream = b'BT /F1 24 Tf 72 700 Td (Hello from the scanned document.) Tj ET'
    resources = b'/Font << /F1 5 0 R >>'
    if image:
        stream = b'q 612 0 0 792 0 0 cm /Im0 Do Q'
        resources += b' /XObject << /Im0 6 0 R >>'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << ' + resources + b' >> /Contents 4 0 R >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    if image:
        width, height, pixels = image
        compressed = zlib.compress(pixels)
        objects.append(f'<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length {len(compressed)} >>\nstream\n'.encode() + compressed + b'\nendstream')
    data, offsets = b'%PDF-1.4\n', []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(i).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    xref = len(data)
    data += f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets)
    return data + f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()


class Documents(unittest.TestCase):
    def test_plain_formats_and_unicode(self):
        for ext in ("txt", "md", "markdown", "csv", "tsv", "json", "xml", "yaml", "yml", "log", "rst"):
            for encoding in ("utf-8-sig", "utf-16", "utf-32"):
                with self.subTest(ext=ext, encoding=encoding):
                    self.assertEqual(documents.import_document(upload("book." + ext, "Hello مرحبا 😀".encode(encoding)))["text"], "Hello مرحبا 😀")

    def test_binary_empty_wrong_extension_and_real_size(self):
        for name, data in (("bad.exe", b"text"), ("empty.txt", b""), ("bad.txt", b"abc\x00def"), ("bad.docx", b"no zip"), ("bad.pdf", b"no pdf"), ("bad.mobi", b"not mobi")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                documents.import_document(upload(name, data))
        with patch.object(documents, "MAX_BYTES", 3), self.assertRaises(ValueError):
            documents.import_document(SimpleNamespace(filename="test.txt", file=io.BytesIO(b"four"), size=1))

    def test_character_limit_exact_and_one_above(self):
        with patch.object(documents, "MAX_IMPORT_TEXT", 100):
            self.assertEqual(len(documents.import_document(upload("test.txt", b"A" * 100))["text"]), 100)
            with self.assertRaises(ValueError):
                documents.import_document(upload("test.txt", b"A" * 101))

    def test_html_and_captions(self):
        html = '<html><head><title>Metadata</title></head><body><p>Hello <b>world</b>.</p><img alt="BAD"/><p hidden>BAD</p><script>BAD</script><span role="doc-pagebreak">12</span><p>Next paragraph.</p></body></html>'
        self.assertEqual(html_text(html), "Hello world.\n\nNext paragraph.")
        for extension in ('html', 'htm', 'xhtml'):
            self.assertEqual(documents.import_document(upload('book.' + extension, html.encode()))['text'], 'Hello world.\n\nNext paragraph.')
        for ext, content in [("srt", '1\n00:00:01,000 --> 00:00:02,000\nHello <b>world</b>.\n\n2\n00:00:02,000 --> 00:00:03,000\nNext.'), ("vtt", 'WEBVTT\n\nNOTE internal\nmetadata\n\n00:01.000 --> 00:02.000\nHello <b>world</b>.\n\n00:02.000 --> 00:03.000\nNext.')]:
            self.assertEqual(documents.import_document(upload('book.' + ext, content.encode()))["text"], "Hello world.\nNext.")

    def test_rtf(self):
        self.assertEqual(documents.import_document(upload("book.rtf", b'{\\rtf1\\ansi Hello \\b world\\b0.\\par Next.}'))["text"], "Hello world.\nNext.")

    def test_docx_revision_textboxes_hidden_and_page_fields(self):
        body = '<w:p><w:r><w:t>Visible</w:t></w:r><w:r><w:rPr><w:vanish/></w:rPr><w:t>HIDDEN</w:t></w:r><w:del><w:r><w:delText>DELETED</w:delText></w:r></w:del><w:r><w:drawing><w:txbxContent><w:p><w:r><w:t>TEXTBOX</w:t></w:r></w:p></w:txbxContent></w:drawing></w:r><w:fldSimple w:instr="PAGE"><w:r><w:t>999</w:t></w:r></w:fldSimple></w:p><w:del><w:p><w:r><w:t>DELETED PARAGRAPH</w:t></w:r></w:p></w:del><w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PAGE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>12</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r><w:r><w:t>End</w:t></w:r></w:p>'
        for ns in (documents.WORD[1:-1], documents.WORD_STRICT[1:-1]):
            data = archive({"word/document.xml": f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>', "word/header1.xml": "not read"})
            self.assertEqual(documents.import_document(upload("book.docx", data))["text"], "Visible\n\nEnd")

    def test_odt_body_only(self):
        xml = '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:automatic-styles><text:p>BAD STYLE</text:p></office:automatic-styles><office:body><office:text><text:p>Hello<text:s text:c="2"/>world<text:page-number>12</text:page-number></text:p><office:annotation><text:p>BAD COMMENT</text:p></office:annotation><text:p>Next.</text:p></office:text></office:body></office:document-content>'
        self.assertEqual(documents.import_document(upload("book.odt", archive({"content.xml": xml})))["text"], "Hello  world\n\nNext.")

    def test_xml_entities_and_oversized_members(self):
        xml = '<!DOCTYPE x [<!ENTITY bad SYSTEM "file:///etc/passwd">]><x>&bad;</x>'
        with self.assertRaises(ValueError):
            documents.import_document(upload("bad.docx", archive({"word/document.xml": xml})))
        with patch.object(documents, "MAX_MEMBER_BYTES", 8), self.assertRaises(ValueError):
            documents.import_document(upload("bad.docx", archive({"word/document.xml": "x" * 9})))

    def test_ebook_navigation_utf16_and_opening(self):
        markup = '<h1 id="intro">Introduction</h1><p>😀 Opening.</p><h1 id="one">Chapter 1</h1><p>One.</p><h1 id="two">Chapter 2</h1><p>Two.</p>'
        nav = ''.join(f'<a href="body.xhtml#{key}">{label}</a>' for key, label in [('intro', 'Introduction'), ('one', 'Chapter 1'), ('two', 'Chapter 2')])
        result = documents.import_document(upload("book.epub", epub(markup, nav)))
        self.assertEqual(len(result["chapters"]), 3)
        self.assertTrue(result["text"].startswith("Introduction"))
        encoded = result["text"].encode("utf-16-le")
        self.assertIn("Chapter 2", encoded[result["chapters"][2]["start"] * 2:result["chapters"][2]["end"] * 2].decode("utf-16-le"))
        self.assertTrue(result["estimatedPages"])

    def test_unlisted_epigraph_before_chapter_navigation(self):
        data = archive({
            'META-INF/container.xml': '<container><rootfile full-path="book.opf"/></container>',
            'book.opf': '<package><manifest><item id="quote" href="quote.xhtml" media-type="application/xhtml+xml"/><item id="body" href="body.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" properties="nav"/></manifest><spine><itemref idref="quote"/><itemref idref="body"/></spine></package>',
            'quote.xhtml': '<p>Book title</p><blockquote>This opening epigraph belongs to the story and must never disappear.</blockquote>',
            'body.xhtml': '<h1>Prologue</h1><p>Opening narrative.</p>',
            'nav.xhtml': '<html xmlns:epub="http://www.idpf.org/2007/ops"><nav epub:type="toc"><a href="body.xhtml">Prologue</a></nav></html>',
        })
        result = documents.import_document(upload('book.epub', data))
        self.assertTrue(result['text'].lstrip().startswith('This opening epigraph'))
        self.assertNotIn('Book title', result['text'])
        self.assertEqual(result['chapters'][0]['start'], 0)

    def test_image_only_part_does_not_eat_prologue(self):
        result = section_ranges("Prologue text.\n\nChapter text.", [(0, "Part One"), (0, "Prologue"), (16, "Chapter 1")], [], [], [])
        self.assertEqual(result["chapters"][0]["label"], "Prologue")
        self.assertEqual(result["chapters"][0]["start"], 0)
        result = section_ranges('Story text.', [(0, 'Chapter 1'), (13, 'Copyright')], [], [], [])
        self.assertEqual(result['text'], 'Story text.')

    def test_ebook_metadata_removed_from_text_not_only_picker(self):
        text = 'Illustrations\nBAD\n\nPrologue\nGood.\n\nMap of Somewhere\nBAD CAPTION\n\nChapter 1\nStory.'
        marks = [(text.index(label), label) for label in ('Illustrations', 'Prologue', 'Map of Somewhere', 'Chapter 1')]
        result = section_ranges(text, marks, [], [], [])
        self.assertNotIn('BAD', result['text'])
        self.assertTrue(result['text'].startswith('Prologue'))
        self.assertEqual([c['label'] for c in result['chapters']], ['Prologue', 'Chapter 1'])

    def test_repeated_ebook_title_and_preview_removed(self):
        text = 'My Book\nBy Someone\n\n'
        marks = [(0, 'My Book')]
        for n in range(1, 4):
            marks.append((len(text), 'Chapter ' + str(n)))
            text += 'My Book\n\nChapter ' + str(n) + '\n\nStory.\n\n'
        text += 'Turn the page for a sneak peek at another book\n\nADVERT'
        result = section_ranges(text, marks, [], [], [])
        self.assertNotIn('My Book', result['text'])
        self.assertNotIn('ADVERT', result['text'])
        self.assertEqual(result['text'].count('Story.'), 3)

    def test_pdf_headers_and_footer_numbers(self):
        pages = ['Manual\nDifferent body paragraph ' + str(i) + '.\nMore words.\nLast paragraph.\n' + str(i) for i in range(1, 5)]
        cleaned, _ = clean_print_pages(pages.copy(), pages.copy())
        self.assertTrue(all('Manual' not in page for page in cleaned))
        self.assertTrue(all(page.endswith('Last paragraph.') for page in cleaned))
        lone = ['7\nFirst line.\nSecond line.\n42']
        self.assertEqual(clean_print_pages(lone.copy(), lone.copy())[0], lone)

    def test_pdf_native_scanned_and_password_protection(self):
        native = pdf_fixture()
        self.assertIn('Hello from the scanned document.', documents.import_document(upload('test.pdf', native))['text'])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'native.pdf'
            path.write_bytes(native)
            subprocess.run(['pdftoppm', '-scale-to', '1500', '-singlefile', str(path), str(Path(folder) / 'image')], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            magic, dimensions, maximum, pixels = (Path(folder) / 'image.ppm').read_bytes().split(b'\n', 3)
            self.assertEqual(magic, b'P6')
            width, height = map(int, dimensions.split())
            result = documents.import_document(upload('scan.pdf', pdf_fixture((width, height, pixels))))
            self.assertIn('Hello from the scanned document', result['text'])
            self.assertTrue(any('OCR' in warning for warning in result['warnings']))
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        writer.add_page(PdfReader(io.BytesIO(native)).pages[0])
        writer.encrypt('not-a-real-secret')
        locked = io.BytesIO()
        writer.write(locked)
        with self.assertRaisesRegex(ValueError, 'password-protected'):
            documents.import_document(upload('locked.pdf', locked.getvalue()))

    def test_published_page_markers_removed(self):
        result = documents.import_document(upload("book.epub", epub('<h1>Chapter 1</h1><p>A.</p><span role="doc-pagebreak" aria-label="2">PAGE 2</span><p>B.</p>')))
        self.assertFalse(result["estimatedPages"])
        self.assertNotIn("PAGE", result["text"])


class Batches(unittest.TestCase):
    def test_sizes_offsets_and_no_lost_content(self):
        for size in (599999, 600000, 600001, 1200000, 1200001, 5000000):
            text = ("Hello مرحبا 😀. " * ((size // 15) + 2))[:size]
            parts = list(split_runs(text))
            self.assertTrue(all(0 < len(piece) <= RUN_CHAR_LIMIT and text[a:b] == piece for a, b, piece in parts))
            self.assertEqual("".join("".join(part[2].split()) for part in parts), "".join(text.split()))
            self.assertEqual(len(parts), (size + RUN_CHAR_LIMIT - 1) // RUN_CHAR_LIMIT)

    def test_massive_whitespace_and_markup(self):
        self.assertEqual([p[2] for p in split_runs("A" + " " * 1800000 + "B")], ["A", "B"])
        text = "a" * 599995 + " [laugh] " + "z" * 100
        self.assertTrue(all("[" not in p[2] or "[laugh]" in p[2] for p in split_runs(text)))
        with self.assertRaises(ValueError):
            list(split_runs('A' + '\u064e' * 1200000))

    def test_browser_batch_count_matches_server(self):
        cases = ['A' * 1200000, 'A' + ' ' * 1800000 + 'B', 'A' * 599995 + ' [laugh] ' + 'B' * 599996,
                 'ا' * 600000 + '\u064e' + 'ب' * 599999, '😀' * 600000 + 'A', ('abc\n\n' * 240001)]
        expected = [len(list(split_runs(text))) for text in cases]
        script = "const f=require('./static/run-splitting.js');let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>console.log(JSON.stringify(JSON.parse(s).map(t=>f.count(t,600000)))));"
        result = subprocess.run(['node', '-e', script], input=json.dumps(cases), text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), expected)

    def test_range_titles_and_malformed_metadata(self):
        text = "x" * 1200000
        parts = list(split_runs(text))
        plan = {"book": "Book", "mode": "ch", "label": "Book-ch1-8", "spans": [[0, 300000, "1"], [300000, 650000, "4"], [650000, 1200000, "8"]]}
        self.assertEqual(web.import_run_titles({"import_range": json.dumps(plan)}, text, parts, plan["label"]), ["Book-ch1-4", "Book-ch4-8"])
        clipped = {**plan, 'spans': [[-2, 300000, '1'], [300000, 650000, '4'], [650000, 1200000, '8']]}
        self.assertEqual(web.import_run_titles({'import_range': json.dumps(clipped)}, text, parts, plan['label']), ['Book-ch1-4', 'Book-ch4-8'])
        for bad in (None, 3, [], {}, [None], [1], [[0, 5]], [[0, "x", "1"]], [[0, 5, []]], [[0, 5, "x</script>"]]):
            for field in ("spans", "book", "mode"):
                corrupt = {**plan, field: bad}
                web.import_run_titles({"import_range": json.dumps(corrupt)}, text, parts, plan["label"])


class API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup()
        web.app.state.imports = ImportTasks()
        cls.client = TestClient(web.app)  # No lifespan: deliberately no GPU/notification workers.

    @classmethod
    def tearDownClass(cls):
        web.app.state.imports.close()

    def setUp(self):
        with connect() as db:
            db.execute("DELETE FROM jobs")
            db.execute("DELETE FROM job_submissions")
            db.execute("DELETE FROM page_notices")

    def submit(self, **values):
        return self.client.post('/jobs', data={"model": "qwen", "script": "Hello.", "request_tab": "tab-a", **values}, follow_redirects=False)

    def test_batch_atomic_titles_and_idempotence(self):
        text = "x" * 1200000
        plan = {"book": "Book", "mode": "ch", "label": "Book-ch1-8", "spans": [[0, 300000, "1"], [300000, 650000, "4"], [650000, 1200000, "8"]]}
        values = dict(script=text, title=plan['label'], import_range=json.dumps(plan), submission_key='a' * 32)
        self.assertEqual(self.submit(**values).status_code, 303)
        self.assertEqual(self.submit(**values).status_code, 303)
        with connect() as db:
            rows = db.execute('SELECT title,script,batch_id FROM jobs ORDER BY rowid').fetchall()
        self.assertEqual([r['title'] for r in rows], ["Book-ch1-4", "Book-ch4-8"])
        self.assertEqual([len(r['script']) for r in rows], [600000, 600000])
        self.assertEqual(rows[0]['batch_id'], rows[1]['batch_id'])
        self.assertEqual(self.submit(**{**values, 'script': 'Changed'}).status_code, 400)

    def test_priority_claim_and_running_exclusion(self):
        self.submit(script='x' * 600001)
        active = claim()
        self.assertIsNotNone(active)
        self.assertEqual(self.submit(script='Short other tab', request_tab='tab-b').status_code, 303)
        self.assertEqual(self.submit(script='Same tab').status_code, 303)
        self.assertEqual(self.submit(script='x' * 150000, request_tab='tab-b').status_code, 303)
        with connect() as db:
            rows = db.execute("SELECT script,queue_priority FROM jobs WHERE status='queued' ORDER BY rowid").fetchall()
        self.assertEqual([r['queue_priority'] for r in rows], [0, 1, 0, 0])
        update(active['id'], status='cancelled')
        following = claim()
        self.assertEqual(following['script'], 'Short other tab')

    def test_bad_settings_and_form_shapes_rollback(self):
        for values in ({'temperature': 'nan'}, {'format': '../bad'}, {'script': 'a\x00b'}, {'script': 'x' * 5000001}, {'project': '9999'}, {'model': 'missing'}):
            self.assertEqual(self.submit(**values).status_code, 400)
        response = self.client.post('/jobs', files={'model': ('model.txt', b'qwen'), 'script': (None, 'hello')})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(one('SELECT COUNT(*) AS n FROM jobs')['n'], 0)

    def test_concurrent_same_submission_is_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            result = list(pool.map(lambda _: self.submit(submission_key='b' * 32).status_code, range(3)))
        self.assertEqual(result, [303] * 3)
        self.assertEqual(one('SELECT COUNT(*) AS n FROM jobs')['n'], 1)

    def test_proxy_same_origin_and_rejection(self):
        response = self.client.post('/import-text', files={'document': ('test.txt', b'Hello')}, headers={'Origin': 'https://speech.example.com', 'Sec-Fetch-Site': 'same-origin', 'X-Import-Async': '1'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.client.post('/import-text', headers={'Origin': 'https://untrusted.example', 'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
        self.assertEqual(self.client.post('/import-text', content=b'', headers={'Content-Length': str(70 * 1024 ** 2)}).status_code, 413)
        def streamed():
            yield b'--audit\r\nContent-Disposition: form-data; name="document"; filename="test.txt"\r\n\r\n'
            for _ in range(66):
                yield b'A' * 1024 ** 2
            yield b'\r\n--audit--\r\n'
        self.assertEqual(self.client.post('/import-text', content=streamed(), headers={'Content-Type': 'multipart/form-data; boundary=audit'}).status_code, 413)
        for _ in range(100):
            result = self.client.get('/imports/' + response.json()['import_id'])
            if result.status_code != 202:
                break
            time.sleep(.03)
        self.assertEqual(result.json()['text'], 'Hello')
        self.assertEqual(self.client.get('/imports/nonexistent').status_code, 404)

    def test_low_disk_rejected_before_queue(self):
        with patch('app.shutil.disk_usage', return_value=SimpleNamespace(free=10)):
            self.assertEqual(self.submit().status_code, 400)
        self.assertEqual(one('SELECT COUNT(*) AS n FROM jobs')['n'], 0)

    def test_reference_snapshot_and_copy_failure_rollback(self):
        key = 'reference-for-audit'
        source = VOICES / (key + '.wav')
        pcm(source)
        original = source.read_bytes()
        with connect() as db:
            db.execute("INSERT OR IGNORE INTO voices(id,user_id,name) VALUES (?,1,'Reference')", (key,))
        self.assertEqual(self.submit(model='qwen_base', voice_id=key, x_vector_only='1', script='x' * 600001).status_code, 303)
        with connect() as db: jobs = db.execute('SELECT id FROM jobs').fetchall()
        source.write_bytes(b'changed source')
        self.assertTrue(all((JOBS / job['id'] / 'reference.wav').read_bytes() == original for job in jobs))
        with connect() as db: db.execute('DELETE FROM jobs')
        before = set(JOBS.iterdir())
        with patch('app.shutil.copyfile', side_effect=OSError('simulated disk failure')):
            response = self.submit(model='qwen_base', voice_id=key, x_vector_only='1', script='x' * 600001)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(one('SELECT COUNT(*) AS n FROM jobs')['n'], 0)
        self.assertEqual(set(JOBS.iterdir()), before)

    def test_65_mib_and_five_million_boundaries(self):
        data = epub('<h1>Chapter 1</h1><p>Boundary test.</p>')
        exact = b'\x00' * (documents.MAX_BYTES - len(data)) + data
        response = self.client.post('/import-text', files={'document': ('boundary.epub', exact)})
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertIn('Boundary test.', response.json()['text'])
        self.assertEqual(self.client.post('/import-text', files={'document': ('boundary.txt', b'A' * (documents.MAX_BYTES + 1))}).status_code, 400)
        response = self.client.post('/import-text', files={'document': ('boundary.txt', b'A' * documents.MAX_IMPORT_TEXT)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['text']), documents.MAX_IMPORT_TEXT)
        self.assertEqual(self.client.post('/import-text', files={'document': ('boundary.txt', b'A' * (documents.MAX_IMPORT_TEXT + 1))}).status_code, 400)


class Assembly(unittest.TestCase):
    def test_checkpoint_rewind_and_corruption(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            part = root / 'part.wav'
            pcm(part)
            assembly = SpeechAssembly(root, ['one', 'two'])
            assembly.append(part, 0)
            assembly.commit(1)
            committed = assembly.size
            assembly.append(part, 100)
            resumed = SpeechAssembly(root, ['one', 'two'])
            self.assertEqual(resumed.index, 1)
            self.assertEqual(resumed.path.stat().st_size, committed)
            (root / 'assembly.json').write_text('{bad')
            self.assertEqual(SpeechAssembly(root, ['one', 'two']).index, 0)
            self.assertEqual((root / 'assembly.pcm').stat().st_size, 0)

    def test_wrong_format_and_truncated_chunk(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            part = root / 'part.wav'
            pcm(part)
            assembly = SpeechAssembly(root, ['one'])
            assembly.append(part, 0)
            pcm(part, rate=48000)
            with self.assertRaises(RuntimeError): assembly.append(part, 0)
            pcm(part)
            part.write_bytes(part.read_bytes()[:-4])
            with self.assertRaises(RuntimeError): assembly.append(part, 0)

    def test_edge_fades_and_bounded_pauses(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            part = root / 'part.wav'
            pcm(part)
            assembly = SpeechAssembly(root, ['one', 'two'])
            assembly.append(part, 300)
            assembly.append(part, 100)
            assembly.commit(2)
            self.assertAlmostEqual(assembly.duration, .3)
            self.assertEqual(assembly.path.read_bytes()[:2], b'\x00\x00')
            self.assertEqual(assembly.path.read_bytes()[-2:], b'\x00\x00')


class ImportGuards(unittest.TestCase):
    def test_backlog_owner_and_cleanup(self):
        setup()
        manager = ImportTasks()
        release = threading.Event()
        def parse(source, result):
            release.wait(5)
            result.write_text('{"text":"hello"}')
            return {'ready': True}
        try:
            with patch.object(manager, 'parse', side_effect=parse):
                keys = [manager.submit(upload('test.txt', b'hello'), 1) for _ in range(4)]
                with self.assertRaises(ValueError): manager.submit(upload('test.txt', b'hello'), 1)
                self.assertEqual(manager.result(keys[0], 2)[1], 404)
                self.assertEqual(manager.result(keys[-1], 1)[1], 202)
                release.set()
        finally:
            release.set()
            manager.close()
        self.assertEqual(list(manager.root.iterdir()), [])

    def test_parser_timeout_kills_process_group(self):
        setup()
        manager = ImportTasks()
        process = SimpleNamespace(pid=12345678, poll=lambda: None, wait=lambda: None)
        try:
            with tempfile.TemporaryDirectory(dir=manager.root) as folder:
                source = Path(folder) / 'source.txt'
                source.write_text('hello')
                with patch('import_tasks.subprocess.Popen', return_value=process), patch('import_tasks.os.killpg') as kill, patch('import_tasks.time.monotonic', side_effect=[0, 91]):
                    result = manager.parse(source, Path(folder) / 'result.json')
                self.assertIn('90 seconds', result['detail'])
                kill.assert_called_once()
                self.assertFalse(source.exists())
        finally:
            manager.close()


class WorkerIntegration(unittest.TestCase):
    def setUp(self):
        setup()
        with connect() as db: db.execute('DELETE FROM jobs')

    def job(self, model='qwen', text='First sentence. Second sentence.', format='wav'):
        settings = defaults(model)
        settings.update(format=format, chunk_size=80, pause_ms=20)
        key = os.urandom(16).hex()
        with connect() as db:
            db.execute("INSERT INTO jobs(id,user_id,model,title,script,settings,status) VALUES (?,1,?,'Test',?,?,'running')", (key, model, text, json.dumps(settings)))
        return one('SELECT * FROM jobs WHERE id=?', (key,))

    def worker(self):
        worker = Worker()
        worker.prepare = lambda *args: ({}, True)
        worker.unload = lambda: None
        worker.send = lambda key, payload, **kwargs: pcm(Path(payload['output']))
        return worker

    def test_all_model_paths_and_exports(self):
        for model in MODELS:
            for format in ('wav', 'mp3', 'flac'):
                with self.subTest(model=model, format=format):
                    job = self.job(model=model, format=format)
                    self.worker().run(job)
                    row = one('SELECT * FROM jobs WHERE id=?', (job['id'],))
                    self.assertEqual(row['status'], 'finished')
                    self.assertGreater((JOBS / job['id'] / ('speech.' + format)).stat().st_size, 40)
                    self.assertAlmostEqual(row['duration'], .1)

    def test_token_limit_adaptive_retry_and_connection_rewind(self):
        text = 'A sentence with several words. Another sentence with several words.'
        job = self.job(text=text)
        worker = self.worker()
        calls = []
        failed = False
        def render(key, payload, **kwargs):
            nonlocal failed
            value = payload['text']
            calls.append(value)
            if len(value) > 40: raise SpeechLimitError('budget')
            if value.startswith('Another') and not failed:
                failed = True
                raise ModelConnectionError('connection lost')
            pcm(Path(payload['output']))
        worker.send = render
        worker.run(job)
        self.assertEqual(calls.count(text), 2)
        self.assertEqual(one('SELECT done FROM jobs WHERE id=?', (job['id'],))['done'], 1)
        self.assertAlmostEqual(one('SELECT duration FROM jobs WHERE id=?', (job['id'],))['duration'], .22)

    def test_stop_resume_and_cancel(self):
        text = 'This is a sentence with enough words to be one chunk. ' * 4
        job = self.job(text=text)
        worker = self.worker()
        calls = 0
        def render(key, payload, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2: raise WorkerStopping()
            pcm(Path(payload['output']))
        worker.send = render
        with self.assertRaises(WorkerStopping): worker.run(job)
        self.assertEqual(one('SELECT done FROM jobs WHERE id=?', (job['id'],))['done'], 1)
        second = self.worker()
        remaining = []
        second.send = lambda key, payload, **kw: (remaining.append(payload['text']), pcm(Path(payload['output'])))
        second.run(job)
        self.assertEqual(len(remaining), len(list(chunk_text(text, 80))) - 1)
        next_job = self.job()
        update(next_job['id'], cancel=1)
        with self.assertRaises(Cancelled): self.worker().run(next_job)

    def test_full_600k_shared_worker_path(self):
        text = ('A complete sentence for testing the recording assembly. ' * 12000)[:600000]
        job = self.job(text=text, format='mp3')
        settings = json.loads(job['settings'])
        settings.update(chunk_size=320, normalize=True)
        job['settings'] = json.dumps(settings)
        worker = self.worker()
        rendered = []
        worker.send = lambda key, payload, **kw: (rendered.append(payload['text']), pcm(Path(payload['output']), frames=120))
        worker.run(job)
        row = one('SELECT * FROM jobs WHERE id=?', (job['id'],))
        self.assertEqual(row['status'], 'finished')
        self.assertEqual(row['done'], row['total'])
        self.assertGreater(len(rendered), 1800)
        self.assertEqual(''.join(''.join(chunk.split()) for chunk in rendered), ''.join(text.split()))

    def test_unload_failure_is_reported_without_claiming_more_work(self):
        worker = Worker()
        with patch.object(worker, 'unload', side_effect=RuntimeError('process still exiting')), self.assertLogs('queue_worker', level='ERROR'):
            self.assertFalse(worker.try_unload())

    def test_export_cancellation_terminates_ffmpeg(self):
        job = self.job()
        folder = JOBS / job['id']
        folder.mkdir()
        pcm(folder / 'part.wav')
        assembly = SpeechAssembly(folder, ['one'])
        assembly.append(folder / 'part.wav', 0)
        assembly.commit(1)
        worker = self.worker()
        killed = []
        process = SimpleNamespace(poll=lambda: None, kill=lambda: killed.append(True), wait=lambda: None)
        with patch('queue_worker.subprocess.Popen', return_value=process), patch.object(worker, 'check', side_effect=[None, Cancelled()]):
            with self.assertRaises(Cancelled): worker.export(job['id'], folder, assembly, defaults('qwen'))
        self.assertEqual(killed, [True])
        self.assertFalse((folder / 'speech.wav').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
