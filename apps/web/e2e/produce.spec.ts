import { expect, test, type Page } from "@playwright/test";

// Makes a simulation the way a person does on the website, start to finish: type the prompt, take the suggested
// answers, let Omnific choose the models, wait for the build, look at it across its stages, accept it and (with
// E2E_PUBLISH=1) publish it to the public library. Real models and minutes of work, so only with E2E_PRODUCE=1.
//   E2E_PRODUCE=1 E2E_PROMPT="..." [E2E_JOB=<id> to continue a job] [E2E_PUBLISH=1] npx playwright test e2e/produce.spec.ts --project=desktop
test.skip(process.env.E2E_PRODUCE !== "1", "makes a real simulation only with E2E_PRODUCE=1");
test.setTimeout(90 * 60_000);

async function settle(page: Page, time: ReturnType<Page["getByRole"]>, v: number) {
  await time.evaluate((el, value) => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    set?.call(el, String(value));
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }, v);
  await page.waitForTimeout(900);
}

test("make, review, accept and publish a simulation", async ({ page, request }, info) => {
  let jobId = process.env.E2E_JOB ?? "";
  if (!jobId) {
    await page.goto("/");
    await page.getByLabel("Scenario").fill(process.env.E2E_PROMPT ?? "a ball rolls down a ramp");
    await page.getByRole("button", { name: "Run simulation" }).click();
    await page.waitForURL(/\/jobs\/[0-9a-f]{32}$/, { timeout: 60_000 });
    jobId = page.url().split("/").pop() as string;
  } else {
    await page.goto(`/jobs/${jobId}`);
  }
  console.log(`produce job ${jobId}`);

  const defaults = page.getByRole("button", { name: "Use defaults for all" });
  const letChoose = page.getByRole("button", { name: "Let Omnific choose all" });
  const accept = page.getByRole("button", { name: "Accept" });
  const done = page.getByRole("heading", { name: "Change this simulation" });
  const failed = page.locator("[role=alert]").filter({ hasText: /stopped|failed|ran out|rejected|rate-limiting/ });
  // walk the build like a person: answer, pick, wait, until it is ready for review, finished or failed
  for (let k = 0; k < 400; k++) {
    if (await done.isVisible().catch(() => false)) break;
    if (await accept.isVisible().catch(() => false)) break;
    if (await failed.isVisible().catch(() => false)) {
      console.log(`produce failed: ${await failed.first().innerText()}`);
      throw new Error("the build failed");
    }
    if (await defaults.isVisible().catch(() => false)) await defaults.click();
    if (await letChoose.isVisible().catch(() => false)) await letChoose.click().catch(() => undefined);
    await page.waitForTimeout(10_000);
  }
  await expect(accept.or(done)).toBeVisible({ timeout: 5 * 60_000 });

  // look at it across its stages (or across the run)
  await page.locator("canvas").first().waitFor();
  await page.waitForTimeout(3000);
  const time = page.getByRole("slider", { name: "Simulation time" });
  const min = Number(await time.getAttribute("min"));
  const max = Number(await time.getAttribute("max"));
  const stages = page.getByRole("group", { name: "Stages of the simulation" }).getByRole("button");
  const n = await stages.count();
  const starts: number[] = [];
  for (let k = 0; k < n; k++) {
    const t = Number((await stages.nth(k).getAttribute("title"))?.match(/from ([-\d.e+]+)/)?.[1] ?? NaN);
    if (Number.isFinite(t)) starts.push(t);
  }
  // the middle of every stage (however short), then the run at even steps
  const moments = starts.map((t, k) => (t + (starts[k + 1] ?? max)) / 2);
  for (const f of [0, 0.25, 0.5, 0.75, 1]) moments.push(min + f * (max - min));
  for (const [i, t] of [...new Set(moments.map((x) => Math.min(max, Math.max(min, x))))].sort((a, b) => a - b).entries()) {
    await settle(page, time, t);
    await page.locator("canvas").first().screenshot({ path: info.outputPath(`view-${String(i).padStart(2, "0")}.png`) });
  }
  await page.screenshot({ path: info.outputPath("page.png"), fullPage: true });

  if (await accept.isVisible().catch(() => false)) {
    await accept.click();
    await expect(done).toBeVisible({ timeout: 2 * 60_000 });
  }
  if (process.env.E2E_PUBLISH === "1") {
    await settle(page, time, min + 0.6 * (max - min)); // the library picture: mid-run, where something is happening
    const panel = page.getByRole("region", { name: "Public library" });
    const description = panel.getByRole("textbox", { name: /Description/ });
    if (process.env.E2E_DESCRIPTION) await description.fill(process.env.E2E_DESCRIPTION);
    await panel.getByRole("button", { name: /^(Publish|Update)$/ }).click();
    await expect(panel.getByRole("heading", { name: "Published in the public library" })).toBeVisible({ timeout: 60_000 });
    console.log(`produce published ${jobId}`);
  }
  const job = await (await request.get(`/api/jobs/${jobId}`)).json();
  console.log(`produce status ${JSON.stringify(job.status)}`);
});
