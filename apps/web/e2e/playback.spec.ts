import { expect, test, type Page } from "@playwright/test";
import { watchErrors } from "./util";

async function firstDemo(page: Page): Promise<string> {
  const index = await (await page.request.get("/demo/index.json")).json();
  return ((Array.isArray(index) ? index : index.demos ?? []) as { slug: string }[])[0]!.slug;
}

test("readouts hold still while the simulation plays @phone", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(`/demo/${await firstDemo(page)}`);
  await expect(page.locator("canvas").first()).toBeVisible();
  await page.getByRole("button", { name: /^(Play|Replay)$/ }).click();
  // Sample the clock, every chart caption and the speed control for a second and a half of playback: none may change
  // size or move (a readout that changes width shifts everything after it, which is what read as jitter).
  const samples = await page.evaluate(async () => {
    const boxes = () => {
      const els = [
        ...document.querySelectorAll('[role="group"][aria-label="Playback controls"] > *'),
        ...document.querySelectorAll("figure figcaption > span"),
        ...document.querySelectorAll('[role="group"][aria-label="Playback controls"] span'),
      ];
      return els.map((el) => {
        const r = el.getBoundingClientRect();
        return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)].join(",");
      });
    };
    const texts = () => [...document.querySelectorAll("figure figcaption")].map((el) => el.textContent ?? "");
    const out: { boxes: string[]; texts: string[] }[] = [];
    for (let i = 0; i < 45; i++) {
      await new Promise((r) => requestAnimationFrame(() => r(null)));
      if (i % 3 === 0) out.push({ boxes: boxes(), texts: texts() });
    }
    return out;
  });
  expect(new Set(samples.map((s) => s.boxes.join("|"))).size, "layout changed while playing").toBe(1);
  if (samples[0]!.texts.length > 0) {
    expect(new Set(samples.map((s) => s.texts.join("|"))).size, "chart readouts update while playing").toBeGreaterThan(1);
  }
  expect(errors).toEqual([]);
});

test("a custom speed changes the pace", async ({ page }) => {
  await page.goto(`/demo/${await firstDemo(page)}`);
  await expect(page.locator("canvas").first()).toBeVisible();
  const time = page.getByRole("slider", { name: "Simulation time" });
  const max = Number(await time.getAttribute("max"));
  const min = Number(await time.getAttribute("min"));
  const speed = page.getByRole("combobox", { name: "Speed" });
  await speed.selectOption("custom");
  const custom = page.getByRole("textbox", { name: /Custom speed/ });
  await custom.fill("0.2");
  await custom.press("Enter");
  await expect(speed).toHaveValue("custom");
  await expect(speed.locator("option:checked")).toHaveText("Custom (0.2×)");

  // at 0.2x a run takes 50 s of wall clock: after 1 s about 2 % of it has played, nowhere near the 10 % 1x would reach
  await page.getByRole("button", { name: /^(Play|Replay)$/ }).click();
  await page.waitForTimeout(1000);
  await page.getByRole("button", { name: "Pause" }).click();
  const slow = (Number(await time.inputValue()) - min) / (max - min);
  expect(slow).toBeGreaterThan(0.005);
  expect(slow).toBeLessThan(0.05);

  // nonsense is refused and the speed stays as it was
  await speed.selectOption("custom");
  await page.getByRole("textbox", { name: /Custom speed/ }).fill("fast");
  await expect(page.getByRole("textbox", { name: /Custom speed/ })).toHaveAttribute("aria-invalid", "true");
  await page.getByRole("textbox", { name: /Custom speed/ }).press("Enter");
  await expect(speed.locator("option:checked")).toHaveText("Custom (0.2×)");

  await speed.selectOption("4");
  await expect(speed).toHaveValue("4");
});
