// A screen's scrolling area. Pulled down from the top, it runs onRefresh (checks for
// newer data). The indicator moves by style, not state, so a long list never
// re-renders while the finger moves.
import { useEffect, useRef, type ReactNode } from "react";
import { Icon } from "./icons";

const TRIGGER = 72;

export function PullToRefresh({ onRefresh, children, label }: { onRefresh: () => Promise<void>; children: ReactNode; label: string }) {
  const box = useRef<HTMLDivElement>(null);
  const spinner = useRef<HTMLDivElement>(null);
  const latest = useRef(onRefresh);
  useEffect(() => {
    latest.current = onRefresh;
  });

  useEffect(() => {
    const el = box.current;
    const ind = spinner.current;
    if (!el || !ind) return;
    let start: number | null = null;
    let pull = 0;
    let busy = false;
    const show = (px: number, spinning = false) => {
      ind.style.transform = `translateY(${px - 44}px) rotate(${px * 4}deg)`;
      ind.style.opacity = String(Math.min(1, px / TRIGGER));
      ind.classList.toggle("spinning", spinning);
    };
    const down = (e: TouchEvent) => {
      start = !busy && el.scrollTop <= 0 ? e.touches[0].clientY : null;
    };
    const moveTouch = (e: TouchEvent) => {
      if (start === null) return;
      const dy = e.touches[0].clientY - start;
      if (dy <= 0 || el.scrollTop > 0) {
        pull = 0;
        show(0);
        return;
      }
      pull = Math.min(110, dy * 0.5);
      ind.classList.add("pulling");
      show(pull);
    };
    const up = () => {
      if (start === null) return;
      start = null;
      ind.classList.remove("pulling");
      if (pull < TRIGGER) {
        pull = 0;
        show(0);
        return;
      }
      busy = true;
      show(TRIGGER, true);
      void latest.current().finally(() => {
        busy = false;
        pull = 0;
        show(0);
      });
    };
    el.addEventListener("touchstart", down, { passive: true });
    el.addEventListener("touchmove", moveTouch, { passive: true });
    el.addEventListener("touchend", up);
    el.addEventListener("touchcancel", up);
    return () => {
      el.removeEventListener("touchstart", down);
      el.removeEventListener("touchmove", moveTouch);
      el.removeEventListener("touchend", up);
      el.removeEventListener("touchcancel", up);
    };
  }, []);

  return (
    <div className="m-scroll" ref={box} aria-label={label}>
      <div className="m-ptr" ref={spinner} aria-hidden>
        <Icon name="refresh" size={20} />
      </div>
      {children}
    </div>
  );
}
