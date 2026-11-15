# Recipient protocol: QR, enrolment and sealed receipts

This is the reference for the recipient PWA (Milestone 6). The Python reference implementation is `server/aidtrace_server/sealed.py`, and the device used in the tests is `server/tests/conftest.py::Device`.

## 1. Keys

| Key | Algorithm | Held by | Purpose |
|---|---|---|---|
| Organisation signing key | Ed25519 | Server (`AIDTRACE_KEY_DIR`) | Signs QR payloads. Devices verify offline with the public key. |
| Server box key | X25519 | Server | Devices seal receipts to its public key (`crypto_box_seal`) |

Public keys are served at `GET /api/recipient/keys`:

```json
{"signing_keys": {"1": "<hex>"}, "active_signing": 1, "box_public_key": "<hex>", "active_box": 1}
```

**Rotation:** `aidtrace-server rotate-keys` creates key id n+1 and makes it active. Earlier keys keep verifying printed QR codes and decrypting queued receipts until they are listed under `retired` in `keyring.json`. Devices refresh public keys whenever they are online.

## 2. QR payload (v1)

Fields are big-endian, Base45-encoded (RFC 9285), signed with Ed25519.

| Bytes | Field |
|---|---|
| 1 | version = 1 |
| 1 | signing key id |
| 4 | shipment id |
| 4 | scan point id |
| 1 | kind: 0 dispatch, 1 handover, 2 final distribution |
| 2 | expiry: days since 2020-01-01 (UTC); rejected after this date |
| 1 + n | destination admin-2 p-code (length-prefixed ASCII) |
| 1 + Σ | manifest: line count, then per line u8 item code + LEB128 varint quantity |
| 64 | Ed25519 signature over all preceding bytes |

A typical payload is 93–109 bytes, or 140–164 Base45 characters: **QR version 6–7 at ECC level M**, against a target of 10 or lower. If the signature is invalid, the key is unknown, the code has expired or it is malformed, the device shows a neutral "Code not recognised".

## 3. Enrolment (online, during vetting)

1. An HQ admin runs `POST /api/recipients {site_name, adm2_pcode}` and receives an **account** (`R-XXXXXX`) and a **one-time enrolment code**. The code expires after 72 hours and is shown once.
2. On the device, the recipient chooses a normal PIN and a duress PIN (4–8 digits, different from each other). The device seals `{"normal": "...", "duress": "..."}` to the server box key and calls `POST /api/recipient/enrol {account, enrolment_code, sealed_pins}`.
3. The server stores **argon2id hashes only** and returns `{account, device_token, signing_keys, box_public_key, pseah_contact, catalogue, problem_types}`.
4. The device stores exactly that response and then forgets the PINs. **No PIN material exists on the device in any form.**

## 4. Submission

`POST /api/recipient/submit`, body = `crypto_box_seal(padded_plaintext, server_box_public_key)`.

```
padded_plaintext = u32_be(len(json)) || json_utf8 || zero bytes up to the bucket size
buckets          = 2048, 8192, 32768, 163840 bytes (smallest that fits)
```

```json
{"v": 1, "kind": "receipt", "account": "R-7K2M9Q", "device_token": "...", "nonce": "<32 hex>",
 "ts": "2026-11-14T09:30:00Z",
 "receipt": {"qr": "<base45>", "pin": "2468        ",
             "items": [{"code": 2, "qty_received": 10, "damaged": 0, "expired": 0}],
             "cold_chain": {"vvm_stage": 1, "indicator_triggered": false},
             "notes": "", "photo": "<base64 JPEG, EXIF stripped, ~100 KB>"}}
```

- **The `pin` field is padded with spaces to 12 characters**, so PIN length never changes the size. The JSON is serialised with sorted keys and no whitespace. As a result, normal and duress submissions of the same receipt are the **same length** (tested), and fall into the same bucket.
- A problem report uses `"kind": "problem"` and `"problem": {"category": shortage|theft|payment_demand|access_blocked|other, "notes": "<=280 chars", "qr": optional}`. It goes through the same endpoint, padding and sealing, so it is indistinguishable on the wire.
- **The server always answers `202 {"status": "received", "message": "Receipt recorded"}`**: for normal, duress and wrong PINs, and for replays, forgeries and malformed payloads. The device shows "Receipt recorded" and deletes the queued payload after a 202.
- **Retry:** if there is no network or the server returns 5xx, the device retries with exponential backoff (for example 30 s × 2ⁿ, capped at 6 h, with jitter). Queued blobs are already sealed, so the queue holds only ciphertext.

## 5. Server classification

1. Open the sealed box (active key, then older keys). Check the bucket size and parse the JSON.
2. **Replay:** a nonce already seen means the submission is rejected (recorded as a security event).
3. Check the account is active and its device token matches. Check the timestamp is no more than 1 hour in the future and no more than 60 days old.
4. Check the QR signature and expiry (at the device timestamp), that the scan point belongs to the shipment, and that the recipient is assigned to the shipment.
5. Check the PIN against **both** argon2 hashes every time:
   - **normal:** confirmed;
   - **duress:** confirmed for general staff, plus a `DuressAlert` and a notification after the response has been sent (in-app, outbox, optional organisation webhook or email; never local authorities);
   - **neither:** "unverified, PIN mismatch".
6. **Two-person confirmation** (high/severe-risk destinations): a scan point is `verified` only when two *different* recipient accounts have confirmed. One confirmation leaves it `partially_verified`.

## 6. Who sees what

| Field | Field staff | Security focal point, HQ admin |
|---|---|---|
| Scan status (`verified`, `partially_verified`, `unverified_pin_mismatch`, `pending`) | ✓ (duress counts as confirmed) | ✓ |
| `duress`, `pin_class` | **never present** | ✓ (each view is audit-logged) |
| `/api/alerts` | 403 | ✓ (audit-logged) |

## 7. SMS fallback (implemented, Milestone 6)

Use this where data signal is unavailable for long periods but SMS works. The reference implementation is `server/aidtrace_server/sms.py`, and the device implementation is `recipient/src/sms.ts`. The two produce byte-identical codes, checked against shared test vectors.

- **Code:** 24 bytes, Base45-encoded as **36 characters**, all in the GSM 7-bit basic set.

  | Part | Size | Contents |
  |---|---|---|
  | Cleartext header | 6 B | version, recipient SMS id (u16), minutes since 2026-01-01 (u24) |
  | Encrypted body | 13 B | shipment id, scan point id, total quantity, flags (damage, cold chain), and the PIN as 4 bytes of packed BCD padded with `F` to 8 digits |
  | Tag | 5 B | HMAC-SHA256 truncated to 40 bits |

- **Key:** each recipient has a 16-byte SMS key, `HMAC-SHA256(server SMS master, recipient id)`. It is given to the device at enrolment, and the server re-derives it, so no per-device secret is stored server-side.
- **Indistinguishable:** normal and duress codes are the same length and look random without the key. The PIN never appears in clear.
- **Server handling:** the server decodes the code through `POST /api/sms/inbound` (the gateway webhook) and classifies it exactly like a data receipt. Replays are rejected. The reply is always the same.
  - With `AIDTRACE_SMS_GATEWAY_SECRET` set, the gateway must send it in `X-Gateway-Secret`.
  - Without the secret, the route only works in demo mode. That is the mock gateway.
- **Photo:** the device also queues the full sealed receipt, with the photo, which goes over data when signal returns. Receipts from the same recipient and scan point count as one confirmation and raise at most one duress alert.
- **Limits:**
  - The 40-bit tag and the per-minute timestamp suit a receipt confirmation, not high-value authorisation.
  - The quantity is a single total, so the per-item check waits for the data receipt.
  - A compromised SMS master key allows forged codes, but a valid PIN would still be needed for them to count as confirmed.

## 8. Recipient PWA behaviour

- **Offline:** after the first visit, a service worker precaches the whole app (1.2 MB). This was tested in Chrome by stopping the server and reloading, and the app still loaded. API responses are never cached.
- **Storage:** IndexedDB holds the enrolment (public keys, account, device token, SMS key) and a queue of sealed blobs only. There is no history and no PIN, both checked by tests. "Remove set-up from this phone" wipes everything.
- **Sending:** an automatic retry every 30 seconds and on `online` events, with exponential backoff (30 s × 2ⁿ, capped at 6 h, ±25% jitter) for each item. Background Sync is used where supported (Chrome/Android).
- **Camera:** if the camera fails or permission is refused, the app falls back to typed code entry. The code text is printed under the QR on each label.
- **Photo:** re-encoded through a canvas (which drops all EXIF and GPS data) and compressed to about 100 KB.
