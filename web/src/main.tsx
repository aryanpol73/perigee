import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { isAppMode, registerServiceWorker } from "./pwa";
import "./styles.css";

// Error tracking only when a DSN is set at build time (production); loaded after the
// app so it never delays the first paint. No personal data, no performance tracing.
const sentryDsn = import.meta.env.VITE_SENTRY_DSN;
if (sentryDsn) {
  void import("@sentry/browser").then((Sentry) =>
    Sentry.init({ dsn: sentryDsn, environment: import.meta.env.MODE, tracesSampleRate: 0 }), // the SDK sends no personal data by default
  );
}

// The installed app gets the mobile layout (its own chunk); the website the dashboard.
const MobileApp = lazy(() => import("./mobile/MobileApp"));
const app = isAppMode();
if (app) {
  document.documentElement.classList.add("app-mode");
  // Draw under the notch and the home indicator; the app pads with the safe areas.
  document.querySelector('meta[name="viewport"]')?.setAttribute("content", "width=device-width, initial-scale=1.0, viewport-fit=cover");
}
registerServiceWorker();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {app ? (
      <Suspense fallback={null}>
        <MobileApp />
      </Suspense>
    ) : (
      <App />
    )}
  </StrictMode>,
);
