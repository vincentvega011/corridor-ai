// Corridor AI service worker: caches the app shell for offline start, never an API response.
// Money data is always fetched live from /api/bff (no-store); nothing personal is ever cached here.
const SHELL = "corridor-shell-v1";
const SHELL_URLS = ["/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(SHELL_URLS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return; // never cache or intercept the API
  if (SHELL_URLS.includes(url.pathname) || url.pathname.startsWith("/_next/static/")) {
    event.respondWith(caches.match(event.request).then((hit) => hit || fetch(event.request)));
  }
});
