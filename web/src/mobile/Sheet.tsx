// A bottom sheet: slides up over the screen, closes by dragging its handle down,
// tapping outside it, the close button or the back button.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Icon } from "./icons";
import { useBackClose } from "./nav";

const EXIT_MS = 260;

export function Sheet({ open, onClose, title, children, tall = false }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; tall?: boolean }) {
  // Stays rendered while it slides out.
  const [mounted, setMounted] = useState(open);
  const [shown, setShown] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const drag = useRef<{ y: number; t: number; dy: number } | null>(null);
  const requestClose = useBackClose(open, onClose);

  useEffect(() => {
    if (open) {
      setMounted(true);
      const raf = requestAnimationFrame(() => requestAnimationFrame(() => setShown(true)));
      return () => cancelAnimationFrame(raf);
    }
    setShown(false);
    const t = window.setTimeout(() => setMounted(false), EXIT_MS);
    return () => window.clearTimeout(t);
  }, [open]);

  if (!mounted) return null;

  const move = (dy: number) => {
    if (panel.current) panel.current.style.transform = dy > 0 ? `translateY(${dy}px)` : "";
  };
  // Above every screen and the navigation bar (a screen's own layer would hold it under them).
  return createPortal(
    <div className={`m-sheet-root${shown ? " shown" : ""}`}>
      <div className="m-sheet-backdrop" onClick={requestClose} aria-hidden />
      <div className={`m-sheet${tall ? " tall" : ""}`} role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined} ref={panel}>
        <div
          className="m-sheet-grab"
          onPointerDown={(e) => {
            if ((e.target as HTMLElement).closest("button")) return; // the close button stays a button
            drag.current = { y: e.clientY, t: performance.now(), dy: 0 };
            e.currentTarget.setPointerCapture(e.pointerId);
            panel.current?.classList.add("dragging");
          }}
          onPointerMove={(e) => {
            if (!drag.current) return;
            drag.current.dy = e.clientY - drag.current.y;
            move(drag.current.dy);
          }}
          onPointerUp={() => {
            const d = drag.current;
            drag.current = null;
            panel.current?.classList.remove("dragging");
            if (!d) return;
            const speed = d.dy / Math.max(1, performance.now() - d.t); // px per ms
            move(0);
            if (d.dy > 110 || speed > 0.6) requestClose();
          }}
          onPointerCancel={() => {
            drag.current = null;
            panel.current?.classList.remove("dragging");
            move(0);
          }}
        >
          <span className="m-sheet-handle" aria-hidden />
          <div className="m-sheet-head">
            <h2>{title}</h2>
            <button type="button" className="m-icon-button" onClick={requestClose} aria-label="Close">
              <Icon name="close" size={20} />
            </button>
          </div>
        </div>
        <div className="m-sheet-body">{children}</div>
      </div>
    </div>,
    document.querySelector(".m-app") ?? document.body,
  );
}
