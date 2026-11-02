// Short messages at the bottom of the app ("Up to date"), optionally with one action.
import { useEffect, useState } from "react";
import { get, type Meta } from "../api";
import { publishedSnapshot } from "../pwa";

export interface Toast {
  id: number;
  message: string;
  action?: { label: string; run: () => void };
}

let seq = 0;
const EVENT = "perigee-toast";

export function toast(message: string, action?: Toast["action"]): void {
  window.dispatchEvent(new CustomEvent<Toast>(EVENT, { detail: { id: ++seq, message, action } }));
}

/** The newest toast, cleared after a few seconds (longer when it has an action). */
export function useToast(): [Toast | null, () => void] {
  const [current, setCurrent] = useState<Toast | null>(null);
  useEffect(() => {
    const on = (e: Event) => setCurrent((e as CustomEvent<Toast>).detail);
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  useEffect(() => {
    if (!current) return;
    const t = window.setTimeout(() => setCurrent(null), current.action ? 8000 : 3000);
    return () => window.clearTimeout(t);
  }, [current]);
  return [current, () => setCurrent(null)];
}

/** Pull to refresh and "Check for new data": the data changes once a day. */
export async function checkForNewData(quiet = false): Promise<boolean> {
  const [loaded, latest] = await Promise.all([get<Meta>("/v1/meta").catch(() => null), publishedSnapshot()]);
  if (latest === null) {
    if (!quiet) toast("Offline: showing the data saved on this phone.");
    return false;
  }
  if (loaded && latest !== loaded.snapshot_id) {
    toast("New orbit data is available.", { label: "Refresh", run: () => window.location.reload() });
    return true;
  }
  if (!quiet) toast("Up to date: this is the newest data.");
  return false;
}
