import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";
import { fitsWidth, watchErrors } from "./util";

// A bomb detonating beside a black hole (fixtures/blast, made by make_blast.py with the real Data and Mesh code):
// effects, stages and key results on the job page.
const JOB = "b".repeat(32);
const FIX = path.join(__dirname, "fixtures", "blast");

async function openBlast(page: Page) {
  const spec = JSON.parse(readFileSync(path.join(FIX, "spec.json"), "utf-8"));
  const outcome = { job_id: JOB, status: "approved", revisions: 1, review_round: 0, reviews: [], spec, research: null,
    eval_report: null, code_key: null, result_key: null, failure_reason: "", playback_key: "p", scene_key: "s",
    blend_key: null, dress_report: null };
  await page.route(`**/api/jobs/${JOB}`, (r: Route) => r.fulfill({ json: { job_id: JOB, state: "completed",
    status: { job_id: JOB, stage: "approved", revision: 1, message: "" }, draft: null, result: outcome, questions: null } }));
  await page.route(`**/api/jobs/${JOB}/**`, (r: Route) => {
    const u = r.request().url();
    if (u.endsWith("playback.json")) return r.fulfill({ body: readFileSync(path.join(FIX, "playback.json")), contentType: "application/json" });
    if (u.endsWith("scene.gltf")) return r.fulfill({ body: readFileSync(path.join(FIX, "scene.gltf")), contentType: "model/gltf+json" });
    return r.fulfill({ json: [] });
  });
  await page.route("**/api/gallery/**", (r: Route) => r.fulfill({ status: 404, json: {} }));
  await page.goto(`/jobs/${JOB}`);
  await page.locator("canvas").first().waitFor();
}

test("stages, key results and the blast play in the viewer @phone", async ({ page }) => {
  const errors = watchErrors(page);
  await openBlast(page);

  const results = page.getByRole("region", { name: "Key results" });
  await expect(results.getByRole("listitem")).toHaveCount(4);
  await expect(results).toContainText("Peak fireball radius");
  await expect(results).toContainText("12 meter");
  await expect(results).toContainText("Redshift at the end");

  const stages = page.getByRole("group", { name: "Stages of the simulation" }).getByRole("button");
  await expect(stages).toHaveText(["Falling in", "Detonation", "Fireball grows", "Redshifted fade"]);
  const time = page.getByRole("slider", { name: "Simulation time" });
  await stages.nth(1).click();
  await expect.poll(async () => Number(await time.inputValue())).toBeCloseTo(2, 3);
  await expect(stages.nth(1)).toHaveAttribute("data-on", "true");
  await expect(page.locator("p[aria-live=polite]")).toHaveText("Detonation");

  // a key result jumps to its moment: peak brightness comes shortly after detonation
  await results.getByRole("button", { name: /Time of peak brightness/ }).click();
  await expect.poll(async () => Number(await time.inputValue())).toBeGreaterThan(2);

  await page.getByRole("button", { name: /^(Play|Replay)$/ }).click();
  await expect.poll(async () => Number(await time.inputValue()), { timeout: 8_000 }).toBeGreaterThan(2.5);
  expect(await fitsWidth(page)).toBe(true);
  expect(errors).toEqual([]);
});

test("screenshot the blast across its stages", async ({ page }, info) => {
  test.skip(!process.env.E2E_SHOTS, "screenshots only on request");
  await openBlast(page);
  const time = page.getByRole("slider", { name: "Simulation time" });
  for (const t of [1.5, 2.05, 2.6, 4, 7.5]) {
    await time.evaluate((el, v) => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      set?.call(el, String(v));
      el.dispatchEvent(new Event("input", { bubbles: true }));
    }, t);
    await page.waitForTimeout(600);
    await page.locator("canvas").first().screenshot({ path: info.outputPath(`blast-${t}.png`) });
  }
  await page.screenshot({ path: info.outputPath("blast-page.png"), fullPage: true });
});
