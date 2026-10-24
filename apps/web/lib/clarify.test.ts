import { describe, expect, it } from "vitest";
import { buildAnswers, defaultValues, validateAnswer } from "./clarify";
import { docCitationLabel, parseDocCitation } from "./citations";
import { validateUpload } from "./upload";
import type { ClarifyQuestion } from "./types";

const q = (o: Partial<ClarifyQuestion>): ClarifyQuestion => ({
  id: "a", question: "?", why: "", kind: "text", options: [], unit: "", default: "x", ...o,
});
const qs = [
  q({ id: "alt", kind: "number", unit: "m", default: "0" }),
  q({ id: "env", kind: "choice", options: ["air", "vacuum"], default: "air" }),
];

describe("clarify answers", () => {
  it("prefills defaults", () => expect(defaultValues(qs)).toEqual({ alt: "0", env: "air" }));
  it("sends only changed values", () =>
    expect(buildAnswers(qs, { alt: "100", env: "air" })).toEqual({ answers: [{ id: "alt", value: "100" }], use_defaults: false }));
  it("use_defaults sends no answers", () =>
    expect(buildAnswers(qs, { alt: "100", env: "vacuum" }, true)).toEqual({ answers: [], use_defaults: true }));
  it("validates numbers", () => {
    expect(validateAnswer(qs[0]!, "abc")).not.toBeNull();
    expect(validateAnswer(qs[0]!, "1e3")).toBeNull();
  });
});

describe("citations", () => {
  it("parses doc urls", () => {
    expect(parseDocCitation("omnific://doc/abc-1#page=7")).toEqual({ docId: "abc-1", page: 7 });
    expect(parseDocCitation("omnific://doc/abc")).toEqual({ docId: "abc", page: null });
    expect(parseDocCitation("https://x.org")).toBeNull();
  });
  it("labels", () => expect(docCitationLabel("T", 3)).toBe("T, p. 3"));
});

describe("upload validation", () => {
  it("accepts supported", () => expect(validateUpload({ name: "a.PDF", size: 10 })).toBeNull());
  it("rejects type", () => expect(validateUpload({ name: "a.exe", size: 10 })).toMatch(/unsupported/));
  it("rejects big", () => expect(validateUpload({ name: "a.pdf", size: 51 * 1024 * 1024 })).toMatch(/50 MB/));
  it("rejects empty", () => expect(validateUpload({ name: "a.pdf", size: 0 })).toMatch(/empty/));
});
