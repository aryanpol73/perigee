import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { viteStaticCopy } from "vite-plugin-static-copy";

// Cesium needs its workers, widgets and assets served as static files; copy them
// into the build under /cesium (a common deployment failure if forgotten).
const cesiumSource = "node_modules/cesium/Build/Cesium";
const apiTarget = process.env.PERIGEE_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig(({ command }) => ({
  plugins: [
    react(),
    viteStaticCopy({
      targets: ["Workers", "ThirdParty", "Assets", "Widgets"].map((dir) => ({
        src: `${cesiumSource}/${dir}`,
        dest: "cesium",
      })),
    }),
  ],
  define: {
    // The dev server serves Cesium straight from node_modules; builds use the copy.
    CESIUM_BASE_URL: JSON.stringify(command === "serve" ? `/${cesiumSource}` : "/cesium"),
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/v1": apiTarget },
  },
  build: {
    chunkSizeWarningLimit: 6000,
  },
  // The orbit worker is an ES module (satellite.js uses top-level await).
  worker: { format: "es" as const },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
}));
