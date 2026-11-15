// Upload queue with exponential backoff. Every item is already a sealed blob; the response is
// identical for every submission, so the device learns nothing from it.
import { allQueued, enqueue, QueueItem, removeQueued, updateQueued } from "./store";

const BASE_MS = 30_000;
const MAX_MS = 6 * 3600_000;
export const SUBMIT_URL = "/api/recipient/submit";

export function backoff(attempts: number, rand = Math.random): number {
  const d = Math.min(MAX_MS, BASE_MS * 2 ** attempts);
  return Math.round(d * (0.75 + 0.5 * rand()));     // +/-25% jitter so devices do not sync up
}

export async function queueSubmission(blob: Uint8Array): Promise<void> {
  await enqueue(blob);
  try {
    // getRegistration() resolves at once (undefined if no worker); `.ready` would hang forever when
    // the service worker could not be registered (private mode, blocked, first load).
    const reg = await navigator.serviceWorker?.getRegistration?.();
    await (reg as any)?.sync?.register("aidtrace-flush");   // Background Sync where supported (Chrome/Android)
  } catch { /* not supported: foreground retry handles it */ }
  void flush();
}

let flushing = false;
export async function flush(now = Date.now(), fetchImpl: typeof fetch = fetch): Promise<{ sent: number; pending: number }> {
  if (flushing) return { sent: 0, pending: (await allQueued()).length };
  flushing = true;
  let sent = 0;
  try {
    for (const item of await allQueued()) {
      if (item.nextAttempt > now) continue;
      let ok = false;
      try {
        const res = await fetchImpl(SUBMIT_URL, { method: "POST", body: item.blob, headers: { "Content-Type": "application/octet-stream" } });
        ok = res.status === 202;
      } catch { ok = false; }
      if (ok) { await removeQueued(item.id!); sent++; }
      else {
        const next: QueueItem = { ...item, attempts: item.attempts + 1, nextAttempt: now + backoff(item.attempts) };
        await updateQueued(next);
      }
    }
  } finally {
    flushing = false;
  }
  return { sent, pending: (await allQueued()).length };
}

export function startAutoFlush(onChange: () => void): () => void {
  const run = () => flush().then(onChange).catch(() => {});
  window.addEventListener("online", run);
  const t = window.setInterval(run, 30_000);
  run();
  return () => { window.removeEventListener("online", run); window.clearInterval(t); };
}
