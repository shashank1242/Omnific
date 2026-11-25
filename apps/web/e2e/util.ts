import type { Page } from "@playwright/test";

/** Requests that answer 404 by design (e.g. "this simulation is not published"): not errors. */
const EXPECTED_404 = [/^\/api\/gallery\/[0-9a-f]{32}$/, /^\/api\/jobs\/[0-9a-f]{32}\/thumbnail$/];

/** Everything that went wrong on a page: console errors, uncaught exceptions and failed same-site requests
 *  (by URL; the browser's own "Failed to load resource" console line has no URL, so it is left to this). */
export function watchErrors(page: Page, ignore: RegExp[] = []): string[] {
  const errors: string[] = [];
  const keep = (text: string) => {
    if (!ignore.some((re) => re.test(text))) errors.push(text);
  };
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) keep(`console: ${m.text()}`);
  });
  page.on("pageerror", (e) => keep(`page: ${e.message}`));
  page.on("requestfailed", (r) => {  // no response at all (refused, reset, aborted by the browser)
    const url = new URL(r.url());
    const why = r.failure()?.errorText ?? "";
    if (why.includes("ERR_ABORTED")) return; // the browser dropped it on navigation: normal
    if (url.origin === new URL(page.url() || r.url()).origin) keep(`failed ${url.pathname}: ${why}`);
  });
  page.on("response", (r) => {
    const url = new URL(r.url());
    if (url.origin !== new URL(page.url() || r.url()).origin || r.status() < 400) return;
    if (r.status() === 404 && EXPECTED_404.some((re) => re.test(url.pathname))) return;
    keep(`${r.status()} ${url.pathname}`);
  });
  return errors;
}

/** No sideways scrolling: the page fits the viewport width. */
export async function fitsWidth(page: Page): Promise<boolean> {
  return page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
}
