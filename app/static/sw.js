self.addEventListener("install", (e) => {
  e.waitUntil(caches.open("christmas-cache-v1").then((cache) => {
    return cache.addAll(["/", "/static/styles.css", "/manifest.webmanifest"]);
  }));
});
self.addEventListener("fetch", (e) => {
  e.respondWith(
    caches.match(e.request).then((resp) => {
      return resp || fetch(e.request);
    })
  );
});
