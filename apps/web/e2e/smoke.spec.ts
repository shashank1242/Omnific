import { expect, test } from "@playwright/test";
import { fitsWidth, watchErrors } from "./util";

const PAGES: { path: string; heading: RegExp }[] = [
  { path: "/", heading: /./ },
  { path: "/explore", heading: /./ },
  { path: "/library", heading: /./ },
];

for (const p of PAGES) {
  test(`${p.path} loads without errors @phone`, async ({ page }) => {
    const errors = watchErrors(page);
    await page.goto(p.path);
    await expect(page.locator("h1, h2").first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(errors).toEqual([]);
    expect(await fitsWidth(page)).toBe(true);
  });
}

test("the home page takes a prompt", async ({ page }) => {
  await page.goto("/");
  const box = page.getByRole("textbox").first();
  await expect(box).toBeVisible();
  await box.fill("a ball rolls down a ramp");
  await expect(box).toHaveValue("a ball rolls down a ramp");
});

test("every demo plays in the interactive viewer @phone", async ({ page, request }) => {
  const index = await (await request.get("/demo/index.json")).json();
  const slugs: string[] = (Array.isArray(index) ? index : index.demos ?? []).map((d: { slug: string }) => d.slug);
  expect(slugs.length).toBeGreaterThan(0);
  const errors = watchErrors(page);
  for (const slug of slugs) {
    errors.length = 0;
    await page.goto(`/demo/${slug}`);
    await expect(page.locator("canvas").first()).toBeVisible();
    const time = page.getByRole("slider", { name: "Simulation time" });
    await expect(time).toBeVisible();
    const before = Number(await time.inputValue());
    await page.getByRole("button", { name: /^(Play|Replay)$/ }).click();
    await expect.poll(async () => Number(await time.inputValue()), { timeout: 8_000 }).toBeGreaterThan(before);
    expect(await fitsWidth(page), `${slug} fits the width`).toBe(true);
    expect(errors, `${slug} errors`).toEqual([]);
  }
});

test("unknown pages get a helpful 404, and every page sends security headers", async ({ page, request }) => {
  const res = await page.goto("/no-such-page");
  expect(res?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: "Nothing here" })).toBeVisible();
  const home = await request.get("/");
  expect(home.headers()["x-content-type-options"]).toBe("nosniff");
  expect(home.headers()["x-frame-options"]).toBe("SAMEORIGIN");
  expect(home.headers()["permissions-policy"]).toContain("microphone=(self)");
  expect(home.headers()["x-powered-by"]).toBeUndefined();
});
