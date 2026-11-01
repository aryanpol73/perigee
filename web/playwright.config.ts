// Browser journey tests (npm run e2e). Starts the API on the committed fixture
// bundle and the web app on their own ports, so a running dev server is untouched.
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

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    channel: channel || undefined,
    viewport: { width: 1280, height: 800 },
    // Cesium needs WebGL; headless browsers get it from the SwiftShader software renderer.
    launchOptions: { args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] },
    trace: "retain-on-failure",
  },
  webServer: [
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
