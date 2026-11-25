import type { ClarifyAnswers, ClarifyQuestion } from "./types";

/** Initial form values: every question prefilled with its default. */
export function defaultValues(questions: ClarifyQuestion[]): Record<string, string> {
  return Object.fromEntries(questions.map((q) => [q.id, q.default]));
}

/** Build the POST body. Unchanged values are omitted (the server marks them as defaults). `custom` marks the
 *  questions answered in the user's own words ("Something else…", or a number typed with its own unit). */
export function buildAnswers(
  questions: ClarifyQuestion[],
  values: Record<string, string>,
  useDefaults = false,
  custom: Record<string, boolean> = {},
): ClarifyAnswers {
  if (useDefaults) return { answers: [], use_defaults: true };
  const answers = questions.flatMap((q) => {
    const v = (values[q.id] ?? "").trim();
    if (v === "" || (v === q.default && !custom[q.id])) return [];
    return [{ id: q.id, value: v, custom: !!custom[q.id] || (q.kind === "number" && hasOwnUnit(v)) }];
  });
  return { answers, use_defaults: false };
}

export function isValidNumber(v: string): boolean {
  return v.trim() !== "" && Number.isFinite(Number(v));
}

// a complete number, then a unit that starts with a letter, ° or % ("3 ft", "20 degC", "45°"); "1.2.3" and "3+" fail
const NUMBER_WITH_UNIT = /^\s*[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?\s*[\p{L}°%][\p{L}\p{N}°%/*^·.\s-]*$/u;

/** A number followed by its own unit, e.g. "3 ft" or "20 degC". */
export function hasOwnUnit(v: string): boolean {
  return !isValidNumber(v) && NUMBER_WITH_UNIT.test(v);
}

/** Returns an error message for an invalid value, else null. Empty values fall back to the default. */
export function validateAnswer(q: ClarifyQuestion, v: string): string | null {
  if (v.trim() === "") return null;
  if (q.kind === "number" && !isValidNumber(v) && !hasOwnUnit(v)) return "Enter a number, optionally with a unit (e.g. 3 ft)";
  return null;
}
