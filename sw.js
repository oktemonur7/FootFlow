const CACHE = "footflow-v70";
const OFFLINE_ASSETS = [
  "./",
  "./index.html",
  "./icons/icon-192.png",
  "./icons/icon-512.png"
];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE).then(async cache => {
      for (const asset of OFFLINE_ASSETS) {
        try {
          const res = await fetch(asset, { cache: "reload" });
          if (res.ok) await cache.put(asset, res);
        } catch (err) {}
      }
    })
  );
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);

  // Cross-origin API isteklerini (Render backend, WebSocket, Sahadan) SW önbelleğinden muaf tut
  if (url.origin !== self.location.origin) return;

  // Sayfa istekleri (index.html / navigation): Network-First, ancak ağ 2.5s içinde yanıt vermezse hemen önbellekten sun
  if (e.request.mode === "navigate" || url.pathname.endsWith("/index.html") || url.pathname.endsWith("/")) {
    e.respondWith(
      new Promise(resolve => {
        let resolved = false;

        fetch(e.request)
          .then(res => {
            if (res.ok) {
              const clone = res.clone();
              caches.open(CACHE).then(c => c.put(e.request, clone));
            }
            if (!resolved) {
              resolved = true;
              resolve(res);
            }
          })
          .catch(async () => {
            if (!resolved) {
              resolved = true;
              const cached = await caches.match(e.request) || await caches.match("./index.html") || await caches.match("./");
              if (cached) resolve(cached);
            }
          });

        // 2.5 saniye zaman aşımı: Ağ gecikirse anında önbelleği aç (30sn beyaz/yükleme ekranını önler)
        setTimeout(async () => {
          if (!resolved) {
            const cached = await caches.match(e.request) || await caches.match("./index.html") || await caches.match("./");
            if (cached) {
              resolved = true;
              resolve(cached);
            }
          }
        }, 2500);
      })
    );
    return;
  }

  // İkonlar ve statik dosyalar: Cache-First
  e.respondWith(
    caches.match(e.request).then(cached => {
      return cached || fetch(e.request).then(res => {
        if (res.ok) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      });
    })
  );
});

// PWA WEB PUSH NOTIFICATION HANDLERS
self.addEventListener("push", e => {
  let data = {
    title: "FootFlow",
    body: "Canlı maç güncellemesi",
    icon: "icons/icon-192.png",
    badge: "icons/icon-192.png",
    url: "./"
  };

  if (e.data) {
    try {
      const parsed = e.data.json();
      data = { ...data, ...parsed };
    } catch (err) {
      data.body = e.data.text();
    }
  }

  const options = {
    body: data.body,
    icon: data.icon || "icons/icon-192.png",
    badge: data.badge || "icons/icon-192.png",
    vibrate: [250, 100, 250, 100, 250],
    data: { url: data.url || "./" },
    tag: data.tag || ("footflow-" + Date.now()),
    renotify: true
  };

  const title = data.title || "FootFlow";

  e.waitUntil(
    self.registration.showNotification(title, options)
  );
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const targetUrl = e.notification.data?.url || "./";
  e.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(windowClients => {
      for (const client of windowClients) {
        if ("focus" in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
