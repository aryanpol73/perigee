// How far off a position forecast from one snapshot of public orbit data typically
// is, by how far past the snapshot it looks and what kind of orbit it is. Measured on
// the 2025 Space-Track history by scripts/forecast_error.py (docs/forecast-uncertainty.md):
// half the objects are closer than the median, one in ten is further than the p90.
// Starlink manoeuvres so often that its forecasts drift about ten times faster.

export type OrbitClass = "starlink" | "leo" | "high";

type Table = readonly (readonly [number, number, number])[]; // [days after the snapshot, median km, p90 km]

export const ERROR_KM: Record<OrbitClass, Table> = {
  starlink: [
    [0.25, 6.4, 40],
    [0.5, 7.7, 53],
    [1, 9.8, 92],
    [2, 15.4, 216],
    [3, 21.5, 406],
    [4, 28.8, 631],
    [5, 37.1, 941],
    [6, 47.8, 1269],
    [7, 58.9, 1725],
  ],
  // LEO (perigee under 2,000 km) without Starlink
  leo: [
    [0.25, 0.3, 3],
    [0.5, 0.4, 4],
    [1, 0.6, 6],
    [2, 1.0, 14],
    [3, 1.5, 26],
    [4, 2.1, 39],
    [5, 3.1, 58],
    [6, 4.7, 89],
    [7, 6.2, 117],
  ],
  high: [
    [0.25, 0.7, 6],
    [0.5, 0.8, 7],
    [1, 1.0, 8],
    [2, 1.4, 13],
    [3, 1.8, 20],
    [4, 2.2, 23],
    [5, 2.8, 31],
    [6, 3.2, 36],
    [7, 3.6, 46],
  ],
};

export const CLASS_NAMES: Record<OrbitClass, { label: string; plural: string }> = {
  starlink: { label: "Starlink", plural: "Starlink satellites" },
  leo: { label: "low orbit", plural: "low-orbit objects" },
  high: { label: "higher orbit", plural: "higher-orbit objects" },
};

/** Which error table applies to an object. */
export function orbitClass(name: string, perigeeKm: number): OrbitClass {
  if (name.toUpperCase().startsWith("STARLINK")) return "starlink";
  return perigeeKm < 2000 ? "leo" : "high";
}

/** Past this many days the forecast is rough (the screening treats days 4-7 the same way). */
export const ROUGH_AFTER_DAYS = 3;

/** Typical (median) and 1-in-10 (p90) position error in km at `leadMs` after the snapshot. */
export function forecastErrorKm(leadMs: number, cls: OrbitClass = "leo"): { median: number; p90: number } {
  const d = leadMs / 86_400_000;
  const t = ERROR_KM[cls];
  const at = (row: readonly [number, number, number]) => ({ median: row[1], p90: row[2] });
  if (d <= t[0][0]) return at(t[0]);
  for (let i = 1; i < t.length; i++) {
    if (d <= t[i][0]) {
      const f = (d - t[i - 1][0]) / (t[i][0] - t[i - 1][0]);
      return { median: t[i - 1][1] + f * (t[i][1] - t[i - 1][1]), p90: t[i - 1][2] + f * (t[i][2] - t[i - 1][2]) };
    }
  }
  return at(t[t.length - 1]);
}

/** Rounded so it never looks more precise than it is: 2, 15, 120, 1,700. */
export function roughKm(km: number): number {
  if (km < 10) return Math.max(1, Math.round(km));
  if (km < 100) return Math.round(km / 5) * 5;
  if (km < 1000) return Math.round(km / 10) * 10;
  return Math.round(km / 100) * 100;
}

/** "±~2 km", "±~15 km", "±~1,700 km" */
export function errorLabel(km: number): string {
  return `±~${roughKm(km).toLocaleString("en-US")} km`;
}
