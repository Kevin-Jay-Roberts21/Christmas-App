// app/static/sw.js
const CACHE_NAME = "christmas-cache-v4"; // bump version when you change this file

const ASSETS = [
  "/",                         // main page
  "/about",                    // about page
  "/static/styles.css?v=3",    // your CSS
  "/manifest.webmanifest"      // manifest served from root (see main.py route)
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.map((key) => (key === CACHE_NAME ? null : caches.delete(key)))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
