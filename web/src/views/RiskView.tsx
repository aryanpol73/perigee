// Shell risk index by altitude (both variants), its history, and the top bands in words.
import { useMemo, useState } from "react";
import { useApi, type ShellHistory, type Shells } from "../api";

type Band = Shells["bands"][number];

const W = 400;
const H = 220;
const PAD = { l: 40, r: 10, t: 10, b: 30 };
const LO = 200;
const HI = 2000;
const Y_LO = -3; // log10 index floor (0.001)
const Y_HI = 2;

const x = (alt: number) => PAD.l + ((alt - LO) / (HI - LO)) * (W - PAD.l - PAD.r);
const y = (v: number) => H - PAD.b - ((Math.log10(Math.max(v, 10 ** Y_LO)) - Y_LO) / (Y_HI - Y_LO)) * (H - PAD.t - PAD.b);

const REGIONS: [number, number, string][] = [
  [300, 450, "constellation satellites climbing to or leaving their shells, and the space stations (about 400 km)"],
  [450, 600, "large constellation shells (mostly Starlink)"],
  [700, 1000, "older constellations and debris from the 2007 Fengyun-1C test and 2009 Iridium-Cosmos collision"],
  [1100, 1300, "the OneWeb constellation"],
  [1350, 1550, "the Globalstar constellation and older debris"],
];

function describe(b: Band): string {
  const mid = (b.alt_lo_km + b.alt_hi_km) / 2;
  const r = REGIONS.find(([lo, hi]) => mid >= lo && mid < hi);
  return r ? r[2] : "a less crowded region";
}

export function RiskView() {
  const shells = useApi<Shells>("/v1/shells");
  const history = useApi<ShellHistory>("/v1/shells/history");
  const [hover, setHover] = useState<Band | null>(null);
  const [table, setTable] = useState(false);
  const bands = useMemo(() => (shells.data?.bands ?? []).filter((b) => b.alt_lo_km >= LO && b.alt_hi_km <= HI), [shells.data]);
  const top = [...bands].sort((a, b) => b.risk_index - a.risk_index).slice(0, 3);

  const line = (key: "risk_index" | "risk_index_weighted") =>
    bands.map((b, i) => `${i ? "L" : "M"}${x((b.alt_lo_km + b.alt_hi_km) / 2).toFixed(1)},${y(b[key]).toFixed(1)}`).join(" ");

  return (
    <div className="view">
      <h2>Risk by altitude</h2>
      <p className="muted">
        A relative index per 25 km band (100 = riskiest band now), from how crowded the band is, how fast objects there would meet,
        and how big they are. It is an index, not a probability: it ignores that active satellites manoeuvre to avoid each other.
      </p>
      {shells.loading && <p>Loading…</p>}
      {shells.error && <p className="error">{shells.error.message}</p>}
      {bands.length > 0 && (
        <>
          <div className="chart-legend">
            <span><span className="line s0" aria-hidden /> all objects</span>
            <span><span className="line s1" aria-hidden /> weighted by mass (bigger debris clouds)</span>
          </div>
          <svg
            viewBox={`0 0 ${W} ${H}`}
            className="chart"
            role="img"
            aria-label="Shell risk index by altitude, log scale; a table view follows"
            onMouseLeave={() => setHover(null)}
          >
            {[-3, -2, -1, 0, 1, 2].map((p) => (
              <g key={p}>
                <line className="grid" x1={PAD.l} x2={W - PAD.r} y1={y(10 ** p)} y2={y(10 ** p)} />
                <text className="tick" x={PAD.l - 4} y={y(10 ** p) + 3} textAnchor="end">{10 ** p >= 1 ? 10 ** p : (10 ** p).toString()}</text>
              </g>
            ))}
            {[200, 600, 1000, 1400, 1800].map((a) => (
              <text key={a} className="tick" x={x(a)} y={H - PAD.b + 14} textAnchor="middle">{a.toLocaleString()}</text>
            ))}
            <text className="tick" x={(W + PAD.l) / 2} y={H - 4} textAnchor="middle">altitude (km)</text>
            <path d={line("risk_index_weighted")} className="series s1" />
            <path d={line("risk_index")} className="series s0" />
            {hover && <line className="crosshair" x1={x((hover.alt_lo_km + hover.alt_hi_km) / 2)} x2={x((hover.alt_lo_km + hover.alt_hi_km) / 2)} y1={PAD.t} y2={H - PAD.b} />}
            {bands.map((b) => (
              <rect
                key={b.alt_lo_km}
                x={x(b.alt_lo_km)}
                width={x(b.alt_hi_km) - x(b.alt_lo_km)}
                y={PAD.t}
                height={H - PAD.t - PAD.b}
                fill="transparent"
                onMouseEnter={() => setHover(b)}
              />
            ))}
          </svg>
          <p className="readout" aria-live="polite">
            {hover
              ? `${hover.alt_lo_km}–${hover.alt_hi_km} km: index ${hover.risk_index.toPrecision(3)} (weighted ${hover.risk_index_weighted.toPrecision(3)}), ${hover.objects.toFixed(0)} objects on average`
              : "Hover the chart to read a band."}
          </p>
          <button type="button" onClick={() => setTable(!table)} aria-expanded={table}>
            {table ? "Hide" : "Show"} table
          </button>
          {table && (
            <table className="table compact">
              <thead>
                <tr><th scope="col">Band (km)</th><th scope="col" className="num">Objects</th><th scope="col" className="num">Index</th><th scope="col" className="num">Weighted</th></tr>
              </thead>
              <tbody>
                {bands.filter((b) => b.objects > 0).map((b) => (
                  <tr key={b.alt_lo_km}>
                    <td>{b.alt_lo_km}–{b.alt_hi_km}</td>
                    <td className="num">{b.objects.toFixed(0)}</td>
                    <td className="num">{b.risk_index.toPrecision(3)}</td>
                    <td className="num">{b.risk_index_weighted.toPrecision(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <h3>The riskiest bands now</h3>
          <ol>
            {top.map((b) => (
              <li key={b.alt_lo_km}>
                <strong>{b.alt_lo_km}–{b.alt_hi_km} km</strong> (index {b.risk_index.toFixed(0)}): {describe(b)}, about {b.objects.toFixed(0)} objects on average.
              </li>
            ))}
          </ol>
        </>
      )}
      {history.data && history.data.runs.length > 0 && (
        <>
          <h3>Over time</h3>
          <p className="muted small">Riskiest band in each stored run. Runs labelled "history" use Space-Track's full catalog (with debris).</p>
          <table className="table compact">
            <thead>
              <tr><th scope="col">Run</th><th scope="col">Riskiest band</th></tr>
            </thead>
            <tbody>
              {history.data.runs.map((r) => {
                const best = r.bands.reduce((a, b) => (b.risk_index > a.risk_index ? b : a), r.bands[0]);
                return (
                  <tr key={r.run_id}>
                    <td>{r.run_id}</td>
                    <td>{best.alt_lo_km}–{best.alt_lo_km + 25} km</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
