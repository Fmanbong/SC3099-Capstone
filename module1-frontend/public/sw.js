/**
 * SAIV service worker.
 *
 * Strategy: cache-first for the static app shell (JS/CSS/manifest/icons),
 * network-first for page navigations (so students always see the latest
 * build when online, falling back to the shell offline).
 *
 * Deliberately NOT cached, ever: any request to the backend API
 * (/api/... on a different origin) or anything under /checkins or /auth.
 * Auth responses and check-in submissions must always be real-time -
 * caching them would let a stale token or a stale "success" be replayed.
 */
const CACHE_NAME = 'saiv-shell-v2'
const APP_SHELL = ['/', '/manifest.json', '/icon.svg']

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  )
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
      )
  )
  self.clients.claim()
})

function isApiRequest(url) {
  return (
    url.pathname.startsWith('/api/') ||
    url.pathname.includes('/auth/') ||
    url.pathname.includes('/checkins') ||
    url.pathname.includes('/face/') ||
    url.pathname.includes('/devices')
  )
}

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url)

  // Never intercept cross-origin (backend) calls or auth/check-in traffic.
  if (url.origin !== self.location.origin || isApiRequest(url)) {
    return
  }

  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match('/').then((res) => res || Response.error()))
    )
    return
  }

  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached
      return fetch(event.request)
        .then((response) => {
          if (response.ok && event.request.method === 'GET') {
            const clone = response.clone()
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone))
          }
          return response
        })
        .catch(() => cached)
    })
  )
})
