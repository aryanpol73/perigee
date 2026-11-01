// Time controls: play/pause, speed, and a slider from the snapshot to 7 days ahead.
import { utc } from "../format";
import { setState, useStore } from "../store";

const SPAN_MS = 7 * 86_400_000;
const SPEEDS = [1, 60, 600, 3600];

export function TimeBar({ snapshot }: { snapshot: string }) {
  const time = useStore((s) => s.time);
  const playing = useStore((s) => s.playing);
  const speed = useStore((s) => s.speed);
  const start = new Date(snapshot).getTime();
  return (
    <div className="timebar">
      <button type="button" onClick={() => setState({ playing: !playing })} aria-pressed={playing}>
        {playing ? "Pause" : "Play"}
      </button>
      <label>
        Speed{" "}
        <select value={speed} onChange={(e) => setState({ speed: Number(e.target.value) })}>
          {SPEEDS.map((s) => (
            <option key={s} value={s}>
              {s === 1 ? "real time" : `${s}×`}
            </option>
          ))}
        </select>
      </label>
      <input
        type="range"
        aria-label="Time after the snapshot"
        min={start}
        max={start + SPAN_MS}
        step={60_000}
        value={Math.min(Math.max(time, start), start + SPAN_MS)}
        onChange={(e) => setState({ time: Number(e.target.value), playing: false })}
      />
      <span className="time">{utc(new Date(time))}</span>
    </div>
  );
}
