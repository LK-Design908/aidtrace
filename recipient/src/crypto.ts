// Device-side crypto. Mirrors server/aidtrace_server/{qr,sealed}.py byte for byte (see docs/PROTOCOL.md).
import sodium from "libsodium-wrappers";

export const ready = sodium.ready;

// ---------------------------------------------------------------- Base45 (RFC 9285)
const B45 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:";

export function b45encode(data: Uint8Array): string {
  let out = "";
  for (let i = 0; i + 1 < data.length; i += 2) {
    let n = data[i] * 256 + data[i + 1];
    const c = Math.floor(n / 2025);
    n %= 2025;
    out += B45[n % 45] + B45[Math.floor(n / 45)] + B45[c];
  }
  if (data.length % 2) {
    const n = data[data.length - 1];
    out += B45[n % 45] + B45[Math.floor(n / 45)];
  }
  return out;
}

export function b45decode(s: string): Uint8Array {
  const v = Array.from(s).map((ch) => {
    const i = B45.indexOf(ch);
    if (i < 0) throw new Error("invalid base45");
    return i;
  });
  const out: number[] = [];
  for (let i = 0; i < v.length; i += 3) {
    const c = v.slice(i, i + 3);
    if (c.length === 3) {
      const n = c[0] + c[1] * 45 + c[2] * 2025;
      if (n > 0xffff) throw new Error("invalid base45");
      out.push(n >> 8, n & 0xff);
    } else if (c.length === 2) {
      const n = c[0] + c[1] * 45;
      if (n > 0xff) throw new Error("invalid base45");
      out.push(n);
    } else throw new Error("invalid base45 length");
  }
  return new Uint8Array(out);
}

// ---------------------------------------------------------------- QR payload
export const KINDS = ["dispatch", "handover", "final_distribution"] as const;
const EPOCH_2020 = Date.UTC(2020, 0, 1);

export interface QRPayload {
  keyId: number; shipmentId: number; scanPointId: number; kind: string; expiry: Date; dest: string;
  manifest: [number, number][];
}

export function parseQR(s: string): { payload: QRPayload; body: Uint8Array; sig: Uint8Array } {
  const raw = b45decode(s.trim());
  if (raw.length < 13 + 64 + 2) throw new Error("too short");
  const body = raw.slice(0, raw.length - 64);
  const sig = raw.slice(raw.length - 64);
  const dv = new DataView(body.buffer, body.byteOffset, body.byteLength);
  if (dv.getUint8(0) !== 1) throw new Error("unsupported version");
  const keyId = dv.getUint8(1);
  const shipmentId = dv.getUint32(2);
  const scanPointId = dv.getUint32(6);
  const kind = KINDS[dv.getUint8(10)];
  if (!kind) throw new Error("bad kind");
  const days = dv.getUint16(11);
  let i = 13;
  const n = body[i];
  const dest = String.fromCharCode(...body.slice(i + 1, i + 1 + n));
  i += 1 + n;
  const lines = body[i++];
  const manifest: [number, number][] = [];
  for (let l = 0; l < lines; l++) {
    const code = body[i++];
    let q = 0, shift = 0, b: number;
    do {
      b = body[i++];
      if (b === undefined) throw new Error("truncated");
      q += (b & 0x7f) * 2 ** shift;
      shift += 7;
    } while (b & 0x80);
    manifest.push([code, q]);
  }
  if (i !== body.length) throw new Error("trailing bytes");
  return { payload: { keyId, shipmentId, scanPointId, kind, expiry: new Date(EPOCH_2020 + days * 864e5), dest, manifest }, body, sig };
}

/** Offline verification: valid signature by a known organisation key and not expired. Else null. */
export function verifyQR(s: string, signingKeys: Record<string, string>, now = new Date()): QRPayload | null {
  try {
    const { payload, body, sig } = parseQR(s);
    const pk = signingKeys[String(payload.keyId)];
    if (!pk) return null;
    if (!sodium.crypto_sign_verify_detached(sig, body, sodium.from_hex(pk))) return null;
    const expDay = payload.expiry.toISOString().slice(0, 10);
    if (now.toISOString().slice(0, 10) > expDay) return null;
    return payload;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- sealed payloads
export const PIN_PAD = 12;
export const BUCKETS = [2048, 8192, 32768, 163840];

/** JSON with sorted keys and no whitespace, matching Python json.dumps(sort_keys=True, separators=(",", ":")). */
export function canonicalJSON(v: unknown): string {
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return `[${v.map(canonicalJSON).join(",")}]`;
  const o = v as Record<string, unknown>;
  return `{${Object.keys(o).sort().filter((k) => o[k] !== undefined).map((k) => `${JSON.stringify(k)}:${canonicalJSON(o[k])}`).join(",")}}`;
}

export function padPin(pin: string): string {
  if (!/^\d{1,8}$/.test(pin)) throw new Error("PIN must be digits");
  return pin + " ".repeat(PIN_PAD - pin.length);
}

export function pad(plain: Uint8Array): Uint8Array {
  const need = plain.length + 4;
  const b = BUCKETS.find((x) => need <= x);
  if (!b) throw new Error("payload too large");
  const out = new Uint8Array(b);
  new DataView(out.buffer).setUint32(0, plain.length);
  out.set(plain, 4);
  return out;
}

export function nowTs(d = new Date()): string {
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function nonce(): string {
  return sodium.to_hex(sodium.randombytes_buf(16));
}

export interface ReceiptInput {
  qr: string; pin: string;
  items: { code: number; qty_received: number; damaged: number; expired: number }[];
  cold_chain: { vvm_stage: number | null; indicator_triggered: boolean } | null;
  notes: string; photoB64: string | null;
}

export function buildReceipt(account: string, deviceToken: string, r: ReceiptInput, ts = new Date()) {
  return {
    v: 1, kind: "receipt", account, device_token: deviceToken, nonce: nonce(), ts: nowTs(ts),
    receipt: { qr: r.qr, pin: padPin(r.pin), items: r.items, cold_chain: r.cold_chain, notes: r.notes.slice(0, 500), photo: r.photoB64 },
  };
}

export function buildProblem(account: string, deviceToken: string, category: string, notes: string, qr: string | null, ts = new Date()) {
  return { v: 1, kind: "problem", account, device_token: deviceToken, nonce: nonce(), ts: nowTs(ts),
           problem: { category, notes: notes.slice(0, 280), qr } };
}

/** Seal to the server box key. The returned bytes are all that is ever stored or sent. */
export function seal(payload: object, serverBoxPublicHex: string): Uint8Array {
  const plain = new TextEncoder().encode(canonicalJSON(payload));
  return sodium.crypto_box_seal(pad(plain), sodium.from_hex(serverBoxPublicHex));
}

export function sealPins(normal: string, duress: string, serverBoxPublicHex: string): string {
  const c = sodium.crypto_box_seal(new TextEncoder().encode(JSON.stringify({ normal, duress })), sodium.from_hex(serverBoxPublicHex));
  return sodium.to_base64(c, sodium.base64_variants.ORIGINAL);
}

export const toB64 = (b: Uint8Array) => sodium.to_base64(b, sodium.base64_variants.ORIGINAL);
export const fromB64 = (s: string) => sodium.from_base64(s, sodium.base64_variants.ORIGINAL);
export const sodiumLib = sodium;
