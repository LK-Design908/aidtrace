import type { FC } from "./api";

/** Area-weighted centroid of the largest polygon of each feature (for district-level markers). */
export function centroids(fc: FC): Record<string, [number, number]> {
  const out: Record<string, [number, number]> = {};
  for (const f of fc.features) {
    const polys: number[][][][] = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
    let best: [number, number] | null = null;
    let bestA = -1;
    for (const poly of polys) {
      const ring = poly[0];
      let a = 0, cx = 0, cy = 0;
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const cross = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
        a += cross;
        cx += (ring[j][0] + ring[i][0]) * cross;
        cy += (ring[j][1] + ring[i][1]) * cross;
      }
      if (Math.abs(a) > bestA && a !== 0) {
        bestA = Math.abs(a);
        best = [cx / (3 * a), cy / (3 * a)];
      }
    }
    if (best) out[f.properties.unit_id] = best;
  }
  return out;
}

export function bbox(fc: FC): [number, number, number, number] {
  let minx = 180, miny = 90, maxx = -180, maxy = -90;
  const walk = (c: any) => {
    if (typeof c[0] === "number") {
      minx = Math.min(minx, c[0]); maxx = Math.max(maxx, c[0]);
      miny = Math.min(miny, c[1]); maxy = Math.max(maxy, c[1]);
    } else c.forEach(walk);
  };
  fc.features.forEach((f) => walk(f.geometry.coordinates));
  return [minx, miny, maxx, maxy];
}
