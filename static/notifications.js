(() => {
  const ios = /iPhone|iPad|iPod/i.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const mobile = ios || /Android/i.test(navigator.userAgent) || navigator.userAgentData?.mobile === true;
  const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const profile = document.body.dataset.profile;
  const storageKey = 'aispeech-push-profile';
  const toggle = document.querySelector('#notification-toggle');
  const test = document.querySelector('#notification-test');
  const status = document.querySelector('#notification-status');
  let subscription;
  let registration;
  let serverKey;
  let audio;
  let enabled = false;

  if (!/(?:^|;\s*)speech_device=[a-f0-9]{32}(?:;|$)/.test(document.cookie)) {
    const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
    document.cookie = 'speech_device=' + id + '; Path=/; Max-Age=31536000; SameSite=Lax' +
      (location.protocol === 'https:' ? '; Secure' : '');
  }

  function owner() {
    try { return localStorage.getItem(storageKey); } catch { return null; }
  }

  function remember(value) {
    try {
      if (value) localStorage.setItem(storageKey, profile);
      else if (owner() === profile) localStorage.removeItem(storageKey);
    } catch {}
  }

  function unlockPing() {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (mobile || !Audio) return;
    try {
      audio ||= new Audio();
      audio.resume().catch(() => {});
    } catch {}
  }

  function playPing() {
    if (audio?.state !== 'running') return false;
    const start = audio.currentTime;
    const gain = audio.createGain();
    gain.gain.setValueAtTime(.001, start);
    gain.gain.exponentialRampToValueAtTime(.13, start + .025);
    gain.gain.exponentialRampToValueAtTime(.001, start + .28);
    gain.connect(audio.destination);
    const tone = audio.createOscillator();
    tone.frequency.setValueAtTime(660, start);
    tone.frequency.setValueAtTime(880, start + .105);
    tone.connect(gain);
    tone.onended = () => { tone.disconnect(); gain.disconnect(); };
    tone.start(start);
    tone.stop(start + .29);
    return true;
  }

  const notice = document.querySelector('#speech-page-notice');
  if (notice) {
    const cursorKey = 'aispeech-notice-cursor:' + profile;
    let cursor;
    let timer;
    let activeRequest = false;
    let hideTimer;
    const queue = [];
    try { cursor = sessionStorage.getItem(cursorKey); } catch {}

    function dismiss() {
      notice.hidden = true;
      clearTimeout(hideTimer);
      showNext();
    }

    function showNext() {
      if (!notice.hidden || !queue.length) return;
      const item = queue.shift();
      notice.querySelector('[data-notice-title]').textContent = item.title;
      notice.querySelector('[data-notice-body]').textContent = item.body;
      notice.querySelector('[data-notice-link]').href = item.url;
      notice.hidden = false;
      if (item.ping && !mobile && document.hasFocus() && !owner()) playPing();
      hideTimer = setTimeout(dismiss, 9000);
    }

    async function poll() {
      if (document.hidden || activeRequest) return;
      activeRequest = true;
      let delay = 5000;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 7000);
      try {
        const search = cursor == null ? '' : '?after=' + encodeURIComponent(cursor);
        const response = await fetch('/notifications/feed' + search, {cache: 'no-store', signal: controller.signal});
        if (!response.ok) throw new Error('Alerts are unavailable');
        const result = await response.json();
        cursor = String(result.after);
        try { sessionStorage.setItem(cursorKey, cursor); } catch {}
        queue.push(...result.notices);
        showNext();
      } catch {
        delay = 15000;
      } finally {
        clearTimeout(timeout);
        activeRequest = false;
        if (!document.hidden) timer = setTimeout(poll, delay);
      }
    }

    notice.querySelector('[data-notice-close]').addEventListener('click', dismiss);
    document.addEventListener('visibilitychange', () => {
      clearTimeout(timer);
      if (!document.hidden) poll();
    });
    if (!mobile) {
      document.addEventListener('pointerdown', unlockPing, {once: true});
      document.addEventListener('keydown', unlockPing, {once: true});
    }
    poll();
  }

  if (mobile && !document.querySelector('link[rel=manifest]')) {
    for (const [rel, href] of [['manifest', '/speech.webmanifest'], ['apple-touch-icon', '/static/speech-icon-180.png']]) {
      const link = document.createElement('link');
      link.rel = rel;
      link.href = href;
      if (rel === 'manifest') link.crossOrigin = 'use-credentials';
      document.head.append(link);
    }
    if (ios) {
      const capable = document.createElement('meta');
      capable.name = 'apple-mobile-web-app-capable';
      capable.content = 'yes';
      document.head.append(capable);
    }
  }

  const install = document.querySelector('#mobile-install');
  if (mobile && !standalone && install && isSecureContext) {
    install.hidden = false;
    const installButton = document.querySelector('#mobile-install-button');
    document.querySelector('#mobile-install-help').textContent = ios
      ? 'In Safari, tap Share, then Add to Home Screen. Open Speech Studio from its new icon to enable alerts.'
      : 'Open the browser menu and choose Install app or Add to Home screen.';
    let prompt;
    window.addEventListener('beforeinstallprompt', event => {
      event.preventDefault();
      prompt = event;
      installButton.hidden = false;
    });
    installButton.addEventListener('click', () => {
      prompt?.prompt();
      prompt = undefined;
      installButton.hidden = true;
    });
    window.addEventListener('appinstalled', () => { install.hidden = true; });
  }

  async function request(action, data) {
    const response = await fetch('/notifications/' + action, data ? {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
    } : {cache: 'no-store'});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Sign in and try again.');
    return result;
  }

  function syncSettings(message) {
    toggle.textContent = enabled ? 'Turn off alerts' : 'Enable alerts';
    toggle.disabled = !registration || !serverKey || (!enabled && Notification.permission === 'denied');
    test.hidden = !enabled;
    status.textContent = message || (enabled ? 'Alerts are on for this profile on this device.'
      : 'Alerts are off. Job titles can appear in notifications.');
  }

  if (!isSecureContext || !('serviceWorker' in navigator)) {
    if (status) status.textContent = 'Open the HTTPS Speech Studio address to enable alerts and install the mobile app.';
    return;
  }

  navigator.serviceWorker.addEventListener('message', event => {
    if (event.data?.type !== 'speech-ping') return;
    const mine = event.data.profile === null ? Boolean(owner()) :
      String(event.data.profile) === profile && (enabled || owner() === profile);
    event.ports[0]?.postMessage(mine && playPing());
  });
  const service = mobile || toggle
    ? navigator.serviceWorker.register('/speech-worker.js' + (mobile ? '?mobile=1' : ''))
    : null;
  service?.catch(() => { if (status) status.textContent = 'Speech Studio could not start browser alerts. Refresh and try again.'; });

  if (!toggle) return;
  if (ios && !standalone) {
    status.textContent = 'Install Speech Studio on your Home Screen to enable iPhone and iPad notifications.';
    return;
  }
  if (!('PushManager' in window) || !('Notification' in window)) {
    status.textContent = 'This browser does not support background notifications.';
    return;
  }

  const ready = async () => {
    await service;
    registration = await navigator.serviceWorker.ready;
    const {key} = await request('config');
    const base64 = key.replace(/-/g, '+').replace(/_/g, '/');
    serverKey = Uint8Array.from(atob(base64), character => character.charCodeAt(0));
    subscription = await registration.pushManager.getSubscription();
    enabled = subscription ? (await request('status', {endpoint: subscription.endpoint})).enabled : false;
    if (enabled) {
      await request('subscribe', {...subscription.toJSON(), mobile});
      remember(true);
    }
    syncSettings(Notification.permission === 'denied'
      ? 'Notifications are blocked. Allow them in your browser or device settings, then refresh.' : '');
  };
  ready().catch(() => { status.textContent = 'Alerts are unavailable right now. Refresh and try again.'; });

  toggle.addEventListener('click', async () => {
    if (!registration || !serverKey) return;
    toggle.disabled = true;
    test.disabled = true;
    unlockPing();
    try {
      if (enabled) {
        await request('unsubscribe', {endpoint: subscription.endpoint});
        enabled = false;
        remember(false);
        await subscription.unsubscribe();
        subscription = null;
      } else {
        subscription ||= await registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: serverKey});
        await request('subscribe', {...subscription.toJSON(), mobile});
        enabled = true;
        remember(true);
      }
      syncSettings();
    } catch (error) {
      syncSettings(Notification.permission === 'denied'
        ? 'Notifications are blocked. Allow them in your browser or device settings, then refresh.'
        : (error.message || 'The browser could not enable alerts.'));
    } finally {
      test.disabled = false;
    }
  });

  test.addEventListener('click', async () => {
    if (!enabled || !subscription) return;
    unlockPing();
    test.disabled = true;
    try {
      await request('test', {endpoint: subscription.endpoint});
      status.textContent = 'Test queued. Look for a Speech Studio notification.';
    } catch (error) {
      status.textContent = error.message;
    } finally {
      test.disabled = false;
    }
  });
})();
