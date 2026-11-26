import { fmtNum } from "./format";
import type { ChatMessage, Playback, SimSpec } from "./types";

/** Matches the API: the last this many messages go with each question, each question at most this long. */
export const CHAT_MAX_MESSAGES = 16;
export const CHAT_MAX_CHARS = 2000;

/** Questions to start with, from what this simulation actually has (its stages, results and departures). */
export function starterQuestions(spec: SimSpec | null, pb: Playback | null): string[] {
  const out: string[] = [];
  const phases = pb?.phases ?? [];
  const outputs = pb?.outputs ?? [];
  if (phases.length > 1) out.push(`What happens during “${phases[Math.min(1, phases.length - 1)]?.name}”?`);
  const first = outputs[0];
  if (first) out.push(`Why is the ${first.label.toLowerCase()} ${fmtNum(first.value)}${first.unit ? ` ${first.unit}` : ""}?`);
  if (spec?.departures?.length) out.push("What part of my request isn't simulated, and why?");
  out.push("What physics is this simulation based on?");
  out.push("How accurate is this, and what does it leave out?");
  return [...new Set(out)].slice(0, 4);
}

/** The history sent with a question: the latest messages, the new question last and trimmed to the limit. */
export function outgoing(history: ChatMessage[], question: string): ChatMessage[] {
  const q = question.trim().slice(0, CHAT_MAX_CHARS);
  const kept = history.map((m) => ({ role: m.role, content: m.content.slice(0, CHAT_MAX_CHARS * 3) }));  // API limit
  return [...kept, { role: "user" as const, content: q }].slice(-CHAT_MAX_MESSAGES);
}

export type Inline = { text: string; bold: boolean };
export type Block = { kind: "p"; parts: Inline[] } | { kind: "ul" | "ol"; items: Inline[][] };

/** **bold** spans of one line (no HTML is ever built from the answer). */
export function inline(line: string): Inline[] {
  const parts: Inline[] = [];
  const re = /\*\*([^*]+)\*\*/g;
  let last = 0;
  for (let m = re.exec(line); m; m = re.exec(line)) {
    if (m.index > last) parts.push({ text: line.slice(last, m.index), bold: false });
    parts.push({ text: m[1] ?? "", bold: true });
    last = m.index + m[0].length;
  }
  if (last < line.length) parts.push({ text: line.slice(last), bold: false });
  return parts;
}

/** The small Markdown subset the guide writes: paragraphs, "-"/"*" lists, "1." lists and **bold**. */
export function blocks(text: string): Block[] {
  const out: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) out.push({ kind: "p", parts: inline(para.join(" ")) });
    para = [];
  };
  for (const raw of text.replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.trim();
    const bullet = /^[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\d+[.)]\s+(.*)$/.exec(line);
    const item = bullet ?? numbered;
    if (!line) { flush(); continue; }
    if (item) {
      flush();
      const kind = bullet ? "ul" : "ol";
      const prev = out[out.length - 1];
      const parts = inline((item[1] ?? "").replace(/^#+\s*/, ""));
      if (prev && prev.kind === kind) prev.items.push(parts);
      else out.push({ kind, items: [parts] });
      continue;
    }
    para.push(line.replace(/^#+\s*/, ""));
  }
  flush();
  return out;
}
