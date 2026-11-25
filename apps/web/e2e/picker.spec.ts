import { expect, test, type Route } from "@playwright/test";
import { fitsWidth, watchErrors } from "./util";

// The asset picker against a mocked API: a job waiting for picks, options with live evaluation badges.
const JOB = "c".repeat(32);
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

const option = (id: string, status: string, score: number, detail = "") => ({
  candidate: { source: "sketchfab", id, name: `Bench ${id}`, license: "CC-BY 4.0", author: "Ann", url: `https://x.test/${id}`,
    tris: 1200, animated: false },
  thumb_key: `jobs/${JOB}/choices/bench/${id}.png`, score, reason: "", status, detail,
});

function job(stage: string) {
  return {
    job_id: JOB, state: "running", status: { job_id: JOB, stage, revision: 0, message: "2 objects to choose" },
    draft: null, result: null, questions: null,
    asset_choices: {
      ready: false,
      entities: [
        { entity: "bench", kind: "park_bench", default_route: "library", routes_available: ["library", "cad", "procedural"],
          query: "wooden park bench", note: "",
          options: [option("a1", "passed", 9), option("b2", "failed", 6, "critic.library: not a bench"), option("c3", "shortlisted", 7)] },
        { entity: "ball", kind: "sphere", default_route: "procedural", routes_available: ["library", "procedural"],
          query: "ball", note: "no CC0 / CC-BY models found for 'ball'", options: [] },
      ],
    },
  };
}

test("choose models while the simulation builds @phone", async ({ page }) => {
  const errors = watchErrors(page);
  let posted: unknown = null;
  await page.route(`**/api/jobs/${JOB}`, (r: Route) => r.fulfill({ json: job("awaiting_asset_picks") }));
  await page.route(`**/api/jobs/${JOB}/versions`, (r: Route) => r.fulfill({ json: [] }));
  await page.route(`**/api/jobs/${JOB}/choices/**`, (r: Route) => r.fulfill({ body: PNG, contentType: "image/png" }));
  await page.route(`**/api/jobs/${JOB}/assets/picks`, async (r: Route) => {
    posted = r.request().postDataJSON();
    await r.fulfill({ status: 202, json: { status: "accepted" } });
  });

  await page.goto(`/jobs/${JOB}`);
  await expect(page.getByRole("heading", { name: "Choose the 3D models" })).toBeVisible();
  await expect(page.getByText("Choose the 3D models below, or let Omnific pick for you.")).toBeVisible();
  // live evaluation badges, and a failed option can't be chosen
  await expect(page.getByText("Passed every check")).toBeVisible();
  await expect(page.getByText(/Failed a check: critic.library/)).toBeVisible();
  await expect(page.locator("label").filter({ hasText: "Bench b2" }).locator("input")).toBeDisabled();
  // the ball has no model to pick: no library type, only the types that exist
  const ballTypes = page.getByRole("radiogroup", { name: "How ball is made" });
  await expect(ballTypes.getByRole("radio")).toHaveCount(2);

  await page.locator("label").filter({ hasText: "Bench c3" }).click();
  await ballTypes.getByLabel("Simple shape").check();
  expect(await fitsWidth(page)).toBe(true);
  await page.getByRole("button", { name: "Use these models" }).click();
  await expect(page.getByText("Your choices are in")).toBeVisible();
  expect(posted).toEqual({ picks: [
    { entity: "bench", route: "library", candidate_id: "sketchfab/c3" },
    { entity: "ball", route: "procedural", candidate_id: "" },
  ] });
  expect(errors).toEqual([]);
});

test("let Omnific choose sends no picks", async ({ page }) => {
  let posted: unknown = null;
  await page.route(`**/api/jobs/${JOB}`, (r: Route) => r.fulfill({ json: job("running") }));
  await page.route(`**/api/jobs/${JOB}/versions`, (r: Route) => r.fulfill({ json: [] }));
  await page.route(`**/api/jobs/${JOB}/choices/**`, (r: Route) => r.fulfill({ body: PNG, contentType: "image/png" }));
  await page.route(`**/api/jobs/${JOB}/assets/picks`, async (r: Route) => {
    posted = r.request().postDataJSON();
    await r.fulfill({ status: 202, json: { status: "accepted" } });
  });
  await page.goto(`/jobs/${JOB}`);
  await expect(page.getByText("No rush: the build continues meanwhile.")).toBeVisible();
  await page.getByRole("button", { name: "Let Omnific choose all" }).click();
  await expect.poll(() => posted).toEqual({ picks: [] });
});
