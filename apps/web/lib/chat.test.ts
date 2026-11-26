import { describe, expect, it } from "vitest";
import { blocks, CHAT_MAX_CHARS, CHAT_MAX_MESSAGES, inline, outgoing, starterQuestions } from "./chat";
import type { ChatMessage, Playback, SimSpec } from "./types";

const pb = {
  version: 1, title: "t", fidelity: "heuristic", t: [0, 1], unit_t: "s", series: {}, entities: [], tracks: [],
  phases: [{ name: "Fall", start: 0, end: 1, description: "" }, { name: "Detonation", start: 1, end: 2, description: "" }],
  outputs: [{ label: "Peak radius", state: "R", measure: "max", value: 120.5, unit: "m", at: 1.2, why: "" }],
} as unknown as Playback;

describe("starter questions", () => {
  it("come from the run's own stages, results and departures", () => {
    const qs = starterQuestions({ departures: ["the shots are not simulated"] } as unknown as SimSpec, pb);
    expect(qs[0]).toContain("Detonation");
    expect(qs[1]).toBe("Why is the peak radius 120.5 m?");
    expect(qs[2]).toContain("isn't simulated");
    expect(qs).toHaveLength(4);
  });
  it("still offer general questions with nothing loaded", () => {
    expect(starterQuestions(null, null)).toEqual([
      "What physics is this simulation based on?", "How accurate is this, and what does it leave out?"]);
  });
});

describe("outgoing history", () => {
  it("keeps the latest messages with the question last, trimmed", () => {
    const history: ChatMessage[] = Array.from({ length: 30 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: `m${i}` }));
    const out = outgoing(history, `  ${"x".repeat(CHAT_MAX_CHARS + 50)}  `);
    expect(out).toHaveLength(CHAT_MAX_MESSAGES);
    expect(out[out.length - 1]).toEqual({ role: "user", content: "x".repeat(CHAT_MAX_CHARS) });
  });
});

describe("answer formatting", () => {
  it("reads bold spans without building HTML", () => {
    expect(inline("a **b** c <i>")).toEqual([{ text: "a ", bold: false }, { text: "b", bold: true }, { text: " c <i>", bold: false }]);
  });
  it("splits paragraphs and lists", () => {
    const b = blocks("Intro line\ncontinues.\n\n- one\n- **two**\n1. first\n2) second\n## Heading");
    expect(b.map((x) => x.kind)).toEqual(["p", "ul", "ol", "p"]);
    expect(b[0]).toEqual({ kind: "p", parts: [{ text: "Intro line continues.", bold: false }] });
    expect(b[1]).toMatchObject({ items: [[{ text: "one" }], [{ text: "two", bold: true }]] });
    expect(b[3]).toEqual({ kind: "p", parts: [{ text: "Heading", bold: false }] });
  });
});
