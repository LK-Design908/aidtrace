// Thin API client. The session token lives in memory only (not localStorage), so a page reload
// requires signing in again: a deliberate trade-off for a tool handling security-sensitive data.

let token: string | null = null;
let onExpired: () => void = () => {};

export function setToken(t: string | null) {
  token = t;
}
export function onSessionExpired(cb: () => void) {
  onExpired = cb;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (res.status === 401 && token) {
    token = null;
    onExpired();
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  patch: <T>(p: string, b: unknown) => request<T>("PATCH", p, b),
};

/** Download an authenticated file (PDF/CSV) via a blob URL. */
export async function download(path: string, filename: string) {
  const res = await fetch(`/api${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ---------------------------------------------------------------- types

export type Role = "hq_admin" | "security_focal_point" | "field_staff";
export interface Me { username: string; role: Role }

export interface Feature { type: "Feature"; properties: { unit_id: string; unit_name: string; adm1_name: string; country: string; country_name: string }; geometry: any }
export interface FC { type: "FeatureCollection"; features: Feature[] }

export interface LayerData {
  label: string; source: string; observed_months: string[]; values: Record<string, (number | null)[]>;
}
export interface ForecastData {
  target_month: string; origin: string;
  values: Record<string, { prob: number; lo: number; hi: number; band: string }>;
}
export interface Layers { months: string[]; layers: Record<string, LayerData>; forecast?: Record<string, ForecastData> }

export interface Trigger { trigger: string; label: string; month: string; evidence: string; source: string }
export interface UnitSummary {
  unit_id: string; unit_name: string; adm1_name: string; country?: string; country_name?: string;
  exposure?: { population: number | null; under5: number | null; buildings: number | null; urban_share: number | null; weight_source: string };
  ipc_p3plus_basis?: string | null;
  prob?: number; prob_lo?: number; prob_hi?: number; band?: string; target_month?: string;
  data_last_month?: string; stale_months?: number;
  need_index?: number | null; need_weight_available?: number; need_components?: Record<string, number | null>;
  need_missing?: string[]; need_month?: string; ipc_phase?: number | null; high_need?: number | null;
  active_triggers: Trigger[]; field_reports_90d: Record<string, number>; quadrant: string; recommendation: string;
}
export interface UnitDetail extends UnitSummary {
  forecast_3m: { prob: number; prob_lo: number; prob_hi: number; band: string; target_month: string };
  need_trend: { month: string; need_index: number | null }[];
  recent_reports: Report[]; recent_deliveries: { code: string; status: string; flags: string[]; window_end: string; dest_site: string }[];
  disclaimer: string;
}
export interface Report {
  id: number; type: string; incident_date: string; adm2_pcode: string; unit_id: string; severity: number;
  reporter_role: string; review_status: string; has_text: boolean; free_text?: string | null; created_at: string;
}
export interface Adm2 { adm2_pcode: string; unit_id: string; adm2_name: string; adm1_pcode: string; adm1_name: string; country: string; country_name: string }
export interface CatalogueItem { code: number; key: string; label: string; cold_chain?: boolean }
export interface AppConfig {
  catalogue: CatalogueItem[]; low_profile_flags: string[]; scan_point_kinds: string[];
  field_report_types: string[]; disclaimer: string; role: Role;
}
export interface ReceiptView {
  receipt_id: number; recipient: string; device_ts: string; received_at: string; channel: string; result: string;
  quantities: { code: number; qty_received: number; damaged: number; expired: number }[];
  cold_chain: { vvm_stage?: number; indicator_triggered?: boolean } | null; notes: string | null; photo: boolean;
  pin_class?: string;
}
export interface ScanView {
  scan_point_id: number; seq: number; kind: string; status: string; required_confirmations: number;
  confirmations: number; pin_mismatches: number; first_receipt_at: string | null; receipts: ReceiptView[]; duress?: boolean;
}
export interface ShipmentView {
  id: number; code: string; status: string; origin: string; dest_adm2_pcode: string; dest_unit_id: string;
  dest_site: string; window_start: string; window_end: string; cold_chain: boolean; low_profile: string[];
  two_person_required: boolean; risk_band_at_creation: string | null;
  items: { item_code: number; item: string; quantity: number; batch: string | null; expiry: string | null }[];
  received_final: { item_code: number; dispatched: number; confirmed: number | null }[];
  recipients: string[]; scan_points: ScanView[]; flags: string[]; created_at: string; duress?: boolean;
}
export interface RecipientRow { account: string; site_name: string; adm2_pcode: string; unit_id: string; enrolled: boolean; active: boolean }
