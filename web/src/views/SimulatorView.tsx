// Breakup simulator controls. Fragments are drawn on the globe; the label that this
// is illustrative stays on screen whenever a simulation is shown.
import { useEffect, useState } from "react";
import { post, type SimulateRequest, type SimulateResponse } from "../api";
import { prefersReducedMotion, setState, useStore } from "../store";

const PRESETS = {
  "Defunct satellite (950 kg)": { mass: 950, kind: "SC" as const },
  "Active satellite (560 kg)": { mass: 560, kind: "SC" as const },
  "Rocket body (1,400 kg)": { mass: 1400, kind: "RB" as const },
};
type Preset = keyof typeof PRESETS;

function frameLabel(h: number): string {
  if (h < 1) return `${Math.round(h * 60)} min`;
  if (h < 48) return `${h.toFixed(0)} h`;
  if (h < 24 * 60) return `${(h / 24).toFixed(0)} days`;
  return `${(h / 24 / 30.4).toFixed(0)} months`;
}

export function SimulatorView() {
  const sim = useStore((s) => s.sim);
  const [form, setForm] = useState({ altitude_km: 800, inclination_deg: 74, rel_speed_km_s: 11.7 });
  const [target, setTarget] = useState<Preset>("Defunct satellite (950 kg)");
  const [impactor, setImpactor] = useState<Preset>("Active satellite (560 kg)");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [auto, setAuto] = useState(!prefersReducedMotion);

  useEffect(() => () => setState({ sim: null }), []); // leaving the view removes the overlay

  useEffect(() => {
    if (!sim || !auto) return;
    const t = setInterval(() => {
      setState((s) => (s.sim ? { sim: { ...s.sim, frame: (s.sim.frame + 1) % s.sim.response.frames.length } } : {}));
    }, 1500);
    return () => clearInterval(t);
  }, [sim?.response, auto]);

  const run = async () => {
    setRunning(true);
    setError(null);
    const body: SimulateRequest = {
      ...form,
      target_mass_kg: PRESETS[target].mass,
      target_kind: PRESETS[target].kind,
      impactor_mass_kg: PRESETS[impactor].mass,
      impactor_kind: PRESETS[impactor].kind,
      seed: 42,
      max_points: 1500,
    };
    try {
      const response = await post<SimulateResponse>("/v1/simulate", body);
      setState({ sim: { response, frame: 0 }, focus: null, selected: null });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const r = sim?.response;
  const frame = r && sim ? r.frames[sim.frame] : null;

  return (
    <div className="view">
      <h2>Breakup simulator</h2>
      <p className="label-strong" role="note">Illustrative, simplified; not a prediction.</p>
      <p className="muted">
        What if two objects collided here? Fragment counts follow NASA's standard breakup model; fragments then move under Earth's
        gravity and its equatorial bulge.
      </p>
      <details className="limits" open>
        <summary>Limits of this simulation</summary>
        <ul>
          <li>No air drag: low fragments never re-enter here, so debris below about 600 km lasts longer than it really would.</li>
          <li>Both objects start on circular orbits of the same inclination, one heading north and one south.</li>
          <li>The breakup model is an average: for the 2009 Iridium-Cosmos collision it gives about half the ~2,300 fragments catalogued.</li>
          <li>Only fragments of 10 cm and larger are counted; sizes and masses are rough class averages.</li>
          <li>The what-if index change assumes the fragments' orbits as simulated and ignores manoeuvres.</li>
        </ul>
      </details>
      <form
        className="controls stack"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <label>
          Altitude {form.altitude_km} km
          <input type="range" min={300} max={1500} step={10} value={form.altitude_km} onChange={(e) => setForm({ ...form, altitude_km: Number(e.target.value) })} />
        </label>
        <label>
          Orbit inclination {form.inclination_deg}°
          <input type="range" min={0} max={110} step={1} value={form.inclination_deg} onChange={(e) => setForm({ ...form, inclination_deg: Number(e.target.value) })} />
        </label>
        <label>
          Relative speed {form.rel_speed_km_s.toFixed(1)} km/s
          <input type="range" min={1} max={15} step={0.1} value={form.rel_speed_km_s} onChange={(e) => setForm({ ...form, rel_speed_km_s: Number(e.target.value) })} />
        </label>
        <label>
          Object 1{" "}
          <select value={target} onChange={(e) => setTarget(e.target.value as Preset)}>
            {Object.keys(PRESETS).map((p) => <option key={p}>{p}</option>)}
          </select>
        </label>
        <label>
          Object 2{" "}
          <select value={impactor} onChange={(e) => setImpactor(e.target.value as Preset)}>
            {Object.keys(PRESETS).map((p) => <option key={p}>{p}</option>)}
          </select>
        </label>
        <button type="submit" disabled={running}>{running ? "Simulating…" : "Simulate collision"}</button>
      </form>
      {error && <p className="error">{error}</p>}
      {r && frame && sim && (
        <section className="detail">
          <h3>{r.catastrophic ? "Catastrophic breakup" : "Partial breakup"}</h3>
          <dl className="facts">
            <dt>Fragments 10 cm and larger</dt>
            <dd>{r.modelled_fragments.toLocaleString()} modelled; {r.points_returned.toLocaleString()} drawn</dd>
            <dt>Impact energy</dt>
            <dd>{Math.round(r.specific_energy_j_per_g).toLocaleString()} J per gram of the larger object (catastrophic above 40)</dd>
            <dt>Mass</dt>
            <dd>{r.input_mass_kg.toLocaleString()} kg in; {Math.round(r.fragment_mass_kg).toLocaleString()} kg in simulated fragments</dd>
          </dl>
          <label>
            After {frameLabel(frame.hours)}
            <input
              type="range"
              min={0}
              max={r.frames.length - 1}
              value={sim.frame}
              onChange={(e) => {
                setAuto(false);
                setState({ sim: { ...sim, frame: Number(e.target.value) } });
              }}
              aria-label="Time after the collision"
            />
          </label>
          <label>
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> play through
          </label>
          <p>
            The debris fills {Math.round(frame.along_track_coverage * 100)}% of the way around its orbits and covers{" "}
            {Math.round(frame.node_coverage * 100)}% of orbit orientations
            {frame.node_coverage > 0.9 ? ": a shell around Earth." : frame.along_track_coverage > 0.9 ? ": a ring." : ": still a cloud."}
          </p>
          <h4>What it would do to the risk index</h4>
          <table className="table compact">
            <thead>
              <tr><th scope="col">Band (km)</th><th scope="col" className="num">Before</th><th scope="col" className="num">After</th></tr>
            </thead>
            <tbody>
              {r.what_if.map((b) => (
                <tr key={b.alt_lo_km}>
                  <td>{b.alt_lo_km}–{b.alt_hi_km}</td>
                  <td className="num">{b.risk_index_before.toPrecision(3)}</td>
                  <td className="num">{b.risk_index_after.toPrecision(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" onClick={() => setState({ sim: null })}>Clear simulation</button>
        </section>
      )}
    </div>
  );
}
