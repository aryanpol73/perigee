// Time controls. LIVE follows the real time; FORECAST shows what the orbit data
// predicts for another moment in its 7-day window; DATA TOO OLD when the real time
// is past that window. The bar re-reads the clock a few times a second itself, so
// nothing else re-renders as time passes.
import { useEffect, useState } from "react";
import { offsetLabel, reachedEnd, status } from "../clock";
import { utc } from "../format";
import { time, useStore, wallNow } from "../store";

const SPEEDS = [1, 60, 600, 3600];

function localTime(t: number): string {
  return new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function TimeBar({ snapshot }: { snapshot: string }) {
  const clock = useStore((s) => s.clock);
  const [now, setNow] = useState(() => time.now());
  const [real, setReal] = useState(() => wallNow());

  useEffect(() => {
    const id = window.setInterval(() => {
      if (reachedEnd(clock, wallNow())) time.seek(clock.end); // stop at the end of the data's window
      setNow(time.now());
      setReal(wallNow());
    }, 250);
    return () => window.clearInterval(id);
  }, [clock]);

  const st = status(clock, real);
  const ageH = (real - new Date(snapshot).getTime()) / 3_600_000;
  const badge =
    st.kind === "live" ? (
      <span className="mode-badge live" title={`Positions computed now from orbit data ${ageH.toFixed(0)} h old`}>
        <span className="dot" aria-hidden /> LIVE
      </span>
    ) : st.kind === "stale" ? (
      <span className="mode-badge stale" title="The real time is past the 7 days this orbit data covers">DATA TOO OLD</span>
    ) : (
      <span className="mode-badge forecast" title="What the orbit data predicts for this moment, not the present">
        FORECAST · {offsetLabel(st.offsetMs)}
      </span>
    );

  return (
    <div className="timebar" data-mode={st.kind}>
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
      {st.kind === "forecast" && real <= clock.end && (
        <button type="button" className="back-live" onClick={() => time.goLive()}>
          Back to live
        </button>
      )}
      <input
        type="range"
        aria-label="Time in the orbit data's 7-day window"
        min={clock.start}
        max={clock.end}
        step={60_000}
        value={now}
        onChange={(e) => time.seek(Number(e.target.value))}
      />
      <span className="time">
        <span className="utc" data-testid="sim-time">{utc(new Date(now))}</span>
        <span className="local muted"> · {localTime(now)} local</span>
      </span>
      <span className="data-age muted small">
        orbit data from {utc(snapshot)}
        {st.kind === "stale" ? ": too old to show the present" : ""}
      </span>
    </div>
  );
}
