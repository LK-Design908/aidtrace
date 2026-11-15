import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { GeoJsonLayer, ScatterplotLayer } from "@deck.gl/layers";
import { api, AppConfig, FC, Layers, Me, Report, ShipmentView } from "./api";
import { bbox, centroids } from "./geo";
import type { FC as FCType } from "./api";
import { DELIVERY_STATUS, hexToRgba, NO_DATA_HEX, scales } from "./scales";
import DistrictPanel from "./DistrictPanel";
import ReportModal from "./ReportModal";

type Choro = "need_index" | "risk1" | "risk3" | "ipc_phase" | "price_anom" | "displacement" | "ucdp_fatalities" | "pop_density" | "bldg_density";

const CHORO_OPTIONS: { id: Choro; label: string; src: string }[] = [
  { id: "need_index", label: "Need index", src: "composite" },
  { id: "risk1", label: "Violence risk, 1 month", src: "model" },
  { id: "risk3", label: "Violence risk, 3 months", src: "model" },
  { id: "ipc_phase", label: "IPC phase (current)", src: "FEWS NET" },
  { id: "price_anom", label: "Staple price anomaly", src: "WFP" },
  { id: "displacement", label: "Displacement inflow", src: "IOM DTM" },
  { id: "ucdp_fatalities", label: "Recorded conflict", src: "UCDP + ACLED" },
  { id: "pop_density", label: "Population density", src: "WorldPop" },
  { id: "bldg_density", label: "Building density", src: "WorldPop" },
];
const HEIGHT_VARS: Choro[] = ["need_index", "risk1", "risk3", "ipc_phase", "displacement", "ucdp_fatalities", "pop_density"];
const REPORT_COLOR = "#6250d6";

const BLANK_STYLE: maplibregl.StyleSpecification = {
  version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#e9eef2" } }],
};

export interface AcledRow {
  unit_id: string; month: string; acled_events: number; acled_fatalities?: number;
  acled_events_battles?: number; acled_events_vac?: number; acled_events_remote?: number;
  acled_events_riots?: number; acled_events_protests?: number; acled_events_strategic?: number;
}

export default function MapTab({ me, cfg, onAcled }: { me: Me; cfg: AppConfig; onAcled: (a: string | null) => void }) {
  const mapEl = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const [fc, setFc] = useState<FC | null>(null);
  const [layers, setLayers] = useState<Layers | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [shipments, setShipments] = useState<ShipmentView[]>([]);
  const [acled, setAcled] = useState<{ rows: AcledRow[]; attribution: string; level: string } | null | "off">(null);
  const [choro, setChoro] = useState<Choro>("need_index");
  const [showReports, setShowReports] = useState(true);
  const [showDeliveries, setShowDeliveries] = useState(true);
  const [is3d, setIs3d] = useState(false);
  const [heightVar, setHeightVar] = useState<Choro>("risk1");
  const [monthIdx, setMonthIdx] = useState<number>(-1);
  const [hover, setHover] = useState<{ x: number; y: number; html: React.ReactNode } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [reportFor, setReportFor] = useState<string | null | undefined>(undefined);
  const [basemapOk, setBasemapOk] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const loadReports = () => api.get<Report[]>("/reports").then(setReports).catch(() => {});
  useEffect(() => {
    Promise.all([api.get<FC>("/units.geojson"), api.get<Layers>("/layers")])
      .then(([f, l]) => { setFc(f); setLayers(l); })
      .catch((e) => setErr(String(e.message || e)));
    loadReports();
    api.get<ShipmentView[]>("/shipments").then(setShipments).catch(() => {});
    api.get<{ rows: AcledRow[]; attribution: string; level: string }>("/acled").then(setAcled).catch(() => setAcled("off"));
  }, []);

  const months = layers?.months ?? [];
  const cents = useMemo(() => (fc ? centroids(fc) : {}), [fc]);

  // ------------------------------------------------------------ values for the selected layer and month
  const isRisk = choro === "risk1" || choro === "risk3";
  const forecast = layers?.forecast?.[choro === "risk3" ? "h3" : "h1"];
  const layerObserved = useMemo(() => {
    if (!layers) return new Set<string>();
    if (isRisk) return new Set<string>();
    return new Set(layers.layers[choro]?.observed_months ?? []);
  }, [layers, choro, isRisk, acled]);
  const forecastMonths = useMemo(() => new Set(isRisk && forecast ? [forecast.target_month] : []), [isRisk, forecast]);

  // pick a sensible month when the layer changes: forecast target, else latest observed
  useEffect(() => {
    if (!months.length) return;
    if (isRisk && forecast) setMonthIdx(months.indexOf(forecast.target_month));
    else {
      const obs = months.filter((m) => layerObserved.has(m));
      setMonthIdx(obs.length ? months.indexOf(obs[obs.length - 1]) : months.length - 1);
    }
  }, [choro, months.length, layerObserved.size]); // eslint-disable-line react-hooks/exhaustive-deps

  const month = months[monthIdx];
  const valueOf = useMemo(() => {
    return (layerId: Choro, unit: string): number | null => {
      if (!layers) return null;
      if (layerId === "risk1" || layerId === "risk3") {
        const f = layers.forecast?.[layerId === "risk3" ? "h3" : "h1"];
        return f?.values[unit]?.prob ?? null;
      }
      const arr = layers.layers[layerId]?.values[unit];
      return arr ? arr[monthIdx] ?? null : null;
    };
  }, [layers, monthIdx]);

  // ACLED is folded into the "Recorded conflict" layer: colour stays UCDP (consistent history since 2017);
  // ACLED district-month counts are shown alongside in the tooltip and district panel where available.
  const acledOn = !!acled && acled !== "off";
  const acledIdx = useMemo(() => {
    const m = new Map<string, AcledRow>();
    if (acled && acled !== "off") acled.rows.forEach((r) => m.set(`${r.unit_id}|${r.month}`, r));
    return m;
  }, [acled]);
  const acledRange = useMemo(() => {
    if (!acled || acled === "off" || !acled.rows.length) return null;
    const ms = acled.rows.map((r) => r.month).sort();
    return [ms[0], ms[ms.length - 1]] as [string, string];
  }, [acled]);
  const scale = isRisk ? scales.risk : scales[choro];
  useEffect(() => onAcled(choro === "ucdp_fatalities" && acled && acled !== "off" ? acled.attribution : null), [choro, acled, onAcled]);

  // ------------------------------------------------------------ map init
  useEffect(() => {
    if (!mapEl.current || mapRef.current) return;
    const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    const map = new maplibregl.Map({
      container: mapEl.current,
      style: `https://tiles.openfreemap.org/styles/${dark ? "dark" : "positron"}`,
      center: [46, 6], zoom: 4.6, attributionControl: false, maxPitch: 60,
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-left");
    map.addControl(new maplibregl.AttributionControl({ compact: true }));
    const fallback = () => { setBasemapOk(false); map.setStyle(BLANK_STYLE); };
    const timer = window.setTimeout(() => { if (!map.isStyleLoaded()) fallback(); }, 8000);
    map.on("error", (e) => { if (String(e.error?.message || "").match(/style|Failed to fetch|NetworkError/i) && !map.isStyleLoaded()) fallback(); });
    map.on("load", () => window.clearTimeout(timer));
    const overlay = new MapboxOverlay({ interleaved: false, layers: [] });
    map.addControl(overlay as unknown as maplibregl.IControl);
    mapRef.current = map;
    overlayRef.current = overlay;
    return () => { window.clearTimeout(timer); map.remove(); mapRef.current = null; };
  }, []);

  useEffect(() => {
    if (fc && mapRef.current) {
      const b = bbox(fc);
      mapRef.current.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 30, duration: 0 });
    }
  }, [fc]);

  const countries = useMemo(() => {
    if (!fc) return [] as { code: string; name: string }[];
    const seen = new Map<string, string>();
    fc.features.forEach((f) => seen.set(f.properties.country, f.properties.country_name));
    return [...seen.entries()].map(([code, name]) => ({ code, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [fc]);
  const zoomTo = (code: string | null) => {
    if (!fc || !mapRef.current) return;
    const sub: FCType = { type: "FeatureCollection", features: code ? fc.features.filter((f) => f.properties.country === code) : fc.features };
    const b = bbox(sub);
    mapRef.current.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 30, duration: 600 });
  };

  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    m.easeTo({ pitch: is3d ? 45 : 0, bearing: is3d ? -10 : 0, duration: 600 });
  }, [is3d]);

  // ------------------------------------------------------------ deck layers
  const heightMax = useMemo(() => {
    if (!fc) return 1;
    const vals = fc.features.map((f) => valueOf(heightVar, f.properties.unit_id)).filter((v): v is number => v !== null);
    return Math.max(1e-9, ...vals);
  }, [fc, heightVar, valueOf]);

  const reportPoints = useMemo(() => {
    const cutoff = Date.now() - 90 * 864e5;
    const by: Record<string, Report[]> = {};
    reports.filter((r) => Date.parse(r.incident_date) >= cutoff && r.review_status !== "rejected")
      .forEach((r) => { (by[r.unit_id] ??= []).push(r); });
    return Object.entries(by).filter(([u]) => cents[u]).map(([u, rs]) => ({ unit: u, pos: cents[u], n: rs.length, rs }));
  }, [reports, cents]);

  const deliveryPoints = useMemo(() => shipments.filter((s) => cents[s.dest_unit_id]).map((s, i) => {
    const c = cents[s.dest_unit_id];
    const st = s.status === "delivered" ? "delivered" : s.scan_points.some((p) => p.status === "partially_verified") ? "partially_verified"
      : s.scan_points.some((p) => p.status === "unverified_pin_mismatch") ? "unverified_pin_mismatch" : s.status === "in_transit" ? "in_transit" : "pending";
    return { s, st, pos: [c[0] + 0.08 * Math.cos(i), c[1] + 0.08 * Math.sin(i)] as [number, number] };
  }), [shipments, cents]);

  useEffect(() => {
    const ov = overlayRef.current;
    if (!ov || !fc) return;
    const unitName = (u: string) => fc.features.find((f) => f.properties.unit_id === u)?.properties.unit_name ?? u;
    const deckLayers: any[] = [
      new GeoJsonLayer({
        id: "districts", data: fc as any, pickable: true, stroked: true, filled: true,
        extruded: is3d, wireframe: false,
        getFillColor: (f: any) => scale.color(valueOf(choro, f.properties.unit_id)),
        getLineColor: [255, 255, 255, 200], lineWidthMinPixels: 0.6,
        getElevation: (f: any) => { const v = valueOf(heightVar, f.properties.unit_id); return v === null ? 0 : 6000 + 120000 * (v / heightMax); },
        updateTriggers: { getFillColor: [choro, monthIdx], getElevation: [heightVar, monthIdx, heightMax] },
        onHover: (info: any) => {
          if (!info.object) return setHover(null);
          const u = info.object.properties.unit_id;
          const v = valueOf(choro, u);
          const f = isRisk ? forecast?.values[u] : null;
          setHover({
            x: info.x, y: info.y, html: (
              <>
                <b>{info.object.properties.unit_name}</b>
                <span className="muted">{info.object.properties.adm1_name}, {info.object.properties.country_name}</span>
                <div>{choro === "ucdp_fatalities" ? "UCDP fatalities" : CHORO_OPTIONS.find((o) => o.id === choro)?.label}: {v === null ? <span className="muted">no data</span> : <b style={{ display: "inline" }}>{scale.format(v)}</b>}</div>
                {choro === "ucdp_fatalities" && acledOn && month && (() => {
                  const a = acledIdx.get(`${u}|${month}`);
                  return a ? <div>ACLED: <b style={{ display: "inline" }}>{a.acled_events}</b> event{a.acled_events === 1 ? "" : "s"}{a.acled_fatalities !== undefined && <>, {a.acled_fatalities} fatalit{a.acled_fatalities === 1 ? "y" : "ies"}</>}</div>
                    : <div className="small muted">ACLED: no data this month{acledRange ? ` (covers ${acledRange[0]} to ${acledRange[1]})` : ""}</div>;
                })()}
                {f && <div className="small">Interval {scales.risk.format(f.lo)}–{scales.risk.format(f.hi)} · forecast for {forecast!.target_month} from data to {forecast!.origin}. Estimate.</div>}
                {!isRisk && month && <div className="small muted">{month}{layerObserved.has(month) ? "" : " · no observation this month"}</div>}
                {is3d && <div className="small muted">Height: {CHORO_OPTIONS.find((o) => o.id === heightVar)?.label} = {(() => { const h = valueOf(heightVar, u); return h === null ? "no data" : (heightVar.startsWith("risk") ? scales.risk : scales[heightVar]).format(h); })()}</div>}
                <div className="small muted">Click for details</div>
              </>
            ),
          });
        },
        onClick: (info: any) => info.object && setSelected(info.object.properties.unit_id),
      }),
    ];
    if (showReports) deckLayers.push(new ScatterplotLayer({
      id: "reports", data: reportPoints, pickable: true, radiusUnits: "pixels",
      getPosition: (d: any) => d.pos, getRadius: (d: any) => 5 + 3 * Math.sqrt(d.n),
      getFillColor: hexToRgba(REPORT_COLOR, 235), stroked: true, getLineColor: [255, 255, 255, 255], lineWidthMinPixels: 2,
      parameters: { depthTest: false },
      onHover: (info: any) => setHover(info.object ? {
        x: info.x, y: info.y, html: (<>
          <b>{info.object.n} field report{info.object.n > 1 ? "s" : ""} (90 days)</b>
          <span className="muted">{unitName(info.object.unit)}</span>
          {Object.entries(info.object.rs.reduce((a: Record<string, number>, r: Report) => ({ ...a, [r.type]: (a[r.type] || 0) + 1 }), {}))
            .map(([t, n]) => <div key={t} className="small">{String(n)} × {t.replace(/_/g, " ")}</div>)}
        </>),
      } : null),
      onClick: (info: any) => info.object && setSelected(info.object.unit),
    }));
    if (showDeliveries) deckLayers.push(new ScatterplotLayer({
      id: "deliveries", data: deliveryPoints, pickable: true, radiusUnits: "pixels", getRadius: 6,
      getPosition: (d: any) => d.pos, getFillColor: (d: any) => hexToRgba(DELIVERY_STATUS[d.st].color, 255),
      stroked: true, getLineColor: [255, 255, 255, 255], lineWidthMinPixels: 2, parameters: { depthTest: false },
      onHover: (info: any) => setHover(info.object ? {
        x: info.x, y: info.y, html: (<>
          <b>{DELIVERY_STATUS[info.object.st].icon} {info.object.s.code}</b>
          <div>{DELIVERY_STATUS[info.object.st].label}</div>
          <div className="small muted">{info.object.s.dest_site} · due {info.object.s.window_end.slice(0, 10)}</div>
        </>),
      } : null),
    }));
    ov.setProps({ layers: deckLayers });
  }, [fc, choro, monthIdx, is3d, heightVar, heightMax, showReports, showDeliveries, reportPoints, deliveryPoints, valueOf, scale, isRisk, forecast, month, layerObserved, acledIdx, acledOn, acledRange]);

  // ------------------------------------------------------------ render
  const years = months.filter((m) => m.endsWith("-01")).map((m) => m.slice(0, 4));
  const selectable = (i: number) => (isRisk ? forecastMonths.has(months[i]) : true);

  return (
    <div className="mapwrap">
      <aside className="layerpanel" aria-label="Map layers">
        <fieldset>
          <legend>Colour (one at a time)</legend>
          {CHORO_OPTIONS.map((o) => (
            <label key={o.id} className="opt">
              <input type="radio" name="choro" checked={choro === o.id} onChange={() => setChoro(o.id)} />
              {o.label}<span className="src">{o.src}</span>
            </label>
          ))}
          {choro === "ucdp_fatalities" && <p className="muted small">Colour: UCDP fatalities. {acledOn ? "ACLED events are shown alongside on hover and in district details." : "ACLED is not enabled for this deployment."}</p>}
        </fieldset>
        <fieldset>
          <legend>Overlays</legend>
          <label className="opt"><input type="checkbox" checked={showReports} onChange={(e) => setShowReports(e.target.checked)} />
            <span className="dot-inline" style={{ color: REPORT_COLOR }}>●</span> Field reports (90 days)</label>
          <label className="opt"><input type="checkbox" checked={showDeliveries} onChange={(e) => setShowDeliveries(e.target.checked)} />
            Deliveries by status</label>
        </fieldset>
        <fieldset>
          <legend>3D</legend>
          <p className="muted small">2D is the default because flat colour reads more accurately. In 3D, height shows a second variable.</p>
          {is3d && (
            <label className="field">Height
              <select value={heightVar} onChange={(e) => setHeightVar(e.target.value as Choro)}>
                {HEIGHT_VARS.map((h) => <option key={h} value={h}>{CHORO_OPTIONS.find((o) => o.id === h)?.label}</option>)}
              </select>
            </label>
          )}
        </fieldset>
        <button className="btn" onClick={() => setReportFor(null)}>+ Add field report</button>
        <p className="muted small">{cfg.disclaimer} All layers are at district (admin-2) resolution.</p>
        {err && <p className="error">{err}</p>}
      </aside>

      <div className="mapcol">
        <div className="map" onMouseLeave={() => setHover(null)}>
          <div ref={mapEl} className="mapcanvas" />
          <div className="maptools">
            <select className="btn small" aria-label="Zoom to country" defaultValue="" onChange={(e) => zoomTo(e.target.value || null)}>
              <option value="">All countries</option>
              {countries.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
            </select>
            <div className="seg" role="group" aria-label="2D or 3D">
              <button aria-pressed={!is3d} onClick={() => setIs3d(false)}>2D</button>
              <button aria-pressed={is3d} onClick={() => setIs3d(true)}>3D</button>
            </div>
          </div>
          <Legend title={choro === "ucdp_fatalities" ? "UCDP fatalities" : CHORO_OPTIONS.find((o) => o.id === choro)!.label}
            items={scale.legend} showReports={showReports} showDeliveries={showDeliveries}
            note={isRisk && forecast ? `Forecast for ${forecast.target_month}, from data to ${forecast.origin}` : undefined} />
          {!basemapOk && <div className="basemapnote notice">Basemap unavailable: district boundaries only.</div>}
          {hover && <div className="tooltip" style={{ left: hover.x + 14, top: hover.y + 14 }}>{hover.html}</div>}
          {selected && <DistrictPanel unitId={selected} me={me} onClose={() => setSelected(null)} onAddReport={(u) => setReportFor(u)}
            conflict={{
              months: months.slice(-12),
              ucdp: (layers?.layers.ucdp_fatalities?.values[selected] ?? []).slice(-12) as (number | null)[],
              acled: acled && acled !== "off" ? acled.rows.filter((r) => r.unit_id === selected) : null,
              acledAttribution: acled && acled !== "off" ? acled.attribution : null,
            }} />}
        </div>

        <div className="timeline" aria-label="Time">
          <div className="head">
            <b className="num">{month ?? "…"}</b>
            {month && (isRisk ? <span className="badge forecast">Forecast</span>
              : layerObserved.has(month) ? <span className="badge">Observed</span> : <span className="badge">No data this month</span>)}
            {isRisk && <span className="muted small">Risk layers show the forecast target month only. Forecast months are striped.</span>}
            <div className="key">
              <span><i style={{ background: "var(--line-strong)" }} />observed</span>
              <span><i style={{ background: "repeating-linear-gradient(135deg, var(--forecast) 0 2px, transparent 2px 4px)", outline: "1px dashed var(--forecast)" }} />forecast</span>
              <span><i style={{ background: "var(--line)" }} />no data</span>
            </div>
          </div>
          <div className="track">
            <div className="cells" aria-hidden>
              {months.map((m, i) => (
                <div key={m} title={m} className={`cell ${i === monthIdx ? "sel" : forecastMonths.has(m) ? "fc" : layerObserved.has(m) ? "obs" : ""}`} />
              ))}
            </div>
            <input type="range" min={0} max={Math.max(0, months.length - 1)} value={Math.max(0, monthIdx)} aria-label="Month"
              aria-valuetext={month} onChange={(e) => { const i = +e.target.value; if (selectable(i)) setMonthIdx(i); }} />
          </div>
          <div className="years">{years.map((y) => <span key={y}>{y}</span>)}</div>
        </div>
      </div>

      {reportFor !== undefined && (
        <ReportModal cfg={cfg} unitId={reportFor} onClose={() => setReportFor(undefined)} onSaved={() => { setReportFor(undefined); loadReports(); }} />
      )}
    </div>
  );
}

function Legend({ title, items, note, showReports, showDeliveries }: {
  title: string; items: { color: string; label: string }[]; note?: string; showReports: boolean; showDeliveries: boolean;
}) {
  return (
    <div className="legend" aria-label="Legend">
      <div className="title">{title}</div>
      {items.map((i) => <div key={i.label}><span className="sw" style={{ background: i.color }} />{i.label}</div>)}
      <div><span className="sw" style={{ background: NO_DATA_HEX }} />No data</div>
      {note && <div className="small muted" style={{ marginTop: 4 }}>{note}</div>}
      {(showReports || showDeliveries) && <div style={{ marginTop: 6, borderTop: "1px solid var(--line)", paddingTop: 6 }}>
        {showReports && <div><span className="dot" style={{ background: REPORT_COLOR }} />Field reports (size = count)</div>}
        {showDeliveries && Object.values(DELIVERY_STATUS).map((d) => (
          <div key={d.label}><span className="dot" style={{ background: d.color }} />{d.icon} {d.label}</div>
        ))}
      </div>}
    </div>
  );
}
