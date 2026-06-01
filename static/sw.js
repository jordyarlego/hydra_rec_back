const CACHE = 'hydrarec-runtime-v3'

self.addEventListener('install', e => {
  // Limpa caches antigos no install
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.map(k => caches.delete(k))))
  )
  self.skipWaiting()
})

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  )
  self.clients.claim()
})

// Permite que o client force ativação de SW novo
self.addEventListener('message', e => {
  if (e.data && e.data.type === 'SKIP_WAITING') self.skipWaiting()
})

self.addEventListener('fetch', e => {
  // Network-first: nunca serve assets stale
  e.respondWith(fetch(e.request))
})

self.addEventListener('push', e => {
  const d = e.data?.json() ?? {}
  e.waitUntil(
    self.registration.showNotification(d.title ?? 'HydraRec', {
      body: d.body ?? 'Atualização de risco climático.',
      icon: '/icon.svg',
      badge: '/icon.svg',
      vibrate: [200, 100, 200],
      data: { url: d.url ?? '/' },
    })
  )
})

self.addEventListener('notificationclick', e => {
  e.notification.close()
  e.waitUntil(clients.openWindow(e.notification.data?.url ?? '/'))
})
