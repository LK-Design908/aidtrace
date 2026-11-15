import { useState } from "react";
import { api, Me, setToken } from "./api";

const ROLES: { role: Me["role"]; title: string; body: string }[] = [
  { role: "field_staff", title: "Field staff", body: "Create shipments, print signed QR labels, log field reports, read the map." },
  { role: "security_focal_point", title: "Security focal point", body: "Everything field staff see, plus duress alerts and report text." },
  { role: "hq_admin", title: "HQ admin", body: "Create recipient accounts, Health Cluster export, user management." },
];

const ATTRIB = [
  "Armed Conflict Location & Event Data (ACLED), www.acleddata.com",
  "UCDP Georeferenced Event Dataset v26.1, Uppsala University",
  "FEWS NET IPC-compatible classifications",
  "WFP market prices, via HDX",
  "IOM Displacement Tracking Matrix",
  "OCHA COD-AB administrative boundaries, via HDX (CC BY-IGO)",
  "VIEWS forecasts (PRIO / Uppsala University)",
  "WorldPop population 2020 and building patterns v1.1 (CC BY-SA 4.0), University of Southampton",
  "Correlates of War: National Material Capabilities v7, MIDLOC 2.1",
  "Basemap: OpenFreeMap, © OpenStreetMap contributors",
];

export default function Landing({ onLogin }: { onLogin: (m: Me) => void }) {
  const [err, setErr] = useState<string | null>(null);
  const enter = async (role: Me["role"]) => {
    try {
      const r = await api.post<{ token: string; role: Me["role"]; username: string }>("/demo/login", { role });
      setToken(r.token);
      onLogin({ username: r.username, role: r.role });
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <div className="landing">
      <div className="demo-banner" role="note">DEMO · all shipments, recipients, reports and PINs here are synthetic</div>
      <header className="hero">
        <h1>AidTrace</h1>
        <p className="lede">Helping humanitarian supplies reach the people they were sent for.</p>
      </header>

      <section className="explain">
        <div>
          <h2>Confirm that aid arrived</h2>
          <p>Each consignment carries a signed QR code. At the clinic or mobile team, a vetted staff member scans it with an app that works offline, records what arrived with a photo, and enters a PIN.</p>
          <p>Each recipient also has a second, <b>safety PIN</b>. If someone forces them to confirm a delivery, they enter it instead. The phone shows exactly the same “Receipt recorded”, but the organisation’s security focal point is warned quietly. Nothing on the phone reveals which PIN was used.</p>
        </div>
        <div>
          <h2>Decide how to deliver, never whether</h2>
          <p>A district map combines food insecurity, prices, displacement and recorded conflict to estimate humanitarian need and the risk of diversion or violence.</p>
          <p>High risk changes the delivery method, through extra verification, split or low-profile consignments and independent checks. It never removes people from the response. All estimates are at district level and carry their data date.</p>
        </div>
      </section>

      <section className="roles">
        <h2>Try it: choose a role</h2>
        <div className="rolegrid">
          {ROLES.map((r) => (
            <button key={r.role} className="rolecard" onClick={() => enter(r.role)}>
              <b>{r.title}</b><span>{r.body}</span><span className="go">Open distributor app →</span>
            </button>
          ))}
          <a className="rolecard recipient" href="/recipient/" target="_blank" rel="noopener">
            <b>Recipient (demo phone)</b><span>Scan a consignment, confirm receipt with the normal or the safety PIN, or report a problem. Best opened on a phone, or in a narrow window.</span>
            <span className="go">Open recipient app ↗</span>
          </a>
        </div>
        {err && <p className="error">{err}</p>}
      </section>

      <section className="walk">
        <h2>Follow one consignment end to end</h2>
        <ol>
          <li><b>Recipient app:</b> tap “Set up a demo phone”. It creates a synthetic clinic account and shows the demo PINs (normal 2468, safety 1357).</li>
          <li><b>Field staff:</b> Resource transfer → New shipment. Choose the demo clinic’s district, tick its account, and create the shipment.</li>
          <li>Open the shipment to see the signed QR for each scan point. Scan it with the phone, or use “Copy code” and paste it into the recipient app.</li>
          <li><b>Recipient app:</b> enter the quantities and confirm with the normal PIN, the safety PIN, or a wrong PIN. The phone looks identical in every case.</li>
          <li><b>Back in the distributor app:</b> field staff see the delivery as verified, whichever PIN was used. Switch to the <b>security focal point</b> to see the duress alert. A wrong PIN shows as “unverified, PIN mismatch”.</li>
        </ol>
      </section>

      <footer className="landing-foot">
        <p><b>Beta.</b> Built as a pilot for the WHO Greater Horn of Africa food insecurity and health crisis response (Strategic Response Plan, Aug 2026–Jan 2027). This beta covers Djibouti, Kenya, Somalia, South Sudan and Sudan. Risk scores are estimates from recorded data and may be out of date.</p>
        <p className="muted small">Data sources: {ATTRIB.join(" · ")}. ACLED appears in the demo as district-month event counts after sign-in.</p>
      </footer>
    </div>
  );
}
