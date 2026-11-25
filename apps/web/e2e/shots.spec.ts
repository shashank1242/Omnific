import { readFileSync } from "node:fs";
import path from "node:path";
import { test, type Route } from "@playwright/test";

// Not a check: screenshots for a human look at the dressed viewer (run with E2E_SHOTS=1).
test.skip(!process.env.E2E_SHOTS, "screenshots only on request");

const JOB = "e".repeat(32);
const FIX = path.join(__dirname, "fixtures", "dressed");

test("screenshot the dressed viewer", async ({ page }, info) => {
  const outcome = { job_id: JOB, status: "approved", revisions: 1, review_round: 0, reviews: [], spec: null, research: null,
    eval_report: null, code_key: null, result_key: null, failure_reason: "", playback_key: "p", scene_key: "s",
    blend_key: "b", dress_report: { dressed: true, attempts: 1, evals: [], notes: [], scripts: [] } };
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
  await page.waitForTimeout(2500);
  await page.screenshot({ path: info.outputPath(`viewer-${info.project.name}.png`) });
  await page.getByRole("button", { name: /^(Play|Replay)$/ }).click();
  await page.waitForTimeout(4000);
  await page.locator("canvas").first().screenshot({ path: info.outputPath(`canvas-${info.project.name}.png`) });
});
