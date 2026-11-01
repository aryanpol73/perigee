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

export function hoursAhead(h: number): string {
  if (h < 1) return `in ${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `in ${h.toFixed(0)} h`;
  return `in ${(h / 24).toFixed(1)} days`;
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
