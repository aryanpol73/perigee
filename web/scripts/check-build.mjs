// Fails the build if Cesium's static files are not where the app loads them from
// (/cesium/...). The browser tests run against the dev server, which serves Cesium
// from node_modules, so only this catches a broken production layout.
import { existsSync } from "node:fs";

const required = [
  "dist/index.html",
  "dist/cesium/Assets/approximateTerrainHeights.json",
  "dist/cesium/Assets/Textures/NaturalEarthII/tilemapresource.xml",
  "dist/cesium/Assets/Textures/SkyBox/tycho2t3_80_px.jpg",
  "dist/cesium/Widgets/widgets.css",
  "dist/cesium/Workers",
  "dist/cesium/ThirdParty",
];
const missing = required.filter((p) => !existsSync(p));
if (missing.length) {
  console.error(`Build is missing files the app loads:\n  ${missing.join("\n  ")}`);
  process.exit(1);
}
console.log("build layout ok");
