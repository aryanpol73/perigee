// How far off a position forecast from one snapshot of public orbit data typically
// is, by how far past the snapshot it looks. LEO objects, measured on the 2025
// Space-Track history by scripts/forecast_error.py (docs/forecast-uncertainty.md):
// half the objects are closer than the median, one in ten is further than the p90.

// [days after the snapshot, median km, 90th percentile km]
export const LEO_ERROR_KM: readonly (readonly [number, number, number])[] = [
  [0.25, 0.9, 18],
  [0.5, 1.1, 22],
  [1, 1.6, 34],
  [2, 2.9, 68],
  [3, 4.9, 120],
  [4, 6.8, 180],
  [5, 9.5, 257],
  [6, 13.6, 347],
  [7, 17.3, 477],
];

/** Past this many days the forecast is rough (the screening treats days 4-7 the same way). */
export const ROUGH_AFTER_DAYS = 3;

/** Typical (median) and 1-in-10 (p90) position error in km at `leadMs` after the snapshot. */
export function forecastErrorKm(leadMs: number): { median: number; p90: number } {
  const d = leadMs / 86_400_000;
  const t = LEO_ERROR_KM;
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

/** Rounded so it never looks more precise than it is: 2, 15, 120. */
export function roughKm(km: number): number {
  return km < 10 ? Math.max(1, Math.round(km)) : km < 100 ? Math.round(km / 5) * 5 : Math.round(km / 10) * 10;
}

/** "±~2 km", "±~15 km", "±~120 km" */
export function errorLabel(km: number): string {
  return `±~${roughKm(km)} km`;
}
