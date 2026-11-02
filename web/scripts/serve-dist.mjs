// Serves the production build (dist/) the way Cloudflare Pages does, for the browser
// tests of the static site: the headers from dist/_headers (content security policy
// included), compressed responses and 404s for missing files.
// Usage: node scripts/serve-dist.mjs [port]
import { createReadStream, existsSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize } from "node:path";
import { createGzip } from "node:zlib";

const root = join(process.cwd(), "dist");
const port = Number(process.argv[2] ?? 4180);
const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".wasm": "application/wasm", ".zip": "application/zip", ".whl": "application/zip",
  ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".xml": "application/xml", ".txt": "text/plain",
};
const COMPRESS = new Set([".html", ".js", ".mjs", ".css", ".json", ".svg", ".xml", ".txt", ".wasm"]);

// _headers: blocks of "path pattern" then indented "Name: value" lines.
const rules = [];
if (existsSync(join(root, "_headers"))) {
  let current = null;
  for (const line of readFileSync(join(root, "_headers"), "utf8").split("\n")) {
    if (!line.trim()) continue;
    if (!/^\s/.test(line)) rules.push((current = { pattern: line.trim(), headers: [] }));
    else if (current) {
      const i = line.indexOf(":");
      current.headers.push([line.slice(0, i).trim(), line.slice(i + 1).trim()]);
    }
  }
}
const matches = (pattern, path) =>
  pattern === path || (pattern.endsWith("*") && path.startsWith(pattern.slice(0, -1)));

createServer((req, res) => {
  const path = decodeURIComponent(new URL(req.url, "http://x").pathname);
  let file = normalize(join(root, path));
  if (!file.startsWith(root)) return res.writeHead(403).end();
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, "index.html");
  for (const r of rules) if (matches(r.pattern, path)) for (const [k, v] of r.headers) res.setHeader(k, v);
  if (!existsSync(file)) return res.writeHead(404, { "Content-Type": "text/plain" }).end("Not found");
  const ext = extname(file);
  res.setHeader("Content-Type", TYPES[ext] ?? "application/octet-stream");
  if (COMPRESS.has(ext) && /\bgzip\b/.test(req.headers["accept-encoding"] ?? "")) {
    res.writeHead(200, { "Content-Encoding": "gzip" });
    createReadStream(file).pipe(createGzip()).pipe(res);
  } else {
    res.writeHead(200);
    createReadStream(file).pipe(res);
  }
}).listen(port, "127.0.0.1", () => console.log(`serving dist on http://127.0.0.1:${port}`));
