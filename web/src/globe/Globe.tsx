// The 3D globe. Every object is one point primitive (entities are far too heavy
// at 30,000); a Web Worker propagates positions with satellite.js a few times per
// second of real time, and the scene renders only when something changes.
import { useEffect, useRef, useState } from "react";
import type { Meta } from "../api";
import { get } from "../api";
import { TYPE_COLORS, altitudeColor, altitudes, decodeOrbitFile, visible, type ObjectType, type Omm, type OrbitFile } from "../orbits";
import { getState, prefersReducedMotion, setState, subscribe, useStore } from "../store";
import { TimeBar } from "./TimeBar";

declare const CESIUM_BASE_URL: string;
(window as unknown as { CESIUM_BASE_URL: string }).CESIUM_BASE_URL = CESIUM_BASE_URL;

const UPDATE_MS = 250; // how often positions are recomputed (real time)

export default function Globe({ meta }: { meta: Meta }) {
  const container = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState("Loading orbit data…");
  const simActive = useStore((s) => s.sim !== null);

  useEffect(() => {
    let disposed = false;
    let cleanup = () => {};
    (async () => {
      const Cesium = await import("cesium");
      await import("cesium/Build/Cesium/Widgets/widgets.css");
      if (disposed || !container.current) return;

      // No Cesium ion: the Natural Earth imagery that ships with Cesium, no token.
      Cesium.Ion.defaultAccessToken = "";
      const viewer = new Cesium.Viewer(container.current, {
        baseLayer: Cesium.ImageryLayer.fromProviderAsync(
          Cesium.TileMapServiceImageryProvider.fromUrl(Cesium.buildModuleUrl("Assets/Textures/NaturalEarthII")),
        ),
        baseLayerPicker: false,
        geocoder: false,
        homeButton: true,
        sceneModePicker: false,
        navigationHelpButton: false,
        animation: false,
        timeline: false,
        fullscreenButton: false,
        infoBox: false,
        selectionIndicator: false,
        requestRenderMode: true,
        maximumRenderTimeChange: Infinity,
      });
      viewer.scene.globe.enableLighting = false;
      viewer.camera.setView({ destination: Cesium.Cartesian3.fromDegrees(10, 20, 4.5e7) });

      const points = viewer.scene.primitives.add(new Cesium.PointPrimitiveCollection()) as InstanceType<typeof Cesium.PointPrimitiveCollection>;
      const fragments = viewer.scene.primitives.add(new Cesium.PointPrimitiveCollection()) as InstanceType<typeof Cesium.PointPrimitiveCollection>;
      const lines = viewer.scene.primitives.add(new Cesium.PolylineCollection()) as InstanceType<typeof Cesium.PolylineCollection>;
      const colors: Record<string, InstanceType<typeof Cesium.Color>> = {};
      const color = (css: string) => (colors[css] ??= Cesium.Color.fromCssColorString(css));

      // Orbit data: one static, content-hashed file the browser can cache.
      const file = await get<OrbitFile>(meta.orbit_file_url);
      if (disposed) return;
      const records: Omm[] = decodeOrbitFile(file);
      const perigee = records.map((r) => altitudes(r).perigee);
      const worker = new Worker(new URL("../orbit.worker.ts", import.meta.url), { type: "module" });
      worker.postMessage({ kind: "load", records });
      const prims = records.map((r) => points.add({ id: r.NORAD_CAT_ID, pixelSize: 3, color: color(TYPE_COLORS.UNK), show: false }));
      const byNorad = new Map(records.map((r, i) => [r.NORAD_CAT_ID, i]));
      const wanted = new Array<boolean>(records.length).fill(false); // passes filters (or is selected)
      let styled = false;
      let latest: Float64Array | null = null;
      let pending = false;
      let requestId = 0;
      let orbitPath: InstanceType<typeof Cesium.Polyline> | null = null;
      let pairLine: InstanceType<typeof Cesium.Polyline> | null = null;

      const restyle = () => {
        const s = getState();
        records.forEach((r, i) => {
          const p = prims[i];
          const kind = (["PAY", "DEB", "R/B"].includes(r.OBJECT_TYPE) ? r.OBJECT_TYPE : "UNK") as ObjectType;
          const isFocus = s.focus?.norads.includes(r.NORAD_CAT_ID) ?? false;
          const isSelected = s.selected === r.NORAD_CAT_ID;
          const risky = s.riskNorads.has(r.NORAD_CAT_ID);
          let css =
            s.colorMode === "type" ? TYPE_COLORS[kind] : s.colorMode === "altitude" ? altitudeColor(perigee[i]) : risky ? "#eb6834" : "#898781";
          const dimmed = s.sim !== null || (s.colorMode === "risk" && !risky && !isFocus);
          if (s.sim) css = "#898781"; // the catalog steps back while fragments are shown
          p.color = color(css).withAlpha(s.sim ? 0.12 : dimmed ? 0.35 : 1);
          p.pixelSize = isSelected || isFocus ? 10 : s.sim ? 2 : s.colorMode === "risk" && risky ? 5 : 3;
          p.outlineWidth = isSelected || isFocus ? 2 : 0;
          p.outlineColor = Cesium.Color.WHITE;
          wanted[i] = visible(r, s.filters) || isSelected || isFocus;
        });
        styled = true;
        place();
      };

      const place = () => {
        if (!latest) return;
        const pos = latest;
        prims.forEach((p, i) => {
          const x = pos[i * 3];
          const ok = Number.isFinite(x) && wanted[i];
          p.show = ok;
          if (ok) p.position = new Cesium.Cartesian3(x, pos[i * 3 + 1], pos[i * 3 + 2]);
        });
        drawPair();
        viewer.scene.requestRender();
      };

      const drawPair = () => {
        const f = getState().focus;
        if (pairLine) {
          lines.remove(pairLine);
          pairLine = null;
        }
        if (!f || !latest) return;
        const [a, b] = f.norads.map((n) => byNorad.get(n));
        if (a === undefined || b === undefined) return;
        const pa = new Cesium.Cartesian3(latest[a * 3], latest[a * 3 + 1], latest[a * 3 + 2]);
        const pb = new Cesium.Cartesian3(latest[b * 3], latest[b * 3 + 1], latest[b * 3 + 2]);
        if ([pa.x, pb.x].some((v) => !Number.isFinite(v))) return;
        pairLine = lines.add({ positions: [pa, pb], width: 2, material: Cesium.Material.fromType("Color", { color: Cesium.Color.WHITE }) });
      };

      worker.onmessage = (event) => {
        const msg = event.data;
        if (msg.kind === "loaded") {
          setStatus("");
          tick();
        } else if (msg.kind === "positions") {
          pending = false;
          latest = msg.positions as Float64Array;
          if (!styled) restyle();
          else place();
        } else if (msg.kind === "path") {
          if (orbitPath) lines.remove(orbitPath);
          orbitPath = null;
          const p = msg.positions as Float64Array;
          const pts = [];
          for (let k = 0; k < p.length; k += 3) if (Number.isFinite(p[k])) pts.push(new Cesium.Cartesian3(p[k], p[k + 1], p[k + 2]));
          if (pts.length > 2) orbitPath = lines.add({ positions: pts, width: 1.5, material: Cesium.Material.fromType("Color", { color: Cesium.Color.WHITE.withAlpha(0.7) }) });
          viewer.scene.requestRender();
        }
      };

      const tick = () => {
        if (pending) return;
        pending = true;
        worker.postMessage({ kind: "positions", id: ++requestId, time: getState().time });
      };

      // Simulation clock: advance by speed while playing; recompute a few times a second.
      let last = performance.now();
      const clock = window.setInterval(() => {
        const now = performance.now();
        const s = getState();
        if (s.playing) setState({ time: s.time + (now - last) * s.speed });
        last = now;
        tick();
      }, UPDATE_MS);

      // Picking an object selects it.
      const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
      handler.setInputAction((click: { position: InstanceType<typeof Cesium.Cartesian2> }) => {
        const picked = viewer.scene.pick(click.position);
        const id = picked?.id;
        if (typeof id === "number") setState({ selected: id, focus: null });
      }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

      // React to store changes: styling, the selected orbit path, camera moves, simulator fragments.
      let prev = getState();
      const unsubscribe = subscribe(() => {
        const s = getState();
        if (s.filters !== prev.filters || s.colorMode !== prev.colorMode || s.selected !== prev.selected || s.focus !== prev.focus || s.riskNorads !== prev.riskNorads || s.sim !== prev.sim) {
          restyle();
        }
        if (s.selected !== prev.selected) {
          if (orbitPath) lines.remove(orbitPath);
          orbitPath = null;
          if (s.selected !== null) worker.postMessage({ kind: "path", id: ++requestId, norad: s.selected, time: s.time, samples: 180 });
          viewer.scene.requestRender();
        }
        if (s.focus && s.focus !== prev.focus) {
          // Fly to the closest approach once positions at TCA arrive.
          const target = s.focus;
          setTimeout(() => {
            const i = byNorad.get(target.norads[0]);
            if (i === undefined || !latest) return;
            const p = new Cesium.Cartesian3(latest[i * 3], latest[i * 3 + 1], latest[i * 3 + 2]);
            if (!Number.isFinite(p.x)) return;
            const carto = Cesium.Cartographic.fromCartesian(p);
            viewer.camera.flyTo({
              destination: Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, carto.height + 2.5e6),
              duration: prefersReducedMotion ? 0 : 1.5,
            });
          }, UPDATE_MS * 2);
        }
        if (s.sim && s.sim.response !== prev.sim?.response) {
          // A new simulation: step back so the whole debris cloud is in view.
          viewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(10, 20, 3.2e7), duration: prefersReducedMotion ? 0 : 1.5 });
        }
        if (s.sim !== prev.sim) {
          fragments.removeAll();
          if (s.sim) {
            const frame = s.sim.response.frames[s.sim.frame];
            for (const [lat, lon, alt] of frame.points) {
              fragments.add({ position: Cesium.Cartesian3.fromDegrees(lon, lat, alt * 1000), pixelSize: 5, color: color("#eb6834") });
            }
          }
          viewer.scene.requestRender();
        }
        prev = s;
      });

      cleanup = () => {
        unsubscribe();
        window.clearInterval(clock);
        handler.destroy();
        worker.terminate();
        viewer.destroy();
      };
    })().catch((e: Error) => setStatus(`The globe could not start: ${e.message}`));
    return () => {
      disposed = true;
      cleanup();
    };
  }, [meta.orbit_file_url]);

  return (
    <div className="globe">
      <div ref={container} className="globe-canvas" />
      {status && <div className="globe-status" role="status">{status}</div>}
      {simActive && <div className="sim-label" role="note">Illustrative, simplified; not a prediction.</div>}
      <TimeBar snapshot={meta.snapshot_time} />
      <Legend provisional={!meta.history_complete} />
    </div>
  );
}

function Legend({ provisional }: { provisional: boolean }) {
  const mode = useStore((s) => s.colorMode);
  const filters = useStore((s) => s.filters);
  return (
    <div className="legend" aria-label="Legend and filters">
      <label>
        Colour by{" "}
        <select value={mode} onChange={(e) => setState({ colorMode: e.target.value as typeof mode })}>
          <option value="type">object type</option>
          <option value="altitude">altitude</option>
          <option value="risk">elevated risk estimate</option>
        </select>
      </label>
      {mode === "type" && (
        <ul>
          {(["PAY", "DEB", "R/B", "UNK"] as ObjectType[]).map((t) => (
            <li key={t}>
              <label>
                <input
                  type="checkbox"
                  checked={filters.types[t]}
                  onChange={(e) => setState((s) => ({ filters: { ...s.filters, types: { ...s.filters.types, [t]: e.target.checked } } }))}
                />
                <span className="swatch" style={{ background: TYPE_COLORS[t] }} aria-hidden />
                {{ PAY: "Payloads", DEB: "Debris", "R/B": "Rocket bodies", UNK: "Unknown" }[t]}
              </label>
            </li>
          ))}
        </ul>
      )}
      {mode === "altitude" && <div className="ramp" aria-label="Light = low orbit, dark = high orbit"><span>low</span><span>high</span></div>}
      {mode === "risk" && (
        <p className="muted">
          Highlighted: objects in close approaches above the model's operating point
          {provisional ? " (provisional estimates this run: under 14 days of update history)" : ""}. Not a collision probability.
        </p>
      )}
      <label className="alt-filter">
        Max perigee{" "}
        <select
          value={filters.maxAltKm}
          onChange={(e) => setState((s) => ({ filters: { ...s.filters, maxAltKm: Number(e.target.value) } }))}
        >
          <option value={50000}>all orbits</option>
          <option value={2000}>LEO (2,000 km)</option>
          <option value={1000}>1,000 km</option>
          <option value={600}>600 km</option>
        </select>
      </label>
    </div>
  );
}
