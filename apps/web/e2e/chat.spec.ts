import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";
import { fitsWidth, watchErrors } from "./util";

// The Ask tab on a simulation page (the blast fixture): suggested questions from the run, the answer with what it is
// based on and follow-ups, the viewer's moment sent along, and a failed answer that keeps the question.
const JOB = "b".repeat(32);
const FIX = path.join(__dirname, "fixtures", "blast");

async function openBlast(page: Page, chat: (body: { messages: { role: string; content: string }[]; view_time: number | null }) => { status: number; json: unknown }) {
  const spec = JSON.parse(readFileSync(path.join(FIX, "spec.json"), "utf-8"));
  const outcome = { job_id: JOB, status: "approved", revisions: 1, review_round: 0, reviews: [], spec, research: null,
    eval_report: null, code_key: null, result_key: null, failure_reason: "", playback_key: "p", scene_key: "s",
    blend_key: null, dress_report: null };
  await page.route(`**/api/jobs/${JOB}`, (r: Route) => r.fulfill({ json: { job_id: JOB, state: "completed",
    status: { job_id: JOB, stage: "approved", revision: 1, message: "" }, draft: null, result: outcome, questions: null } }));
  await page.route(`**/api/jobs/${JOB}/**`, (r: Route) => {
    const u = r.request().url();
    if (u.endsWith("/chat")) {
      const { status, json } = chat(r.request().postDataJSON());
      return r.fulfill({ status, json });
    }
    if (u.endsWith("playback.json")) return r.fulfill({ body: readFileSync(path.join(FIX, "playback.json")), contentType: "application/json" });
    if (u.endsWith("scene.gltf")) return r.fulfill({ body: readFileSync(path.join(FIX, "scene.gltf")), contentType: "model/gltf+json" });
    return r.fulfill({ json: [] });
  });
  await page.route("**/api/gallery/**", (r: Route) => r.fulfill({ status: 404, json: {} }));
  await page.goto(`/jobs/${JOB}`);
  await page.locator("canvas").first().waitFor();
}

test("ask about the simulation and get an answer from its record @phone", async ({ page }) => {
  const errors = watchErrors(page, [/502 \/api\/jobs\/b+\/chat/]);
  const sent: { messages: { role: string; content: string }[]; view_time: number | null }[] = [];
  let fail = false;
  await openBlast(page, (body) => {
    sent.push(body);
    if (fail) return { status: 502, json: { detail: "The AI service's account has run out of credits." } };
    return { status: 200, json: { answer: "The fireball peaks at **12 m**.\n\n- it cools as it grows\n- then it fades",
      grounded_in: ["simulation", "general physics"], follow_ups: ["Why does it fade?", "How hot is it?"] } };
  });

  await page.getByRole("tab", { name: "Ask" }).click();
  const panel = page.getByRole("region", { name: "Ask about this simulation" });
  const suggested = panel.getByRole("list", { name: "Suggested questions" }).getByRole("button");
  await expect(suggested.first()).toBeVisible();
  await expect(suggested).toContainText(["Detonation"]);  // from the run's own stages

  await suggested.first().click();
  const log = panel.getByRole("log", { name: "Conversation" });
  await expect(log.locator("strong", { hasText: "12 m" })).toBeVisible();
  await expect(log.getByRole("listitem")).toHaveText(["it cools as it grows", "then it fades"]);
  await expect(log).toContainText("Based on: simulation · general physics");
  expect(sent[0]?.messages).toHaveLength(1);
  expect(sent[0]?.messages[0]?.role).toBe("user");
  expect(typeof sent[0]?.view_time).toBe("number");

  // a follow-up carries the conversation so far
  await panel.getByRole("list", { name: "Follow-up questions" }).getByRole("button", { name: "Why does it fade?" }).click();
  await expect(log).toContainText("Why does it fade?");
  await expect.poll(() => sent.length).toBe(2);
  expect(sent[1]?.messages.map((m) => m.role)).toEqual(["user", "assistant", "user"]);

  // a failed answer says why and puts the question back to try again
  fail = true;
  const box = panel.getByRole("textbox", { name: "Your question" });
  await box.fill("What is the redshift at the end?");
  await box.press("Enter");
  await expect(panel.getByRole("alert")).toContainText("run out of credits");
  await expect(box).toHaveValue("What is the redshift at the end?");
  await expect(log).not.toContainText("What is the redshift at the end?");

  expect(await fitsWidth(page)).toBe(true);
  expect(errors).toEqual([]);
});
