export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
export const ACCEPTED_EXTENSIONS = [
  "pdf", "docx", "pptx", "xlsx", "csv", "html", "htm", "md", "txt", "png", "jpg", "jpeg", "webp",
];
export const ACCEPT_ATTR = ACCEPTED_EXTENSIONS.map((e) => `.${e}`).join(",");

/** Returns an error message, or null when the file may be uploaded. */
export function validateUpload(file: { name: string; size: number }): string | null {
  const ext = file.name.includes(".") ? (file.name.split(".").pop() ?? "").toLowerCase() : "";
  if (!ACCEPTED_EXTENSIONS.includes(ext)) {
    return `${file.name}: unsupported type. Use ${ACCEPTED_EXTENSIONS.map((e) => e.toUpperCase()).join(", ")}.`;
  }
  if (file.size === 0) return `${file.name}: the file is empty.`;
  if (file.size > MAX_UPLOAD_BYTES) return `${file.name}: larger than 50 MB.`;
  return null;
}
