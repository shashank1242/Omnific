import type { ClarifyAnswers, ClarifyQuestion } from "./types";

/** Initial form values: every question prefilled with its default. */
export function defaultValues(questions: ClarifyQuestion[]): Record<string, string> {
  return Object.fromEntries(questions.map((q) => [q.id, q.default]));
}

/** Build the POST body. Unchanged values are omitted (the server marks them as defaults). */
export function buildAnswers(
  questions: ClarifyQuestion[],
  values: Record<string, string>,
  useDefaults = false,
): ClarifyAnswers {
  if (useDefaults) return { answers: [], use_defaults: true };
  const answers = questions.flatMap((q) => {
    const v = (values[q.id] ?? "").trim();
    return v === "" || v === q.default ? [] : [{ id: q.id, value: v }];
  });
  return { answers, use_defaults: false };
}

export function isValidNumber(v: string): boolean {
  return v.trim() !== "" && Number.isFinite(Number(v));
}

/** Returns an error message for an invalid value, else null. Empty values fall back to the default. */
export function validateAnswer(q: ClarifyQuestion, v: string): string | null {
  if (v.trim() === "") return null;
  if (q.kind === "number" && !isValidNumber(v)) return "Enter a number";
  return null;
}
