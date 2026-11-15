// Headless DOM tests, no browser or computer control. Needs jsdom + fake-indexeddb.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM, VirtualConsole} = require('jsdom');
const {indexedDB} = require('fake-indexeddb');
const root = require('node:path').resolve(__dirname, '..');
const html = fs.readFileSync(process.env.STUDIO_TEST_HTML || root + '/audit-studio.html', 'utf8');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let errors = [];

async function page(session = {}, reload = false) {
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('error', (...values) => errors.push(values.map(value => value?.stack || String(value)).join(' ')));
  virtualConsole.on('jsdomError', error => {
    if (!/navigation|HTMLMediaElement/.test(String(error))) errors.push(String(error));
  });
  const dom = new JSDOM(html, {url: 'https://speech.example.com/', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole});
  const w = dom.window;
  w.indexedDB = indexedDB;
  w.performance.getEntriesByType = () => [{type: reload ? 'reload' : 'navigate'}];
  w.matchMedia = () => ({matches: false, addEventListener() {}});
  w.htmx = {trigger() {}, process() {}};
  w.CSS = {escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, char => '\\' + char)};
  w.fetch = async () => ({ok: true, status: 200, json: async () => ({})});
  for (const [key, value] of Object.entries(session)) w.sessionStorage.setItem(key, value);
  w.eval(fs.readFileSync(root + '/static/draft-storage.js', 'utf8'));
  w.eval(fs.readFileSync(root + '/static/run-splitting.js', 'utf8') + '\nwindow.SpeechRuns = SpeechRuns;');
  await w.eval(fs.readFileSync(root + '/static/studio.js', 'utf8'));
  return dom;
}

function session(dom) {
  return Object.fromEntries(Object.keys(dom.window.sessionStorage).map(key => [key, dom.window.sessionStorage.getItem(key)]));
}

function input(dom, selector, value) {
  const el = dom.window.document.querySelector(selector);
  el.value = value;
  el.dispatchEvent(new dom.window.Event('input', {bubbles: true}));
  return el;
}

function submission(dom) {
  const w = dom.window;
  const form = w.document.querySelector('#speech-form');
  const detail = {elt: form, formData: new w.FormData(form)};
  w.document.dispatchEvent(new w.CustomEvent('htmx:configRequest', {detail}));
  return detail.formData;
}

(async () => {
  let dom = await page();
  let d = dom.window.document;
  assert.equal(d.querySelector('#ebook-picker').hidden, true);
  const text = 'A'.repeat(300000) + 'B'.repeat(350000) + 'C'.repeat(550000);
  const book = {name: 'filename.epub', title: 'Test book', text, estimatedPages: true,
    chapters: [{label: 'Chapter 1', start: 0, end: 300000}, {label: 'Chapter 2', start: 300000, end: 650000}, {label: 'Chapter 3', start: 650000, end: 1200000}],
    sections: [{label: 'Part 1', start: 0, end: 1200000}], pages: [{label: '1', start: 0, end: 1200000}]};
  d.querySelector('#ebook-picker').dispatchEvent(new dom.window.CustomEvent('booksImported', {detail: [book]}));
  assert.equal(d.querySelector('#ebook-picker').hidden, false);
  const through = d.querySelector('#ebook-through');
  through.value = '2';
  through.dispatchEvent(new dom.window.Event('change', {bubbles: true}));
  d.querySelector('#load-ebook-text').click();
  assert.equal(d.querySelector('[name=title]').value, 'Test book-ch1-3');
  assert.equal(d.querySelector('#generate-label').textContent, 'Generate audio (2 runs)');
  let request = submission(dom);
  assert.deepEqual(errors, []);
  assert.equal(request.get('script').length, 1200000);
  assert.equal(JSON.parse(request.get('import_range')).spans.length, 3);
  assert.equal(request.get('submission_key'), submission(dom).get('submission_key'));
  d.querySelector('#script').setSelectionRange(300000, 650000);
  d.querySelector('#script').dispatchEvent(new dom.window.Event('select'));
  request = submission(dom);
  assert.equal(request.get('script').length, 350000);
  assert.deepEqual(JSON.parse(request.get('import_range')).spans, [[0, 350000, '2']]);
  const firstTab = request.get('request_tab');
  input(dom, '#script', 'Z'.repeat(5000000));
  input(dom, '#model', 'lahgtna');
  input(dom, '[name=title]', 'Custom title');
  await delay(300);
  assert.equal(submission(dom).get('import_range'), null, 'Editing invalidates stale chapter offsets');
  const saved = session(dom);
  dom.window.close();
  dom = await page(saved);
  d = dom.window.document;
  assert.equal(d.querySelector('#script').value.length, 5000000, 'Large draft survives navigation');
  assert.equal(d.querySelector('#model').value, 'lahgtna');
  assert.equal(d.querySelector('[name=title]').value, 'Custom title');
  assert.equal(d.querySelector('#ebook-picker').hidden, false, 'Imported book survives navigation alongside script');
  assert.notEqual(submission(dom).get('request_tab'), firstTab, 'Duplicated sessions still have distinct request identities');
  dom.window.close();
  dom = await page(saved, true);
  d = dom.window.document;
  assert.equal(d.querySelector('#script').value, '', 'Manual reload intentionally resets draft');
  assert.equal(d.querySelector('#model').value, 'qwen');
  assert.equal(d.querySelector('#ebook-picker').hidden, true);
  dom.window.close();

  dom = await page();
  d = dom.window.document;
  const w = dom.window;
  let receiptStatus = 200;
  let receiptBody = {text: 'Imported document text.', warnings: ['Some pages were read with OCR.']};
  let asynchronous = false;
  w.XMLHttpRequest = class {
    constructor() { this.upload = {}; }
    open() {}
    setRequestHeader(name, value) { if (name === 'X-Import-Async' && value === '1') asynchronous = true; }
    send() {
      this.status = 202;
      this.response = {import_id: 'test-import'};
      setTimeout(() => { this.upload.onload?.(); this.onload(); }, 0);
    }
  };
  w.fetch = async () => ({status: receiptStatus, headers: {get: () => 'application/json'}, json: async () => receiptBody});
  function chooseFile(name) {
    const chooser = d.querySelector('#import-text');
    Object.defineProperty(chooser, 'files', {configurable: true, value: [new w.File(['fixture'], name)]});
    chooser.dispatchEvent(new w.Event('change', {bubbles: true}));
  }
  chooseFile('Example document.pdf');
  await delay(1000);
  assert.equal(asynchronous, true);
  assert.equal(d.querySelector('#script').value, 'Imported document text.');
  assert.equal(d.querySelector('[name=title]').value, 'Example document');
  assert.equal(d.querySelector('#ebook-picker').hidden, true, 'PDF never opens the ebook picker');
  assert.match(d.querySelector('#import-status').textContent, /OCR/);
  receiptStatus = 400;
  receiptBody = {detail: 'This document is damaged.'};
  chooseFile('bad.docx');
  await delay(1000);
  assert.match(d.querySelector('#import-status').textContent, /damaged/);
  assert.equal(d.querySelector('#script').value, 'Imported document text.', 'Failed imports preserve the existing script');
  assert.equal(d.querySelector('#import-progress').hidden, true);
  receiptStatus = 401;
  chooseFile('expired-login.pdf');
  await delay(1000);
  assert.match(d.querySelector('#import-status').textContent, /Sign in to Pangolin/);
  assert.equal(d.querySelector('#script').value, 'Imported document text.');
  d.querySelector('#ebook-picker').dispatchEvent(new w.CustomEvent('booksImported', {detail: [{...book, title: 'Interlude book', text: 'Spoken text.',
    chapters: [{label: 'Interlude I-1: Opening', start: 0, end: 12}]}]}));
  d.querySelector('#load-ebook-text').click();
  assert.equal(d.querySelector('[name=title]').value, 'Interlude book-chI-1');
  dom.window.close();
  assert.deepEqual(errors, []);
  console.log('PASS: ebook-only picker, ranges, 1.2M batching label, selected-range submission, idempotence, 5M draft + ebook retention, model retention, distinct request identities, reload reset, async upload/polling, filename title, OCR warnings, failure preserves script, expired-login message.');
})().catch(error => { console.error(error); process.exitCode = 1; });
