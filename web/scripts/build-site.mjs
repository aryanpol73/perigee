// Builds the complete static site: the app (reading its data from /data), the data
// exported by `python -m src.main export-static`, Pyodide for the simulator, and the
// security and cache headers. The deploy and publish workflows run this before
// uploading dist/ to Cloudflare Pages; the static browser tests run it on the fixture.
// Usage: node scripts/build-site.mjs --data <exported dir>
import { cpSync, existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { build } from "vite";
import { headersFile } from "./write-headers.mjs";

const i = process.argv.indexOf("--data");
const data = i > 0 ? process.argv[i + 1] : null;
if (!data || !existsSync(join(data, "index.json"))) {
  console.error("Usage: node scripts/build-site.mjs --data <dir written by `python -m src.main export-static`>");
  process.exit(2);
}

process.env.VITE_DATA_BASE = "/data";
execFileSync(process.execPath, ["scripts/vendor-pyodide.mjs"], { stdio: "inherit" });
await build({ logLevel: "warn" });
cpSync(data, "dist/data", { recursive: true });
writeFileSync("dist/_headers", headersFile({ sentryDsn: process.env.VITE_SENTRY_DSN ?? "" }));
execFileSync(process.execPath, ["scripts/check-build.mjs", "--static"], { stdio: "inherit" });
console.log("static site in dist/");
