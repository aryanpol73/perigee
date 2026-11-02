// Browser journey tests (npm run e2e). Two modes, the same tests:
//   default            the dev app against the API on the committed fixture bundle
//   PW_MODE=static     the production build of the static site (what Cloudflare serves:
//                      no API, data files, the browser simulator, the real headers),
//                      built from tests/fixtures/static and served like Pages
// Both use their own ports, so a running dev server is untouched.
// Locally this drives the installed Chrome (PW_CHANNEL=msedge for Edge); CI can run
// `npx playwright install chromium` and set PW_CHANNEL="" to use the bundled one.
import { resolve } from "node:path";
import { defineConfig } from "@playwright/test";

const API_PORT = 8010;
const WEB_PORT = 5180;
// Playwright runs from web/; the API starts from the repository root.
const python =
  process.env.PERIGEE_PYTHON ?? (process.platform === "win32" ? resolve("..", ".venv", "Scripts", "python.exe") : "python");
const channel = process.env.PW_CHANNEL ?? "chrome";
const STATIC = process.env.PW_MODE === "static";
const STATIC_PORT = 4181;

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  // On CI, failures also become annotations on the run (readable without the log).
  reporter: process.env.CI ? [["list"], ["github"]] : [["list"]],
  use: {
    baseURL: STATIC ? `http://127.0.0.1:${STATIC_PORT}` : `http://localhost:${WEB_PORT}`,
    channel: channel || undefined,
    viewport: { width: 1280, height: 800 },
    // Cesium needs WebGL; headless browsers get it from the SwiftShader software renderer.
    launchOptions: { args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] },
    trace: "retain-on-failure",
    // The production build registers a service worker (offline app); tests get fresh
    // network answers unless one asks for it (e2e/mobile.spec.ts).
    serviceWorkers: "block",
  },
  webServer: STATIC
    ? [
        {
          command: `node scripts/build-site.mjs --data ../tests/fixtures/static && node scripts/serve-dist.mjs ${STATIC_PORT}`,
          url: `http://127.0.0.1:${STATIC_PORT}/data/index.json`,
          timeout: 300_000,
          reuseExistingServer: false,
        },
      ]
    : [
    {
      command: `"${python}" -m src.main api --fixture --port ${API_PORT}`,
      cwd: "..",
      url: `http://127.0.0.1:${API_PORT}/v1/meta`,
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: `npx vite --port ${WEB_PORT}`,
      env: { PERIGEE_API_URL: `http://127.0.0.1:${API_PORT}` },
      url: `http://localhost:${WEB_PORT}`,
      timeout: 120_000,
      reuseExistingServer: false,
    },
  ],
});
