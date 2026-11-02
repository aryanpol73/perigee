// Settings: Earth quality and globe layers, time display, notifications and the watch
// list, the data this phone has, offline storage, and the method, sources and limits.
import { useEffect, useState } from "react";
import { useApi, type Health, type Meta } from "../api";
import { utc } from "../format";
import { useStore } from "../store";
import { MethodView } from "../views/MethodView";
import { GlobeSettings, QualityPicker, setUtcTimes, useUtcTimes } from "./GlobeScreen";
import { Icon } from "./icons";
import { navigate, useBackClose } from "./nav";
import {
  ALERT_HOURS,
  ALERT_MISS_KM,
  enableNotifications,
  notificationsSupported,
  notify,
  permission,
  setNotifyPrefs,
  toggleWatch,
  useNotifyPrefs,
  useWatchList,
} from "./notify";
import { PullToRefresh } from "./PullToRefresh";
import { checkForNewData, toast } from "./toast";

export function SettingsScreen({ meta, health, method, active }: { meta?: Meta; health: Health | null; method: boolean; active: boolean }) {
  const prefs = useNotifyPrefs();
  const watch = useWatchList();
  const inUtc = useUtcTimes();
  const quality = useStore((s) => s.quality);
  const [perm, setPerm] = useState(permission());
  const [storage, setStorage] = useState<string | null>(null);
  const closeMethod = useBackClose(active && method, () => void navigate("settings"));

  useEffect(() => {
    navigator.storage?.estimate?.().then((e) => e.usage !== undefined && setStorage(e.usage < 1e6 ? "under 1 MB" : `about ${(e.usage / 1e6).toFixed(0)} MB`), () => undefined);
  }, [active]);

  const turnOn = async (on: boolean) => {
    if (!on) {
      setNotifyPrefs({ enabled: false });
      return;
    }
    const ok = await enableNotifications();
    setPerm(permission());
    if (!ok) toast(permission() === "denied" ? "Notifications are blocked: allow them in your phone's settings for Perigee." : "Notifications are not available here.");
  };

  const clearSaved = async () => {
    const names = (await caches?.keys?.()) ?? [];
    await Promise.all(names.filter((n) => n.startsWith("perigee-") && n !== "perigee-state").map((n) => caches.delete(n)));
    toast("Saved data cleared. It downloads again as you use the app.");
    setStorage(null);
  };

  const notifyOn = prefs.enabled && perm === "granted";

  return (
    <>
      <PullToRefresh onRefresh={() => checkForNewData().then(() => undefined)} label="Settings">
        <header className="m-screen-head m-brand">
          <img src="/icons/logo-96.png" alt="" width={56} height={56} />
          <div>
            <h1>Perigee</h1>
            <p className="m-muted">Orbital debris risk from public data</p>
          </div>
        </header>

        <section className="m-group" aria-labelledby="s-earth">
          <h2 id="s-earth" className="m-section-title">Earth &amp; globe</h2>
          <div className="m-card">
            <h4 className="m-label first">Earth quality</h4>
            <QualityPicker />
            <p className="m-muted small">
              {quality === "high"
                ? "Best on recent phones; uses more battery."
                : quality === "battery"
                  ? "Smoothest on older phones and longest battery life."
                  : "A good balance for most phones."}
            </p>
            <GlobeSettings provisional={meta ? !meta.history_complete : false} withQuality={false} />
          </div>
        </section>

        <section className="m-group" aria-labelledby="s-time">
          <h2 id="s-time" className="m-section-title">Time</h2>
          <div className="m-card m-list flat">
            <label className="m-row m-switch-row">
              <span className="m-row-text">
                <strong>Show times in UTC</strong>
                <small>{inUtc ? "Times show in UTC, as orbit data uses." : "Times show in your phone's time zone."}</small>
              </span>
              <input type="checkbox" className="m-switch" checked={inUtc} onChange={(e) => setUtcTimes(e.target.checked)} />
            </label>
          </div>
        </section>

        <section className="m-group" aria-labelledby="s-notify">
          <h2 id="s-notify" className="m-section-title">Notifications</h2>
          <div className="m-card m-list flat">
            <label className="m-row m-switch-row">
              <Icon name="bell" size={20} />
              <span className="m-row-text">
                <strong>Allow notifications</strong>
                <small>
                  {!notificationsSupported()
                    ? "Not available on this device."
                    : perm === "denied"
                      ? "Blocked: allow them in your phone's settings."
                      : notifyOn
                        ? "On"
                        : "Off"}
                </small>
              </span>
              <input
                type="checkbox"
                className="m-switch"
                checked={notifyOn}
                disabled={!notificationsSupported() || perm === "denied"}
                onChange={(e) => void turnOn(e.target.checked)}
              />
            </label>
            <label className="m-row m-switch-row">
              <span className="m-row-text">
                <strong>New data each day</strong>
                <small>When fresh orbit data and close approaches are published.</small>
              </span>
              <input type="checkbox" className="m-switch" checked={prefs.newData} disabled={!notifyOn} onChange={(e) => setNotifyPrefs({ newData: e.target.checked })} />
            </label>
            <label className="m-row m-switch-row">
              <span className="m-row-text">
                <strong>Watched objects</strong>
                <small>
                  An approach within {ALERT_HOURS} h closer than {ALERT_MISS_KM} km, or with an elevated risk estimate.
                </small>
              </span>
              <input type="checkbox" className="m-switch" checked={prefs.watched} disabled={!notifyOn} onChange={(e) => setNotifyPrefs({ watched: e.target.checked })} />
            </label>
            {notifyOn && (
              <button type="button" className="m-row m-row-button" onClick={() => void notify("Perigee notifications are on", "You will hear about new data and your watched objects.", "/?app=1#/settings", "perigee-test")}>
                <span className="m-row-text"><strong>Send a test notification</strong></span>
                <Icon name="chevron" size={18} className="m-chev" />
              </button>
            )}
          </div>
          <p className="m-muted small m-pad">
            Checked when the app opens and, where your phone allows it, about once a day in the background. Nothing is sent to
            any server: there is no account and the watch list stays on this phone.
          </p>

          <h3 className="m-label m-pad">Watched objects ({watch.length})</h3>
          <div className="m-card m-list flat">
            {watch.length === 0 && <p className="m-muted m-row">Tap Watch on any object or close approach.</p>}
            {watch.map((n) => (
              <WatchedSetting key={n} norad={n} />
            ))}
          </div>
        </section>

        <section className="m-group" aria-labelledby="s-data">
          <h2 id="s-data" className="m-section-title">Data</h2>
          <div className="m-card m-list flat">
            <Fact label="Orbit snapshot" value={meta ? utc(meta.snapshot_time) : "…"} />
            <Fact label="Data age" value={health ? `${health.data_age_hours.toFixed(0)} h${health.status === "stale" ? " (stale)" : ""}` : "…"} />
            <Fact label="Objects" value={meta ? meta.object_count.toLocaleString() : "…"} />
            <Fact label="Close approaches" value={meta ? meta.conjunction_count.toLocaleString() : "…"} />
            <Fact label="Risk model" value={meta?.model_version ? `${meta.model_version}${meta.history_complete ? "" : " (provisional)"}` : "unavailable"} />
            <button type="button" className="m-row m-row-button" onClick={() => void checkForNewData()}>
              <Icon name="refresh" size={20} />
              <span className="m-row-text"><strong>Check for new data</strong></span>
            </button>
            <button type="button" className="m-row m-row-button" onClick={() => void clearSaved()}>
              <span className="m-row-text">
                <strong>Clear saved data</strong>
                <small>Offline copy of the app, data and Earth imagery{storage ? `: ${storage}` : ""}.</small>
              </span>
            </button>
          </div>
        </section>

        <section className="m-group" aria-labelledby="s-about">
          <h2 id="s-about" className="m-section-title">About</h2>
          <div className="m-card m-list flat">
            <button type="button" className="m-row m-row-button" onClick={() => void navigate("method")}>
              <Icon name="info" size={20} />
              <span className="m-row-text"><strong>Method and limits</strong><small>What the numbers mean, and what they do not</small></span>
              <Icon name="chevron" size={18} className="m-chev" />
            </button>
            <a className="m-row m-row-button" href="/?app=0" target="_blank" rel="noopener">
              <Icon name="external" size={20} />
              <span className="m-row-text"><strong>Open the full dashboard</strong><small>The website, for large screens</small></span>
            </a>
          </div>
          <p className="m-disclaimer">
            <strong>Estimates from public data; not for operational use.</strong> Perigee shows estimated close approaches and a
            relative risk index; it never says that two objects will collide. Data: CelesTrak, Space-Track.org (USSPACECOM). Earth imagery: NASA Blue
            Marble &amp; Black Marble (GIBS).
          </p>
        </section>
      </PullToRefresh>

      <div className={`m-page${method ? " open" : ""}`} aria-hidden={!method} inert={!method}>
        <header className="m-page-bar">
          <button type="button" className="m-icon-button" onClick={closeMethod} aria-label="Back to settings">
            <Icon name="back" size={22} />
          </button>
          <strong>Method and limits</strong>
        </header>
        <div className="m-scroll">
          <div className="m-card m-view">{method && <MethodView meta={meta} />}</div>
        </div>
      </div>
    </>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="m-row">
      <span className="m-row-text"><small>{label}</small></span>
      <span className="m-row-value">{value}</span>
    </div>
  );
}

function WatchedSetting({ norad }: { norad: number }) {
  const d = useApi<{ name: string }>(`/v1/objects/${norad}`);
  return (
    <div className="m-row">
      <Icon name="starFilled" size={18} className="m-star" />
      <span className="m-row-text">
        <strong>{d.data?.name ?? norad}</strong>
        <small>NORAD {norad}</small>
      </span>
      <button type="button" className="m-button ghost compact" onClick={() => toggleWatch(norad)} aria-label={`Stop watching ${d.data?.name ?? norad}`}>
        Remove
      </button>
    </div>
  );
}
