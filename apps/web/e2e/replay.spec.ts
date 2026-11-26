import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Route } from "@playwright/test";

// Demo replay: a prompt the API recognises (replayed: true) plays the build diagram, then shows the finished run.
const JOB = "b".repeat(32);
const FIX = path.join(__dirname, "fixtures", "blast");

test("a replayed prompt plays the build, then shows the simulation", async ({ page }) => {
  const spec = JSON.parse(readFileSync(path.join(FIX, "spec.json"), "utf-8"));
  const outcome = { job_id: JOB, status: "approved", revisions: 1, review_round: 0, reviews: [], spec, research: null,
    eval_report: null, code_key: null, result_key: null, failure_reason: "", playback_key: "p", scene_key: "s",
    blend_key: null, dress_report: null };
  await page.clock.install();
  await page.route("**/api/jobs", (r: Route) => r.request().method() === "POST"
    ? r.fulfill({ json: { job_id: JOB, replayed: true } }) : r.fallback());
  await page.route(`**/api/jobs/${JOB}`, (r: Route) => r.fulfill({ json: { job_id: JOB, state: "completed",
    status: { job_id: JOB, stage: "approved", revision: 1, message: "" }, draft: null, result: outcome, questions: null } }));
  await page.route(`**/api/jobs/${JOB}/**`, (r: Route) => {
    const u = r.request().url();
    if (u.endsWith("playback.json")) return r.fulfill({ body: readFileSync(path.join(FIX, "playback.json")), contentType: "application/json" });
    if (u.endsWith("scene.gltf")) return r.fulfill({ body: readFileSync(path.join(FIX, "scene.gltf")), contentType: "model/gltf+json" });
    return r.fulfill({ json: [] });
  });
  await page.route("**/api/gallery/**", (r: Route) => r.fulfill({ status: 404, json: {} }));

  await page.goto("/");
  await page.getByLabel("Scenario").fill("Atomic bomb exploding in the event horizon of a black hole");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await page.waitForURL(`**/jobs/${JOB}`);
  await expect(page.getByText("Researching the physics", { exact: false })).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("canvas")).toHaveCount(0);  // nothing of the result yet
  await page.clock.runFor(40_000);
  await expect(page.locator("canvas").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("region", { name: "Key results" })).toBeVisible();
});
