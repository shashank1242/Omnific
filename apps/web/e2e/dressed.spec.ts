import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Route } from "@playwright/test";
import { fitsWidth, watchErrors } from "./util";

// A finished job whose scene was dressed in Blender (fixtures captured from a real run): the viewer must stay fully
// interactive and offer the .blend.
const JOB = "d".repeat(32);
const FIX = path.join(__dirname, "fixtures", "dressed");
const scene = readFileSync(path.join(FIX, "scene.gltf"));
const playback = readFileSync(path.join(FIX, "playback.json"));

const outcome = {
  job_id: JOB, status: "approved", revisions: 1, review_round: 0, reviews: [], spec: null, research: null,
  eval_report: null, code_key: null, result_key: null, failure_reason: "",
  playback_key: `jobs/${JOB}/playback.json`, scene_key: `jobs/${JOB}/dress/attempt-0/scene.gltf`,
  raw_scene_key: `jobs/${JOB}/scene.gltf`, blend_key: `jobs/${JOB}/dress/attempt-0/scene.blend`,
  dress_report: {
    dressed: true, attempts: 1, notes: ["dressed in Blender on attempt 1"],
    evals: [{ stage: "dress", attempt: 0, passed: true, critique: "",
      checks: [{ name: "dress.protected", passed: true, detail: "the entities are untouched", blame: null },
               { name: "dress.critic", passed: true, detail: "score 9/10, clear", blame: null }] }],
    scripts: [{ agent: "scenery", code: "omnific.prop('park_bench', location=(6, 6, 0))", notes: "a bench" }],
    props: [{ id: "park_bench", name: "Park Bench", key: `jobs/${JOB}/props/park_bench.glb`, credit: "CC0" }],
  },
};

test("a Blender-dressed scene plays and offers the .blend @phone", async ({ page }) => {
  const errors = watchErrors(page);
  await page.route(`**/api/jobs/${JOB}`, (r: Route) => r.fulfill({ json: {
    job_id: JOB, state: "completed", status: { job_id: JOB, stage: "approved", revision: 1, message: "" },
    draft: null, result: outcome, questions: null } }));
  await page.route(`**/api/jobs/${JOB}/versions`, (r: Route) => r.fulfill({ json: [] }));
  await page.route(`**/api/jobs/${JOB}/variants`, (r: Route) => r.fulfill({ json: [] }));
  await page.route(`**/api/gallery/${JOB}`, (r: Route) => r.fulfill({ status: 404, json: { detail: "not published" } }));
  await page.route(`**/api/jobs/${JOB}/artifacts/playback.json`, (r: Route) =>
    r.fulfill({ body: playback, contentType: "application/json" }));
  await page.route(`**/api/jobs/${JOB}/artifacts/scene.gltf`, (r: Route) =>
    r.fulfill({ body: scene, contentType: "model/gltf+json" }));

  await page.goto(`/jobs/${JOB}`);
  await expect(page.locator("canvas").first()).toBeVisible();
  const time = page.getByRole("slider", { name: "Simulation time" });
  await page.getByRole("button", { name: /^(Play|Replay)$/ }).click();
  await expect.poll(async () => Number(await time.inputValue()), { timeout: 8_000 }).toBeGreaterThan(0);

  const blend = page.getByRole("link", { name: /Open in Blender/ });
  await expect(blend).toHaveAttribute("href", `/api/jobs/${JOB}/artifacts/scene.blend`);
  await expect(blend).toHaveAttribute("download", "");

  await page.getByRole("tab", { name: "3D models" }).click();
  await expect(page.getByRole("heading", { name: "Scene dressing in Blender" })).toBeVisible();
  await expect(page.getByText("Park Bench:")).toBeVisible();
  expect(await fitsWidth(page)).toBe(true);
  expect(errors).toEqual([]);
});
