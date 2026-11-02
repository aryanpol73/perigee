// The installed app's layout (src/mobile), on a phone-sized screen: bottom navigation,
// close approaches as cards with a detail sheet, search and the watch list, settings,
// and the back button. The website keeps the dashboard on the same phone. On the
// production static site: the files that make it installable and work offline.
import { expect, test, type Page } from "@playwright/test";

const STATIC = process.env.PW_MODE === "static";
const NOW = "2026-11-01T12:00:00Z";
const errors: string[] = [];

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

test.beforeEach(async ({ page }) => {
  errors.length = 0;
  page.on("pageerror", (e) => errors.push(e.message));
  // Battery quality: CI runners draw WebGL in software.
  await page.addInitScript(() => {
    try {
      window.localStorage.setItem("perigee.quality", "battery");
    } catch {
      // about:blank and other opaque documents have no storage
    }
  });
});

test.afterEach(() => {
  expect(errors, `uncaught page errors: ${errors.join("; ")}`).toEqual([]);
});

const app = (hash = "") => `/?app=1&now=${NOW}${hash}`;

async function appReady(page: Page) {
  await expect(page.locator(".m-nav")).toBeVisible();
  await expect(page.locator(".m-splash")).toHaveCount(0);
  await expect(page.locator(".globe-canvas canvas")).toBeAttached();
}

const screen = (page: Page, name: string) => page.locator(`[data-screen="${name}"]`);

test("the app opens on the globe with its time controls", async ({ page }) => {
  await page.goto(app());
  await appReady(page);
  await expect(page.locator(".m-appbar")).toContainText("Perigee");
  await expect(page.locator(".m-appbar .mode-badge")).toContainText("LIVE");
  await expect(page.getByTestId("m-time")).toBeVisible();
  await expect(page.locator(".topbar")).toHaveCount(0); // not the dashboard
  await page.getByRole("button", { name: "Speed real time; tap to change" }).click();
  await expect(page.locator(".m-appbar .mode-badge")).toContainText("FORECAST");
  await page.locator(".m-timebar").getByRole("button", { name: "Live" }).click();
  await expect(page.locator(".m-appbar .mode-badge")).toContainText("LIVE");
});

test("a close approach opens in a sheet and shows on the globe", async ({ page }) => {
  await page.goto(app());
  await appReady(page);
  await page.locator(".m-nav").getByRole("button", { name: "Approaches" }).click();
  const cards = screen(page, "conjunctions").locator(".m-approach");
  await expect(cards.first()).toBeVisible();
  expect(await cards.count()).toBeGreaterThan(5);
  await expect(screen(page, "conjunctions").locator(".tag.provisional").first()).toBeVisible();
  await screen(page, "conjunctions").getByRole("button", { name: "Under 1 km" }).click();
  await expect(screen(page, "conjunctions").getByRole("button", { name: "Under 1 km" })).toHaveAttribute("aria-pressed", "true");
  await cards.first().click();
  const sheet = page.getByRole("dialog", { name: "Close approach" });
  await expect(sheet).toContainText("Predicted miss");
  await expect(sheet).toContainText("Not a collision probability");
  // The back button closes the sheet, not the screen.
  await page.goBack();
  await expect(sheet).toHaveCount(0);
  await expect(page).toHaveURL(/#\/conjunctions$/);
  await cards.first().click();
  await sheet.getByRole("button", { name: "Show on globe" }).click();
  await expect(page.locator(".m-screen.active")).toHaveCount(0); // back on the globe (the first entry)
  await expect(page.locator(".m-appbar .mode-badge")).toContainText("FORECAST"); // paused at the closest approach
  await expect(page.getByRole("region", { name: "Selected close approach" })).toContainText("×");
});

test("search finds an object, which can be watched", async ({ page }) => {
  await page.goto(app("#/object"));
  await appReady(page);
  await page.getByLabel("Find by name or NORAD id").fill("ISS");
  const results = page.getByRole("list", { name: "Results" });
  await results.getByRole("button").first().click();
  const objectPage = page.locator(".m-page.open");
  await expect(objectPage).toContainText("Perigee");
  await expect(objectPage).toContainText("Close approaches in the next 7 days");
  await objectPage.getByRole("button", { name: "Watch" }).click();
  await expect(objectPage.getByRole("button", { name: "Watching" })).toHaveAttribute("aria-pressed", "true");
  await objectPage.getByRole("button", { name: "Back to search" }).click();
  await expect(page.locator(".m-page.open")).toHaveCount(0);
  await page.getByLabel("Find by name or NORAD id").fill("");
  await expect(screen(page, "object").locator(".m-list")).toContainText("ISS");
  // Settings lists it too, and the globe settings apply.
  await page.locator(".m-nav").getByRole("button", { name: "Settings" }).click();
  await expect(screen(page, "settings")).toContainText("Watched objects (1)");
  await screen(page, "settings").getByRole("radio", { name: /High/ }).click();
  await expect(screen(page, "settings").getByRole("radio", { name: /High/ })).toHaveAttribute("aria-checked", "true");
  expect(await page.evaluate(() => window.localStorage.getItem("perigee.quality"))).toBe("high");
  await screen(page, "settings").getByRole("button", { name: /Method and limits/ }).click();
  await expect(page.locator(".m-page.open")).toContainText("Why it is inaccurate");
});

test("risk by altitude and the simulator have their own views", async ({ page }) => {
  test.setTimeout(STATIC ? 200_000 : 90_000); // the static site loads Python for the simulator
  await page.goto(app("#/risk"));
  await appReady(page);
  await expect(screen(page, "risk")).toContainText("The riskiest bands now");
  await screen(page, "risk").getByRole("tab", { name: "Simulator" }).click();
  await expect(page).toHaveURL(/#\/simulator$/);
  await screen(page, "risk").getByRole("button", { name: "Simulate collision" }).click();
  const toGlobe = screen(page, "risk").getByRole("button", { name: /see them on the globe/ });
  await expect(toGlobe).toBeVisible({ timeout: STATIC ? 150_000 : 30_000 });
  await toGlobe.click();
  await expect(page.getByRole("region", { name: "Breakup simulation" })).toContainText("fragments");
  await expect(page.locator(".sim-label")).toContainText("not a prediction");
});

test("the website keeps the dashboard on a phone", async ({ page }) => {
  await page.goto(`/?now=${NOW}`);
  await expect(page.locator(".topbar")).toBeVisible();
  await expect(page.locator(".m-nav")).toHaveCount(0);
});

test.describe("installable", () => {
  test.skip(!STATIC, "the production build only");
  test.use({ serviceWorkers: "allow" });

  test("manifest, icons, asset links and an offline-ready service worker", async ({ page, request }) => {
    const manifest = await (await request.get("/manifest.webmanifest")).json();
    expect(manifest.start_url).toBe("/?app=1");
    expect(manifest.display).toBe("standalone");
    for (const icon of manifest.icons) expect((await request.get(icon.src)).headers()["content-type"]).toBe("image/png");
    const links = await (await request.get("/.well-known/assetlinks.json")).json();
    expect(links[0].target.package_name).toBe("io.github.aryanpol73.perigee");
    expect((await request.get("/sw.js")).headers()["cache-control"]).toBe("no-cache");

    await page.goto(app());
    await appReady(page);
    const state = await page.evaluate(async () => {
      const reg = await navigator.serviceWorker.ready;
      return { active: reg.active?.state, caches: await caches.keys() };
    });
    expect(state.active).toBe("activated");
    expect(state.caches).toContain("perigee-shell-v1");
  });
});
