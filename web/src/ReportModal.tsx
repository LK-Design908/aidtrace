import { useEffect, useState } from "react";
import { Adm2, api, AppConfig } from "./api";

export default function ReportModal({ cfg, unitId, onClose, onSaved }: {
  cfg: AppConfig; unitId: string | null; onClose: () => void; onSaved: () => void;
}) {
  const [adm2, setAdm2] = useState<Adm2[]>([]);
  const [type, setType] = useState(cfg.field_report_types[0]);
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [pcode, setPcode] = useState("");
  const [severity, setSeverity] = useState(3);
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<Adm2[]>("/adm2").then((rows) => {
      setAdm2(rows);
      const first = rows.find((r) => r.unit_id === unitId);
      if (first) setPcode(first.adm2_pcode);
    });
  }, [unitId]);

  const options = unitId ? adm2.filter((a) => a.unit_id === unitId) : adm2;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      let attachment_b64: string | undefined;
      if (file) {
        if (file.size > 1_400_000) throw new Error("Attachment too large (max ~1.4 MB)");
        const buf = new Uint8Array(await file.arrayBuffer());
        let s = "";
        buf.forEach((b) => (s += String.fromCharCode(b)));
        attachment_b64 = btoa(s);
      }
      await api.post("/reports", { type, incident_date: date, adm2_pcode: pcode, severity, free_text: text || null, attachment_b64 });
      onSaved();
    } catch (e2: any) {
      setErr(e2.message || "Could not save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modalbg" role="dialog" aria-modal="true" aria-label="Add field report" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <form className="modal" onSubmit={submit}>
        <h2>Add field report</h2>
        <div className="grid2">
          <label className="field">Type
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {cfg.field_report_types.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
            </select>
          </label>
          <label className="field">Date of incident<input type="date" value={date} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setDate(e.target.value)} required /></label>
          <label className="field">District
            <select value={pcode} onChange={(e) => setPcode(e.target.value)} required>
              <option value="" disabled>Select…</option>
              {options.map((a) => <option key={a.adm2_pcode} value={a.adm2_pcode}>{a.adm2_name || a.adm2_pcode} ({a.adm1_name}, {a.country_name})</option>)}
            </select>
          </label>
          <label className="field">Severity (1 low – 5 critical)
            <input type="range" min={1} max={5} value={severity} onChange={(e) => setSeverity(+e.target.value)} />
            <span className="num">{severity}</span>
          </label>
        </div>
        <label className="field">What happened
          <textarea rows={4} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)}
            placeholder="Facts only: what, where (district level), who was affected. Do not name informants." />
        </label>
        <label className="field">Attachment (optional; photo location data is removed)
          <input type="file" accept="image/jpeg,application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <p className="muted small">Text and attachments are visible only to HQ admins and security focal points (and to you). Reports appear on the map at once, marked “unverified” until reviewed. Do not use this form for sexual exploitation or abuse allegations: use the confidential PSEAH channel.</p>
        {err && <p className="error" role="alert">{err}</p>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || !pcode}>{busy ? "Saving…" : "Save report"}</button>
        </div>
      </form>
    </div>
  );
}
