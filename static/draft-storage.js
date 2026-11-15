// IndexedDB holds full books without the roughly 5 MB sessionStorage ceiling.
// A tab namespace keeps profiles/drafts isolated; manual reload clears this tab.
window.openSpeechDrafts = async function(namespace, reset) {
  const legacy = key => {
    try { return JSON.parse(sessionStorage.getItem(key)); } catch { return null; }
  };
  let database;
  try {
    database = await new Promise((resolve, reject) => {
      const request = indexedDB.open('aispeech-drafts', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('drafts');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error('Draft storage blocked'));
    });
    await new Promise((resolve, reject) => {
      const transaction = database.transaction('drafts', 'readwrite');
      const request = transaction.objectStore('drafts').openCursor();
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) return;
        if ((reset && String(cursor.key).startsWith(namespace + ':')) || Date.now() - cursor.value.saved > 7 * 86400000) cursor.delete();
        cursor.continue();
      };
      transaction.oncomplete = resolve;
      transaction.onabort = () => reject(transaction.error);
    });
  } catch { database = null; }
  return {
    async get(key) {
      if (!database) {
        return legacy(key);
      }
      return new Promise(resolve => {
        const request = database.transaction('drafts').objectStore('drafts').get(namespace + ':' + key);
        request.onsuccess = () => resolve(request.result?.value ?? legacy(key));
        request.onerror = () => resolve(null);
      });
    },
    set(key, value) {
      const report = () => {
        const status = document.querySelector('#import-status');
        if (status) {
          status.hidden = false;
          status.className = 'inline-message notice-error';
          status.textContent = 'Browser draft storage is unavailable or full. Keep this tab open until you finish, or save a copy of your script.';
        }
      };
      if (!database) {
        try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { report(); }
        return Promise.resolve();
      }
      return new Promise(resolve => {
        try {
          const transaction = database.transaction('drafts', 'readwrite');
          transaction.objectStore('drafts').put({saved: Date.now(), value}, namespace + ':' + key);
          transaction.oncomplete = resolve;
          transaction.onabort = () => { report(); resolve(); };
        } catch { report(); resolve(); }
      });
    },
  };
};
