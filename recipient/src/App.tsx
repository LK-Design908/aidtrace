import { useEffect, useRef, useState } from "react";
import { buildProblem, buildReceipt, fromB64, QRPayload, seal, sealPins, verifyQR } from "./crypto";
import { compressPhoto } from "./photo";
import { flush, queueSubmission, startAutoFlush } from "./queue";
import { encodeSms } from "./sms";
import { allQueued, clearEnrolment, DB_NAME, Enrolment, loadEnrolment, saveEnrolment } from "./store";

type Screen =
  | { k: "home" } | { k: "scan" } | { k: "receipt"; qr: string; p: QRPayload }
  | { k: "done"; sms?: { code: string; number: string } } | { k: "problem" } | { k: "problem_done" } | { k: "settings" };

export default function App() {
  const [enr, setEnr] = useState<Enrolment | null | undefined>(undefined);
  const [screen, setScreen] = useState<Screen>({ k: "home" });
  const [pending, setPending] = useState(0);
  const [online, setOnline] = useState(navigator.onLine);

  const refreshPending = () => allQueued().then((q) => setPending(q.length)).catch(() => {});
  useEffect(() => { loadEnrolment().then((e) => setEnr(e ?? null)); }, []);
  useEffect(() => {
    if (!enr) return;
    const stop = startAutoFlush(refreshPending);
    const on = () => setOnline(true), off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => { stop(); window.removeEventListener("online", on); window.removeEventListener("offline", off); };
  }, [enr]);

  if (enr === undefined) return <div className="app"><p className="muted">Loading…</p></div>;
  if (enr === null) return <Enrol onDone={setEnr} />;

  return (
    <div className="app">
      {enr.mode === "demo" && <div className="demo-banner">DEMO · synthetic data only</div>}
      <header className="bar">
        <button className="link" onClick={() => setScreen({ k: "home" })} aria-label="Home"><b>Receipt</b></button>
        <span className={`net ${online ? "on" : "off"}`}>{online ? "Online" : "Offline"}</span>
        {pending > 0 && <span className="pill" title="Sealed reports waiting for signal">{pending} waiting to send</span>}
        <span className="grow" />
        <button className="link" onClick={() => setScreen({ k: "settings" })} aria-label="Settings">⚙</button>
      </header>

      {screen.k === "home" && (
        <main className="stack">
          <p className="muted">{enr.site_name} · {enr.account}</p>
          <button className="big primary" onClick={() => setScreen({ k: "scan" })}>Scan consignment</button>
          <button className="big" onClick={() => setScreen({ k: "problem" })}>Report a problem</button>
          {pending > 0 && <button className="btn" onClick={() => flush().then(refreshPending)}>Try sending now</button>}
          <Pseah enr={enr} />
        </main>
      )}
      {screen.k === "scan" && <Scan enr={enr} onCode={(qr, p) => setScreen({ k: "receipt", qr, p })} onCancel={() => setScreen({ k: "home" })} />}
      {screen.k === "receipt" && (
        <ReceiptForm enr={enr} qr={screen.qr} p={screen.p} online={online}
          onDone={(sms) => { refreshPending(); setScreen({ k: "done", sms }); }} onCancel={() => setScreen({ k: "home" })} />
      )}
      {screen.k === "done" && (
        <main className="stack center">
          <div className="tick" aria-hidden>✓</div>
          <h1>Receipt recorded</h1>
          {screen.sms ? (
            <>
              <p>Send this code by SMS to <b>{screen.sms.number}</b>:</p>
              <code className="smscode">{screen.sms.code}</code>
              <a className="btn" href={`sms:${screen.sms.number.replace(/[^\d+]/g, "")}?body=${encodeURIComponent(screen.sms.code)}`}>Open SMS app</a>
              <p className="muted small">The photo will be sent automatically when there is a data signal.</p>
            </>
          ) : <p className="muted">It will be sent automatically when there is a signal.</p>}
          <button className="big primary" onClick={() => setScreen({ k: "home" })}>Done</button>
        </main>
      )}
      {screen.k === "problem" && <Problem enr={enr} onDone={() => { refreshPending(); setScreen({ k: "problem_done" }); }} onCancel={() => setScreen({ k: "home" })} />}
      {screen.k === "problem_done" && (
        <main className="stack center">
          <div className="tick" aria-hidden>✓</div><h1>Report recorded</h1>
          <p className="muted">It will be sent automatically when there is a signal.</p>
          <button className="big primary" onClick={() => setScreen({ k: "home" })}>Done</button>
        </main>
      )}
      {screen.k === "settings" && <Settings enr={enr} onReset={() => setEnr(null)} onBack={() => setScreen({ k: "home" })} />}
    </div>
  );
}

// ---------------------------------------------------------------- enrolment (online, during vetting)

function Enrol({ onDone }: { onDone: (e: Enrolment) => void }) {
  const [account, setAccount] = useState("");
  const [code, setCode] = useState("");
  const [p1, setP1] = useState(""), [p1b, setP1b] = useState("");
  const [p2, setP2] = useState(""), [p2b, setP2b] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"live" | "demo">("live");
  const [demoInfo, setDemoInfo] = useState<string | null>(null);
  useEffect(() => { fetch("/api/health").then((r) => r.json()).then((h) => setMode(h.mode === "demo" ? "demo" : "live")).catch(() => {}); }, []);

  async function demoPhone() {
    setErr(null);
    try {
      const r = await (await fetch("/api/demo/device", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json();
      setAccount(r.account); setCode(r.enrolment_code);
      setP1(r.suggested_pins.normal); setP1b(r.suggested_pins.normal);
      setP2(r.suggested_pins.safety); setP2b(r.suggested_pins.safety);
      setDemoInfo(`Demo account ${r.account} in district ${r.adm2_pcode}. Demo PINs: normal ${r.suggested_pins.normal}, safety ${r.suggested_pins.safety}. Tap “Set up”.`);
    } catch { setErr("Could not create a demo phone."); }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!/^\d{4,8}$/.test(p1) || !/^\d{4,8}$/.test(p2)) return setErr("PINs must be 4 to 8 digits.");
    if (p1 !== p1b || p2 !== p2b) return setErr("The PINs you typed twice do not match.");
    if (p1 === p2) return setErr("The two PINs must be different.");
    setBusy(true);
    try {
      const keys = await (await fetch("/api/recipient/keys")).json();
      const sealed = sealPins(p1, p2, keys.box_public_key);
      setP1(""); setP1b(""); setP2(""); setP2b("");           // PINs leave memory as soon as they are sealed
      const res = await fetch("/api/recipient/enrol", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account: account.trim().toUpperCase(), enrolment_code: code.trim().toUpperCase(), sealed_pins: sealed }),
      });
      if (!res.ok) throw new Error(res.status === 403 ? "Account or enrolment code not accepted." : "Enrolment failed.");
      const e2: Enrolment = { ...(await res.json()), mode };
      await saveEnrolment(e2);
      onDone(e2);
    } catch (e2: any) {
      setErr(e2.message || "Enrolment needs a data connection.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      {mode === "demo" && <div className="demo-banner">DEMO · synthetic data only</div>}
      <form className="stack" onSubmit={submit}>
        <h1>Set up this phone</h1>
        <p className="muted">Do this with your supervisor during vetting. You need a data connection once.</p>
        {mode === "demo" && (
          <div className="card">
            <button type="button" className="big" onClick={demoPhone}>Set up a demo phone</button>
            {demoInfo && <p className="small">{demoInfo}</p>}
          </div>
        )}
        <label className="field">Account<input value={account} onChange={(e) => setAccount(e.target.value)} placeholder="R-XXXXXX" autoCapitalize="characters" required /></label>
        <label className="field">Enrolment code<input value={code} onChange={(e) => setCode(e.target.value)} autoCapitalize="characters" required /></label>
        <div className="card">
          <b>Your normal PIN</b>
          <p className="small muted">Use it to confirm receipts.</p>
          <PinInput label="Normal PIN" value={p1} set={setP1} />
          <PinInput label="Type it again" value={p1b} set={setP1b} />
        </div>
        <div className="card">
          <b>Your safety PIN</b>
          <p className="small">If anyone forces you to confirm a delivery, enter this PIN instead. The phone will look exactly the same and say “Receipt recorded”, but your organisation’s security team will be warned quietly. Never tell anyone which PIN is which.</p>
          <PinInput label="Safety PIN" value={p2} set={setP2} />
          <PinInput label="Type it again" value={p2b} set={setP2b} />
        </div>
        <p className="small muted">Neither PIN is stored on this phone. Remember both.</p>
        {err && <p className="error" role="alert">{err}</p>}
        <button className="big primary" disabled={busy}>{busy ? "Setting up…" : "Set up"}</button>
      </form>
    </div>
  );
}

function PinInput({ label, value, set }: { label: string; value: string; set: (v: string) => void }) {
  return (
    <label className="field">{label}
      <input type="password" inputMode="numeric" pattern="\d*" autoComplete="off" maxLength={8} value={value}
        onChange={(e) => set(e.target.value.replace(/\D/g, ""))} />
    </label>
  );
}

// ---------------------------------------------------------------- scanning

function Scan({ enr, onCode, onCancel }: { enr: Enrolment; onCode: (qr: string, p: QRPayload) => void; onCancel: () => void }) {
  const [manual, setManual] = useState(false);
  const [text, setText] = useState("");
  const [bad, setBad] = useState(false);
  const scannerRef = useRef<any>(null);

  const tryCode = (s: string) => {
    const p = verifyQR(s, enr.signing_keys);
    if (p) { onCode(s.trim(), p); return true; }
    setBad(true);
    return false;
  };

  useEffect(() => {
    if (manual) return;
    let stopped = false;
    let running = false;
    // html5-qrcode's stop() throws synchronously when the camera never started (for example when
    // permission is refused), so every stop goes through this guard.
    const safeStop = () => {
      const sc = scannerRef.current;
      if (!sc || !running) return;
      running = false;
      try { Promise.resolve(sc.stop()).catch(() => {}); } catch { /* not running */ }
    };
    import("html5-qrcode").then(({ Html5Qrcode }) => {
      if (stopped) return;
      const sc = new Html5Qrcode("reader", { verbose: false } as any);
      scannerRef.current = sc;
      sc.start({ facingMode: "environment" }, { fps: 8, qrbox: 240 }, (decoded: string) => {
        if (tryCode(decoded)) safeStop();
      }, () => {})
        .then(() => { running = true; if (stopped) safeStop(); })
        .catch(() => { if (!stopped) setManual(true); });
    }).catch(() => setManual(true));
    return () => { stopped = true; safeStop(); };
  }, [manual]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <main className="stack">
      <h1>Scan consignment</h1>
      {!manual ? <div id="reader" className="reader" /> : (
        <label className="field">Type or paste the code printed under the QR
          <textarea rows={4} value={text} onChange={(e) => { setText(e.target.value.toUpperCase()); setBad(false); }} autoCapitalize="characters" spellCheck={false} />
        </label>
      )}
      {bad && <p className="notice" role="status">Code not recognised.</p>}
      {manual ? <button className="big primary" onClick={() => tryCode(text)} disabled={!text.trim()}>Check code</button>
        : <button className="btn" onClick={() => setManual(true)}>Camera not working? Type the code</button>}
      <button className="btn" onClick={onCancel}>Cancel</button>
    </main>
  );
}

// ---------------------------------------------------------------- receipt

interface Line { code: number; expected: number; got: string; damaged: string; expired: string }

function ReceiptForm({ enr, qr, p, online, onDone, onCancel }: {
  enr: Enrolment; qr: string; p: QRPayload; online: boolean;
  onDone: (sms?: { code: string; number: string }) => void; onCancel: () => void;
}) {
  const label = (c: number) => enr.catalogue.find((x) => x.code === c)?.label ?? `Item ${c}`;
  const cold = p.manifest.some(([c]) => enr.catalogue.find((x) => x.code === c)?.cold_chain);
  const [lines, setLines] = useState<Line[]>(p.manifest.map(([code, q]) => ({ code, expected: q, got: "", damaged: "0", expired: "0" })));
  const [vvm, setVvm] = useState<string>("");
  const [indicator, setIndicator] = useState(false);
  const [notes, setNotes] = useState("");
  const [photo, setPhoto] = useState<{ b64: string; bytes: number } | null>(null);
  const [pin, setPin] = useState("");
  const [viaSms, setViaSms] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const n = (s: string) => Math.max(0, parseInt(s || "0", 10) || 0);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!/^\d{4,8}$/.test(pin)) return setErr("Enter your PIN (4 to 8 digits).");
    if (lines.some((l) => l.got === "")) return setErr("Enter the quantity received for every item (0 if none).");
    setBusy(true);
    setErr(null);
    const pinNow = pin;
    setPin("");                                     // PIN is cleared from the form immediately
    try {
      const items = lines.map((l) => ({ code: l.code, qty_received: n(l.got), damaged: n(l.damaged), expired: n(l.expired) }));
      const cc = cold ? { vvm_stage: vvm ? +vvm : null, indicator_triggered: indicator } : null;
      const blob = seal(buildReceipt(enr.account, enr.device_token, { qr, pin: pinNow, items, cold_chain: cc, notes, photoB64: photo?.b64 ?? null }), enr.box_public_key);
      let sms: { code: string; number: string } | undefined;
      if (viaSms) {
        const code = await encodeSms(fromB64(enr.sms_key), {
          smsId: enr.sms_id, shipmentId: p.shipmentId, scanPointId: p.scanPointId,
          totalQty: items.reduce((a, i) => a + i.qty_received, 0),
          damaged: items.some((i) => i.damaged || i.expired), coldChainProblem: !!cc && (cc.indicator_triggered || (cc.vvm_stage ?? 0) >= 3),
          pin: pinNow,
        });
        sms = { code, number: enr.sms_number };
      }
      await queueSubmission(blob);                  // sealed bytes only; sent when there is signal
      onDone(sms);
    } catch {
      setErr("Could not record the receipt. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <h1>Confirm receipt</h1>
      <div className="card small">
        <div>Consignment <b>#{p.shipmentId}</b> · {p.kind.replace(/_/g, " ")}</div>
        <div className="muted">Destination {p.dest}</div>
      </div>
      {lines.map((l, i) => (
        <fieldset key={l.code} className="card">
          <legend><b>{label(l.code)}</b> <span className="muted">expected {l.expected}</span></legend>
          <div className="row3">
            <label className="field">Received<input inputMode="numeric" value={l.got} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, got: e.target.value.replace(/\D/g, "") } : x))} /></label>
            <label className="field">Damaged<input inputMode="numeric" value={l.damaged} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, damaged: e.target.value.replace(/\D/g, "") } : x))} /></label>
            <label className="field">Expired<input inputMode="numeric" value={l.expired} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, expired: e.target.value.replace(/\D/g, "") } : x))} /></label>
          </div>
        </fieldset>
      ))}
      {cold && (
        <fieldset className="card">
          <legend><b>Cold chain</b></legend>
          <label className="field">Vaccine vial monitor (VVM) stage
            <select value={vvm} onChange={(e) => setVvm(e.target.value)}>
              <option value="">Not checked</option><option value="1">1: use</option><option value="2">2: use first</option>
              <option value="3">3: do not use</option><option value="4">4: do not use</option>
            </select>
          </label>
          <label className="check"><input type="checkbox" checked={indicator} onChange={(e) => setIndicator(e.target.checked)} /> Temperature indicator triggered</label>
        </fieldset>
      )}
      <label className="field">Notes (optional)<textarea rows={2} maxLength={500} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
      <label className="field">Photo of the consignment
        <input type="file" accept="image/*" capture="environment" onChange={async (e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) { try { setPhoto(await compressPhoto(f)); } catch { setErr("Could not read the photo."); } }
        }} />
      </label>
      {photo && <p className="small muted">Photo ready ({Math.round(photo.bytes / 1024)} KB, location data removed).</p>}
      <label className="check"><input type="checkbox" checked={viaSms} onChange={(e) => setViaSms(e.target.checked)} />
        No data signal for a long time? Also give me an SMS code{online ? "" : " (recommended now)"}</label>
      <PinInput label="PIN" value={pin} set={setPin} />
      {err && <p className="error" role="alert">{err}</p>}
      <button className="big primary" disabled={busy}>{busy ? "Recording…" : "Confirm receipt"}</button>
      <button type="button" className="btn" onClick={onCancel}>Cancel</button>
    </form>
  );
}

// ---------------------------------------------------------------- problem report

function Problem({ enr, onDone, onCancel }: { enr: Enrolment; onDone: () => void; onCancel: () => void }) {
  const [cat, setCat] = useState(enr.problem_types[0]);
  const [notes, setNotes] = useState("");
  const labels: Record<string, string> = { shortage: "Shortage", theft: "Theft", payment_demand: "Someone demanded payment",
    access_blocked: "Access blocked", other: "Other supply problem" };
  return (
    <form className="stack" onSubmit={async (e) => {
      e.preventDefault();
      await queueSubmission(seal(buildProblem(enr.account, enr.device_token, cat, notes, null), enr.box_public_key));
      onDone();
    }}>
      <h1>Report a supply problem</h1>
      <label className="field">What happened?
        <select value={cat} onChange={(e) => setCat(e.target.value)}>
          {enr.problem_types.map((t) => <option key={t} value={t}>{labels[t] ?? t}</option>)}
        </select>
      </label>
      <label className="field">Short details (optional, 280 characters)
        <textarea rows={3} maxLength={280} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </label>
      <Pseah enr={enr} strong />
      <button className="big primary">Send report</button>
      <button type="button" className="btn" onClick={onCancel}>Cancel</button>
    </form>
  );
}

function Pseah({ enr, strong }: { enr: Enrolment; strong?: boolean }) {
  if (!enr.pseah_contact) return null;
  return (
    <div className={`card small ${strong ? "pseah" : ""}`}>
      <b>Sexual exploitation or abuse</b>
      <p>Do not report it in this app. Contact the confidential PSEAH focal point, who is trained to help:</p>
      <p><b>{enr.pseah_contact.name}</b><br /><a href={`tel:${enr.pseah_contact.phone.replace(/[^\d+]/g, "")}`}>{enr.pseah_contact.phone}</a></p>
      {enr.pseah_contact.note && <p className="muted">{enr.pseah_contact.note}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- settings

function Settings({ enr, onReset, onBack }: { enr: Enrolment; onReset: () => void; onBack: () => void }) {
  return (
    <main className="stack">
      <h1>This phone</h1>
      <p>{enr.site_name}<br /><span className="muted">{enr.account}</span></p>
      <p className="small muted">This phone keeps no list of past receipts. Waiting reports are encrypted and can only be read by your organisation.</p>
      <button className="btn danger" onClick={async () => {
        if (!window.confirm("Remove this phone's set-up? Any reports still waiting to send will be lost.")) return;
        await clearEnrolment();
        indexedDB.deleteDatabase(DB_NAME);
        const regs = await navigator.serviceWorker?.getRegistrations?.();
        regs?.forEach((r) => r.unregister());
        onReset();
      }}>Remove set-up from this phone</button>
      <button className="btn" onClick={onBack}>Back</button>
    </main>
  );
}
