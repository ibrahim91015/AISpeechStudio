const editor = document.querySelector('#script');
let previewUrl = '';
let recorder = null;

function refreshCount() {
  if (editor) document.querySelector('#character-count').textContent = `${editor.value.length.toLocaleString()} characters`;
}

function replaceText(value) {
  editor.value = value;
  refreshCount();
  editor.focus();
}

function previewReference() {
  const clip = document.querySelector('#reference');
  const audio = document.querySelector('#reference-preview');
  if (!clip || !audio) return;
  const dialect = document.querySelector('#dialect').value;
  const voice = document.querySelector('#voice-id').value;
  const file = clip.files[0];
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = file ? URL.createObjectURL(file) : '';
  const src = previewUrl || (voice ? `/voices/${voice}/audio` : dialect !== 'auto' ? `/references/${dialect}` : '');
  audio.hidden = !src;
  if (src) audio.src = src;
  else audio.removeAttribute('src');
  document.querySelector('#reset-reference').hidden = !file;
  document.querySelector('#reference-status').textContent = file ? file.name : 'Optional. A clip overrides the selected voice for this job.';
}

async function record(button) {
  const status = document.querySelector('#reference-status');
  if (recorder?.state === 'recording') {
    recorder.stop();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    status.textContent = 'Recording requires HTTPS or localhost. You can still upload an audio file.';
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const active = new MediaRecorder(stream);
    recorder = active;
    const chunks = [];
    active.ondataavailable = (event) => chunks.push(event.data);
    active.onstop = () => {
      stream.getTracks().forEach((track) => track.stop());
      button.textContent = 'Record';
      const input = document.querySelector('#reference');
      if (!input || !button.isConnected) return;
      const recorded = new DataTransfer();
      const ext = active.mimeType.includes('mp4') ? 'm4a' : active.mimeType.includes('ogg') ? 'ogg' : 'webm';
      recorded.items.add(new File(chunks, `Recording.${ext}`, { type: active.mimeType }));
      input.files = recorded.files;
      previewReference();
    };
    active.start();
    button.textContent = 'Stop recording';
    status.textContent = 'Recording microphone…';
    window.setTimeout(() => { if (active.state === 'recording') active.stop(); }, 30000);
  } catch {
    status.textContent = 'Microphone access failed. Check the browser permission or upload a file.';
  }
}

if (editor) editor.addEventListener('input', refreshCount);

document.addEventListener('change', async (event) => {
  const el = event.target;
  if (el.hasAttribute('data-autosubmit')) el.form.submit();
  if (el.id === 'load-preset' && el.value) location.assign(`/?preset=${encodeURIComponent(el.value)}`);
  if (['dialect', 'voice-id', 'reference'].includes(el.id)) previewReference();
  if (el.id === 'import-text' && el.files[0]) {
    const text = await el.files[0].text();
    if (text.length > editor.maxLength) {
      document.querySelector('#notice').textContent = 'The file exceeds 150,000 characters.';
    } else replaceText(text);
    el.value = '';
  }
});

document.addEventListener('pointerdown', (event) => {
  if (event.target.closest('[data-mark]')) event.preventDefault();
});

document.addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (!button || !editor) return;
  const model = document.querySelector('#model').value;
  if (button.id === 'record-reference') record(button);
  if (button.id === 'reset-reference') {
    document.querySelector('#reference').value = '';
    previewReference();
  }
  if (button.id === 'clear-text') replaceText('');
  if (button.id === 'remove-marks') replaceText(editor.value.replace(/[\u064b-\u0652]/g, ''));
  if (button.id === 'sample-text' || button.id === 'random-text') {
    const catalog = JSON.parse(document.querySelector('#editor-catalog').textContent);
    if (model === 'qwen') replaceText('The lights are on. Let’s record a short introduction.');
    else if (button.id === 'random-text') replaceText(catalog.examples[Math.floor(Math.random() * catalog.examples.length)]);
    else replaceText(catalog.dialects[document.querySelector('#dialect').value][2] || catalog.examples[0]);
  }
  if (button.dataset.mark) {
    const end = editor.selectionEnd;
    editor.setRangeText(button.dataset.mark, end, end, 'end');
    editor.focus();
    refreshCount();
  }
});

document.addEventListener('htmx:beforeSwap', (event) => {
  if (event.detail.target?.id !== 'activity') return;
  const isPoll = event.detail.xhr.responseURL.endsWith('/activity');
  if (isPoll && [...event.detail.target.querySelectorAll('audio')].some((audio) => !audio.paused)) {
    event.detail.shouldSwap = false;
  }
});

document.addEventListener('htmx:afterSwap', (event) => {
  if (event.detail.target?.id === 'fields') {
    if (recorder?.state === 'recording') recorder.stop();
    document.querySelector('#arabic-tools').hidden = document.querySelector('#model').value !== 'lahgtna';
    previewReference();
  }
});
