// Writes dist/_headers for Cloudflare Pages: security headers (a content security
// policy that allows only this site, the API, NASA's imagery tiles and, if set,
// Sentry) and cache lifetimes (hashed assets for a year, index.html never).
// Run after `vite build`, with the same VITE_API_BASE the build used:
//   VITE_API_BASE=https://owner-perigee.hf.space node scripts/write-headers.mjs
import { writeFileSync } from "node:fs";

const GIBS = "https://gibs.earthdata.nasa.gov";

export function originOf(url) {
  if (!url) return "";
  const u = new URL(url);
  if (u.protocol !== "https:" && u.hostname !== "localhost" && u.hostname !== "127.0.0.1") {
    throw new Error(`API base must be https: ${url}`);
  }
  return u.origin;
}

export function contentSecurityPolicy({ apiBase = "", sentryDsn = "" } = {}) {
  const api = originOf(apiBase);
  const sentry = sentryDsn ? new URL(sentryDsn).origin : "";
  const connect = ["'self'", api, GIBS, sentry].filter(Boolean).join(" ");
  return [
    "default-src 'self'",
    // Cesium needs both (checked: without them the globe does not start). Its widgets
    // bind with Knockout, which compiles bindings with new Function, and its decoders
    // are WebAssembly. Scripts still load only from this site.
    "script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval'",
    "worker-src 'self' blob:",
    "style-src 'self' 'unsafe-inline'",
    `img-src 'self' data: blob: ${GIBS}`,
    `connect-src ${connect}`,
    "font-src 'self' data:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "upgrade-insecure-requests",
  ].join("; ");
}

export function headersFile(options) {
  return `/*
  Content-Security-Policy: ${contentSecurityPolicy(options)}
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
  Cross-Origin-Opener-Policy: same-origin
  Strict-Transport-Security: max-age=31536000

/
  Cache-Control: no-cache

/index.html
  Cache-Control: no-cache

/assets/*
  Cache-Control: public, max-age=31536000, immutable

/cesium/*
  Cache-Control: public, max-age=604800
`;
}

if (process.argv[1]?.endsWith("write-headers.mjs")) {
  const apiBase = process.env.VITE_API_BASE ?? "";
  if (!apiBase) console.warn("VITE_API_BASE is empty: the policy allows the API only on this site's own origin.");
  writeFileSync("dist/_headers", headersFile({ apiBase, sentryDsn: process.env.VITE_SENTRY_DSN ?? "" }));
  console.log("wrote dist/_headers");
}
