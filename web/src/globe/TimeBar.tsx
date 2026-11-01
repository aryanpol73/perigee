// Time controls. LIVE follows the real time; FORECAST shows what the orbit data
// predicts for another moment in its 7-day window; DATA TOO OLD when the real time
// is past that window. The track shows which part of the window is already past
// (calculated from the data) and which is still a forecast, with a "now" marker.
// The bar re-reads the clock a few times a second itself, so nothing else
// re-renders as time passes.
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { offsetLabel, reachedEnd, status } from "../clock";
import { utc } from "../format";
import { time, useStore, wallNow } from "../store";
import { ROUGH_AFTER_DAYS, errorLabel, forecastErrorKm, roughKm } from "../uncertainty";

const SPEEDS = [1, 60, 600, 3600];

function localTime(t: number): string {
  return new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

const unit = (x: number) => Math.min(1, Math.max(0, x));

export function TimeBar({ snapshot }: { snapshot: string }) {
  const clock = useStore((s) => s.clock);
  const [real, setReal] = useState(() => wallNow());
  const bar = useRef<HTMLDivElement>(null);

  // Tell the globe how tall the bar is, so the legend above never runs under it.
  useEffect(() => {
    const el = bar.current;
    if (!el?.parentElement || typeof ResizeObserver === "undefined") return;
    const parent = el.parentElement;
    const ro = new ResizeObserver(() => parent.style.setProperty("--timebar-h", `${el.offsetHeight}px`));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (reachedEnd(clock, wallNow())) time.seek(clock.end); // stop at the end of the data's window
      setReal(wallNow());
    }, 250);
    return () => window.clearInterval(id);
  }, [clock]);

  // Read the clock on every render (not from a timer), so the thumb follows a drag at once.
  const shown = time.now();
  const st = status(clock, real);
  const snapshotMs = new Date(snapshot).getTime();
  const ageH = (real - snapshotMs) / 3_600_000;
  const lead = shown - snapshotMs;
  const spread = forecastErrorKm(lead);
  const error = errorLabel(spread.median);
  const tail = `1 in 10 objects is off by more than ${roughKm(spread.p90)} km`;
  const rough = lead > ROUGH_AFTER_DAYS * 86_400_000;
  const span = clock.end - clock.start;
  const nowInWindow = real >= clock.start && real <= clock.end;
  const track = {
    "--now": unit((real - clock.start) / span),
    "--rough": unit((ROUGH_AFTER_DAYS * 86_400_000) / span),
  } as CSSProperties;

  const badge =
    st.kind === "live" ? (
      <span className="mode-badge live" title={`Positions computed now from orbit data ${ageH.toFixed(0)} h old`}>
        <span className="dot" aria-hidden /> LIVE
      </span>
    ) : st.kind === "stale" ? (
      <span className="mode-badge stale" title="The real time is past the 7 days this orbit data covers">DATA TOO OLD</span>
    ) : (
      <span className="mode-badge forecast" title={`What the orbit data predicts for this moment, not the present. Typical position error then: ${error}; ${tail}.`}>
        FORECAST · {offsetLabel(st.offsetMs)} · {error}
      </span>
    );

  return (
    <div className="timebar" data-mode={st.kind} ref={bar}>
      {badge}
      <button type="button" onClick={() => time.togglePlay()} aria-pressed={clock.playing}>
        {clock.playing ? "Pause" : "Play"}
      </button>
      <label>
        Speed{" "}
        <select value={clock.mode === "live" ? 1 : clock.speed} onChange={(e) => time.setSpeed(Number(e.target.value))}>
          {SPEEDS.map((s) => (
            <option key={s} value={s}>
              {s === 1 ? "real time" : `${s}×`}
            </option>
          ))}
        </select>
      </label>
      {st.kind === "forecast" && nowInWindow && (
        <button type="button" className="back-live" onClick={() => time.goLive()}>
          Back to live
        </button>
      )}
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
        {nowInWindow && (
          <span className="now-mark" data-testid="now-mark" aria-hidden>
            <span>now</span>
          </span>
        )}
      </div>
      <span className="time">
        <span className="utc" data-testid="sim-time">{utc(new Date(shown))}</span>
        <span className="local muted"> · {localTime(shown)} local</span>
      </span>
      <span className="data-age muted small">
        orbit data from {utc(snapshot)}
        {st.kind === "stale" ? ": too old to show the present" : ""} · <span className="key past" aria-hidden /> calculated up to now ·{" "}
        <span className="key future" aria-hidden /> forecast · typical position error here <span title={tail}>{error}</span>
      </span>
      {rough && (
        <span className="rough-note small" role="note">
          More than {ROUGH_AFTER_DAYS} days after the data was taken: positions are rough. {tail}, mostly from manoeuvres made since
          and changes in drag.
        </span>
      )}
    </div>
  );
}
