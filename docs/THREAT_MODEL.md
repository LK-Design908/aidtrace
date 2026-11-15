# Threat model (beta)

Scope: the AidTrace server, the distributor web app, the recipient PWA, the SMS fallback, and the pipeline outputs they serve. Status as of Milestone 7 (2026-11-15). Each threat lists the mitigations **implemented** (with the test that checks them where one exists) and the **residual risk**.

## Assets and adversaries

| Asset | Why it matters |
|---|---|
| Recipient safety, and whether a duress PIN was used | Revealing it endangers the recipient |
| Recipient identities, sites, PINs | Targeting, coercion |
| Field reports (text, attachments) | Can name places and actors; informant safety |
| District risk map and need index | Dual-use: could guide attacks on deliveries |
| Shipment details (contents, timing, routes) | Theft and diversion planning |
| Signing, box and SMS keys | Forged QR codes, decrypted receipts, forged SMS |
| ACLED-derived data | Licence obligations |

Adversaries: armed groups or gatekeepers at handover points; local officials who may be the threat; thieves; a curious or malicious insider; an external attacker with a stolen staff password; an attacker who obtains a recipient's phone.

---

## 1. Seized recipient phone

**Threat.** The phone is taken, or inspected forensically, to learn whether the recipient reported coercion, to find past deliveries, or to obtain PINs.

**Mitigations.**
- **No PIN material on the device in any form.** PINs are sealed to the server at enrolment and stored only as argon2id hashes on the server. `saveEnrolment` refuses PIN-like fields. *Tests:* `test_payload_sizes_identical_and_no_pin_material` (server), `the device stores no PIN material and no readable history` (PWA).
- **No history.** IndexedDB holds only the enrolment (public keys, account, device token, SMS key) and a queue of sealed ciphertext blobs, each deleted after upload. Queued content (quantities, notes, account) is unreadable on the device; this is tested.
- **The device never checks the PIN** and shows the same "Receipt recorded" for every PIN. Nothing on screen or in storage depends on which PIN was entered.
- **"Remove set-up from this phone"** wipes IndexedDB and the service worker.
- **Photos** are re-encoded through a canvas, dropping EXIF and GPS data, and stripped again on the server. *Test:* `test_photo_exif_stripped_server_side`.

**Residual risk.**
- The phone shows *that* it is enrolled, and how many reports are waiting to send, though not their content or which PIN was used.
- The device token and SMS key on the phone would let an attacker submit receipts as that account, but those still need a valid PIN to count as confirmed. The org should **deactivate the account** (`Recipient.active=false`) when a phone is reported lost.
- The PIN sits briefly in JavaScript memory until it is sealed. JavaScript cannot zero strings, so a live memory dump at that moment could expose it. Nothing is ever written to storage. *Accepted.*

## 2. Coerced recipient

**Threat.** Someone forces the recipient to confirm a delivery that did not arrive intact, possibly watching the screen and listening.

**Mitigations.**
- **Duress PIN** produces exactly the same device behaviour and the same server response (`202 {"status":"received","message":"Receipt recorded"}`) as the normal PIN. *Tests:* `test_end_to_end_normal_duress_mismatch`, `test_demo_end_to_end`.
- **Same size:** normal and duress payloads are byte-for-byte the same length (fixed-width PIN, padding buckets), and so are SMS codes. *Tests:* payload-size tests in both suites; `test_sms_codes_equal_length_and_roundtrip`.
- **Same timing:** every submission is padded to a fixed minimum duration (`AIDTRACE_SUBMIT_MIN_MS`, default 800 ms), including rejects. Both argon2 verifications always run. Notifications are sent after the response. *Test:* `test_submission_timing_is_padded`.
- **Field staff see a duress delivery as normally verified.** Only security focal points and HQ admins see the flag or alerts, and each view is audit-logged.
- **Alerts go to the organisation's security focal point**: in-app, an outbox file, and an optional org-owned webhook or email. They never go to local authorities.
- **No spoken secret.**
- **Two-person confirmation** in high-risk districts means one coerced person cannot fully verify a delivery.

**Residual risk.**
- A coercer who knows the system exists may demand both PINs, or demand to see a staff member's distributor screen. Training and protocol matter more than software here.
- A well-resourced observer correlating network timing from many submissions could still find small differences; padding reduces this but cannot remove it.
- If the security focal point's response is visible (for example an immediate visit), the coercer may infer a duress report. The response protocol must be discreet.

## 3. Compromised distributor account

**Threat.** A staff password is phished or guessed.

**Mitigations.**
- argon2id password hashes.
- Lockout after 5 failures (15 min) and per-IP login rate limiting. *Test:* `test_login_lockout_and_rbac`.
- Sessions time out after 30 minutes idle and 12 hours absolute. *Test:* `test_session_idle_timeout`.
- The bearer token is held in memory only, not localStorage.
- Role-based access:
  - field staff cannot see duress status, alerts or other people's report text, or export Health Cluster data;
  - recipients have no distributor access at all.
- An audit log records logins, lockouts, user creation, duress-alert views, report-text views, label printing and exports.
- HSTS and no-store caching on API responses.

**Residual risk.**
- **No second factor in the beta.** Strongly recommended before live use for security focal points and HQ admins (TOTP or WebAuthn).
- A compromised security-focal-point account reveals duress alerts.
- Rate limiting is in-memory (single process). Use a gateway or Redis in production.

## 4. Insider at HQ

**Threat.** A legitimate HQ user abuses access to identify who used duress, leak field reports, or tamper with records.

**Mitigations.**
- **HQ never sees recipient PINs.** Recipients choose them on their own device.
- Duress visibility is limited to two roles and **every view is audit-logged** (`view_duress_alerts`, `view_shipment_with_duress_status`, `view_field_report_detail`).
- Health Cluster export contains no report text, duress information, recipient identities or ACLED content. *Test:* `test_health_cluster_export_is_clean`.
- Data retention: photos and free text are purged after 180 days. *Test:* `test_retention_purges_content_keeps_status`.
- The independent verification queue is drawn by CSPRNG, so an insider cannot steer which deliveries get checked.

**Residual risk.**
- The audit log is in the same database an admin with server access could edit. Ship audit events to append-only storage in production.
- Anyone with database access can read argon2 PIN hashes. With 4–8 digit PINs, offline brute force is feasible despite argon2. **Protect the database and its backups as secrets.**
- Key files on disk are readable by the server's OS user. Production should use a KMS or HSM, or at least restricted file permissions and disk encryption.

## 5. Leaked risk map

**Threat.** The district risk and need layers, or field-report locations, leak and are used to target deliveries or civilians.

**Mitigations.**
- **Admin-2 (district) resolution only.** There is no sub-district prediction and no armed-group location output. Field reports and deliveries are drawn at district centroids.
- The decoy-shipment feature is deferred (DECISIONS.md).
- Risk scores model violence in general, not delivery routes. They come with dates and are labelled as estimates.
- The map and all risk endpoints require authentication. The demo uses synthetic shipments, recipients and reports, and no ACLED.
- ACLED stays off (`acled_display=none`) and is forced off in demo. *Test:* `test_demo_seed_is_synthetic_and_isolated`.

**Residual risk.**
- District risk and need maps are sensitive in aggregate, and the open-data inputs are public. Treat exports and screenshots as **restricted**.
- Low-profile logistics flags on shipments reveal intent if leaked.

## 6. Forged QR

**Threat.** A fake label is used to make a diverted consignment look delivered, or to collect receipts for goods that never existed.

**Mitigations.**
- Ed25519-signed payloads, verified offline on the device and again on the server.
- Expiry checks, key ids and key rotation.
- The server checks that the scan point belongs to the shipment and that the submitting recipient is assigned to it. *Tests:* `test_qr_sign_verify_size_tamper_expiry`, `test_forged_qr_and_unassigned_recipient_rejected`, JS QR rejection test.

**Residual risk.**
- A *genuine* label can be photographed and re-printed: QR codes prove the label came from the org, not that the goods are present. The photo, the quantity reconciliation, two-person confirmation and independent verification address this.
- A stolen signing key allows forgery until it is rotated or retired.

## 7. Replayed submissions

**Threat.** A captured sealed receipt or SMS is re-sent, to inflate confirmations or to probe the server.

**Mitigations.**
- Every sealed payload carries a 128-bit nonce, and every SMS code is unique by its full contents. Both are stored and rejected on reuse.
- Device timestamps must be no more than 1 hour in the future and no more than 60 days old.
- Replays get the same response as anything else, so they give no oracle.
- *Tests:* `test_replay_rejected`, `test_sms_gateway_demo_mode` (replay part).

**Residual risk.** Nonces older than 67 days are pruned. Older replays are already rejected by the timestamp window.

---

## Other threats considered

| Threat | Mitigation | Residual |
|---|---|---|
| SMS interception or spoofing | Codes are encrypted and authenticated per recipient (40-bit tag). The PIN is encrypted. The gateway webhook requires a shared secret outside demo. | A 40-bit tag allows online guessing in principle; rate limiting applies. SMS metadata (sender number) is visible to the carrier. |
| Demo exposing real data | Separate process, database, keys and data directory. The demo refuses the live directory. `/api/demo/*` only exists in demo mode. The seed refuses live mode. | Operator error (pointing the demo at live paths) is blocked by name checks only. |
| Malicious file upload | Attachments are size-limited, JPEG metadata is stripped, and files are stored outside the web root. | No antivirus scan in the beta. |
| XSS stealing tokens | React escaping, token held in memory, no third-party scripts except MapLibre and deck.gl bundled at build time. Tiles come from OpenFreeMap. | No Content-Security-Policy header yet. Add one before live use. |
| ACLED licence breach | Restricted outputs live in a gitignored folder. The display gate is off, and forced off in demo. ACLED is never in exports or scores. | The written permission is still missing (DECISIONS.md). |

## Before live deployment (must-do)

1. TLS everywhere, and second-factor authentication for security focal points and HQ admins.
2. Key storage in a KMS or HSM, and database encryption at rest with protected backups.
3. Append-only audit log and a Content-Security-Policy header.
4. Production rate limiting (gateway or Redis), a real SMS gateway with a secret, and a configured webhook or email for duress alerts.
5. A written duress-response protocol and training for security focal points, plus PSEAH contact details configured per country.
6. Field test on low-end Android over slow 3G. Decide on Capacitor if iOS devices are used.
