const selectRecordings = document.querySelector('#select-recordings');
const bulk = document.querySelector('#bulk-recordings');
const picks = [...document.querySelectorAll('.recording-pick')];
const selected = () => picks.filter(label => label.querySelector('input').checked).length;

function updateSelection() {
  const count = selected();
  bulk.hidden = count === 0;
  document.querySelector('#selected-count').textContent = count + ' selected';
}

selectRecordings?.addEventListener('click', () => {
  const enabled = selectRecordings.getAttribute('aria-pressed') !== 'true';
  selectRecordings.setAttribute('aria-pressed', enabled);
  selectRecordings.textContent = enabled ? 'Done' : 'Select';
  for (const pick of picks) {
    pick.hidden = !enabled;
    if (!enabled) pick.querySelector('input').checked = false;
  }
  updateSelection();
});

for (const pick of picks) pick.addEventListener('change', updateSelection);
bulk?.addEventListener('submit', event => {
  if (!selected() || (event.submitter?.value === 'delete' &&
      !window.confirm('Permanently delete ' + selected() + ' selected recordings and their audio?'))) {
    event.preventDefault();
  }
});

const dialog = document.querySelector('#share-dialog');
const shareForm = document.querySelector('#share-form');
const rates = document.querySelector('#share-rate');
const shareProgress = document.querySelector('#share-progress');
const shareNotice = document.querySelector('#share-notice');
const shareDownload = document.querySelector('#share-download');
const submitShare = shareForm.querySelector('button');
let currentShare;
let dialogVersion = 0;

const formatSize = bytes => (bytes / 1_000_000).toFixed(bytes < 100_000 ? 3 : 2) + ' MB';

async function shareRequest(path, method) {
  const response = await fetch(path, {method});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'The MP3 copy is unavailable.');
  return data;
}

document.querySelectorAll('[data-share]').forEach(button => button.addEventListener('click', async () => {
  const version = ++dialogVersion;
  currentShare = '/jobs/' + button.dataset.share + '/share';
  document.querySelector('#share-title').textContent = button.dataset.title;
  shareForm.hidden = shareProgress.hidden = shareDownload.hidden = true;
  shareNotice.textContent = 'Checking available sizes…';
  dialog.showModal();
  try {
    const {options} = await shareRequest(currentShare, 'GET');
    if (version !== dialogVersion) return;
    rates.replaceChildren(...options.map(option => new Option(
      option.name + ' · ' + option.rate + ' kbps · ' + formatSize(option.bytes), option.rate)));
    shareForm.hidden = options.length === 0;
    submitShare.disabled = false;
    shareNotice.textContent = options.length ? '' : 'This recording is already smaller than the available MP3 presets.';
  } catch (error) {
    if (version === dialogVersion) shareNotice.textContent = error.message;
  }
}));

dialog.addEventListener('close', () => { dialogVersion++; });
rates.addEventListener('change', () => {
  shareProgress.hidden = shareDownload.hidden = true;
  shareNotice.textContent = '';
});
shareForm.addEventListener('submit', async event => {
  event.preventDefault();
  const version = dialogVersion;
  const url = currentShare + '/' + rates.value;
  submitShare.disabled = rates.disabled = true;
  shareDownload.hidden = true;
  shareNotice.textContent = '';
  showProgress(shareProgress, 'Preparing MP3', 0);
  try {
    let result = await shareRequest(url, 'POST');
    while (version === dialogVersion) {
      if (result.state === 'ready') {
        showProgress(shareProgress, 'MP3 ready', 100);
        shareDownload.href = url + '?download=true';
        shareDownload.textContent = 'Download MP3 · ' + formatSize(result.bytes);
        shareDownload.hidden = false;
        break;
      }
      if (['failed', 'idle'].includes(result.state)) throw new Error('Conversion failed. Try another MP3 size.');
      showProgress(shareProgress, result.state === 'queued' ? 'Waiting for conversion' : 'Converting MP3', result.percent);
      await new Promise(resolve => setTimeout(resolve, 500));
      if (version === dialogVersion) result = await shareRequest(url, 'GET');
    }
  } catch (error) {
    if (version === dialogVersion) {
      shareProgress.hidden = true;
      shareNotice.textContent = error.message;
    }
  } finally {
    if (version === dialogVersion) submitShare.disabled = rates.disabled = false;
  }
});
