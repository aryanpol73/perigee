// Puts Pyodide (Python in WebAssembly) and the packages the simulator needs into
// public/pyodide/, so the site serves them itself: no third-party CDN at runtime,
// and the content security policy stays 'self'. The runtime comes from the pinned
// npm package; the numpy and pandas wheels are downloaded once from the matching
// Pyodide release and checked against the sha256 in its pyodide-lock.json.
// Runs before dev, build and the browser tests (package.json).
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const pkg = join(web, "node_modules", "pyodide");
const out = join(web, "public", "pyodide");
const cache = join(web, "node_modules", ".cache", "pyodide-wheels");
const version = JSON.parse(readFileSync(join(pkg, "package.json"), "utf8")).version;
const lock = JSON.parse(readFileSync(join(pkg, "pyodide-lock.json"), "utf8"));
const RUNTIME = ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"];
const WANTED = ["numpy", "pandas"];

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

// The wanted packages and everything they depend on.
const packages = new Set();
const visit = (name) => {
  if (packages.has(name)) return;
  const p = lock.packages[name];
  if (!p) throw new Error(`pyodide-lock.json has no package ${name}`);
  packages.add(name);
  (p.depends ?? []).forEach(visit);
};
WANTED.forEach(visit);

mkdirSync(out, { recursive: true });
mkdirSync(cache, { recursive: true });
for (const f of RUNTIME) copyFileSync(join(pkg, f), join(out, f));

for (const name of packages) {
  const { file_name: file, sha256: want } = lock.packages[name];
  const cached = join(cache, file);
  let data = existsSync(cached) ? readFileSync(cached) : null;
  if (!data || sha256(data) !== want) {
    const url = `https://cdn.jsdelivr.net/pyodide/v${version}/full/${file}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
    data = Buffer.from(await res.arrayBuffer());
    if (sha256(data) !== want) throw new Error(`${file}: checksum does not match pyodide-lock.json`);
    writeFileSync(cached, data);
  }
  writeFileSync(join(out, file), data);
}
console.log(`pyodide ${version} with ${[...packages].join(", ")} in public/pyodide`);
