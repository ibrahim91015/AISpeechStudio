const editor = document.querySelector('#script');
let recorder = null;
let importing = false;
let voiceUpload = null;
const draftPrefix = 'aispeech-draft:';
const draftKey = draftPrefix + document.body.dataset.profile;
const studioForm = document.querySelector('#speech-form');
const studioFields = document.querySelector('#fields');
const modelSelect = document.querySelector('#model');
const catalog = editor ? JSON.parse(document.querySelector('#editor-catalog').textContent) : {};
let lastArabicSample;
let draft = {models: {}};
let draftTimer;
try {
  if (performance.getEntriesByType('navigation')[0]?.type === 'reload') {
    for (const key of Object.keys(sessionStorage)) if (key.startsWith(draftPrefix)) sessionStorage.removeItem(key);
  }
  if (!new URLSearchParams(location.search).size) {
    draft = JSON.parse(sessionStorage.getItem(draftKey)) || draft;
  }
} catch { /* The editor still works with browser storage disabled. */ }

function draftControls(container) {
  return [...container.querySelectorAll('input[name], select[name], textarea[name], details')]
    .filter(el => (container !== studioForm || !studioFields.contains(el)) && !['file', 'submit'].includes(el.type));
}

function values(container) {
  return Object.fromEntries(draftControls(container)
    .map((el, index) => [el.name || ('details:' + index), el.type === 'checkbox' ? el.checked :
      el.tagName === 'DETAILS' ? el.open : el.value]));
}

function restore(container, fields) {
  if (!fields) return;
  draftControls(container).forEach((el, index) => {
    const saved = fields[el.name || ('details:' + index)];
    if (saved === undefined || ['file', 'submit'].includes(el.type)) return;
    if (el.type === 'checkbox') el.checked = saved;
    else if (el.tagName === 'DETAILS') el.open = saved;
    else if (el.tagName !== 'SELECT' || [...el.options].some(option => option.value === saved)) el.value = saved;
  });
}

function rememberDraft() {
  if (!studioForm) return;
  const fields = studioFields.firstElementChild;
  if (fields) draft.models[fields.dataset.model] = values(fields);
  draft.common = values(studioForm);
  draft.model = modelSelect.value;
  try { sessionStorage.setItem(draftKey, JSON.stringify(draft)); } catch { /* Best effort when storage is full. */ }
}

function scheduleSave() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(rememberDraft, 160);
}

function refreshCount() {
  if (editor) document.querySelector('#character-count').textContent = `${editor.value.length.toLocaleString()} characters`;
}

function replaceText(value) {
  editor.value = value;
  refreshCount();
  editor.focus();
  scheduleSave();
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

function uploadFile(url, field, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append(field, file);
    xhr.open('POST', url);
    xhr.responseType = 'json';
    xhr.upload.onprogress = (event) => onProgress(event.lengthComputable ? event.loaded / event.total : null);
    xhr.upload.onload = () => onProgress(1);
    xhr.onload = () => {
      if (xhr.status === 200 && xhr.response) resolve(xhr.response);
      else reject(new Error(file.name + ': ' + (xhr.response?.detail || 'Upload failed.')));
    };
    xhr.onerror = () => reject(new Error('Connection lost during upload.'));
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
      const { text } = await uploadFile('/import-text', 'document', file, (fraction) => {
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
  const dialect = document.querySelector('#dialect');
  const reference = document.querySelector('#voice-id');
  if (dialect && reference) {
    reference.options[0].textContent = dialect.value === 'auto' ? 'Dialect reference · Auto-detect' :
      'Dialect reference · ' + dialect.selectedOptions[0].textContent;
  }
  for (const [selector, player] of [['#voice-id', '#reference-preview'], ['#emotion-voice', '#emotion-preview']]) {
    const select = document.querySelector(selector);
    const audio = document.querySelector(player);
    if (!select || !audio) continue;
    const voice = select.value || (dialect?.value && dialect.value !== 'auto' ? 'dialect:' + dialect.value : '');
    const src = voice.startsWith('dialect:') ? '/references/' + voice.slice(8) :
      (voice ? '/voices/' + voice + '/audio?reference=true' : '');
    audio.hidden = !src;
    if (src && audio.getAttribute('src') !== src) audio.src = src;
    else if (!src && audio.hasAttribute('src')) {
      audio.removeAttribute('src');
      audio.load();
    }
  }
}

function refreshModelUi() {
  const isArabic = studioFields.firstElementChild.dataset.model === 'lahgtna';
  document.querySelector('#arabic-tools').hidden = !isArabic;
  document.querySelector('#random-text').hidden = !isArabic;
  document.querySelector('#sample-text').hidden = isArabic;
  const mode = document.querySelector('#emotion-mode');
  if (mode) studioFields.querySelectorAll('[data-emotion]').forEach(el => { el.hidden = el.dataset.emotion !== mode.value; });
  previewReference();
}

function uploadVoice(file, progress, callback) {
  if (file.size > 25 * 1024 * 1024) return Promise.reject(new Error(file.name + ' exceeds 25 MB.'));
  return uploadFile('/voices/upload', 'reference', file, fraction => {
    showProgress(progress, fraction === 1 ? 'Saving ' + file.name : 'Uploading ' + file.name,
      fraction === null ? null : Math.floor(fraction * 100));
  }).then(callback);
}

function addVoice(option, chosen) {
  studioFields.querySelectorAll('#voice-id, #emotion-voice').forEach(select => {
    let group = select.querySelector('optgroup[label="Uploaded voices"]');
    if (!group) {
      group = document.createElement('optgroup');
      group.label = 'Uploaded voices';
      select.options[0].after(group);
    }
    group.append(new Option(option.name, option.id, false, select.id === chosen));
    if (select.id === chosen) select.value = option.id;
  });
  previewReference();
}

function uploadStudioReference(file, selected) {
  if (!file || voiceUpload) return;
  const status = document.querySelector('#reference-status');
  const progress = document.querySelector('#reference-upload-progress');
  modelSelect.disabled = true;
  status.hidden = true;
  showProgress(progress, 'Uploading ' + file.name, 0);
  voiceUpload = uploadVoice(file, progress, voice => {
    addVoice(voice, selected);
    status.textContent = 'Saved ' + voice.name;
    rememberDraft();
  }).catch(error => { status.textContent = error.message; }).finally(() => {
    status.hidden = false;
    progress.hidden = true;
    modelSelect.disabled = false;
    voiceUpload = null;
  });
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
      const ext = active.mimeType.includes('mp4') ? 'm4a' : active.mimeType.includes('ogg') ? 'ogg' : 'webm';
      uploadStudioReference(new File(chunks, 'Recording.' + ext, { type: active.mimeType }), 'voice-id');
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
  restore(studioForm, draft.common);
  const current = studioFields.firstElementChild.dataset.model;
  restore(studioFields, draft.models[current]);
  if (draft.model && draft.model !== current && [...modelSelect.options].some(option => option.value === draft.model)) {
    modelSelect.value = draft.model;
    window.addEventListener('DOMContentLoaded', () => htmx.trigger(modelSelect, 'change'), {once: true});
  }
  refreshCount();
  refreshModelUi();
  studioForm.addEventListener('input', scheduleSave);
  studioForm.addEventListener('change', scheduleSave);
  studioForm.addEventListener('toggle', scheduleSave, true);
  window.addEventListener('pagehide', rememberDraft);
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
  if (['dialect', 'voice-id', 'emotion-voice', 'emotion-mode'].includes(el.id)) refreshModelUi();
  if (['reference', 'emotion-reference'].includes(el.id)) {
    uploadStudioReference(el.files[0], el.id === 'reference' ? 'voice-id' : 'emotion-voice');
    el.value = '';
  }
  if (el.id === 'import-text' && el.files[0]) {
    importFiles([...el.files]);
    el.value = '';
  }
});

const libraryFile = document.querySelector('#voice-files');
if (libraryFile) {
  const name = document.querySelector('#new-voice-name');
  const progress = document.querySelector('#voice-upload-progress');
  const status = document.querySelector('#voice-notice');
  let latestVoice;
  const refresh = async () => {
    const response = await fetch('/voices');
    if (!response.ok) throw new Error('Refresh Voices to see the latest clips.');
    const page = new DOMParser().parseFromString(await response.text(), 'text/html');
    document.querySelector('#saved-voices').replaceWith(page.querySelector('#saved-voices'));
  };
  libraryFile.addEventListener('change', () => {
    const files = [...libraryFile.files];
    if (!files.length || voiceUpload) return;
    latestVoice = null;
    name.closest('form').hidden = files.length > 1;
    name.value = files.length === 1 ? files[0].name.replace(/\.[^.]+$/, '').slice(0, 100) : '';
    name.disabled = true;
    libraryFile.disabled = true;
    status.textContent = '';
    voiceUpload = (async () => {
      let saved = 0;
      const errors = [];
      for (const [index, file] of files.entries()) {
        try {
          showProgress(progress, 'Uploading ' + (index + 1) + ' of ' + files.length + ' · ' + file.name, 0);
          const item = await uploadVoice(file, progress, voice => voice);
          if (files.length === 1) {
            latestVoice = item.id;
            name.value = item.name;
          }
          saved++;
        } catch (error) { errors.push(error.message); }
      }
      if (saved) await refresh();
      status.className = errors.length ? 'inline-message notice-error' : 'inline-message notice-ok';
      status.textContent = [saved ? 'Saved ' + saved + (saved === 1 ? ' voice.' : ' voices.') : '', ...errors].filter(Boolean).join(' ');
    })().catch(error => { status.textContent = error.message; }).finally(() => {
      progress.hidden = true;
      libraryFile.disabled = false;
      name.disabled = !latestVoice;
      libraryFile.value = '';
      voiceUpload = null;
    });
  });
  const rename = document.querySelector('#name-latest-voice');
  rename.addEventListener('submit', async event => {
    event.preventDefault();
    if (!latestVoice) return;
    try {
      const result = await fetch('/voices/' + latestVoice + '/rename', {
        method: 'POST', headers: {Accept: 'application/json'}, body: new URLSearchParams({name: name.value})
      });
      if (!result.ok) throw new Error('Could not rename the voice.');
      name.value = (await result.json()).name;
      await refresh();
      status.textContent = 'Voice renamed.';
    } catch (error) { status.textContent = error.message; }
  });
}

document.addEventListener('click', (event) => {
  const link = event.target.closest('a[href]');
  if (!voiceUpload || !link || link.origin !== location.origin || !['/','/library','/voices'].includes(link.pathname) ||
      event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  voiceUpload.then(() => location.assign(link.href));
});

document.addEventListener('submit', (event) => {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});

document.addEventListener('htmx:beforeRequest', (event) => {
  const form = event.detail.elt;
  if (form === studioForm && voiceUpload) {
    event.preventDefault();
    document.querySelector('#notice').textContent = 'Wait for the reference upload to finish.';
    return;
  }
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
  if (button.id === 'clear-text') replaceText('');
  if (button.id === 'remove-marks') replaceText(editor.value.replace(/[\u064b-\u0652]/g, ''));
  if (button.id === 'sample-text' || button.id === 'random-text') {
    if (button.id === 'random-text') {
      const samples = Object.values(catalog).map(item => item[2]).filter(sample => sample && sample !== lastArabicSample);
      lastArabicSample = samples[Math.floor(Math.random() * samples.length)];
      replaceText(lastArabicSample);
    } else replaceText('The lights are on. Let’s record a short introduction.');
  }
  if (button.dataset.mark) {
    const end = editor.selectionEnd;
    editor.setRangeText(button.dataset.mark, end, end, 'end');
    editor.focus();
    refreshCount();
    scheduleSave();
  }
});

document.addEventListener('htmx:beforeSwap', (event) => {
  const target = event.detail.target;
  if (target?.id === 'fields') rememberDraft();
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
    } else if (old.dataset.status === 'finished' && row.dataset.status === 'finished') {
      // Finished jobs are immutable. Keep their media elements mounted across refreshes.
      continue;
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
    restore(studioFields, draft.models[studioFields.firstElementChild.dataset.model]);
    refreshModelUi();
    scheduleSave();
  }
});
