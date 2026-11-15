// Colour scales for map layers. Palettes follow the dataviz reference instance:
// sequential = one hue light->dark (blue); second sequential context = orange (validated ordinal ramp);
// diverging = blue <-> red with a grey midpoint; IPC uses the official IPC phase colours (domain convention).

export type RGBA = [number, number, number, number];

const hex = (h: string, a = 220): RGBA => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16), a];

export const NO_DATA: RGBA = hex("#d9d8d2", 150);
export const NO_DATA_HEX = "#d9d8d2";

export interface LegendItem { color: string; label: string }
export interface Scale { color: (v: number | null | undefined) => RGBA; legend: LegendItem[]; format: (v: number) => string }

function binned(stops: number[], colors: string[], labels: string[], format: (v: number) => string): Scale {
  return {
    color: (v) => {
      if (v === null || v === undefined || Number.isNaN(v)) return NO_DATA;
      let i = 0;
      while (i < stops.length && v >= stops[i]) i++;
      return hex(colors[Math.max(0, i - 1)]);
    },
    legend: colors.map((c, i) => ({ color: c, label: labels[i] })),
    format,
  };
}

const BLUE = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"];   // steps 100, 250, 400, 550, 700
const ORANGE = ["#efa37e", "#e57a49", "#c9521f", "#8f3410"];             // validated ordinal ramp (light & dark)

export const RISK_BANDS: Record<string, string> = { low: ORANGE[0], elevated: ORANGE[1], high: ORANGE[2], severe: ORANGE[3] };

export const scales: Record<string, Scale> = {
  need_index: binned([0, 0.2, 0.4, 0.6, 0.8], BLUE, ["0–0.2", "0.2–0.4", "0.4–0.6", "0.6–0.8", "0.8–1"], (v) => v.toFixed(2)),
  ipc_phase: binned([1, 2, 3, 4, 5], ["#cdfacd", "#fae61e", "#e67800", "#c80000", "#640000"],
    ["1 Minimal", "2 Stressed", "3 Crisis", "4 Emergency", "5 Famine"], (v) => `Phase ${v.toFixed(0)}`),
  price_anom: binned([-10, -0.2, -0.05, 0.05, 0.2], ["#256abf", "#9ec5f4", "#f0efec", "#f0a0a0", "#c63a3a"],
    ["≤ −20%", "−20 to −5%", "±5%", "+5 to +20%", "≥ +20%"], (v) => `${v >= 0 ? "+" : ""}${(100 * (Math.exp(v) - 1)).toFixed(0)}%`),
  displacement: binned([0, 1, 100, 1000, 5000], BLUE, ["0", "1–99", "100–999", "1,000–4,999", "≥ 5,000"], (v) => v.toLocaleString()),
  ucdp_fatalities: binned([0, 1, 10, 50], ORANGE, ["0 recorded", "1–9", "10–49", "≥ 50"], (v) => v.toLocaleString()),
  pop_density: binned([0, 10, 50, 200, 1000], BLUE, ["< 10", "10–49", "50–199", "200–999", "≥ 1,000 /km²"], (v) => `${Math.round(v).toLocaleString()} /km²`),
  bldg_density: binned([0, 2, 10, 50, 250], BLUE, ["< 2", "2–9", "10–49", "50–249", "≥ 250 /km²"], (v) => `${v.toFixed(1)} /km²`),
  risk: {
    color: (v) => (v === null || v === undefined ? NO_DATA : hex(v < 0.1 ? ORANGE[0] : v < 0.3 ? ORANGE[1] : v < 0.6 ? ORANGE[2] : ORANGE[3])),
    legend: [
      { color: ORANGE[0], label: "Low (<10%)" }, { color: ORANGE[1], label: "Elevated (10–30%)" },
      { color: ORANGE[2], label: "High (30–60%)" }, { color: ORANGE[3], label: "Severe (≥60%)" },
    ],
    format: (v) => (v < 0.01 ? "<1%" : `${(100 * v).toFixed(0)}%`),
  },
};

export const DELIVERY_STATUS: Record<string, { color: string; label: string; icon: string }> = {
  delivered: { color: "#0ca30c", label: "Verified delivery", icon: "✓" },
  partially_verified: { color: "#fab219", label: "Partially verified", icon: "◐" },
  in_transit: { color: "#3987e5", label: "In transit", icon: "→" },
  pending: { color: "#898781", label: "Pending", icon: "…" },
  unverified_pin_mismatch: { color: "#d03b3b", label: "Unverified (PIN mismatch)", icon: "!" },
};

export const hexToRgba = hex;
