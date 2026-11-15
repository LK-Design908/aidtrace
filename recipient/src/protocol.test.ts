import "fake-indexeddb/auto";
import { beforeAll, describe, expect, it } from "vitest";
import V from "./test-vectors.json";
import { b45decode, b45encode, buildReceipt, canonicalJSON, ready, seal, sodiumLib, verifyQR } from "./crypto";
import { encodeSms } from "./sms";
import { backoff, flush, queueSubmission } from "./queue";
import { allQueued, DB_NAME, saveEnrolment } from "./store";

const hex = (h: string) => Uint8Array.from(h.match(/../g)!.map((x) => parseInt(x, 16)));

beforeAll(async () => { await ready; });

describe("matches the Python reference vectors", () => {
  it("base45", () => {
    expect(b45encode(hex(V.base45.bytes_hex))).toBe(V.base45.text);
    expect(Array.from(b45decode(V.base45.text))).toEqual(Array.from(hex(V.base45.bytes_hex)));
  });

  it("QR verifies offline and parses identically", () => {
    const p = verifyQR(V.qr.code, V.qr.signing_keys, new Date("2027-02-01T00:00:00Z"))!;
    expect(p).not.toBeNull();
    expect([p.shipmentId, p.scanPointId, p.kind, p.dest]).toEqual([V.qr.shipment_id, V.qr.scan_point_id, V.qr.kind, V.qr.dest]);
    expect(p.manifest).toEqual(V.qr.manifest);
    expect(p.expiry.toISOString().slice(0, 10)).toBe(V.qr.expiry);
  });

  it("QR rejected when expired, tampered or signed by an unknown key", () => {
    expect(verifyQR(V.qr.code, V.qr.signing_keys, new Date("2027-03-02T00:00:00Z"))).toBeNull();
    const c = V.qr.code;
    const t = c.slice(0, 20) + (c[20] === "0" ? "1" : "0") + c.slice(21);
    expect(verifyQR(t, V.qr.signing_keys, new Date("2027-02-01T00:00:00Z"))).toBeNull();
    expect(verifyQR(V.qr.code, { "2": V.qr.signing_keys["1"] }, new Date("2027-02-01T00:00:00Z"))).toBeNull();
    expect(verifyQR("NOT A CODE", V.qr.signing_keys)).toBeNull();
  });

  it("SMS codes are byte-identical to the server encoder and equal length for normal/duress", async () => {
    const i = V.sms.input;
    const base = { smsId: i.sms_id, shipmentId: i.shipment_id, scanPointId: i.scan_point_id, totalQty: i.total_qty,
                   damaged: (i.flags & 1) === 1, coldChainProblem: (i.flags & 2) === 2, at: new Date(i.at) };
    const normal = await encodeSms(hex(V.sms.key_hex), { ...base, pin: "2468" });
    const duress = await encodeSms(hex(V.sms.key_hex), { ...base, pin: "13579864" });
    expect(normal).toBe(V.sms.code);
    expect(duress).toBe(V.sms.code_duress);
    expect(normal.length).toBe(duress.length);
  });

  it("canonical JSON matches Python json.dumps(sort_keys=True, separators=(',', ':'))", () => {
    expect(canonicalJSON({ b: [1, { z: null, a: "é" }], a: true })).toBe('{"a":true,"b":[1,{"a":"é","z":null}]}');
  });
});

describe("duress design on the device", () => {
  const items = [{ code: 3, qty_received: 400, damaged: 0, expired: 0 }];
  const input = (pin: string) => ({ qr: V.qr.code, pin, items, cold_chain: null, notes: "box torn", photoB64: "A".repeat(90000) });

  it("normal and duress sealed payloads are exactly the same size", () => {
    const ts = new Date("2026-11-15T10:00:00Z");
    const sizes = ["2468", "13579864", "0000"].map((pin) => seal(buildReceipt("R-ABCDEF", "tok", input(pin), ts), V.box.public_hex).length);
    expect(new Set(sizes).size).toBe(1);
  });

  it("sealed payload opens on the server key and carries a fixed-width PIN", () => {
    const blob = seal(buildReceipt("R-ABCDEF", "tok", input("2468")), V.box.public_hex);
    const plain = sodiumLib.crypto_box_seal_open(blob, hex(V.box.public_hex), hex(V.box.private_hex));
    expect(V.buckets).toContain(plain.length);
    const n = new DataView(plain.buffer).getUint32(0);
    const d = JSON.parse(new TextDecoder().decode(plain.slice(4, 4 + n)));
    expect(d.receipt.pin).toHaveLength(V.pin_pad);
    expect(d.receipt.pin.trim()).toBe("2468");
  });

  it("the device stores no PIN material and no readable history", async () => {
    await expect(saveEnrolment({ account: "R-X", normal_pin: "1" } as any)).rejects.toThrow();
    await queueSubmission(seal(buildReceipt("R-ABCDEF", "tok", input("13579864")), V.box.public_hex));
    const dump: string[] = [];
    await new Promise<void>((resolve) => {
      const r = indexedDB.open(DB_NAME);
      r.onsuccess = () => {
        const db = r.result;
        const t = db.transaction(["config", "queue"]);
        let left = 2;
        for (const s of ["config", "queue"]) {
          const q = t.objectStore(s).getAll();
          q.onsuccess = () => {
            for (const v of q.result) dump.push(JSON.stringify(v, (_k, x) => (x instanceof Uint8Array ? new TextDecoder("latin1").decode(x) : x)));
            if (--left === 0) { db.close(); resolve(); }
          };
        }
      };
    });
    const all = dump.join("");
    expect(all).not.toContain("13579864");
    expect(all).not.toContain("box torn");       // queued content is ciphertext only
    expect(all).not.toContain("R-ABCDEF");
  });

  it("queue deletes after a 202 and backs off on failure", async () => {
    const before = (await allQueued()).length;
    expect(before).toBeGreaterThan(0);
    const fail = await flush(Date.now(), (async () => { throw new Error("offline"); }) as any);
    expect(fail.pending).toBe(before);
    const q = await allQueued();
    expect(q.every((x) => x.attempts >= 1 && x.nextAttempt > Date.now())).toBe(true);
    const ok = await flush(Date.now() + 10 * 3600_000, (async () => new Response(null, { status: 202 })) as any);
    expect(ok.pending).toBe(0);
  });

  it("backoff grows exponentially and is capped", () => {
    const mid = () => 0.5;
    expect(backoff(0, mid)).toBe(30_000);
    expect(backoff(3, mid)).toBe(240_000);
    expect(backoff(30, mid)).toBe(6 * 3600_000);
  });
});
