export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
export const ACCEPTED_EXTENSIONS = [
  "pdf", "docx", "pptx", "xlsx", "csv", "html", "htm", "md", "txt", "png", "jpg", "jpeg", "webp",
];
export const ACCEPT_ATTR = ACCEPTED_EXTENSIONS.map((e) => `.${e}`).join(",");

// Job attachments interpreted into the prompt (services/api INPUT_TYPES); 25 MB is OpenAI's transcription limit.
export const MAX_INPUT_BYTES = 25 * 1024 * 1024;
export const INPUT_IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "webp"];
export const INPUT_AUDIO_EXTENSIONS = ["mp3", "wav", "m4a", "webm", "ogg"];
export const INPUT_EXTENSIONS = [...INPUT_IMAGE_EXTENSIONS, ...INPUT_AUDIO_EXTENSIONS];
export const INPUT_ACCEPT_ATTR = INPUT_EXTENSIONS.map((e) => `.${e}`).join(",");
export const MAX_INPUTS = 10;

const extOf = (name: string) => (name.includes(".") ? (name.split(".").pop() ?? "").toLowerCase() : "");

function check(file: { name: string; size: number }, exts: string[], max: number): string | null {
  if (!exts.includes(extOf(file.name))) {
    return `${file.name}: unsupported type. Use ${exts.map((e) => e.toUpperCase()).join(", ")}.`;
  }
  if (file.size === 0) return `${file.name}: the file is empty.`;
  if (file.size > max) return `${file.name}: larger than ${max / (1024 * 1024)} MB.`;
  return null;
}

/** Returns an error message, or null when the file may be uploaded. */
export function validateUpload(file: { name: string; size: number }): string | null {
  return check(file, ACCEPTED_EXTENSIONS, MAX_UPLOAD_BYTES);
}

/** Same for a job attachment (sketch/photo or voice note). */
export function validateInput(file: { name: string; size: number }): string | null {
  return check(file, INPUT_EXTENSIONS, MAX_INPUT_BYTES);
}

export const inputKind = (name: string): "image" | "audio" =>
  INPUT_IMAGE_EXTENSIONS.includes(extOf(name)) ? "image" : "audio";

/** File extension for a MediaRecorder mime type, e.g. "audio/webm;codecs=opus" -> "webm". */
export function recordingExtension(mime: string): string {
  const base = (mime.split(";")[0] ?? "").trim().toLowerCase();
  if (base.endsWith("/ogg")) return "ogg";
  if (base.endsWith("/mp4") || base.endsWith("/m4a") || base.endsWith("/x-m4a")) return "m4a";
  if (base.endsWith("/mpeg")) return "mp3";
  if (base.endsWith("/wav")) return "wav";
  return "webm";
}
