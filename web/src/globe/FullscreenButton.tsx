// Shows the globe (with its time bar and legend) on the whole screen. Esc, or the
// button again, returns. Hidden where the browser has no Fullscreen API (iPhone Safari).
import { useEffect, useState, type RefObject } from "react";

export function FullscreenButton({ target }: { target: RefObject<HTMLElement | null> }) {
  const [active, setActive] = useState(false);

  useEffect(() => {
    const sync = () => setActive(document.fullscreenElement === target.current);
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, [target]);

  if (typeof document === "undefined" || !document.fullscreenEnabled) return null;

  const toggle = () => {
    // Embedded pages (an iframe without allowfullscreen) are refused; nothing to do then.
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else target.current?.requestFullscreen().catch(() => {});
  };

  const label = active ? "Exit full screen" : "Full screen";
  return (
    <button type="button" className="fullscreen-button" onClick={toggle} aria-pressed={active} aria-label={label} title={`${label} (Esc to leave)`}>
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden>
        {active ? (
          <path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        ) : (
          <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        )}
      </svg>
    </button>
  );
}
