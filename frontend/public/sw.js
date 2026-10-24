// Hand-written on purpose, not build-plugin output: this project's Vite
// build produces its hashed JS/CSS through a nitro/Vercel pipeline (see
// vite.config.ts) whose intermediate output directories don't line up
// with where a bundler-emitted service worker expects to land - a real
// build showed the file getting stranded outside .vercel/output/static,
// which 404s in production. A plain static file in public/ sidesteps
// that: it ships the same way favicon.ico and icons/ already do, and
// runtime (not precache-list) caching means it never needs to know the
// hashed asset filenames a given build produced.
const CACHE_NAME = "fluidwatch-shell-v1";
const APP_SHELL = [
  "/",
  "/manifest.webmanifest",
  "/favicon.ico",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  // Live ward telemetry (readings, frame snapshots) must never be served
  // stale from cache - a nurse seeing a cached bed level is worse than a
  // failed request. Let these hit the network untouched.
  if (url.pathname.startsWith("/api/")) return;

  // Stale-while-revalidate: answer instantly from cache when we have it,
  // refresh the cache in the background either way.
  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || network;
    }),
  );
});
