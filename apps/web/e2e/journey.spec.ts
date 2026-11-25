import { expect, test } from "@playwright/test";
import { fitsWidth, watchErrors } from "./util";

// The whole product, for real: prompt -> clarifying questions -> research, model, solve, check -> 3D scene dressed in
// Blender -> review -> accept -> .blend. It calls the real models and takes several minutes, so it only runs with
// E2E_REAL=1 against a full stack (docker compose up).
test.skip(!process.env.E2E_REAL, "the real journey runs only with E2E_REAL=1");
test.setTimeout(45 * 60_000);

test("a prompt becomes an interactive, dressed simulation with a .blend", async ({ page, request }) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await page.getByLabel("Scenario").fill(process.env.E2E_PROMPT ?? "a red ball rolls down a wooden ramp in a park");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await page.waitForURL(/\/jobs\/[0-9a-f]{32}$/, { timeout: 60_000 });
  const jobId = page.url().split("/").pop() as string;
  console.log(`journey job ${jobId}`);

  // the clarifier may ask a few questions: take the defaults
  const defaults = page.getByRole("button", { name: "Use defaults for all" });
  const accept = page.getByRole("button", { name: "Accept" });
  await expect(defaults.or(accept)).toBeVisible({ timeout: 10 * 60_000 }).catch(() => undefined);
  if (await defaults.isVisible()) await defaults.click();
  // the asset picker (generated meshes only): let Omnific choose
  const letChoose = page.getByRole("button", { name: "Let Omnific choose all" });
  await expect(letChoose.or(accept)).toBeVisible({ timeout: 30 * 60_000 });
  if (await letChoose.isVisible()) await letChoose.click();

  // ready for review: an interactive viewer that plays
  await expect(accept).toBeVisible({ timeout: 30 * 60_000 });
  await expect(page.locator("canvas").first()).toBeVisible();
  const time = page.getByRole("slider", { name: "Simulation time" });
  await page.getByRole("button", { name: /^(Play|Replay)$/ }).click();
  await expect.poll(async () => Number(await time.inputValue()), { timeout: 10_000 }).toBeGreaterThan(0);

  const job = await (await request.get(`/api/jobs/${jobId}`)).json();
  const outcome = job.draft ?? job.result;
  expect(outcome.eval_report?.passed, "the physics evaluation passed").toBe(true);
  expect(outcome.dress_report, "the scene went through Blender dressing").toBeTruthy();
  if (outcome.blend_key) {
    const blend = await request.get(`/api/jobs/${jobId}/artifacts/scene.blend`);
    expect(blend.status()).toBe(200);
    expect(blend.headers()["content-disposition"]).toContain(".blend");
    const head = await blend.body();
    // a .blend is saved compressed (zstd frame) or plain ("BLENDER"): Blender opens both
    expect(head.subarray(0, 4).equals(Buffer.from([0x28, 0xb5, 0x2f, 0xfd])) || head.subarray(0, 7).toString() === "BLENDER")
      .toBe(true);
  }

  await accept.click();
  // accepted: the page turns into the finished simulation (its "Change this simulation" panel), and the API agrees
  await expect(page.getByRole("heading", { name: "Change this simulation" })).toBeVisible({ timeout: 60_000 });
  await expect.poll(async () => (await (await request.get(`/api/jobs/${jobId}`)).json()).result?.status,
    { timeout: 60_000 }).toBe("approved");
  expect(await fitsWidth(page)).toBe(true);
  expect(errors).toEqual([]);
});
