// SMS fallback receipt code. Mirrors server/aidtrace_server/sms.py (see that file for the layout).
import { b45encode } from "./crypto";

const EPOCH_2026 = Date.UTC(2026, 0, 1);

async function hmac(key: Uint8Array, ...parts: Uint8Array[]): Promise<Uint8Array> {
  const k = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const len = parts.reduce((a, p) => a + p.length, 0);
  const buf = new Uint8Array(len);
  let o = 0;
  for (const p of parts) { buf.set(p, o); o += p.length; }
  return new Uint8Array(await crypto.subtle.sign("HMAC", k, buf));
}

const u24 = (n: number) => new Uint8Array([(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff]);
const ascii = (s: string) => new TextEncoder().encode(s);

export function packPin(pin: string): Uint8Array {
  if (!/^\d{1,8}$/.test(pin)) throw new Error("PIN must be 1-8 digits");
  const nib = [...pin].map(Number).concat(Array(8 - pin.length).fill(0xf));
  return new Uint8Array([0, 2, 4, 6].map((i) => (nib[i] << 4) | nib[i + 1]));
}

export interface SmsInput {
  smsId: number; shipmentId: number; scanPointId: number; totalQty: number; damaged: boolean; coldChainProblem: boolean;
  pin: string; at?: Date;
}

export async function encodeSms(key: Uint8Array, r: SmsInput): Promise<string> {
  const minutes = Math.floor(((r.at ?? new Date()).getTime() - EPOCH_2026) / 60000);
  const header = new Uint8Array([1, (r.smsId >> 8) & 0xff, r.smsId & 0xff, ...u24(minutes)]);
  const qty = Math.min(r.totalQty, 0xffff);
  const flags = (r.damaged ? 1 : 0) | (r.coldChainProblem ? 2 : 0);
  const body = new Uint8Array([...u24(r.shipmentId), ...u24(r.scanPointId), qty >> 8, qty & 0xff, flags, ...packPin(r.pin)]);
  const ks = (await hmac(key, ascii("ks"), header)).slice(0, body.length);
  const ct = body.map((b, i) => b ^ ks[i]);
  const tag = (await hmac(key, ascii("tag"), header, ct)).slice(0, 5);
  return b45encode(new Uint8Array([...header, ...ct, ...tag]));
}
