import { useEffect, useState } from "react";
import { api } from "./api";

interface Alert {
  id: number; created_at: string; shipment: string; shipment_id: number; scan_point: string; site: string;
  district: string; recipient: string; acknowledged_at: string | null; notified: Record<string, unknown>;
}

export default function AlertsTab() {
  const [rows, setRows] = useState<Alert[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => api.get<Alert[]>("/alerts").then(setRows).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  return (
    <div className="page">
      <div className="card stack">
        <h2>Duress alerts</h2>
        <p className="muted small">Visible only to security focal points and HQ admins. Every view of this page is recorded in the audit log. A duress receipt looks like a normal confirmed receipt to everyone else, including the recipient's device. Never contact the recipient through ordinary channels, and never involve local authorities.</p>
        {err && <p className="error">{err}</p>}
        {!rows ? <p className="muted">Loading…</p> : rows.length === 0 ? <p className="muted">No duress alerts.</p> : (
          <table className="data">
            <thead><tr><th>Received</th><th>Shipment</th><th>Scan point</th><th>Site</th><th>Recipient account</th><th>Notified</th><th>Status</th></tr></thead>
            <tbody>{rows.map((a) => (
              <tr key={a.id}>
                <td className="num small">{a.created_at.slice(0, 16).replace("T", " ")} UTC</td>
                <td><b>{a.shipment}</b></td><td>{a.scan_point.replace(/_/g, " ")}</td><td>{a.site} <span className="muted small">{a.district}</span></td>
                <td>{a.recipient}</td>
                <td className="small">{Object.entries(a.notified).filter(([k, v]) => k !== "at" && v === true).map(([k]) => k).join(", ") || "pending"}</td>
                <td>{a.acknowledged_at ? <span className="badge good">✓ acknowledged</span> :
                  <button className="btn small" onClick={async () => { await api.post(`/alerts/${a.id}/ack`); load(); }}>Acknowledge</button>}</td>
              </tr>))}</tbody>
          </table>
        )}
      </div>
    </div>
  );
}
