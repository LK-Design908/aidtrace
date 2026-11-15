import { useEffect, useState } from "react";
import { api, Me, UnitDetail } from "./api";
import { RISK_BANDS, scales } from "./scales";
import type { AcledRow } from "./MapTab";

export interface ConflictSummary {
  months: string[]; ucdp: (number | null)[]; acled: AcledRow[] | null; acledAttribution: string | null;
}
const ACLED_TYPES: [keyof AcledRow, string][] = [
  ["acled_events_battles", "battles"], ["acled_events_vac", "violence against civilians"],
  ["acled_events_remote", "explosions / remote violence"], ["acled_events_riots", "riots"],
  ["acled_events_protests", "protests"], ["acled_events_strategic", "strategic developments"],
];

const COMPONENT_LABELS: Record<string, string> = {
  ipc_phase: "IPC phase", ipc_p3plus: "In IPC 3+", malnutrition: "Acute malnutrition", outbreaks: "Active outbreaks",
  facility_functionality: "Facility functionality", displacement: "Displacement inflow", immunisation: "Immunisation gap",
  wash: "WASH", rainfall_anomaly: "Rainfall anomaly", flood_hazard: "Flood hazard", price_anomaly: "Food price anomaly",
};
const QUADRANT_LABEL: Record<string, string> = {
  high_need_lower_risk: "High need · lower risk", high_need_higher_risk: "High need · higher risk",
  lower_need_higher_risk: "Lower need · higher risk", lower_need_lower_risk: "Lower need · lower risk",
  need_unknown_higher_risk: "Need unknown · higher risk", need_unknown: "Need unknown",
};

export default function DistrictPanel({ unitId, me, onClose, onAddReport, conflict }: {
  unitId: string; me: Me; onClose: () => void; onAddReport: (u: string) => void; conflict?: ConflictSummary;
}) {
  const [d, setD] = useState<UnitDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setD(null);
    api.get<UnitDetail>(`/risk/${unitId}`).then(setD).catch((e) => setErr(e.message));
  }, [unitId]);

  return (
    <aside className="drawer" aria-label="District details">
      <button className="btn small close" onClick={onClose} aria-label="Close">Close ✕</button>
      {err && <p className="error">{err}</p>}
      {!d ? <p className="muted">Loading…</p> : (
        <>
          <div>
            <h2>{d.unit_name}</h2>
            <div className="muted">{d.adm1_name}, {d.country_name} · {d.unit_id}</div>
            {d.exposure && (d.exposure.population !== null || d.exposure.buildings !== null) && (
              <div className="small" style={{ marginTop: 4 }}>
                {d.exposure.population !== null && <>Population {Math.round(d.exposure.population).toLocaleString()}
                  {d.exposure.under5 !== null && <> · under-5 {Math.round(d.exposure.under5).toLocaleString()}</>} <span className="muted">(WorldPop 2020)</span></>}
                {d.exposure.population === null && d.exposure.buildings !== null && <>{Math.round(d.exposure.buildings).toLocaleString()} buildings
                  {d.exposure.urban_share !== null && <> · {Math.round(100 * d.exposure.urban_share)}% in urban areas</>} <span className="muted">(WorldPop buildings)</span></>}
              </div>
            )}
          </div>
          <div className="rec">
            <b>{QUADRANT_LABEL[d.quadrant] ?? d.quadrant}</b><br />{d.recommendation}
          </div>

          <section className="stack">
            <h3>Violence risk (estimate)</h3>
            {[{ h: "1 month", p: d.prob, lo: d.prob_lo, hi: d.prob_hi, band: d.band, t: d.target_month },
              { h: "3 months", p: d.forecast_3m?.prob, lo: d.forecast_3m?.prob_lo, hi: d.forecast_3m?.prob_hi, band: d.forecast_3m?.band, t: d.forecast_3m?.target_month }]
              .map((r) => r.p === undefined ? null : (
                <div key={r.h} className="comp" style={{ gridTemplateColumns: "90px 1fr auto" }}>
                  <span>{r.h} <span className="muted small">({r.t})</span></span>
                  <div className="bar" title={`interval ${scales.risk.format(r.lo!)}–${scales.risk.format(r.hi!)}`}>
                    <span style={{ left: `${100 * r.lo!}%`, width: `${Math.max(1, 100 * (r.hi! - r.lo!))}%`, background: RISK_BANDS[r.band!], opacity: 0.45 }} />
                    <span style={{ left: `${Math.max(0, 100 * r.p - 0.75)}%`, width: "1.5%", background: RISK_BANDS[r.band!] }} />
                  </div>
                  <span className="num"><b>{scales.risk.format(r.p)}</b> <span className="badge">{r.band}</span></span>
                </div>
              ))}
            <p className="muted small">Data to {d.data_last_month} ({d.stale_months} months ago). Bar = 5–95% interval across models (model uncertainty only). {d.disclaimer}</p>
          </section>

          {conflict && <ConflictSection c={conflict} />}

          <section className="stack">
            <h3>Need index</h3>
            <div className="num"><b style={{ fontSize: 20 }}>{d.need_index?.toFixed(2) ?? "—"}</b>
              <span className="muted small"> {d.need_month} · {Math.round(100 * (d.need_weight_available ?? 0))}% of index weight backed by data</span></div>
            {d.need_components && Object.entries(d.need_components).filter(([k]) => COMPONENT_LABELS[k]).map(([k, v]) => (
              <div key={k} className="comp">
                <span>{COMPONENT_LABELS[k]}</span>
                <div className="bar">{v !== null && <span style={{ width: `${100 * v}%` }} />}</div>
                <span className="num small">{v === null ? <span className="muted">missing</span> : v.toFixed(2)}</span>
              </div>
            ))}
            {d.ipc_p3plus_basis && <p className="muted small">“In IPC 3+” is the share of {d.ipc_p3plus_basis === "population" ? "people (WorldPop)" : d.ipc_p3plus_basis === "buildings" ? "buildings (proxy for people)" : "land area"} in Phase 3 or worse.</p>}
            {d.need_trend?.length > 1 && <Sparkline pts={d.need_trend.map((t) => t.need_index)} labels={d.need_trend.map((t) => t.month)} />}
          </section>

          <section className="stack">
            <h3>Active SRP triggers</h3>
            {d.active_triggers.length === 0 ? <p className="muted small">None in the last 3 months of data.</p> :
              d.active_triggers.map((t, i) => (
                <div key={i} className="small"><span className="badge warn">⚠ {t.label}</span> {t.month}: {t.evidence} <span className="muted">({t.source})</span></div>
              ))}
          </section>

          <section className="stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h3>Recent field reports</h3>
              <button className="btn small" onClick={() => onAddReport(unitId)}>+ Add report</button>
            </div>
            {d.recent_reports.length === 0 ? <p className="muted small">None.</p> :
              d.recent_reports.map((r) => (
                <div key={r.id} className="small">
                  <b>{r.type.replace(/_/g, " ")}</b> · {r.incident_date.slice(0, 10)} · severity {r.severity} · {r.reporter_role.replace(/_/g, " ")}
                  {" "}<span className={`badge ${r.review_status === "verified" ? "good" : ""}`}>{r.review_status}</span>
                  {r.free_text ? <div className="muted">{r.free_text}</div> : r.has_text ? <div className="muted">Text visible to authorised roles only.</div> : null}
                </div>
              ))}
          </section>

          <section className="stack">
            <h3>Recent deliveries</h3>
            {d.recent_deliveries.length === 0 ? <p className="muted small">None.</p> :
              d.recent_deliveries.map((s) => (
                <div key={s.code} className="small"><b>{s.code}</b> · {s.dest_site} · {s.status.replace(/_/g, " ")}
                  {s.flags.map((f) => <span key={f} className="badge" style={{ marginLeft: 4 }}>{f.replace(/_/g, " ")}</span>)}</div>
              ))}
          </section>
          <p className="muted small">Signed in as {me.role.replace(/_/g, " ")}.</p>
        </>
      )}
    </aside>
  );
}

function Sparkline({ pts, labels }: { pts: (number | null)[]; labels: string[] }) {
  const w = 360, h = 56, pad = 4;
  const xs = (i: number) => pad + (i * (w - 2 * pad)) / Math.max(1, pts.length - 1);
  const ys = (v: number) => h - pad - v * (h - 2 * pad);
  let d = "";
  pts.forEach((v, i) => { if (v === null) return; d += `${d && pts[i - 1] !== null ? "L" : "M"}${xs(i).toFixed(1)},${ys(v).toFixed(1)}`; });
  return (
    <figure style={{ margin: 0 }}>
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img" aria-label="Need index over the last 24 months">
        <line x1={pad} x2={w - pad} y1={ys(0)} y2={ys(0)} stroke="var(--line-strong)" />
        <path d={d} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" />
        {pts.map((v, i) => v === null ? null : <circle key={i} cx={xs(i)} cy={ys(v)} r={2.5} fill="var(--accent)"><title>{labels[i]}: {v.toFixed(2)}</title></circle>)}
      </svg>
      <figcaption className="muted small">Need index, {labels[0]} to {labels[labels.length - 1]} (gaps = no data)</figcaption>
    </figure>
  );
}

function ConflictSection({ c }: { c: ConflictSummary }) {
  const obs = c.ucdp.map((v, i) => [c.months[i], v] as const).filter(([, v]) => v !== null);
  const ucdpSum = obs.reduce((a, [, v]) => a + (v as number), 0);
  const rows = (c.acled ?? []).slice().sort((a, b) => a.month.localeCompare(b.month));
  const tot = (k: keyof AcledRow) => rows.reduce((a, r) => a + (Number(r[k]) || 0), 0);
  const types = ACLED_TYPES.map(([k, l]) => [tot(k), l] as const).filter(([n]) => n > 0).map(([n, l]) => `${n} ${l}`).join(" · ");
  return (
    <section className="stack">
      <h3>Recorded conflict</h3>
      <div className="small">
        <b>UCDP:</b>{" "}
        {obs.length ? <>{Math.round(ucdpSum)} fatalities, {obs[0][0]} to {obs[obs.length - 1][0]}</> : <span className="muted">no data in the last 12 months</span>}
      </div>
      {c.acled !== null && (rows.length ? (
        <div className="small">
          <b>ACLED:</b> {tot("acled_events")} events, {tot("acled_fatalities")} fatalities, {rows[0].month} to {rows[rows.length - 1].month}
          <div className="muted">{types || "no events recorded"}</div>
        </div>
      ) : <div className="small muted"><b>ACLED:</b> no data for this district.</div>)}
      <p className="muted small">UCDP and ACLED count differently (UCDP: organised violence with at least one death; ACLED: all political violence and demonstrations), so they are shown side by side, not combined. District-month totals only.{c.acledAttribution && <> Source: {c.acledAttribution}.</>}</p>
    </section>
  );
}
