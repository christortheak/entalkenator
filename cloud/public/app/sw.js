// Service worker: keeps the app shell available offline so the installed app
// opens without a network. Library data, audio and sign-in pages are never cached.
"use strict";

const CACHE = "entalkenator-shell-v1";
const SHELL = ["app.css", "app.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  const scope = new URL(self.registration.scope);
  if (req.method !== "GET" || url.origin !== scope.origin || !url.pathname.startsWith(scope.pathname)) return;
  const rest = url.pathname.slice(scope.pathname.length);
  if (/^(api|media|login|logout)(\/|$)/.test(rest)) return;

  if (req.mode === "navigate") {
    // Network first; fall back to the last good copy of the app page when offline.
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok && !res.redirected && (rest === "" || rest === "index.html")) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(scope.href, copy));
          }
          return res;
        })
        .catch(() => caches.match(scope.href).then((r) => r || new Response("Offline", { status: 503 }))),
    );
    return;
  }

  // Static files: serve from cache, refresh in the background.
  event.respondWith(
    caches.open(CACHE).then(async (c) => {
      const cached = await c.match(req);
      const fresh = fetch(req)
        .then((res) => {
          if (res.ok && !res.redirected) c.put(req, res.clone());
          return res;
        })
        .catch(() => cached);
      return cached || fresh;
    }),
  );
});
