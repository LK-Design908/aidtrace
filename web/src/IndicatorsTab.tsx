import { useEffect, useState } from "react";
import { api, download, Me } from "./api";

interface Ind { id: string; label: string; value: number | null; unit: string; n: number; srp: string }
interface Data {
  window_days: number; indicators: Ind[];
  stock_by_district_item: { unit_id: string; item: string; confirmed_quantity: number }[];
  access_reports_by_district: { unit_id: string; reports: number }[];
  trigger_alerts_by_district?: { unit_id: string; active_triggers: number }[];
  trigger_alerts_note?: string;
}

export default function IndicatorsTab({ me }: { me: Me }) {
  const [days, setDays] = useState(90);
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setD(null); api.get<Data>(`/indicators?days=${days}`).then(setD).catch((e) => setErr(e.message)); }, [days]);
  const canExport = me.role === "hq_admin" || me.role === "security_focal_point";

  return (
    <div className="page">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="seg" role="group" aria-label="Time window">
          {[30, 90, 180].map((n) => <button key={n} aria-pressed={days === n} onClick={() => setDays(n)}>Last {n} days</button>)}
        </div>
        <div className="row">
          <button className="btn" onClick={() => download(`/indicators.csv?days=${days}`, "aidtrace_indicators.csv")}>Export indicators (CSV)</button>
          {canExport && <button className="btn primary" onClick={() => download("/export/health-cluster.csv", "aidtrace_health_cluster.csv")}>Health Cluster export (CSV)</button>}
        </div>
      </div>
      {canExport && <p className="muted small">The Health Cluster export holds aggregated delivery data by district, month and item only. It contains no field-report text, no duress information, no recipient identities and no ACLED-derived scores.</p>}
      {err && <p className="error">{err}</p>}
      {!d ? <p className="muted">Loading…</p> : (<>
        <div className="tiles">
          {d.indicators.map((i) => (
            <div key={i.id} className="tile">
              <span className="small muted">{i.label}</span>
              <span className="v num">{i.value === null ? "—" : i.value}{i.value !== null && <span style={{ fontSize: 14, fontWeight: 400 }}> {i.unit}</span>}</span>
              <span className="small muted">n = {i.n}</span>
              <span className="srp">SRP §7: {i.srp}</span>
            </div>
          ))}
        </div>
        <div className="split">
          <div className="card stack">
            <h3>Confirmed stock delivered, by district and item</h3>
            {d.stock_by_district_item.length === 0 ? <p className="muted small">No confirmed deliveries in this window.</p> : (
              <table className="data"><thead><tr><th>District</th><th>Item</th><th className="num">Quantity</th></tr></thead>
                <tbody>{d.stock_by_district_item.map((r, i) => <tr key={i}><td>{r.unit_id}</td><td>{r.item}</td><td className="num">{r.confirmed_quantity.toLocaleString()}</td></tr>)}</tbody></table>
            )}
          </div>
          <div className="card stack">
            <h3>Access-constraint reports by district</h3>
            {d.access_reports_by_district.length === 0 ? <p className="muted small">None in this window.</p> : (
              <table className="data"><thead><tr><th>District</th><th className="num">Reports</th></tr></thead>
                <tbody>{d.access_reports_by_district.map((r) => <tr key={r.unit_id}><td>{r.unit_id}</td><td className="num">{r.reports}</td></tr>)}</tbody></table>
            )}
            <h3 style={{ marginTop: 12 }}>SRP trigger alerts by district</h3>
            <p className="muted small">{d.trigger_alerts_note}</p>
            {d.trigger_alerts_by_district && (
              <table className="data"><thead><tr><th>District</th><th className="num">Distinct active triggers</th></tr></thead>
                <tbody>{d.trigger_alerts_by_district.map((r) => <tr key={r.unit_id}><td>{r.unit_id}</td><td className="num">{r.active_triggers}</td></tr>)}</tbody></table>
            )}
          </div>
        </div>
      </>)}
    </div>
  );
}
