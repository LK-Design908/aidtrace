import { useEffect, useState } from "react";
import { api, ApiError, AppConfig, Me, onSessionExpired, setToken } from "./api";
import MapTab from "./MapTab";
import TransferTab from "./TransferTab";
import IndicatorsTab from "./IndicatorsTab";
import AlertsTab from "./AlertsTab";
import Landing from "./Landing";

type Tab = "map" | "transfer" | "indicators" | "alerts";

const ATTRIBUTION =
  "Data: UCDP GED v26.1 (Uppsala University) · FEWS NET · WFP market prices via HDX · IOM DTM · OCHA COD-AB via HDX · " +
  "VIEWS (PRIO/Uppsala) · WorldPop (Univ. of Southampton; buildings CC BY-SA 4.0, Ecopia/Maxar) · Correlates of War · " +
  "Basemap © OpenFreeMap © OpenStreetMap contributors. Risk scores are estimates.";

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [tab, setTab] = useState<Tab>("map");
  const [expired, setExpired] = useState(false);
  const [acledAttribution, setAcledAttribution] = useState<string | null>(null);

  const [mode, setMode] = useState<string | null>(null);
  useEffect(() => onSessionExpired(() => { setMe(null); setExpired(true); }), []);
  useEffect(() => { api.get<{ mode: string }>("/health").then((h) => setMode(h.mode)).catch(() => setMode("live")); }, []);
  useEffect(() => { if (me) api.get<AppConfig>("/config").then(setCfg).catch(() => {}); }, [me]);

  if (mode === null) return null;
  if (!me) return mode === "demo"
    ? <Landing onLogin={(m) => { setExpired(false); setMe(m); }} />
    : <Login expired={expired} onLogin={(m) => { setExpired(false); setMe(m); }} />;
  const seesAlerts = me.role === "hq_admin" || me.role === "security_focal_point";

  return (
    <div className={`shell ${mode === "demo" ? "is-demo" : ""}`}>
      {mode === "demo" && <div className="demo-banner" role="note">DEMO · synthetic data only · <button className="linkbtn" onClick={() => { setToken(null); setMe(null); }}>switch role</button></div>}
      <header className="topbar">
        <div className="brand">AidTrace<small>Distributor · Horn of Africa beta</small></div>
        <nav className="tabs" role="tablist" aria-label="Sections">
          <TabBtn id="map" cur={tab} set={setTab}>Map</TabBtn>
          <TabBtn id="transfer" cur={tab} set={setTab}>Resource transfer</TabBtn>
          <TabBtn id="indicators" cur={tab} set={setTab}>Indicators</TabBtn>
          {seesAlerts && <TabBtn id="alerts" cur={tab} set={setTab}>Security alerts</TabBtn>}
        </nav>
        <div className="spacer" />
        <span className="who">{me.username} · {me.role.replace(/_/g, " ")}</span>
        <button className="btn small" onClick={async () => { try { await api.post("/auth/logout"); } catch { /* ignore */ } setToken(null); setMe(null); }}>
          Sign out
        </button>
      </header>
      <main>
        {tab === "map" && cfg && <MapTab me={me} cfg={cfg} onAcled={setAcledAttribution} />}
        {tab === "transfer" && cfg && <TransferTab me={me} cfg={cfg} />}
        {tab === "indicators" && <IndicatorsTab me={me} />}
        {tab === "alerts" && seesAlerts && <AlertsTab />}
      </main>
      <footer className="footer">
        {ATTRIBUTION}
        {acledAttribution && <> · {acledAttribution}</>}
      </footer>
    </div>
  );
}

function TabBtn({ id, cur, set, children }: { id: Tab; cur: Tab; set: (t: Tab) => void; children: React.ReactNode }) {
  return (
    <button role="tab" className="tab" aria-selected={cur === id} onClick={() => set(id)}>{children}</button>
  );
}

function Login({ onLogin, expired }: { onLogin: (m: Me) => void; expired: boolean }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="login">
      <form className="card" onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setErr(null);
        try {
          const r = await api.post<{ token: string; role: Me["role"]; username: string }>("/auth/login", { username, password });
          setToken(r.token);
          onLogin({ username: r.username, role: r.role });
        } catch (e2) {
          setErr(e2 instanceof ApiError ? e2.message : "Sign-in failed");
        } finally {
          setBusy(false);
        }
      }}>
        <h2>AidTrace · Distributor sign-in</h2>
        {expired && <div className="notice warn">Your session expired. Please sign in again.</div>}
        <label className="field">Username<input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required /></label>
        <label className="field">Password<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
        {err && <div className="error" role="alert">{err}</div>}
        <button className="btn primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="muted small">Sessions end after 30 minutes of inactivity. Recipients use the separate recipient app.</p>
      </form>
    </div>
  );
}
