// IndexedDB storage. Two stores only:
//   config: the enrolment response (account, device token, public keys, catalogue, contacts, SMS key).
//           Never any PIN, in any form.
//   queue:  sealed ciphertext blobs awaiting upload, plus retry bookkeeping. Deleted after a 202.
// There is no history of past reports on the device.

export const DB_NAME = "aidtrace-recipient";
const VERSION = 1;

export interface Enrolment {
  account: string; device_token: string; site_name: string;
  signing_keys: Record<string, string>; active_signing: number; box_public_key: string; active_box: number;
  sms_id: number; sms_key: string; sms_number: string;
  pseah_contact: { name: string; phone: string; note?: string } | null;
  problem_types: string[]; catalogue: { code: number; label: string; cold_chain: boolean }[];
  mode?: "live" | "demo";
}

export interface QueueItem { id?: number; blob: Uint8Array; attempts: number; nextAttempt: number }

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("config")) db.createObjectStore("config");
      if (!db.objectStoreNames.contains("queue")) db.createObjectStore("queue", { keyPath: "id", autoIncrement: true });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx<T>(store: string, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const r = fn(t.objectStore(store));
    t.oncomplete = () => { resolve(r.result); db.close(); };
    t.onerror = () => { reject(t.error); db.close(); };
  });
}

const PIN_LIKE = /pin/i;

export async function saveEnrolment(e: Enrolment): Promise<void> {
  // Defence in depth: refuse to persist anything that looks like PIN material.
  if (Object.keys(e).some((k) => PIN_LIKE.test(k))) throw new Error("refusing to store PIN-like fields");
  await tx("config", "readwrite", (s) => s.put(e, "enrolment"));
}
export const loadEnrolment = () => tx<Enrolment | undefined>("config", "readonly", (s) => s.get("enrolment"));
export const clearEnrolment = () => tx("config", "readwrite", (s) => s.delete("enrolment"));

export const enqueue = (blob: Uint8Array) =>
  tx("queue", "readwrite", (s) => s.add({ blob, attempts: 0, nextAttempt: 0 } as QueueItem));
export const allQueued = () => tx<QueueItem[]>("queue", "readonly", (s) => s.getAll());
export const removeQueued = (id: number) => tx("queue", "readwrite", (s) => s.delete(id));
export const updateQueued = (item: QueueItem) => tx("queue", "readwrite", (s) => s.put(item));
