const mobile = new URL(self.location.href).searchParams.get('mobile') === '1';
const offlineCache = 'speech-studio-offline-v1';
const fallback = '/static/speech-offline.html';
const navigationPaths = new Set(['/', '/library', '/voices']);

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    if (mobile) {
      const response = await fetch(fallback, {cache: 'reload', redirect: 'error'});
      if (!response.ok) throw new Error('Offline page is unavailable.');
      const cache = await caches.open(offlineCache);
      await cache.put(fallback, response);
    }
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(key => key.startsWith('speech-studio-offline-') && key !== offlineCache)
      .map(key => caches.delete(key)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (!mobile || event.request.mode !== 'navigate' || url.origin !== self.location.origin ||
      !navigationPaths.has(url.pathname) || event.request.method !== 'GET') return;
  event.respondWith((async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(event.request, {signal: controller.signal});
      if (![502, 503, 504].includes(response.status)) return response;
    } catch {
      // The server or network is unreachable.
    } finally {
      clearTimeout(timeout);
    }
    return await caches.match(fallback) || new Response('Can’t connect to Speech Studio. Try again when you are online.',
      {status: 503, headers: {'Content-Type': 'text/plain; charset=utf-8'}});
  })());
});

async function requestPing(profile) {
  const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
  const client = windows.find(page => page.focused) || windows[0];
  if (!client) return false;
  const channel = new MessageChannel();
  return new Promise(resolve => {
    const complete = played => {
      clearTimeout(timeout);
      channel.port1.close();
      resolve(played);
    };
    const timeout = setTimeout(() => complete(false), 250);
    channel.port1.onmessage = event => complete(event.data === true);
    client.postMessage({type: 'speech-ping', profile}, [channel.port2]);
  });
}

function libraryLink(path) {
  const url = new URL(path || '/library', self.location.origin);
  return url.origin === self.location.origin && url.pathname === '/library'
    ? url.href : new URL('/library', self.location.origin).href;
}

self.addEventListener('push', event => {
  event.waitUntil((async () => {
    let data;
    try { data = event.data?.json(); } catch {}
    const silent = !mobile && data?.ping ? await requestPing(data.profile).catch(() => false) : false;
    await self.registration.showNotification(data?.title || 'Speech Studio', {
      body: data?.body || 'Open Speech Studio to check your recordings.',
      icon: '/static/speech-icon-192.png',
      tag: data?.tag || 'speech-studio',
      data: {url: libraryLink(data?.url)},
      ...(silent ? {silent: true} : {}),
    });
  })());
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const url = libraryLink(event.notification.data?.url);
    const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    const existing = windows.find(page => page.url === url);
    if (existing) return existing.focus();
    const studio = windows.find(page => navigationPaths.has(new URL(page.url).pathname));
    if (studio) {
      const navigated = await studio.navigate(url).catch(() => null);
      if (navigated) return navigated.focus();
    }
    return self.clients.openWindow(url);
  })());
});
