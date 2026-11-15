/* AidTrace recipient service worker.
 * - Precaches the whole app shell at install (list injected at build), so the app works with the
 *   network fully disabled after the first visit.
 * - Never caches /api/ responses.
 * - Background Sync ("aidtrace-flush"): uploads queued sealed blobs from IndexedDB and deletes each
 *   one after a 202. The blobs are ciphertext; the worker never sees any plaintext.
 */
const VERSION = "__VERSION__";
const PRECACHE = __PRECACHE__;
const CACHE = `aidtrace-recipient-${VERSION}`;
const SCOPE = self.registration.scope;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE.map((p) => new URL(p, SCOPE).toString()))).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k.startsWith("aidtrace-recipient-") && k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const hit = await cache.match(e.request, { ignoreSearch: true });
    if (hit) return hit;
    if (e.request.mode === "navigate") {
      const shell = await cache.match(new URL("index.html", SCOPE).toString());
      if (shell) return shell;
    }
    return fetch(e.request);
  })());
});

function idb() {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open("aidtrace-recipient", 1);
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
    r.onupgradeneeded = () => {
      const db = r.result;
      if (!db.objectStoreNames.contains("config")) db.createObjectStore("config");
      if (!db.objectStoreNames.contains("queue")) db.createObjectStore("queue", { keyPath: "id", autoIncrement: true });
    };
  });
}

async function flushQueue() {
  const db = await idb();
  const items = await new Promise((res, rej) => {
    const q = db.transaction("queue").objectStore("queue").getAll();
    q.onsuccess = () => res(q.result);
    q.onerror = () => rej(q.error);
  });
  let failed = false;
  for (const it of items) {
    try {
      const resp = await fetch("/api/recipient/submit", { method: "POST", body: it.blob, headers: { "Content-Type": "application/octet-stream" } });
      if (resp.status === 202) {
        await new Promise((res) => { const t = db.transaction("queue", "readwrite"); t.objectStore("queue").delete(it.id); t.oncomplete = res; });
      } else failed = true;
    } catch { failed = true; }
  }
  db.close();
  if (failed) throw new Error("retry later");   // the browser reschedules the sync with its own backoff
}

self.addEventListener("sync", (e) => {
  if (e.tag === "aidtrace-flush") e.waitUntil(flushQueue());
});
