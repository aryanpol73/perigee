// Display formatting. Risk values are estimates; wording never says "will collide".

export function utc(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

export function km(value: number, digits = 2): string {
  return `${value.toFixed(digits)} km`;
}

export function percent(p: number | null | undefined): string {
  if (p === null || p === undefined || Number.isNaN(p)) return "no estimate";
  if (p < 0.01) return "<1%";
  return `${Math.round(p * 100)}%`;
}

export function ageDays(days: number): string {
  if (days < 1) return `${Math.round(days * 24)} h old`;
  return `${days.toFixed(1)} days old`;
}

/** "in 3 h", "21 h ago": hours from now (negative = already past). */
export function hoursAhead(h: number): string {
  const a = Math.abs(h);
  const span = a < 1 ? `${Math.max(1, Math.round(a * 60))} min` : a < 48 ? `${a.toFixed(0)} h` : `${(a / 24).toFixed(1)} days`;
  return h < 0 ? `${span} ago` : `in ${span}`;
}

/** Hours from the real time to an ISO timestamp. */
export function hoursFromNow(iso: string, now: number): number {
  return (new Date(iso).getTime() - now) / 3_600_000;
}

/** Plain-language label for a risk estimate, relative to the model's operating point. */
export function riskLabel(p: number | null | undefined, operating: number | null | undefined): string {
  if (p === null || p === undefined) return "Outside model scope";
  if (operating !== null && operating !== undefined && p >= operating) return "Elevated risk estimate";
  return "Lower risk estimate";
}

export function objectTypeName(t: string | null | undefined): string {
  return { PAY: "Payload", DEB: "Debris", "R/B": "Rocket body", UNK: "Unknown" }[t ?? "UNK"] ?? "Unknown";
}
