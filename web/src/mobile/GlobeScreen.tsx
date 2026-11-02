// What the mobile app draws over the globe: the top bar, the time controls, the card
// for whatever is selected (an object, a close approach, a simulation) and the
// layers sheet. The globe itself (src/globe/Globe.tsx) stays mounted under every screen.
import { useEffect, useState, useSyncExternalStore, type CSSProperties } from "react";
import { useApi, type ConjunctionDetail, type ObjectDetail } from "../api";
import { offsetLabel, reachedEnd, status } from "../clock";
import { hoursAhead, hoursFromNow, km, objectTypeName, utc } from "../format";
import { OBJECT_TYPES, STATION_COLOR, TYPE_COLORS, type ColorMode, type ObjectType } from "../orbits";
import { moveCamera, setQuality, setState, time, useStore, wallNow, type Quality } from "../store";
import { ROUGH_AFTER_DAYS, errorLabel, forecastErrorKm } from "../uncertainty";
import { Risk } from "../views/ConjunctionsView";
import { frameLabel } from "../views/SimulatorView";
import { Icon } from "./icons";
import { navigate } from "./nav";
import { toggleWatch, useWatchList } from "./notify";
import { Sheet } from "./Sheet";

// --- Time display preference (local time or UTC) ------------------------------------

const TIME_KEY = "perigee.utc";
let utcTimes = (() => {
  try {
    return window.localStorage.getItem(TIME_KEY) === "1";
  } catch {
    return false;
  }
})();
const timeListeners = new Set<() => void>();

export function useUtcTimes(): boolean {
  return useSyncExternalStore(
    (l) => {
      timeListeners.add(l);
      return () => timeListeners.delete(l);
    },
    () => utcTimes,
  );
}

export function setUtcTimes(on: boolean): void {
  utcTimes = on;
  try {
    window.localStorage.setItem(TIME_KEY, on ? "1" : "0");
  } catch {
    // not remembered
  }
  timeListeners.forEach((l) => l());
}

/** "14:05" and "Sun 2 Nov", in local time or UTC. */
export function clockParts(t: number, inUtc: boolean): { time: string; date: string } {
  const opts = inUtc ? { timeZone: "UTC" } : {};
  return {
    time: new Date(t).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", ...opts }) + (inUtc ? " UTC" : ""),
    date: new Date(t).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", ...opts }),
  };
}

// --- The overlay ---------------------------------------------------------------------

export function GlobeOverlay({ snapshot, provisional, active }: { snapshot: string; provisional: boolean; active: boolean }) {
  const [layers, setLayers] = useState(false);
  const selected = useStore((s) => s.selected);
  const focus = useStore((s) => s.focus);
  const sim = useStore((s) => s.sim);
  if (!active && layers) setLayers(false); // another screen opened: the sheet goes with the globe

  return (
    <div className="m-globe-overlay">
      <header className="m-appbar">
        <img src="/icons/logo-96.png" alt="" width={34} height={34} className="m-logo" />
        <div className="m-appbar-title">
          <strong>Perigee</strong>
          <ModeBadge snapshot={snapshot} active={active} />
        </div>
        <button type="button" className="m-round" onClick={() => moveCamera({ kind: "home" })} aria-label="Whole Earth view">
          <Icon name="home" size={21} />
        </button>
        <button type="button" className="m-round" onClick={() => setLayers(true)} aria-label="Globe layers and quality">
          <Icon name="layers" size={21} />
        </button>
      </header>

      <div className="m-globe-bottom">
        {sim ? <SimCard /> : focus ? <ApproachPeek id={focus.conjunctionId} /> : selected !== null ? <ObjectPeek norad={selected} /> : null}
        <MobileTimeBar snapshot={snapshot} active={active} />
      </div>

      <Sheet open={layers} onClose={() => setLayers(false)} title="Globe">
        <GlobeSettings provisional={provisional} />
      </Sheet>
    </div>
  );
}

function useRealTime(active: boolean): number {
  const clock = useStore((s) => s.clock);
  const [real, setReal] = useState(() => wallNow());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => {
      if (reachedEnd(clock, wallNow())) time.seek(clock.end);
      setReal(wallNow());
    }, 250);
    return () => window.clearInterval(id);
  }, [clock, active]);
  return real;
}

function ModeBadge({ snapshot, active }: { snapshot: string; active: boolean }) {
  const clock = useStore((s) => s.clock);
  const real = useRealTime(active);
  const st = status(clock, real);
  const ageH = (real - new Date(snapshot).getTime()) / 3_600_000;
  if (st.kind === "live")
    return (
      <span className="mode-badge live" title={`Positions computed now from orbit data ${ageH.toFixed(0)} h old`}>
        <span className="dot" aria-hidden /> LIVE
      </span>
    );
  if (st.kind === "stale") return <span className="mode-badge stale">DATA TOO OLD</span>;
  return <span className="mode-badge forecast">FORECAST · {offsetLabel(st.offsetMs)}</span>;
}

const SPEEDS = [1, 60, 600, 3600];
const unit = (x: number) => Math.min(1, Math.max(0, x));

function MobileTimeBar({ snapshot, active }: { snapshot: string; active: boolean }) {
  const clock = useStore((s) => s.clock);
  const real = useRealTime(active);
  const inUtc = useUtcTimes();
  const shown = time.now();
  const st = status(clock, real);
  const lead = shown - new Date(snapshot).getTime();
  const span = clock.end - clock.start;
  const nowInWindow = real >= clock.start && real <= clock.end;
  const speed = clock.mode === "live" ? 1 : clock.speed;
  const parts = clockParts(shown, inUtc);
  const track = { "--now": unit((real - clock.start) / span), "--rough": unit((ROUGH_AFTER_DAYS * 86_400_000) / span) } as CSSProperties;

  return (
    <div className="m-timebar" data-mode={st.kind}>
      <div className="m-timebar-row">
        <button type="button" className="m-play" onClick={() => time.togglePlay()} aria-pressed={clock.playing} aria-label={clock.playing ? "Pause" : "Play"}>
          <Icon name={clock.playing ? "pause" : "play"} size={22} />
        </button>
        <div className="m-timebar-time">
          <strong data-testid="m-time">{parts.time}</strong>
          <span>{parts.date}</span>
        </div>
        {st.kind === "forecast" && nowInWindow && (
          <button type="button" className="m-chip live" onClick={() => time.goLive()}>
            <span className="dot" aria-hidden /> Live
          </button>
        )}
        <button
          type="button"
          className="m-chip"
          onClick={() => time.setSpeed(SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length] ?? 1)}
          aria-label={`Speed ${speed === 1 ? "real time" : `${speed} times`}; tap to change`}
        >
          <Icon name="speed" size={16} /> {speed === 1 ? "1×" : `${speed.toLocaleString()}×`}
        </button>
      </div>
      <div className="track" style={track}>
        <input
          type="range"
          aria-label="Time in the orbit data's 7-day window"
          aria-valuetext={utc(new Date(shown))}
          min={clock.start}
          max={clock.end}
          step={60_000}
          value={shown}
          onChange={(e) => time.seek(Number(e.target.value))}
        />
        {nowInWindow && <span className="now-mark" aria-hidden />}
      </div>
      {st.kind !== "live" && (
        <p className="m-timebar-note">
          {st.kind === "stale"
            ? "The real time is past the 7 days this orbit data covers."
            : `Forecast: typical position error ${errorLabel(forecastErrorKm(lead, "leo").median)}${lead > ROUGH_AFTER_DAYS * 86_400_000 ? ", rough after day 3" : ""}.`}
        </p>
      )}
    </div>
  );
}

// --- Cards for what is selected --------------------------------------------------

export function WatchButton({ norad, name, compact = false }: { norad: number; name?: string | null; compact?: boolean }) {
  const watched = useWatchList().includes(norad);
  return (
    <button type="button" className={`m-button ${watched ? "on" : "ghost"}${compact ? " compact" : ""}`} onClick={() => toggleWatch(norad)} aria-pressed={watched}>
      <Icon name={watched ? "starFilled" : "star"} size={18} /> {watched ? "Watching" : "Watch"}
      {name ? ` ${name}` : ""}
    </button>
  );
}

function ObjectPeek({ norad }: { norad: number }) {
  const d = useApi<ObjectDetail>(`/v1/objects/${norad}`);
  const o = d.data;
  return (
    <section className="m-card m-peek" aria-label="Selected object">
      <button type="button" className="m-peek-close" onClick={() => setState({ selected: null })} aria-label="Clear selection">
        <Icon name="close" size={18} />
      </button>
      {!o ? (
        <p className="m-muted">{d.error ? "No details for this object." : "Loading…"}</p>
      ) : (
        <>
          <div className="m-peek-head">
            <span className="m-type-dot" style={{ background: TYPE_COLORS[(o.object_type as ObjectType) ?? "UNK"] ?? TYPE_COLORS.UNK }} aria-hidden />
            <div>
              <h3>{o.name}</h3>
              <p className="m-muted">
                {objectTypeName(o.object_type)} · NORAD {o.norad_id} · {o.perigee_km.toFixed(0)}–{o.apogee_km.toFixed(0)} km
              </p>
            </div>
          </div>
          <p className="m-peek-line">
            {o.conjunctions.length === 0 ? "No close approaches in the next 7 days." : `${o.conjunctions.length} close approach${o.conjunctions.length === 1 ? "" : "es"} in the next 7 days.`}
          </p>
          <div className="m-actions">
            <button type="button" className="m-button primary" onClick={() => void navigate(`object/${o.norad_id}`)}>
              Details <Icon name="chevron" size={16} />
            </button>
            <WatchButton norad={o.norad_id} />
          </div>
        </>
      )}
    </section>
  );
}

function ApproachPeek({ id }: { id: string }) {
  const d = useApi<ConjunctionDetail>(`/v1/conjunctions/${encodeURIComponent(id)}`);
  const op = useApi<{ operating_probability: number | null }>("/v1/meta").data?.operating_probability ?? null;
  const c = d.data;
  return (
    <section className="m-card m-peek" aria-label="Selected close approach">
      <button type="button" className="m-peek-close" onClick={() => setState({ focus: null })} aria-label="Clear selection">
        <Icon name="close" size={18} />
      </button>
      {!c ? (
        <p className="m-muted">{d.error ? "Could not load this approach." : "Loading…"}</p>
      ) : (
        <>
          <h3 className="m-pair">
            {c.object_details[0].name} <span>×</span> {c.object_details[1].name}
          </h3>
          <p className="m-muted">
            Closest {hoursAhead(hoursFromNow(c.tca, wallNow()))} · {km(c.miss_distance_km)} at {c.relative_speed_kms.toFixed(1)} km/s
          </p>
          <div className="m-peek-line">
            <Risk value={c.risk_probability_estimate ?? null} operating={op} status={c.estimate_status} />
          </div>
          <div className="m-actions">
            <button type="button" className="m-button primary" onClick={() => void navigate(`conjunctions/${encodeURIComponent(c.id)}`)}>
              Details <Icon name="chevron" size={16} />
            </button>
          </div>
        </>
      )}
    </section>
  );
}

function SimCard() {
  const sim = useStore((s) => s.sim)!;
  const r = sim.response;
  const frame = r.frames[sim.frame];
  return (
    <section className="m-card m-peek" aria-label="Breakup simulation">
      <button type="button" className="m-peek-close" onClick={() => setState({ sim: null })} aria-label="Clear the simulation">
        <Icon name="close" size={18} />
      </button>
      <h3>{r.catastrophic ? "Catastrophic breakup" : "Partial breakup"}</h3>
      <p className="m-muted">
        {r.modelled_fragments.toLocaleString()} fragments ≥10 cm · after {frameLabel(frame.hours)} · illustrative, not a prediction
      </p>
      <input
        type="range"
        className="m-range"
        aria-label="Time after the collision"
        min={0}
        max={r.frames.length - 1}
        value={sim.frame}
        onChange={(e) => setState({ sim: { ...sim, frame: Number(e.target.value) } })}
      />
      <div className="m-actions">
        <button type="button" className="m-button ghost" onClick={() => void navigate("simulator")}>
          Change the collision
        </button>
      </div>
    </section>
  );
}

// --- Globe settings (layers sheet and the settings screen) ---------------------------

const QUALITY: [Quality, string, string][] = [
  ["high", "High", "Glow, full resolution, 60 fps"],
  ["balanced", "Balanced", "Night lights, smooth edges"],
  ["battery", "Battery saver", "30 fps, lighter drawing"],
];
const COLOR_MODES: [ColorMode, string][] = [
  ["type", "Type"],
  ["altitude", "Altitude"],
  ["risk", "Risk"],
];
const TYPE_NAMES: Record<ObjectType, string> = { PAY: "Payloads", DEB: "Debris", "R/B": "Rocket bodies", UNK: "Unknown" };
const MAX_ALT: [number, string][] = [
  [50000, "All"],
  [2000, "LEO"],
  [1000, "<1,000 km"],
  [600, "<600 km"],
];

export function QualityPicker() {
  const quality = useStore((s) => s.quality);
  return (
    <div className="m-options" role="radiogroup" aria-label="Earth quality">
      {QUALITY.map(([q, label, note]) => (
        <button key={q} type="button" role="radio" aria-checked={quality === q} className="m-option" onClick={() => setQuality(q)}>
          <span className="m-option-radio" aria-hidden />
          <span>
            <strong>{label}</strong>
            <small>{note}</small>
          </span>
        </button>
      ))}
    </div>
  );
}

export function GlobeSettings({ provisional, withQuality = true }: { provisional: boolean; withQuality?: boolean }) {
  const mode = useStore((s) => s.colorMode);
  const filters = useStore((s) => s.filters);
  return (
    <div className="m-settings-block">
      <h4 className="m-label">Colour objects by</h4>
      <div className="m-segmented" role="radiogroup" aria-label="Colour objects by">
        {COLOR_MODES.map(([m, label]) => (
          <button key={m} type="button" role="radio" aria-checked={mode === m} onClick={() => setState({ colorMode: m })}>
            {label}
          </button>
        ))}
      </div>
      {mode === "altitude" && <div className="m-ramp" aria-label="Light = low orbit, deeper blue = high orbit"><span>low</span><span>high</span></div>}
      {mode === "risk" && (
        <p className="m-muted small">
          Highlighted: objects in close approaches above the model's operating point{provisional ? " (provisional this run)" : ""}. Not a collision probability.
        </p>
      )}

      <h4 className="m-label">Show</h4>
      <div className="m-list">
        {OBJECT_TYPES.map((t) => (
          <label key={t} className="m-row m-switch-row">
            <span className="m-type-dot" style={{ background: TYPE_COLORS[t] }} aria-hidden />
            <span className="m-row-text">{TYPE_NAMES[t]}</span>
            <input
              type="checkbox"
              className="m-switch"
              checked={filters.types[t]}
              onChange={(e) => setState((s) => ({ filters: { ...s.filters, types: { ...s.filters.types, [t]: e.target.checked } } }))}
            />
          </label>
        ))}
        <div className="m-row">
          <span className="m-type-dot" style={{ background: STATION_COLOR }} aria-hidden />
          <span className="m-row-text m-muted">Crewed stations (ISS, Tiangong) always show</span>
        </div>
      </div>
      <p className="m-muted small">Phones start with payloads only: debris adds thousands of points.</p>

      <h4 className="m-label">Orbits up to</h4>
      <div className="m-segmented" role="radiogroup" aria-label="Maximum perigee">
        {MAX_ALT.map(([alt, label]) => (
          <button key={alt} type="button" role="radio" aria-checked={filters.maxAltKm === alt} onClick={() => setState((s) => ({ filters: { ...s.filters, maxAltKm: alt } }))}>
            {label}
          </button>
        ))}
      </div>

      {withQuality && (
        <>
          <h4 className="m-label">Earth quality</h4>
          <QualityPicker />
        </>
      )}
    </div>
  );
}
