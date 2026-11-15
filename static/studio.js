const editor = document.querySelector('#script');
let previewUrl = '';
let recorder = null;
let importing = false;

function refreshCount() {
  if (editor) document.querySelector('#character-count').textContent = `${editor.value.length.toLocaleString()} characters`;
}

function replaceText(value) {
  editor.value = value;
  refreshCount();
  editor.focus();
}

function showProgress(panel, label, percent) {
  panel.hidden = false;
  const [message, number] = panel.querySelectorAll('span');
  const bar = panel.querySelector('progress');
  if (message.textContent !== label) message.textContent = label;
  number.textContent = percent === null ? '' : percent + '%';
  if (percent === null) bar.removeAttribute('value');
  else bar.value = percent;
}

function requestImport(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append('document', file);
    xhr.open('POST', '/import-text');
    xhr.responseType = 'json';
    xhr.upload.onprogress = (event) => onProgress(event.lengthComputable ? event.loaded / event.total : null);
    xhr.upload.onload = () => onProgress(1);
    xhr.onload = () => {
      if (xhr.status === 200 && xhr.response) resolve(xhr.response.text);
      else reject(new Error(file.name + ': ' + (xhr.response?.detail || 'Import failed.')));
    };
    xhr.onerror = () => reject(new Error('Connection lost during document import.'));
    xhr.send(form);
  });
}

async function importFiles(files) {
  if (!files.length || importing) return;
  const status = document.querySelector('#import-status');
  const progress = document.querySelector('#import-progress');
  const initial = editor.value;
  const texts = [];
  const total = files.reduce((sum, file) => sum + file.size, 0);
  let uploaded = 0;
  let length = 0;
  importing = true;
  status.hidden = true;
  status.className = 'inline-message';
  showProgress(progress, 'Uploading documents', 0);
  try {
    for (const file of files) {
      if (file.size > 12 * 1024 * 1024) throw new Error(file.name + ' exceeds 12 MB.');
      const text = await requestImport(file, (fraction) => {
        const percent = fraction === null || !total ? null : Math.floor(100 * (uploaded + fraction * file.size) / total);
        showProgress(progress, fraction === 1 ? 'Reading ' + file.name : 'Uploading documents', percent);
      });
      uploaded += file.size;
      length += text.length + (texts.length ? 2 : 0);
      if (length > editor.maxLength) throw new Error('The combined text exceeds 150,000 characters.');
      texts.push(text);
    }
    if (editor.value !== initial) throw new Error('The script changed during import. Drop the files again to replace it.');
    replaceText(texts.join('\n\n'));
    status.classList.add('notice-ok');
    status.textContent = files.length === 1 ? 'Imported ' + files[0].name + '.' : 'Imported ' + files.length + ' files.';
  } catch (error) {
    status.classList.add('notice-error');
    status.textContent = error.message;
  } finally {
    progress.hidden = true;
    status.hidden = false;
    importing = false;
  }
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
  const src = previewUrl || (voice ? `/voices/${voice}/audio?reference=true` : dialect !== 'auto' ? `/references/${dialect}` : '');
  audio.hidden = !src;
  if (src) audio.src = src;
  else audio.removeAttribute('src');
  document.querySelector('#reset-reference').hidden = !file;
  const status = document.querySelector('#reference-status');
  status.textContent = file ? file.name : '';
  status.hidden = !file;
}

async function record(button) {
  const status = document.querySelector('#reference-status');
  status.hidden = false;
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

if (editor) {
  editor.addEventListener('input', refreshCount);
  editor.addEventListener('dragover', (event) => {
    if (!event.dataTransfer.types.includes('Files')) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    editor.classList.add('drop-target');
  });
  editor.addEventListener('dragleave', () => editor.classList.remove('drop-target'));
  editor.addEventListener('drop', (event) => {
    editor.classList.remove('drop-target');
    if (!event.dataTransfer.files.length) return;
    event.preventDefault();
    importFiles([...event.dataTransfer.files]);
  });
  const mobileLayout = matchMedia('(max-width: 740px)');
  const submitRow = document.querySelector('.submit-row');
  const orderSubmit = () => {
    document.querySelector(mobileLayout.matches ? '#speech-form' : '.script-panel').append(submitRow);
  };
  orderSubmit();
  mobileLayout.addEventListener('change', orderSubmit);
}

document.addEventListener('change', (event) => {
  const el = event.target;
  if (el.hasAttribute('data-autosubmit')) el.form.submit();
  if (el.id === 'load-preset' && el.value) location.assign(`/?preset=${encodeURIComponent(el.value)}`);
  if (['dialect', 'voice-id', 'reference'].includes(el.id)) previewReference();
  if (el.id === 'import-text' && el.files[0]) {
    importFiles([...el.files]);
    el.value = '';
  }
});

document.addEventListener('voiceSaved', (event) => {
  const select = document.querySelector('#voice-id');
  const file = document.querySelector('#reference');
  if (!select || !file) return;
  const { id, name } = event.detail;
  select.add(new Option(name, id, false, true));
  file.value = '';
  previewReference();
});

document.addEventListener('submit', (event) => {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});

document.addEventListener('htmx:beforeRequest', (event) => {
  const form = event.detail.elt;
  const panel = document.getElementById(form.dataset.uploadProgress);
  if (!panel) return;
  const voice = panel.id === 'voice-upload-progress';
  const uploading = voice ? 'Uploading audio clip' : 'Uploading script and reference';
  const processing = voice ? 'Saving voice clip' : 'Processing submission';
  const xhr = event.detail.xhr;
  showProgress(panel, uploading, 0);
  xhr.upload.addEventListener('progress', (progress) => {
    const percent = progress.lengthComputable ? Math.floor(100 * progress.loaded / progress.total) : null;
    showProgress(panel, uploading, percent);
  });
  xhr.upload.addEventListener('load', () => showProgress(panel, processing, 100));
  xhr.addEventListener('loadend', () => { panel.hidden = true; });
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
  const target = event.detail.target;
  if (target?.id !== 'activity') return;
  const next = new DOMParser().parseFromString(event.detail.serverResponse, 'text/html').querySelector('#activity');
  const list = target.querySelector('.job-list');
  const nextList = next?.querySelector('.job-list');
  if (!list || !nextList) return;
  event.detail.shouldSwap = false;
  target.querySelector('.status-pill').replaceWith(next.querySelector('.status-pill'));
  target.querySelector('#gpu-progress').replaceWith(next.querySelector('#gpu-progress'));
  const existing = new Map([...list.children].map((row) => [row.dataset.job, row]));
  for (const row of [...nextList.children].reverse()) {
    const old = existing.get(row.dataset.job);
    existing.delete(row.dataset.job);
    if (!old) {
      list.prepend(row);
      htmx.process(row);
    } else if (old.innerHTML !== row.innerHTML || old.dataset.status !== row.dataset.status) {
      const focused = old.contains(document.activeElement);
      const nextProgress = row.querySelector('.job-progress');
      if (focused && nextProgress) {
        old.dataset.status = row.dataset.status;
        const progress = old.querySelector('.job-progress');
        if (progress && progress.innerHTML !== nextProgress.innerHTML) progress.replaceWith(nextProgress);
      } else {
        old.replaceWith(row);
        htmx.process(row);
        if (focused) row.querySelector('a, button')?.focus();
      }
    }
  }
  for (const row of existing.values()) {
    const audio = row.querySelector('audio');
    if (!row.contains(document.activeElement) && !(audio && audio.currentTime > 0 && !audio.ended)) row.remove();
  }
});

document.addEventListener('htmx:afterSwap', (event) => {
  if (event.detail.target?.id === 'fields') {
    if (recorder?.state === 'recording') recorder.stop();
    document.querySelector('#arabic-tools').hidden = document.querySelector('#model').value !== 'lahgtna';
    previewReference();
  }
});
