"""Draw the object-count-by-altitude chart for the latest catalog snapshot.

Writes docs/figures/altitude_bands.svg (small multiples: one panel per object
class, each on its own y-scale, sharing the altitude axis) and the same numbers
as docs/figures/altitude_bands.csv, which is the chart's table view.

Usage: python scripts/altitude_chart.py
"""

from __future__ import annotations

import sys
from html import escape
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Settings  # noqa: E402
from src.storage.summary import OBJECT_CLASSES, altitude_band_counts  # noqa: E402

BAND_KM, LO_KM, HI_KM = 25, 200, 2_000
LABEL_COLUMN_KM = 1_000
OUT_DIR = ROOT / "docs" / "figures"
PANEL_TITLES = {
    "Payload": "Payloads",
    "Debris": "Debris",
    "Rocket body / other": "Rocket bodies and unidentified (analyst) objects",
}

# Reference palette, categorical slots 1-3 (validated light and dark).
STYLE = """
  .surface { fill: #fcfcfb; }
  .ink { fill: #0b0b0b; } .ink2 { fill: #52514e; } .muted { fill: #898781; }
  .grid { stroke: #e1e0d9; } .axis { stroke: #c3c2b7; } .leader { stroke: #898781; }
  .s0 { fill: #2a78d6; } .s1 { fill: #eb6834; } .s2 { fill: #1baf7a; }
  @media (prefers-color-scheme: dark) {
    .surface { fill: #1a1a19; }
    .ink { fill: #f0efec; } .ink2 { fill: #c3c2b7; }
    .grid { stroke: #2c2c2a; } .axis { stroke: #383835; }
    .s0 { fill: #3987e5; } .s1 { fill: #d95926; } .s2 { fill: #199e70; }
  }
  text { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  .tick { font-size: 11px; font-variant-numeric: tabular-nums; }
"""

# Annotations: (panel, CelesTrak group whose peak band to point at, label)
ANNOTATIONS = [
    ("Payload", "active", "Starlink shells"),
    ("Debris", "fengyun-1c-debris", "Fengyun-1C debris (2007 ASAT test)"),
    ("Debris", "cosmos-2251-debris", "Cosmos 2251 debris (2009 collision)"),
]


def nice_max(value: float) -> tuple[float, float]:
    """Round an axis maximum up to a clean number; return (max, tick step)."""
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000):
        if value / step <= 4:
            return step * max(1, -(-value // step)), step
    return value, value / 4


def bar_path(x: float, y: float, w: float, h: float, r: float = 3.0) -> str:
    """Bar with rounded top corners, square at the baseline."""
    r = min(r, w / 2, h)
    return (
        f"M{x:.1f},{y + h:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
        f"H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}V{y + h:.1f}Z"
    )


def render(bands: pd.DataFrame, snapshot_label: str, total: int) -> str:
    width, left, right = 960, 64, 24
    panel_h, panel_gap, top = 150, 46, 96
    plot_w = width - left - right
    n_bands = (HI_KM - LO_KM) // BAND_KM
    slot = plot_w / n_bands
    bar_w = slot - 2  # 2px surface gap between adjacent bars
    height = top + len(OBJECT_CLASSES) * (panel_h + panel_gap) + 30

    def x_of(alt: float) -> float:
        return left + (alt - LO_KM) / (HI_KM - LO_KM) * plot_w

    by_class = bands.groupby(["object_class", "band_km"])["objects"].sum()
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-labelledby="t d">',
        f"<title id='t'>Tracked objects by mean altitude, {BAND_KM} km bands</title>",
        f"<desc id='d'>Small multiples for payloads, debris and rocket bodies from {LO_KM} to "
        f"{HI_KM} km. Numbers are in altitude_bands.csv.</desc>",
        f"<style>{STYLE}</style>",
        f'<rect class="surface" width="{width}" height="{height}" rx="8"/>',
        f'<text class="ink" x="{left}" y="32" font-size="17" font-weight="600">'
        f"Where tracked objects orbit: count per {BAND_KM} km band of mean altitude</text>",
        f'<text class="ink2" x="{left}" y="54" font-size="12.5">'
        f"{total:,} objects from CelesTrak public GP data, snapshot {escape(snapshot_label)}. "
        "Each panel has its own scale.</text>",
    ]

    for i, cls in enumerate(OBJECT_CLASSES):
        y0 = top + i * (panel_h + panel_gap)
        base = y0 + panel_h
        counts = by_class.get(cls, pd.Series(dtype=int))
        ymax, step = nice_max(float(counts.max()) if len(counts) else 1.0)
        parts.append(
            f'<text class="ink" x="{left}" y="{y0 - 8}" font-size="13" font-weight="600">'
            f"{escape(PANEL_TITLES[cls])}  <tspan class='ink2' font-weight='400'>({int(counts.sum()):,})</tspan></text>"
        )
        tick = 0.0
        while tick <= ymax + 1e-9:
            ty = base - tick / ymax * panel_h
            css = "axis" if tick == 0 else "grid"
            parts.append(f'<line class="{css}" x1="{left}" x2="{width - right}" y1="{ty:.1f}" y2="{ty:.1f}" stroke-width="1"/>')
            parts.append(f'<text class="muted tick" x="{left - 8}" y="{ty + 4:.1f}" text-anchor="end">{int(tick):,}</text>')
            tick += step
        for band, n in counts.items():
            h = n / ymax * panel_h
            x = x_of(band) + 1
            parts.append(
                f'<path class="s{i}" d="{bar_path(x, base - h, bar_w, h)}">'
                f"<title>{escape(cls)}: {n:,} objects at {band}-{band + BAND_KM} km</title></path>"
            )

        # Labels sit in stacked rows in the empty space right of the peaks,
        # with a leader line to the bar each one names, so they never collide.
        row = 0
        for panel, group, label in ANNOTATIONS:
            if panel != cls:
                continue
            g = bands[(bands["object_class"] == cls) & (bands["source_group"] == group)]
            if g.empty:
                continue
            peak = int(g.groupby("band_km")["objects"].sum().idxmax())
            n = int(counts.get(peak, 0))
            bx = x_of(peak) + slot / 2
            by = base - n / ymax * panel_h
            lx = x_of(LABEL_COLUMN_KM)
            ly = y0 + 16 + row * 20
            parts.append(
                f'<polyline class="leader" fill="none" stroke-width="1" '
                f'points="{bx:.1f},{by - 3:.1f} {bx + 12:.1f},{ly - 4:.1f} {lx - 6:.1f},{ly - 4:.1f}"/>'
            )
            parts.append(
                f'<text class="ink2" x="{lx:.1f}" y="{ly:.1f}" font-size="12">{escape(label)}, '
                f"peak {peak}-{peak + BAND_KM} km</text>"
            )
            row += 1

    axis_y = top + len(OBJECT_CLASSES) * (panel_h + panel_gap) - panel_gap + 18
    for alt in range(LO_KM, HI_KM + 1, 200):
        parts.append(f'<text class="muted tick" x="{x_of(alt):.1f}" y="{axis_y}" text-anchor="middle">{alt:,}</text>')
    parts.append(
        f'<text class="ink2" x="{left + plot_w / 2:.1f}" y="{axis_y + 20}" font-size="12" '
        'text-anchor="middle">Mean altitude (km)</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    settings = Settings.from_env()
    bands = altitude_band_counts(settings.processed_dir, BAND_KM, LO_KM, HI_KM)
    if bands.empty:
        raise SystemExit("No catalog snapshot stored yet. Run: python -m src.main data")

    from src.storage.summary import catalog_summary

    summary = catalog_summary(settings.processed_dir)
    sid = summary.snapshot_id or ""
    label = f"{sid[0:4]}-{sid[4:6]}-{sid[6:8]} {sid[9:11]}:{sid[11:13]} UTC"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    table = (
        bands.groupby(["band_km", "object_class"])["objects"].sum().unstack(fill_value=0)
        .reindex(columns=list(OBJECT_CLASSES), fill_value=0)
    )
    table.index.name = "band_start_km"
    table.to_csv(OUT_DIR / "altitude_bands.csv")
    (OUT_DIR / "altitude_bands.svg").write_text(
        render(bands, label, int(bands["objects"].sum())), encoding="utf-8"
    )
    print(f"Wrote {OUT_DIR / 'altitude_bands.svg'} and altitude_bands.csv")
    print(table.sort_values("Payload", ascending=False).head(5).to_string())
    print(table.sort_values("Debris", ascending=False).head(5).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
