/**
 * Service worker: makes the app installable and lets its SHELL (HTML,
 * JS, CSS, fonts) load even with no connection — NOT a replacement for
 * this app's existing offline-data logic (IndexedDB + the sync engine),
 * which already handles actual delivery data completely separately.
 * This only concerns itself with the static files the app is built from.
 *
 * Deliberately uses a RUNTIME caching strategy (cache whatever gets
 * fetched, as it's fetched) rather than a hardcoded list of files to
 * pre-cache on install. A hand-written hardcoded list would go stale the
 * moment Vite's production build renames its output files (they include
 * a content hash, e.g. index-a1b2c3.js) — runtime caching adapts
 * automatically instead of needing to be kept in sync with the build.
 *
 * Explicitly does NOT cache API requests (anything to the FastAPI
 * backend) — that data has its own, more correct offline story already
 * (IndexedDB + conflict-resolved sync), and letting a service worker
 * cache-and-serve stale API responses on top of that would just
 * introduce a second, conflicting source of "offline truth."
 */

const CACHE_NAME = "delivery-sync-shell-v1";
const API_ORIGIN = "http://127.0.0.1:8000";

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Never intercept API calls — always go straight to the network,
  // exactly as if this service worker didn't exist for these requests.
  if (url.origin === API_ORIGIN) {
    return;
  }

  // Only handle GET requests for same-origin app-shell files
  if (event.request.method !== "GET" || url.origin !== self.location.origin) {
    return;
  }

  if (event.request.mode === "navigate") {
    // HTML page loads: try the network first (so you always get the
    // latest version while online), falling back to the cached shell
    // if there's no connection.
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return response;
        })
        .catch(() => caches.match(event.request).then((cached) => cached || caches.match("/")))
    );
    return;
  }

  // Static assets (JS, CSS, fonts, images): cache-first, since content-hashed
  // filenames mean a cached copy is always still valid.
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((response) => {
        const clone = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        return response;
      });
    })
  );
});
