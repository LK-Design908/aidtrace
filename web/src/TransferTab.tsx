import { useEffect, useMemo, useState } from "react";
import { Adm2, api, AppConfig, download, Me, RecipientRow, ShipmentView, UnitSummary } from "./api";
import { DELIVERY_STATUS, RISK_BANDS } from "./scales";

type Sub = "shipments" | "new" | "recipients" | "verification" | "reconciliation";

export default function TransferTab({ me, cfg }: { me: Me; cfg: AppConfig }) {
  const [sub, setSub] = useState<Sub>("shipments");
  const [shipments, setShipments] = useState<ShipmentView[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const reload = () => api.get<ShipmentView[]>("/shipments").then(setShipments).catch(() => {});
  useEffect(() => { reload(); }, []);
  const canCreate = me.role !== "security_focal_point";

  return (
    <div className="page">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="seg" role="tablist" aria-label="Resource transfer sections">
          {([["shipments", "Shipments"], ...(canCreate ? [["new", "New shipment"]] : []), ["recipients", "Recipients"],
             ["verification", "Verification queue"], ["reconciliation", "Consumption check"]] as [Sub, string][])
            .map(([id, label]) => <button key={id} aria-pressed={sub === id} onClick={() => setSub(id)}>{label}</button>)}
        </div>
      </div>
      {sub === "shipments" && <ShipmentList rows={shipments} onOpen={setOpenId} />}
      {sub === "new" && <NewShipment cfg={cfg} onCreated={(s) => { reload(); setSub("shipments"); setOpenId(s.id); }} />}
      {sub === "recipients" && <Recipients me={me} />}
      {sub === "verification" && <Verification />}
      {sub === "reconciliation" && <Reconciliation />}
      {openId !== null && <ShipmentDetail id={openId} me={me} onClose={() => { setOpenId(null); reload(); }} />}
    </div>
  );
}

function statusOf(s: ShipmentView) {
  const st = s.status === "delivered" ? "delivered" : s.scan_points.some((p) => p.status === "unverified_pin_mismatch") ? "unverified_pin_mismatch"
    : s.scan_points.some((p) => p.status === "partially_verified") ? "partially_verified" : s.status === "in_transit" ? "in_transit" : "pending";
  return DELIVERY_STATUS[st];
}

function ShipmentList({ rows, onOpen }: { rows: ShipmentView[]; onOpen: (id: number) => void }) {
  if (!rows.length) return <div className="card muted">No shipments yet.</div>;
  return (
    <div className="card" style={{ padding: 0, overflow: "auto" }}>
      <table className="data">
        <thead><tr><th>Shipment</th><th>Destination</th><th>Window</th><th>Status</th><th>Scan points</th><th>Flags</th></tr></thead>
        <tbody>
          {rows.map((s) => {
            const st = statusOf(s);
            return (
              <tr key={s.id} className="click" onClick={() => onOpen(s.id)}>
                <td><b>{s.code}</b>{s.two_person_required && <div className="small muted">two-person</div>}</td>
                <td>{s.dest_site}<div className="small muted">{s.dest_adm2_pcode}{s.risk_band_at_creation && <> · risk {s.risk_band_at_creation}</>}</div></td>
                <td className="num small">{s.window_start.slice(0, 10)} → {s.window_end.slice(0, 10)}</td>
                <td><span style={{ color: st.color }}>{st.icon}</span> {st.label}</td>
                <td className="small">{s.scan_points.map((p) => `${p.seq}. ${p.kind.replace(/_/g, " ")}: ${p.status.replace(/_/g, " ")} (${p.confirmations}/${p.required_confirmations})`).join(" · ")}</td>
                <td>{s.flags.map((f) => <span key={f} className={`badge ${f === "pin_failures" || f === "cold_chain_failure" ? "crit" : "warn"}`} style={{ margin: 2 }}>{f.replace(/_/g, " ")}</span>)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ShipmentDetail({ id, me, onClose }: { id: number; me: Me; onClose: () => void }) {
  const [s, setS] = useState<ShipmentView | null>(null);
  const [qrs, setQrs] = useState<{ seq: number; kind: string; qr: string; qr_version: number }[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.get<ShipmentView>(`/shipments/${id}`).then(setS).catch((e) => setErr(e.message));
    api.get<typeof qrs>(`/shipments/${id}/qr`).then(setQrs).catch(() => {});
  }, [id]);
  const seesDuress = me.role === "hq_admin" || me.role === "security_focal_point";
  return (
    <div className="modalbg" onClick={(e) => e.target === e.currentTarget && onClose()} role="dialog" aria-modal="true">
      <div className="modal" style={{ width: "min(860px, 100%)" }}>
        {err && <p className="error">{err}</p>}
        {!s ? <p className="muted">Loading…</p> : (<>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div><h2>{s.code}</h2><div className="muted small">{s.origin} → {s.dest_site} ({s.dest_adm2_pcode})</div></div>
            <div className="row">
              <button className="btn" onClick={() => download(`/shipments/${s.id}/labels.pdf`, `${s.code}_labels.pdf`)}>Download labels (PDF)</button>
              <button className="btn" onClick={onClose}>Close</button>
            </div>
          </div>
          {seesDuress && s.duress && (
            <div className="notice" style={{ borderColor: "var(--crit)" }} role="alert">
              <b style={{ color: "var(--crit)" }}>⚠ Duress PIN used on this shipment.</b> Follow the security protocol. Field staff see this delivery as normally confirmed; do not discuss it through ordinary channels.
            </div>
          )}
          <div className="split">
            <div className="stack">
              <h3>Contents</h3>
              <table className="data"><thead><tr><th>Item</th><th className="num">Dispatched</th><th className="num">Confirmed (final)</th><th>Batch / expiry</th></tr></thead>
                <tbody>{s.items.map((it, i) => {
                  const rec = s.received_final.find((r) => r.item_code === it.item_code);
                  return <tr key={i}><td>{it.item}</td><td className="num">{it.quantity}</td><td className="num">{rec?.confirmed ?? "—"}</td>
                    <td className="small">{it.batch ?? "—"} {it.expiry ? `· exp ${it.expiry.slice(0, 10)}` : ""}</td></tr>;
                })}</tbody></table>
              <div className="small muted">Window {s.window_start.slice(0, 10)} → {s.window_end.slice(0, 10)} · {s.cold_chain ? "cold chain" : "no cold chain"}
                {s.low_profile.length > 0 && <> · low-profile: {s.low_profile.join(", ").replace(/_/g, " ")}</>}
                {s.two_person_required && <> · two-person confirmation required</>}</div>
              {s.flags.length > 0 && <div>{s.flags.map((f) => <span key={f} className="badge warn" style={{ margin: 2 }}>{f.replace(/_/g, " ")}</span>)}</div>}
            </div>
            <div className="stack">
              <h3>Scan points</h3>
              {s.scan_points.map((p) => (
                <div key={p.scan_point_id} className="scan">
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <b>{p.seq}. {p.kind.replace(/_/g, " ")}</b>
                    <span className="badge">{p.status.replace(/_/g, " ")} · {p.confirmations}/{p.required_confirmations}</span>
                  </div>
                  {p.receipts.map((r) => (
                    <div key={r.receipt_id} className="small">
                      {r.recipient} · {r.device_ts.slice(0, 16).replace("T", " ")} · {r.result.replace(/_/g, " ")}
                      {seesDuress && r.pin_class === "duress" && <span className="badge crit" style={{ marginLeft: 6 }}>DURESS</span>}
                      {r.cold_chain && <> · VVM {r.cold_chain.vvm_stage ?? "?"}{r.cold_chain.indicator_triggered ? ", indicator triggered" : ""}</>}
                      {r.photo && <> · photo</>}
                      {r.notes && <div className="muted">{r.notes}</div>}
                    </div>
                  ))}
                  {qrs.find((q) => q.seq === p.seq) && <QrBlock shipmentId={s.id} seq={p.seq} q={qrs.find((q) => q.seq === p.seq)!} />}
                </div>
              ))}
            </div>
          </div>
        </>)}
      </div>
    </div>
  );
}

function QrBlock({ shipmentId, seq, q }: { shipmentId: number; seq: number; q: { qr: string; qr_version: number } }) {
  const [url, setUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    let u: string | null = null;
    api.get<string>(`/shipments/${shipmentId}/qr/${seq}.svg`).then((svg) => {
      u = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
      setUrl(u);
    }).catch(() => {});
    return () => { if (u) URL.revokeObjectURL(u); };
  }, [shipmentId, seq]);
  return (
    <div className="row" style={{ alignItems: "center" }}>
      {url && <img src={url} alt={`Signed QR code for scan point ${seq}`} width={132} height={132} style={{ background: "#fff", borderRadius: 4 }} />}
      <div className="stack" style={{ flex: 1, minWidth: 0 }}>
        <span className="small muted">Signed QR (version {q.qr_version}). Scan with the recipient app, or copy the code into it.</span>
        <code className="qr">{q.qr}</code>
        <button type="button" className="btn small" onClick={async () => {
          try { await navigator.clipboard.writeText(q.qr); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard blocked */ }
        }}>{copied ? "Copied ✓" : "Copy code"}</button>
      </div>
    </div>
  );
}

interface ItemRow { item_code: number; quantity: number; batch: string; expiry: string }

function NewShipment({ cfg, onCreated }: { cfg: AppConfig; onCreated: (s: ShipmentView) => void }) {
  const today = new Date().toISOString().slice(0, 10);
  const plus = (d: number) => new Date(Date.now() + d * 864e5).toISOString().slice(0, 10);
  const [adm2, setAdm2] = useState<Adm2[]>([]);
  const [recips, setRecips] = useState<RecipientRow[]>([]);
  const [risk, setRisk] = useState<Record<string, UnitSummary>>({});
  const [origin, setOrigin] = useState("");
  const [pcode, setPcode] = useState("");
  const [site, setSite] = useState("");
  const [ws, setWs] = useState(today);
  const [we, setWe] = useState(plus(14));
  const [items, setItems] = useState<ItemRow[]>([{ item_code: cfg.catalogue[0].code, quantity: 1, batch: "", expiry: "" }]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [scans, setScans] = useState<string[]>(["dispatch", "final_distribution"]);
  const [flags, setFlags] = useState<string[]>([]);
  const [coldChain, setColdChain] = useState(false);
  const [extraTwo, setExtraTwo] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<Adm2[]>("/adm2").then(setAdm2);
    api.get<RecipientRow[]>("/recipients").then(setRecips);
    api.get<{ units: UnitSummary[] }>("/risk?horizon=1").then((r) => setRisk(Object.fromEntries(r.units.map((u) => [u.unit_id, u]))));
  }, []);
  const dest = adm2.find((a) => a.adm2_pcode === pcode);
  const u = dest ? risk[dest.unit_id] : undefined;
  const highRisk = !!u && (u.band === "high" || u.band === "severe");
  const twoPerson = highRisk || extraTwo;
  const candidates = useMemo(() => recips.filter((r) => r.enrolled && r.active && (!dest || r.unit_id === dest.unit_id)), [recips, dest]);
  const needsCold = coldChain || items.some((i) => cfg.catalogue.find((c) => c.code === i.item_code)?.cold_chain);

  const toggle = (arr: string[], v: string, set: (a: string[]) => void) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const s = await api.post<ShipmentView>("/shipments", {
        origin, dest_adm2_pcode: pcode, dest_site: site, window_start: ws, window_end: we, cold_chain: needsCold,
        items: items.map((i) => ({ item_code: i.item_code, quantity: i.quantity, batch: i.batch || null, expiry: i.expiry || null })),
        recipients: chosen, scan_points: cfg.scan_point_kinds.filter((k) => scans.includes(k)), low_profile: flags,
        two_person_override: extraTwo || null,
      });
      onCreated(s);
    } catch (e2: any) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit} style={{ maxWidth: 980 }}>
      <h2>New shipment</h2>
      <div className="grid2">
        <label className="field">Origin (warehouse / hub)<input value={origin} onChange={(e) => setOrigin(e.target.value)} required maxLength={200} /></label>
        <label className="field">Destination district
          <select value={pcode} onChange={(e) => { setPcode(e.target.value); setChosen([]); }} required>
            <option value="" disabled>Select…</option>
            {adm2.map((a) => <option key={a.adm2_pcode} value={a.adm2_pcode}>{a.adm2_name || a.adm2_pcode} ({a.adm1_name}, {a.country_name})</option>)}
          </select>
        </label>
        <label className="field">Destination site (facility or mobile team)
          <input value={site} onChange={(e) => setSite(e.target.value)} required maxLength={200} placeholder="HeRAMS facility list not yet loaded; type the site name" />
        </label>
        <div className="row">
          <label className="field">Window start<input type="date" value={ws} onChange={(e) => setWs(e.target.value)} required /></label>
          <label className="field">Window end<input type="date" value={we} min={ws} onChange={(e) => setWe(e.target.value)} required /></label>
        </div>
      </div>
      {u && (
        <div className={`notice ${highRisk ? "warn" : ""}`}>
          <span className="badge" style={{ background: RISK_BANDS[u.band!], color: "#fff", borderColor: "transparent" }}>{u.band} risk</span>{" "}
          {u.unit_name}: {u.recommendation}
          {highRisk && <div><b>High-risk destination:</b> two-person confirmation and at least two scan points are required.</div>}
        </div>
      )}

      <h3>Contents</h3>
      <table className="data">
        <thead><tr><th>Item</th><th>Quantity</th><th>Batch</th><th>Expiry</th><th /></tr></thead>
        <tbody>
          {items.map((it, i) => (
            <tr key={i}>
              <td><select value={it.item_code} onChange={(e) => setItems(items.map((x, j) => j === i ? { ...x, item_code: +e.target.value } : x))}>
                {cfg.catalogue.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}</select></td>
              <td><input type="number" min={1} value={it.quantity} style={{ width: 100 }} onChange={(e) => setItems(items.map((x, j) => j === i ? { ...x, quantity: +e.target.value } : x))} /></td>
              <td><input value={it.batch} maxLength={64} onChange={(e) => setItems(items.map((x, j) => j === i ? { ...x, batch: e.target.value } : x))} /></td>
              <td><input type="date" value={it.expiry} onChange={(e) => setItems(items.map((x, j) => j === i ? { ...x, expiry: e.target.value } : x))} /></td>
              <td>{items.length > 1 && <button type="button" className="btn small" onClick={() => setItems(items.filter((_, j) => j !== i))}>Remove</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row">
        <button type="button" className="btn small" onClick={() => setItems([...items, { item_code: cfg.catalogue[0].code, quantity: 1, batch: "", expiry: "" }])}>+ Add item</button>
        <label className="opt"><input type="checkbox" checked={needsCold} onChange={(e) => setColdChain(e.target.checked)} /> Cold chain required{needsCold && !coldChain && " (vaccines)"}</label>
      </div>

      <div className="grid2">
        <fieldset className="stack" style={{ border: 0, padding: 0 }}>
          <h3>Scan points</h3>
          {cfg.scan_point_kinds.map((k) => (
            <label key={k} className="opt"><input type="checkbox" checked={scans.includes(k)} onChange={() => toggle(scans, k, setScans)} /> {k.replace(/_/g, " ")}</label>
          ))}
          {highRisk && scans.length < 2 && <p className="error">Choose at least two scan points for this destination.</p>}
        </fieldset>
        <fieldset className="stack" style={{ border: 0, padding: 0 }}>
          <h3>Low-profile logistics</h3>
          {cfg.low_profile_flags.map((f) => (
            <label key={f} className="opt"><input type="checkbox" checked={flags.includes(f)} onChange={() => toggle(flags, f, setFlags)} /> {f.replace(/_/g, " ")}</label>
          ))}
        </fieldset>
      </div>

      <h3>Recipient accounts at the site</h3>
      {!dest ? <p className="muted small">Choose a destination first.</p> : candidates.length === 0 ? (
        <p className="muted small">No enrolled recipient accounts in this district yet. Create one under “Recipients”.</p>
      ) : candidates.map((r) => (
        <label key={r.account} className="opt"><input type="checkbox" checked={chosen.includes(r.account)} onChange={() => toggle(chosen, r.account, setChosen)} />
          {r.account} · {r.site_name}</label>
      ))}
      <label className="opt"><input type="checkbox" checked={twoPerson} disabled={highRisk} onChange={(e) => setExtraTwo(e.target.checked)} />
        Two-person confirmation {highRisk ? "(required for this destination)" : "(optional here)"}</label>
      {twoPerson && chosen.length < 2 && <p className="error">Select at least two recipient accounts.</p>}

      {err && <p className="error" role="alert">{err}</p>}
      <div><button className="btn primary" disabled={busy || !pcode || !chosen.length || (twoPerson && chosen.length < 2) || (highRisk && scans.length < 2)}>
        {busy ? "Creating…" : "Create shipment and signed QR codes"}</button></div>
    </form>
  );
}

function Recipients({ me }: { me: Me }) {
  const [rows, setRows] = useState<RecipientRow[]>([]);
  const [adm2, setAdm2] = useState<Adm2[]>([]);
  const [site, setSite] = useState("");
  const [pcode, setPcode] = useState("");
  const [created, setCreated] = useState<{ account: string; enrolment_code: string; expires: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => api.get<RecipientRow[]>("/recipients").then(setRows);
  useEffect(() => { load(); api.get<Adm2[]>("/adm2").then(setAdm2); }, []);
  return (
    <div className="split">
      <div className="card" style={{ padding: 0, overflow: "auto" }}>
        <table className="data"><thead><tr><th>Account</th><th>Site</th><th>District</th><th>Enrolled</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.account}><td><b>{r.account}</b></td><td>{r.site_name}</td><td>{r.adm2_pcode}</td>
            <td>{r.enrolled ? <span className="badge good">✓ enrolled</span> : <span className="badge">awaiting device</span>}</td></tr>)}</tbody></table>
      </div>
      {me.role === "hq_admin" ? (
        <form className="card stack" onSubmit={async (e) => {
          e.preventDefault(); setErr(null);
          try { setCreated(await api.post("/recipients", { site_name: site, adm2_pcode: pcode })); setSite(""); load(); }
          catch (e2: any) { setErr(e2.message); }
        }}>
          <h2>New recipient account</h2>
          <p className="muted small">For a vetted facility in-charge or staff member. The recipient enrols their own device and sets both PINs on it; HQ never sees the PINs.</p>
          <label className="field">Site name<input value={site} onChange={(e) => setSite(e.target.value)} required /></label>
          <label className="field">District
            <select value={pcode} onChange={(e) => setPcode(e.target.value)} required>
              <option value="" disabled>Select…</option>
              {adm2.map((a) => <option key={a.adm2_pcode} value={a.adm2_pcode}>{a.adm2_name || a.adm2_pcode} ({a.adm1_name}, {a.country_name})</option>)}
            </select>
          </label>
          <button className="btn primary">Create account</button>
          {err && <p className="error">{err}</p>}
          {created && (
            <div className="notice" role="status">
              Account <b>{created.account}</b>. One-time enrolment code: <b className="num" style={{ fontSize: 18, letterSpacing: 2 }}>{created.enrolment_code}</b><br />
              <span className="small">Give it to the recipient in person during vetting. It is shown only once and expires {created.expires.slice(0, 16).replace("T", " ")} UTC.</span>
            </div>
          )}
        </form>
      ) : <div className="card muted">Only HQ admins create recipient accounts.</div>}
    </div>
  );
}

function Verification() {
  const [rows, setRows] = useState<any[]>([]);
  const load = () => api.get<any[]>("/verification").then(setRows);
  useEffect(() => { load(); }, []);
  return (
    <div className="card stack">
      <h2>Independent verification queue</h2>
      <p className="muted small">When a delivery is confirmed, the server draws at random (10% of deliveries; 30% in high-risk districts) whether a third-party monitor follows it up. The draw uses a cryptographic random number generator, so no site can predict which deliveries will be checked.</p>
      {rows.length === 0 ? <p className="muted">No deliveries selected yet.</p> : (
        <table className="data"><thead><tr><th>Shipment</th><th>Site</th><th>Selected</th><th>Rate</th><th>Outcome</th><th /></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id}><td><b>{r.shipment}</b></td><td>{r.site} <span className="muted small">{r.district}</span></td>
              <td className="small">{r.selected_at.slice(0, 10)}</td><td className="num">{Math.round(100 * r.selection_rate)}%</td>
              <td>{r.outcome.replace(/_/g, " ")}{r.outcome_notes && <div className="small muted">{r.outcome_notes}</div>}</td>
              <td>{r.outcome === "pending" && ["confirmed", "discrepancy", "not_reachable"].map((o) => (
                <button key={o} className="btn small" style={{ margin: 2 }} onClick={async () => {
                  const notes = o === "confirmed" ? null : window.prompt("Notes (optional)") ?? null;
                  await api.post(`/verification/${r.id}`, { outcome: o, notes }); load();
                }}>{o.replace(/_/g, " ")}</button>))}</td></tr>))}</tbody></table>
      )}
    </div>
  );
}

function Reconciliation() {
  const [d, setD] = useState<{ ratios: any[]; rows: any[]; note: string } | null>(null);
  useEffect(() => { api.get<typeof d>("/reconciliation").then(setD); }, []);
  if (!d) return <div className="card muted">Loading…</div>;
  return (
    <div className="card stack">
      <h2>Consumption reconciliation</h2>
      <p className="muted small">Checks that supplies reached patients, not merely the facility: deliveries are compared with expected use from facility service data (for example SAM admissions), using configurable ratios. {d.note}</p>
      <table className="data"><thead><tr><th>Item</th><th>Metric</th><th>Ratio</th></tr></thead>
        <tbody>{d.ratios.map((r, i) => <tr key={i}><td>{r.item_code}</td><td>{r.metric}</td><td className="small">{r.note}</td></tr>)}</tbody></table>
      {d.rows.length === 0 ? <p className="muted">No facility service data entered yet, so there is nothing to reconcile.</p> : (
        <table className="data"><thead><tr><th>Site</th><th>Item</th><th>Window</th><th className="num">Delivered</th><th className="num">Expected use</th><th>Flag</th></tr></thead>
          <tbody>{d.rows.map((r, i) => <tr key={i}><td>{r.site} <span className="muted small">{r.account}</span></td><td>{r.item_code}</td><td className="small">{r.window}</td>
            <td className="num">{r.delivered_confirmed ?? "—"}</td><td className="num">{r.expected_use ?? "—"}</td>
            <td>{r.flag ? <span className="badge warn">⚠ {r.flag.replace(/_/g, " ")}</span> : <span className="muted small">{r.ratio_note}</span>}</td></tr>)}</tbody></table>
      )}
    </div>
  );
}
