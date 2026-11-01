"""Validate the shell risk index and the breakup simulator (build guide 9.2-9.4).

Writes docs/risk-validation.md, docs/figures/shell_risk.svg (+ .csv, the chart's
table view) and docs/figures/breakup_cloud.svg (+ .csv).

Checks:
* shape: the index peaks where the catalog is crowded (constellation shells
  around 450-600 km, older constellations and breakup debris around 700-1,000 km,
  OneWeb and Globalstar around 1,100-1,500 km);
* stability: monthly over 2025 from the Space-Track history;
* sensitivity: band width, size mapping, relative-speed method;
* simulator: Iridium 33 / Cosmos 2251-like collision, point -> ring -> shell,
  mass budget, what-if index change.

Usage: python scripts/validate_risk.py  (needs the 2025 Space-Track history)
"""

from __future__ import annotations

import sys
import time
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import find_peaks
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Settings
from src.orbits.elements import EARTH_RADIUS_KM
from src.risk.shells import ShellConfig, prepare_objects, shell_index, what_if
from src.screening.run import latest_catalog, load_satcat
from src.sim.breakup import LABEL, Collision, as_catalog_objects, positions_at, simulate, spread
from src.storage.elsets import ElsetHistory

DOCS = ROOT / "docs"
FIG = DOCS / "figures"
REFERENCE_DATE = pd.Timestamp("2025-12-31", tz="UTC")
STABILITY_DATES = [pd.Timestamp(f"2025-{m:02d}-01", tz="UTC") for m in range(2, 13)] + [REFERENCE_DATE]
EXPECTED = [("constellation shells", 450, 600), ("older constellations and breakup debris", 700, 1_000),
            ("OneWeb / Globalstar", 1_100, 1_500)]
CLOUD_HOURS = (5 / 60, 24.0, 24.0 * 365)

STYLE = """
  .surface { fill: #fcfcfb; }
  .ink { fill: #0b0b0b; } .ink2 { fill: #52514e; } .muted { fill: #898781; }
  .grid { stroke: #e1e0d9; } .axis { stroke: #c3c2b7; } .earth { fill: #e1e0d9; }
  .l0 { stroke: #2a78d6; } .l1 { stroke: #eb6834; } .f0 { fill: #2a78d6; } .f1 { fill: #eb6834; }
  @media (prefers-color-scheme: dark) {
    .surface { fill: #1a1a19; }
    .ink { fill: #f0efec; } .ink2 { fill: #c3c2b7; }
    .grid { stroke: #2c2c2a; } .axis { stroke: #383835; } .earth { fill: #2c2c2a; }
    .l0 { stroke: #3987e5; } .l1 { stroke: #d95926; } .f0 { fill: #3987e5; } .f1 { fill: #d95926; }
  }
  text { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  .tick { font-size: 11px; font-variant-numeric: tabular-nums; }
"""


def peaks(res: pd.DataFrame) -> pd.DataFrame:
    log_index = np.log10(res["risk_index"].clip(lower=1e-6)).to_numpy()
    idx, _ = find_peaks(np.r_[-6, log_index, -6], prominence=0.3)
    return res.iloc[idx - 1]


def to_50km(res: pd.DataFrame) -> pd.Series:
    """Highest index within each 50 km region, so different band widths compare."""
    return res.groupby((res["alt_lo_km"] // 50) * 50)["risk_index"].max()


def top_regions(res: pd.DataFrame, k: int = 5) -> str:
    s = to_50km(res).nlargest(k)
    return ", ".join(f"{int(a)}-{int(a) + 50}" for a in s.index)


# --- Charts ----------------------------------------------------------------------


def shell_chart(series: list[tuple[str, pd.DataFrame]]) -> str:
    width, left, right, top, plot_h = 960, 64, 40, 110, 300
    plot_w = width - left - right
    height = top + plot_h + 60
    lo, hi = 200, 2_000
    y_lo, y_hi = -3, 2  # log10 of index: 0.001 .. 100

    def x_of(alt: float) -> float:
        return left + (alt - lo) / (hi - lo) * plot_w

    def y_of(v: float) -> float:
        lv = np.log10(max(v, 10.0**y_lo))
        return top + plot_h - (lv - y_lo) / (y_hi - y_lo) * plot_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        'role="img" aria-labelledby="t d">',
        "<title id='t'>Shell risk index by altitude</title>",
        "<desc id='d'>Relative collision-rate index per 25 km band, log scale, for two catalogs. "
        "Numbers are in shell_risk.csv.</desc>",
        f"<style>{STYLE}</style>",
        f'<rect class="surface" width="{width}" height="{height}" rx="8"/>',
        f'<text class="ink" x="{left}" y="32" font-size="17" font-weight="600">Shell risk index per 25 km band '
        "(100 = riskiest band in each catalog)</text>",
        f'<text class="ink2" x="{left}" y="54" font-size="12.5">Log scale; values below 0.001 sit on the floor. '
        "A relative index from public data, not a collision probability. Data: CelesTrak; Space-Track.org (USSPACECOM).</text>",
    ]
    for p in range(y_lo, y_hi + 1):
        y = y_of(10.0**p)
        css = "axis" if p == y_lo else "grid"
        parts.append(f'<line class="{css}" x1="{left}" x2="{left + plot_w}" y1="{y:.1f}" y2="{y:.1f}" stroke-width="1"/>')
        label = f"{10.0**p:g}"
        parts.append(f'<text class="muted tick" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{label}</text>')
    for alt in range(lo, hi + 1, 200):
        parts.append(f'<text class="muted tick" x="{x_of(alt):.1f}" y="{top + plot_h + 18}" text-anchor="middle">{alt:,}</text>')
    parts.append(
        f'<text class="ink2" x="{left + plot_w / 2:.1f}" y="{top + plot_h + 40}" font-size="12" text-anchor="middle">'
        "Altitude (km)</text>"
    )
    for i, (name, res) in enumerate(series):
        pts = [(x_of((r.alt_lo_km + r.alt_hi_km) / 2), y_of(r.risk_index), r) for r in res.itertuples()]
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts)
        parts.append(f'<polyline class="l{i}" fill="none" stroke-width="2" stroke-linejoin="round" points="{path}"/>')
        for x, y, r in pts:  # invisible, larger hover targets
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="transparent"><title>{escape(name)}: '
                f"{r.alt_lo_km:.0f}-{r.alt_hi_km:.0f} km, index {r.risk_index:.3g}, {r.objects:,.0f} objects</title></circle>"
            )
        # Legend row, plus a short direct label on the line in the quiet region near 1,650 km.
        ly = 74 + i * 18
        parts.append(f'<line class="l{i}" x1="{left}" x2="{left + 18}" y1="{ly}" y2="{ly}" stroke-width="2"/>')
        parts.append(f'<text class="ink2" x="{left + 24}" y="{ly + 4}" font-size="12">{escape(name)}</text>')
        at = res.loc[(res["alt_lo_km"] - 1_650).abs().idxmin()]
        parts.append(
            f'<text class="ink2" x="{x_of(1_660):.1f}" y="{y_of(at.risk_index) - 6 + i * 4:.1f}" font-size="11">'
            f"{escape(name.split(',')[0])}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


def cloud_chart(fragments: pd.DataFrame, max_points: int = 2_000) -> str:
    """Fragments in sky coordinates (right ascension vs declination) at three moments."""
    rng = np.random.default_rng(0)
    pick = rng.choice(len(fragments), min(max_points, len(fragments)), replace=False)
    sub = fragments.iloc[pick]
    titles = ("5 minutes: a compact cloud", "1 day: rings along both parents' orbits", "1 year: a shell around Earth")
    width, left, top, panel_w, panel_h, gap = 960, 40, 96, 280, 170, 30
    height = top + panel_h + 74
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        'role="img" aria-labelledby="t d">',
        "<title id='t'>Simulated debris cloud spreading after a collision at 800 km, 74 deg</title>",
        f"<desc id='d'>{escape(LABEL)} Fragment positions by right ascension and declination at 15 minutes, "
        "1 day and 1 year. Numbers are in breakup_cloud.csv.</desc>",
        f"<style>{STYLE}</style>",
        f'<rect class="surface" width="{width}" height="{height}" rx="8"/>',
        f'<text class="ink" x="{left}" y="32" font-size="17" font-weight="600">Simulated collision at 800 km, 74 deg: '
        "fragments spread from a point to a ring to a shell</text>",
        f'<text class="ink2" x="{left}" y="54" font-size="12.5" font-weight="600">{escape(LABEL)}</text>',
        f'<text class="ink2" x="{left}" y="72" font-size="12.5">{len(sub):,} of {len(fragments):,} simulated fragments '
        "(10 cm and larger), two-body + J2 motion, no drag. Each dot is one fragment's position in the sky.</text>",
    ]
    for k, (hours, title) in enumerate(zip(CLOUD_HOURS, titles, strict=True)):
        x0 = left + k * (panel_w + gap)
        r = positions_at(sub, hours)
        ra = np.degrees(np.arctan2(r[:, 1], r[:, 0])) % 360
        dec = np.degrees(np.arcsin(r[:, 2] / np.linalg.norm(r, axis=1)))
        s = spread(fragments, hours)
        parts.append(f'<text class="ink" x="{x0}" y="{top - 8}" font-size="13" font-weight="600">{escape(title)}</text>')
        parts.append(f'<rect class="earth" x="{x0}" y="{top}" width="{panel_w}" height="{panel_h}" rx="4" opacity="0.35"/>')
        for d in (-90, 0, 90):
            y = top + panel_h / 2 - d / 180 * panel_h
            parts.append(f'<line class="grid" x1="{x0}" x2="{x0 + panel_w}" y1="{y:.1f}" y2="{y:.1f}" stroke-width="1"/>')
        dots = "".join(
            f'<circle cx="{x0 + a / 360 * panel_w:.1f}" cy="{top + panel_h / 2 - b / 180 * panel_h:.1f}" r="1.4"/>'
            for a, b in zip(ra, dec, strict=True)
        )
        parts.append(f'<g class="f0" opacity="0.7">{dots}</g>')
        parts.append(
            f'<text class="ink2" x="{x0}" y="{top + panel_h + 18}" font-size="12">along the orbit '
            f"{s['along_track_coverage']:.0%}, orbit orientations {s['node_coverage']:.0%}</text>"
        )
    parts.append(
        f'<text class="muted tick" x="{left}" y="{top + panel_h + 40}">x: right ascension 0-360 deg; '
        "y: declination -90 to +90 deg</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts)


# --- Report ------------------------------------------------------------------------


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    rows += ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(rows)


def main() -> int:
    started = time.monotonic()
    settings = Settings.from_env()
    satcat = load_satcat(settings)
    history = ElsetHistory(settings.processed_dir)
    try:
        catalogs = {t: history.catalog_at(t) for t in STABILITY_DATES}
    finally:
        history.con.close()
    snap = latest_catalog(settings)
    snap_label = pd.Timestamp(snap["snapshot_time"].iloc[0]).strftime("%Y-%m-%d %H:%M UTC")
    ref_cat = catalogs[REFERENCE_DATE]
    ref_obj = prepare_objects(ref_cat, satcat)
    ref = shell_index(ref_obj)
    live = shell_index(prepare_objects(snap, satcat))

    # Shape.
    shape_rows, ok_all = [], True
    pk = peaks(ref)
    top_band = ref.loc[ref["risk_index"].idxmax()]
    for name, lo, hi in EXPECTED:
        inside = pk[(pk["alt_lo_km"] >= lo) & (pk["alt_lo_km"] < hi)]
        ok = not inside.empty
        if name == "constellation shells":
            ok = ok and lo <= top_band["alt_lo_km"] < hi
        ok_all &= ok
        where = ", ".join(f"{int(r.alt_lo_km)}-{int(r.alt_hi_km)} km (index {r.risk_index:.3g})" for r in inside.itertuples())
        shape_rows.append((f"{name}, {lo}-{hi} km", where or "none", "pass" if ok else "FAIL"))

    # Stability.
    stab, prev = [], None
    for t, cat in catalogs.items():
        res = shell_index(prepare_objects(cat, satcat))
        top = res.loc[res["risk_index"].idxmax()]
        rho = float("nan")
        if prev is not None:
            both = (res["objects"] > 0) & (prev["objects"] > 0)
            rho = spearmanr(res.loc[both, "rate_per_year"], prev.loc[both, "rate_per_year"]).statistic
        stab.append((f"{t:%Y-%m-%d}", f"{len(cat):,}", f"{int(top.alt_lo_km)}-{int(top.alt_hi_km)}",
                     top_regions(res, 3), "-" if np.isnan(rho) else f"{rho:.3f}"))
        prev = res
    rhos = [float(r[4]) for r in stab if r[4] != "-"]

    # Sensitivity (end-2025 catalog).
    base50 = to_50km(ref)
    variants = [
        ("baseline: 25 km bands, default sizes, sampled speed", ShellConfig(), ref_obj),
        ("10 km bands", ShellConfig(band_km=10), ref_obj),
        ("50 km bands", ShellConfig(band_km=50), ref_obj),
        ("unknown sizes -> MEDIUM", ShellConfig(size_mapping="unknown_medium"), prepare_objects(ref_cat, satcat, "unknown_medium")),
        ("constant 10 km/s relative speed", ShellConfig(rel_speed="constant"), ref_obj),
    ]
    sens = []
    for name, cfg, objs in variants:
        res = shell_index(objs, cfg)
        r50 = to_50km(res).reindex(base50.index)
        rho = spearmanr(r50, base50, nan_policy="omit").statistic
        sens.append((name, top_regions(res), f"{rho:.3f}"))

    # Simulator.
    collision = Collision(altitude_km=800, inclination_deg=86.4, target_mass_kg=950, impactor_mass_kg=560, rel_speed_km_s=11.7)
    b = simulate(collision)
    demo = simulate(Collision(altitude_km=800, inclination_deg=74.0))
    frag = demo.fragments
    by_inc = []
    for inc in (53.0, 74.0, 86.4, 98.0):
        f = simulate(Collision(altitude_km=800, inclination_deg=inc)).fragments
        by_inc.append((f"{inc:g}", *(f"{spread(f, 24 * d)['node_coverage']:.0%}" for d in (90, 365, 730))))
    spread_rows = [
        (f"{h:g} h", f"{s['cloud_radius_km']:,.0f}", f"{s['along_track_coverage']:.0%}", f"{s['node_coverage']:.0%}")
        for h in (0.25, 1, 6, 24, 24 * 7, 24 * 30, 24 * 90, 24 * 365)
        for s in [spread(frag, h)]
    ]
    alt_rows = []
    for h in (0.25, 24, 24 * 365):
        alt = np.linalg.norm(positions_at(frag, h), axis=1) - EARTH_RADIUS_KM
        alt_rows.append((f"{h:g} h", f"{np.percentile(alt, 5):,.0f}", f"{np.median(alt):,.0f}", f"{np.percentile(alt, 95):,.0f}"))
    wi = what_if(ref_obj, as_catalog_objects(b.fragments))
    wi = wi[wi["risk_index_before"] > 0.01].copy()  # ignore near-empty bands, where any change is huge
    wi["change_pct"] = 100 * (wi["risk_index_after"] / wi["risk_index_before"] - 1)
    wi_top = wi.nlargest(5, "change_pct")

    # Figures and tables.
    FIG.mkdir(parents=True, exist_ok=True)
    (FIG / "shell_risk.svg").write_text(
        shell_chart([(f"{REFERENCE_DATE:%Y-%m-%d}, Space-Track history ({len(ref_cat):,} objects)", ref),
                     (f"{snap_label}, CelesTrak ({len(snap):,} objects)", live)]),
        encoding="utf-8",
    )
    table = ref[["alt_lo_km", "alt_hi_km", "objects", "mean_rel_speed_km_s", "risk_index", "risk_index_weighted"]].merge(
        live[["alt_lo_km", "objects", "risk_index", "risk_index_weighted"]], on="alt_lo_km", suffixes=("_2025_12_31", "_latest")
    )
    table.round(4).to_csv(FIG / "shell_risk.csv", index=False)
    (FIG / "breakup_cloud.svg").write_text(cloud_chart(frag), encoding="utf-8")
    pd.DataFrame(spread_rows, columns=["after", "cloud_radius_km", "along_track_coverage", "node_coverage"]).to_csv(
        FIG / "breakup_cloud.csv", index=False
    )

    s = b.summary()
    lines = [
        "# Shell risk index and breakup simulator: validation",
        "",
        f"Generated by `scripts/validate_risk.py` in {time.monotonic() - started:.0f} s. Data from CelesTrak and "
        "Space-Track.org (USSPACECOM). The index is a relative measure from public data, never a probability; "
        f"the simulator is {LABEL[0].lower() + LABEL[1:]}",
        "",
        "![Shell risk index](figures/shell_risk.svg)",
        "",
        "## Method in one paragraph",
        "",
        "25 km bands from 200 to 2,000 km. Each orbit is sampled at 72 equal time steps over one revolution and "
        "credits each band with the share of time spent in it (eccentric orbits spend longer near apogee). Mean "
        "relative speed per band comes from 4,000 random pairs meeting at a latitude both orbits reach. "
        "Cross-sections come from SATCAT radar size classes (small 0.1 m, medium 0.4 m, large 1.5 m radius); "
        "objects without a size class are assumed large if payload or rocket body and small if debris. Rate = "
        "1/2 n^2 sigma v V, scaled so the riskiest band is 100. The weighted variant weights objects by mass^0.75.",
        "",
        "## Shape check",
        "",
        f"End-2025 full catalog ({len(ref_cat):,} objects from the Space-Track history). Peaks are local maxima "
        f"of the log index. Riskiest band: {int(top_band.alt_lo_km)}-{int(top_band.alt_hi_km)} km.",
        "",
        md_table(pd.DataFrame(shape_rows, columns=["Expected region", "Peaks found", "Result"])),
        "",
        f"Overall: **{'pass' if ok_all else 'FAIL'}**. These are the regions ESA's annual Space Environment Report "
        "shows as densest; this matches shape only, not ESA's numbers.",
        "",
        f"The latest CelesTrak snapshot ({snap_label}, {len(snap):,} objects; most small debris is not in the "
        f"groups we fetch) peaks at {int(live.loc[live['risk_index'].idxmax(), 'alt_lo_km'])}-"
        f"{int(live.loc[live['risk_index'].idxmax(), 'alt_hi_km'])} km: during 2026 Starlink moved many satellites "
        "from about 550 km down to about 480 km, so the top band moved with them.",
        "",
        "## Stability check (monthly, 2025)",
        "",
        "Spearman rank correlation of the per-band rate with the previous month. A sound index changes slowly.",
        "",
        md_table(pd.DataFrame(stab, columns=["Date", "Objects", "Top band (km)", "Top 3 regions (50 km)", "Rank corr. vs previous"])),
        "",
        f"Month-to-month rank correlation: min {min(rhos):.3f}, median {np.median(rhos):.3f}. The top band moves only "
        "when constellation shells are raised or lowered, which is a real change, not a data bug.",
        "",
        "## Sensitivity (end-2025 catalog)",
        "",
        "Each variant's five riskiest 50 km regions, and the rank correlation of its index with the baseline on a "
        "common 50 km grid (highest band in each region).",
        "",
        md_table(pd.DataFrame(sens, columns=["Variant", "Top 5 regions (km)", "Rank corr. vs baseline"])),
        "",
        "The ranking of the crowded regions survives every variant; the index is a modelling choice, and the "
        "size mapping for objects without a radar size class is its weakest input.",
        "",
        "## Breakup simulator",
        "",
        f"**{LABEL}**",
        "",
        "Iridium 33 / Cosmos 2251-like collision: 560 kg and 950 kg at 11.7 km/s, 800 km, 86.4 deg.",
        "",
        f"- Specific energy {s['specific_energy_j_per_g']:,} J/g, so catastrophic (threshold 40 J/g).",
        f"- NASA breakup model: **{s['modelled_fragments_over_min_lc']:,} fragments of 10 cm or larger**. The real "
        "2009 collision produced roughly 2,300 catalogued fragments, so the model gives about half: the same order "
        "of magnitude, which is what the guide asks of this check.",
        f"- Simulated {s['simulated_fragments']:,} fragments kept in orbit; {s['lost_immediately']['reentered_at_once']} "
        "got perigees below 100 km and are dropped.",
        f"- Mass: {s['input_mass_kg']:,.0f} kg in; {s['fragment_mass_kg']:,.0f} kg in simulated fragments; "
        f"{s['unresolved_mass_kg']:,.0f} kg unresolved (fragments below 10 cm and remnants); the remaining "
        f"{s['input_mass_kg'] - s['fragment_mass_kg'] - s['unresolved_mass_kg']:,.0f} kg went with the fragments that "
        "reentered at once. Fragments are dropped at random until the total is within the input mass.",
        "",
        "![Debris cloud](figures/breakup_cloud.svg)",
        "",
        "Spread over time for the demo collision (same masses and speed, 800 km, both orbits at 74 deg; "
        "`spread()` in `src/sim/breakup.py`):",
        "",
        md_table(pd.DataFrame(spread_rows, columns=["After", "Cloud radius (km)", "Along-orbit coverage", "Orbit-orientation coverage"])),
        "",
        "Point within minutes, ring within a day (fragments with slightly different periods drift apart along the "
        "orbit), shell over months (the orbit planes precess at different rates under J2). The guide expects the "
        "shell within days to weeks; with secular J2 and these delta-v values it takes months, and longer the "
        "closer the orbits are to polar, because J2 precession scales with cos(inclination):",
        "",
        md_table(pd.DataFrame(by_inc, columns=["Inclination (deg)", "Orientation coverage at 90 d", "at 1 year", "at 2 years"])),
        "",
        "Altitude of the demo fragments (km):",
        "",
        md_table(pd.DataFrame(alt_rows, columns=["After", "5th percentile", "Median", "95th percentile"])),
        "",
        "What-if: the Iridium-Cosmos-like fragments added to the end-2025 catalog, scaled to the baseline's riskiest band; the five "
        "bands whose index rises most (relative):",
        "",
        md_table(wi_top[["alt_lo_km", "alt_hi_km", "risk_index_before", "risk_index_after", "change_pct"]].round(3)),
        "",
        "## Limits",
        "",
        "- No drag: low fragments do not decay, so long-term altitude spread is overstated below about 600 km.",
        "- Both parents start on circular orbits of the same inclination, one heading north and one south; "
        "real geometry differs.",
        "- The index counts each object's time in a band, not its actual encounters; active satellites "
        "manoeuvre to avoid collisions, which the index ignores.",
        "",
    ]
    (DOCS / "risk-validation.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"shape {'pass' if ok_all else 'FAIL'}; stability min rho {min(rhos):.3f}; wrote docs/risk-validation.md")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
