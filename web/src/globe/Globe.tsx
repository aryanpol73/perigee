// The 3D globe. Every object is one point primitive (entities are far too heavy
// at 30,000). A Web Worker propagates positions and velocities with satellite.js a
// few times a second; every frame the main thread glides each point along a smooth
// curve between those samples (src/motion.ts), reusing the same objects so nothing
// is allocated per frame. The Earth uses NASA Blue Marble (day) and Black Marble
// (city lights) imagery with real sunlight for the shown time; the imagery bundled
// with Cesium sits underneath as a fallback.
import { useEffect, useRef, useState } from "react";
import type { Meta } from "../api";
import { get } from "../api";
import { Motion, type Sample } from "../motion";
import {
  STATIONS,
  STATION_COLOR,
  TYPE_COLORS,
  altitudeColor,
  altitudes,
  decodeOrbitFile,
  visible,
  type ObjectType,
  type Omm,
  type OrbitFile,
} from "../orbits";
import { getState, prefersReducedMotion, setQuality, setState, subscribe, time, useStore, type Quality } from "../store";
import { TimeBar } from "./TimeBar";

declare const CESIUM_BASE_URL: string;
(window as unknown as { CESIUM_BASE_URL: string }).CESIUM_BASE_URL = CESIUM_BASE_URL;

const SAMPLE_EVERY_MS = 120; // real time between worker samples while time moves
const TRAIL_MINUTES = 12;
const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";

// Measured per frame and exposed for the browser tests (window.__perigee).
interface Perf {
  frames: number;
  updateMs: number[]; // JS time spent placing points, last 120 frames
  samples: number;
}

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

      // No Cesium ion: bundled Natural Earth imagery underneath, NASA GIBS on top.
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
        shouldAnimate: false, // our clock drives Cesium's, so the Sun and the night side match the shown time
      });
      const scene = viewer.scene;
      const globe = scene.globe;
      const nasa = (layer: string, ext: string) =>
        new Cesium.UrlTemplateImageryProvider({
          url: `${GIBS}/${layer}/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.${ext}`,
          maximumLevel: 8,
          credit: new Cesium.Credit("Earth imagery: NASA Blue Marble &amp; Black Marble (GIBS)", true),
        });
      const day = viewer.imageryLayers.addImageryProvider(nasa("BlueMarble_ShadedRelief_Bathymetry", "jpeg"));
      const night = viewer.imageryLayers.addImageryProvider(nasa("VIIRS_Black_Marble", "png"));
      viewer.imageryLayers.get(0).nightAlpha = 0.0;
      day.nightAlpha = 0.0;
      night.dayAlpha = 0.0;
      night.nightAlpha = 1.0;
      night.brightness = 1.6;

      globe.baseColor = Cesium.Color.fromCssColorString("#0b1a2e");
      globe.showGroundAtmosphere = true;
      globe.dynamicAtmosphereLighting = true;
      globe.dynamicAtmosphereLightingFromSun = true;
      globe.atmosphereLightIntensity = 12;
      if (scene.skyAtmosphere) scene.skyAtmosphere.show = true;
      scene.backgroundColor = Cesium.Color.BLACK;
      if (scene.sun) scene.sun.show = true;
      if (scene.moon) scene.moon.show = true;
      viewer.camera.setView({ destination: Cesium.Cartesian3.fromDegrees(20, 15, 2.6e7) });

      const points = scene.primitives.add(new Cesium.PointPrimitiveCollection()) as InstanceType<typeof Cesium.PointPrimitiveCollection>;
      const fragments = scene.primitives.add(new Cesium.PointPrimitiveCollection()) as InstanceType<typeof Cesium.PointPrimitiveCollection>;
      const lines = scene.primitives.add(new Cesium.PolylineCollection()) as InstanceType<typeof Cesium.PolylineCollection>;
      const labels = scene.primitives.add(new Cesium.LabelCollection()) as InstanceType<typeof Cesium.LabelCollection>;
      const colors: Record<string, InstanceType<typeof Cesium.Color>> = {};
      const color = (css: string) => (colors[css] ??= Cesium.Color.fromCssColorString(css));
      // Points grow as the camera comes closer; far away they shrink to stay crisp.
      const sizeByDistance = new Cesium.NearFarScalar(4e5, 2.4, 3.5e7, 0.6);
      const glow = (css: string, power = 0.25) => Cesium.Material.fromType("PolylineGlow", { color: color(css), glowPower: power, taperPower: 1 });

      // --- Quality -----------------------------------------------------------------
      const applyQuality = (q: Quality) => {
        const high = q === "high";
        const battery = q === "battery";
        globe.enableLighting = !battery;
        night.show = !battery;
        day.show = true;
        scene.postProcessStages.fxaa.enabled = !battery;
        const bloom = scene.postProcessStages.bloom;
        bloom.enabled = high;
        bloom.uniforms.glowOnly = false;
        bloom.uniforms.contrast = 128;
        bloom.uniforms.brightness = -0.62; // only the brightest pixels glow
        bloom.uniforms.delta = 1.0;
        bloom.uniforms.sigma = 1.4;
        bloom.uniforms.stepSize = 1.0;
        viewer.useBrowserRecommendedResolution = !high; // full device pixels only on High
        viewer.targetFrameRate = battery ? 30 : 60;
        scene.requestRender();
      };
      applyQuality(getState().quality);

      // --- Orbit data and the worker ---------------------------------------------------
      const file = await get<OrbitFile>(meta.orbit_file_url);
      if (disposed) return;
      const records: Omm[] = decodeOrbitFile(file);
      const n = records.length;
      const perigee = records.map((r) => altitudes(r).perigee);
      const kinds = records.map((r) => (["PAY", "DEB", "R/B"].includes(r.OBJECT_TYPE) ? r.OBJECT_TYPE : "UNK") as ObjectType);
      const worker = new Worker(new URL("../orbit.worker.ts", import.meta.url), { type: "module" });
      worker.postMessage({ kind: "load", records });
      const prims = records.map((r) =>
        points.add({ id: r.NORAD_CAT_ID, pixelSize: 2.5, color: color(TYPE_COLORS.UNK), show: false, scaleByDistance: sizeByDistance }),
      );
      const byNorad = new Map(records.map((r, i) => [r.NORAD_CAT_ID, i]));
      const wanted = new Uint8Array(n); // passes filters (or is selected)
      const motion = new Motion(n);
      const positions = new Float64Array(n * 3);
      const scratch = new Cesium.Cartesian3();
      const julian = new Cesium.JulianDate();
      const perf: Perf = { frames: 0, updateMs: [], samples: 0 };
      (window as unknown as { __perigee: Perf }).__perigee = perf;
      let pending = false;
      let requestId = 0;
      let lastSampleAt = 0;
      let orbitPath: InstanceType<typeof Cesium.Polyline> | null = null;
      let pairLine: InstanceType<typeof Cesium.Polyline> | null = null;
      let trails: InstanceType<typeof Cesium.Polyline>[] = [];
      let lastTrailAt = 0;
      let lastShownTime = Number.NaN;
      let lastPlacedTime = Number.NaN;
      const tagged = new Map<number, InstanceType<typeof Cesium.Label>>();

      const restyle = () => {
        const s = getState();
        for (let i = 0; i < n; i++) {
          const r = records[i];
          const p = prims[i];
          const id = r.NORAD_CAT_ID;
          const isFocus = s.focus?.norads.includes(id) ?? false;
          const isSelected = s.selected === id;
          const risky = s.riskNorads.has(id);
          const station = STATIONS.has(id);
          let css =
            s.colorMode === "type"
              ? station
                ? STATION_COLOR
                : TYPE_COLORS[kinds[i]]
              : s.colorMode === "altitude"
                ? altitudeColor(perigee[i])
                : risky
                  ? TYPE_COLORS.DEB
                  : "#8d8a82";
          if (s.sim) css = "#8d8a82"; // the catalog steps back while fragments are shown
          const dim = s.sim ? 0.1 : s.colorMode === "risk" && !risky && !isFocus ? 0.3 : kinds[i] === "DEB" ? 0.85 : 1;
          p.color = color(isSelected || isFocus ? "#ffffff" : css).withAlpha(dim);
          p.pixelSize = isSelected || isFocus ? 9 : station ? 5 : s.sim ? 1.6 : s.colorMode === "risk" && risky ? 4.5 : kinds[i] === "DEB" ? 2 : 2.6;
          p.outlineWidth = isSelected || isFocus ? 2 : 0;
          p.outlineColor = color(css);
          wanted[i] = visible(r, s.filters) || isSelected || isFocus ? 1 : 0;
          if (!wanted[i]) p.show = false;
        }
        // Name labels for the selected object, the pair in focus and crewed stations.
        const names = new Set<number>([...(s.selected !== null ? [s.selected] : []), ...(s.focus?.norads ?? [])]);
        if (!s.sim) STATIONS.forEach((id) => byNorad.has(id) && wanted[byNorad.get(id)!] && names.add(id));
        for (const [id, label] of tagged) {
          if (!names.has(id)) {
            labels.remove(label);
            tagged.delete(id);
          }
        }
        for (const id of names) {
          const i = byNorad.get(id);
          if (i === undefined || tagged.has(id)) continue;
          const below = s.focus?.norads[1] === id;
          tagged.set(
            id,
            labels.add({
              position: Cesium.Cartesian3.ZERO,
              text: records[i].OBJECT_NAME,
              font: "600 13px system-ui, sans-serif",
              fillColor: Cesium.Color.WHITE,
              showBackground: true,
              backgroundColor: color("#0b0f17").withAlpha(0.72),
              backgroundPadding: new Cesium.Cartesian2(6, 4),
              pixelOffset: new Cesium.Cartesian2(12, below ? 12 : -12),
              horizontalOrigin: Cesium.HorizontalOrigin.LEFT,
              verticalOrigin: below ? Cesium.VerticalOrigin.TOP : Cesium.VerticalOrigin.BOTTOM,
              scaleByDistance: new Cesium.NearFarScalar(1e6, 1.0, 4e7, 0.7),
              show: false,
            }),
          );
        }
        lastShownTime = Number.NaN; // place everything on the next frame
        scene.requestRender();
      };

      // Per frame: current time -> Cesium's clock (Sun, night side) -> every point glides.
      const place = (t: number) => {
        const started = performance.now();
        if (!motion.positions(t, positions)) return;
        for (let i = 0; i < n; i++) {
          const p = prims[i];
          const o = i * 3;
          const x = positions[o];
          if (!wanted[i] || Number.isNaN(x)) {
            if (p.show) p.show = false;
            continue;
          }
          scratch.x = x;
          scratch.y = positions[o + 1];
          scratch.z = positions[o + 2];
          p.position = scratch; // copied into the point's own Cartesian3: no allocation
          if (!p.show) p.show = true;
        }
        for (const [id, label] of tagged) {
          const i = byNorad.get(id)!;
          const o = i * 3;
          if (Number.isNaN(positions[o]) || !wanted[i]) {
            label.show = false;
            continue;
          }
          scratch.x = positions[o];
          scratch.y = positions[o + 1];
          scratch.z = positions[o + 2];
          label.position = scratch;
          label.show = true;
        }
        drawPair();
        perf.frames++;
        perf.updateMs.push(performance.now() - started);
        if (perf.updateMs.length > 120) perf.updateMs.shift();
      };

      const drawPair = () => {
        const f = getState().focus;
        if (!f) {
          if (pairLine) pairLine.show = false;
          return;
        }
        const a = byNorad.get(f.norads[0]);
        const b = byNorad.get(f.norads[1]);
        if (a === undefined || b === undefined || Number.isNaN(positions[a * 3]) || Number.isNaN(positions[b * 3])) return;
        const pa = new Cesium.Cartesian3(positions[a * 3], positions[a * 3 + 1], positions[a * 3 + 2]);
        const pb = new Cesium.Cartesian3(positions[b * 3], positions[b * 3 + 1], positions[b * 3 + 2]);
        if (!pairLine) pairLine = lines.add({ positions: [pa, pb], width: 6, material: glow("#ffd166", 0.35) });
        else pairLine.positions = [pa, pb];
        pairLine.show = true;
        // A slow pulse so the pair stands out; steady with reduced motion.
        const pulse = prefersReducedMotion ? 0.9 : 0.6 + 0.4 * Math.sin(performance.now() / 400);
        (pairLine.material.uniforms as { color: InstanceType<typeof Cesium.Color> }).color.alpha = pulse;
      };

      const requestSample = (t: number) => {
        pending = true;
        lastSampleAt = performance.now();
        worker.postMessage({ kind: "sample", id: ++requestId, time: t });
      };

      const requestTrails = (t: number) => {
        const s = getState();
        const ids = [...new Set([...(s.selected !== null ? [s.selected] : []), ...(s.focus?.norads ?? [])])].filter((id) => byNorad.has(id));
        lastTrailAt = performance.now();
        if (!ids.length) {
          trails.forEach((l) => lines.remove(l));
          trails = [];
          return;
        }
        worker.postMessage({ kind: "trail", id: ++requestId, norads: ids, time: t, minutes: TRAIL_MINUTES, samples: 48 });
      };

      worker.onmessage = (event) => {
        const msg = event.data;
        if (msg.kind === "loaded") {
          setStatus("");
          restyle();
          requestSample(time.now());
        } else if (msg.kind === "sample") {
          pending = false;
          perf.samples++;
          motion.add({ time: msg.time, states: msg.states } as Sample);
          lastShownTime = Number.NaN;
          scene.requestRender();
        } else if (msg.kind === "path") {
          if (orbitPath) lines.remove(orbitPath);
          orbitPath = null;
          const p = msg.positions as Float64Array;
          const pts = [];
          for (let k = 0; k < p.length; k += 3) if (!Number.isNaN(p[k])) pts.push(new Cesium.Cartesian3(p[k], p[k + 1], p[k + 2]));
          if (pts.length > 2) orbitPath = lines.add({ positions: pts, width: 4, material: glow("#bfe3ff", 0.18) });
          scene.requestRender();
        } else if (msg.kind === "trail") {
          trails.forEach((l) => lines.remove(l));
          trails = [];
          const p = msg.positions as Float64Array;
          (msg.norads as number[]).forEach((_, k) => {
            const pts = [];
            for (let j = 0; j < msg.samples; j++) {
              const at = (k * msg.samples + j) * 3;
              if (!Number.isNaN(p[at])) pts.push(new Cesium.Cartesian3(p[at], p[at + 1], p[at + 2]));
            }
            if (pts.length > 2) trails.push(lines.add({ positions: pts, width: 5, material: glow("#ffffff", 0.3) }));
          });
          scene.requestRender();
        }
      };

      // Every rendered frame: read the clock, keep samples ahead of it, glide the points.
      const onPreRender = () => {
        const s = getState();
        const t = time.now();
        Cesium.JulianDate.fromDate(new Date(t), julian);
        viewer.clock.currentTime = julian;
        const moving = s.clock.playing;
        const speed = s.clock.mode === "live" ? 1 : s.clock.speed;
        const lead = moving ? Math.max(1_000, speed * SAMPLE_EVERY_MS * 2) : 0;
        const want = motion.next(t, lead);
        if (want !== null && !pending && (!moving || performance.now() - lastSampleAt >= SAMPLE_EVERY_MS)) requestSample(want);
        if ((trails.length || s.selected !== null || s.focus) && performance.now() - lastTrailAt > 1_500) requestTrails(t);
        if (t !== lastShownTime || getState().focus) {
          place(t);
          lastShownTime = t;
          lastPlacedTime = t;
        }
      };
      scene.preRender.addEventListener(onPreRender);

      // While time moves, render every frame; when it stands still, only on change (battery).
      let raf = 0;
      const loop = () => {
        if (getState().clock.playing) scene.requestRender();
        raf = window.requestAnimationFrame(loop);
      };
      raf = window.requestAnimationFrame(loop);

      // Picking an object selects it.
      const handler = new Cesium.ScreenSpaceEventHandler(scene.canvas);
      handler.setInputAction((click: { position: InstanceType<typeof Cesium.Cartesian2> }) => {
        const picked = scene.pick(click.position);
        const id = picked?.id;
        if (typeof id === "number") setState({ selected: id, focus: null });
      }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

      // React to store changes: styling, the selected orbit path, camera moves, simulator fragments.
      let prev = getState();
      const unsubscribe = subscribe(() => {
        const s = getState();
        if (s.quality !== prev.quality) applyQuality(s.quality);
        if (
          s.filters !== prev.filters ||
          s.colorMode !== prev.colorMode ||
          s.selected !== prev.selected ||
          s.focus !== prev.focus ||
          s.riskNorads !== prev.riskNorads ||
          s.sim !== prev.sim
        ) {
          restyle();
        }
        if (s.clock !== prev.clock) {
          if (Math.abs(time.now() - lastPlacedTime) > 300_000) motion.clear();
          lastShownTime = Number.NaN;
          scene.requestRender();
        }
        if (s.selected !== prev.selected || s.focus !== prev.focus) {
          if (orbitPath) lines.remove(orbitPath);
          orbitPath = null;
          if (s.selected !== null) worker.postMessage({ kind: "path", id: ++requestId, norad: s.selected, time: time.now(), samples: 240 });
          requestTrails(time.now());
          scene.requestRender();
        }
        if (s.focus && s.focus !== prev.focus) {
          // Fly to the closest approach once positions at TCA arrive.
          const target = s.focus;
          window.setTimeout(() => {
            const i = byNorad.get(target.norads[0]);
            if (i === undefined || Number.isNaN(positions[i * 3])) return;
            const carto = Cesium.Cartographic.fromCartesian(new Cesium.Cartesian3(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]));
            viewer.camera.flyTo({
              destination: Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, carto.height + 2.5e6),
              duration: prefersReducedMotion ? 0 : 1.5,
            });
          }, 500);
        }
        if (s.sim && s.sim.response !== prev.sim?.response) {
          // A new simulation: step back so the whole debris cloud is in view.
          viewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(10, 20, 3.2e7), duration: prefersReducedMotion ? 0 : 1.5 });
        }
        if (s.sim !== prev.sim) {
          fragments.removeAll();
          if (s.sim) {
            const frames = s.sim.response.frames;
            const frame = frames[s.sim.frame];
            // Fresh debris is bright; it dims as the cloud spreads.
            const age = frames.length > 1 ? s.sim.frame / (frames.length - 1) : 0;
            const c = color("#ff8a4c").withAlpha(1 - 0.45 * age);
            for (const [lat, lon, alt] of frame.points) {
              fragments.add({ position: Cesium.Cartesian3.fromDegrees(lon, lat, alt * 1000), pixelSize: 4, color: c, scaleByDistance: sizeByDistance });
            }
          }
          scene.requestRender();
        }
        prev = s;
      });

      cleanup = () => {
        unsubscribe();
        window.cancelAnimationFrame(raf);
        scene.preRender.removeEventListener(onPreRender);
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
  const quality = useStore((s) => s.quality);
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
          <li className="muted small">
            <span className="swatch" style={{ background: STATION_COLOR }} aria-hidden />
            Crewed stations (ISS, Tiangong)
          </li>
        </ul>
      )}
      {mode === "altitude" && (
        <div className="ramp" aria-label="Light = low orbit, deeper blue = high orbit">
          <span>low</span>
          <span>high</span>
        </div>
      )}
      {mode === "risk" && (
        <p className="muted">
          Highlighted: objects in close approaches above the model's operating point
          {provisional ? " (provisional estimates this run: under 14 days of update history)" : ""}. Not a collision probability.
        </p>
      )}
      <label className="alt-filter">
        Max perigee{" "}
        <select value={filters.maxAltKm} onChange={(e) => setState((s) => ({ filters: { ...s.filters, maxAltKm: Number(e.target.value) } }))}>
          <option value={50000}>all orbits</option>
          <option value={2000}>LEO (2,000 km)</option>
          <option value={1000}>1,000 km</option>
          <option value={600}>600 km</option>
        </select>
      </label>
      <label className="alt-filter">
        Quality{" "}
        <select value={quality} onChange={(e) => setQuality(e.target.value as Quality)}>
          <option value="high">high (glow, full resolution)</option>
          <option value="balanced">balanced</option>
          <option value="battery">battery saver</option>
        </select>
      </label>
    </div>
  );
}
