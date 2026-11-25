import type { AssetPicks, ClarifyAnswers, CreateEditBody, CreateJobBody, InputKind, JobView, LibraryDocument, PublishRequest, PublishedSimulation, ReviewDecision, VariantChanges, VariantRecord, VersionInfo } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, { ...init, cache: "no-store" });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail !== undefined) detail = JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const createJob = (body: CreateJobBody) => request<{ job_id: string }>("/jobs", post(body));
export const getJob = (id: string) => request<JobView>(`/jobs/${encodeURIComponent(id)}`);
export const submitReview = (id: string, d: ReviewDecision) =>
  request<{ status: string }>(`/jobs/${encodeURIComponent(id)}/review`, post(d));
export const artifactUrl = (id: string, name: string) => `/api/jobs/${encodeURIComponent(id)}/artifacts/${name}`;
export const stepUrl = (id: string, entity: string) =>
  `/api/jobs/${encodeURIComponent(id)}/assets/${encodeURIComponent(entity)}/model.step`;

// Artifacts use the browser's HTTP cache: a finished job's are served immutable, a draft's revalidate by ETag.
export async function fetchJson(url: string): Promise<unknown> {
  const res = await fetch(url);
  if (!res.ok) throw new ApiError(res.status, `${url}: HTTP ${res.status}`);
  return res.json();
}

/** Text body, or null when the resource does not exist. */
export async function fetchText(url: string): Promise<string | null> {
  const res = await fetch(url);
  if (res.status === 404) return null;
  if (!res.ok) throw new ApiError(res.status, `${url}: HTTP ${res.status}`);
  return res.text();
}

export const answerClarification = (id: string, a: ClarifyAnswers) =>
  request<{ status: string }>(`/jobs/${encodeURIComponent(id)}/clarify`, post(a));

// --- follow-up edits: each version is a job of its own ---
export const createEdit = (id: string, body: CreateEditBody) =>
  request<{ job_id: string }>(`/jobs/${encodeURIComponent(id)}/edits`, post(body));
export const listVersions = (id: string) => request<VersionInfo[]>(`/jobs/${encodeURIComponent(id)}/versions`);

// --- variants: change values and re-run ---
export const createVariant = (id: string, changes: VariantChanges) =>
  request<VariantRecord>(`/jobs/${encodeURIComponent(id)}/variants`, post(changes));
export const listVariants = (id: string) => request<VariantRecord[]>(`/jobs/${encodeURIComponent(id)}/variants`);
export const variantPlaybackUrl = (id: string, vid: string) =>
  `/api/jobs/${encodeURIComponent(id)}/variants/${encodeURIComponent(vid)}/playback.json`;

// --- asset picker ---
export const submitPicks = (id: string, picks: AssetPicks) =>
  request<{ status: string }>(`/jobs/${encodeURIComponent(id)}/assets/picks`, post(picks));
export const choiceThumbUrl = (id: string, entity: string, n: number) =>
  `/api/jobs/${encodeURIComponent(id)}/choices/${encodeURIComponent(entity)}/${n}/thumb`;

// --- .blend export ("Open in Blender") ---
export const blendUrl = (id: string) => artifactUrl(id, "scene.blend");
export const variantBlendUrl = (id: string, vid: string) =>
  `/api/jobs/${encodeURIComponent(id)}/variants/${encodeURIComponent(vid)}/scene.blend`;
export const startVariantBlend = (id: string, vid: string) =>
  request<{ status: string }>(`/jobs/${encodeURIComponent(id)}/variants/${encodeURIComponent(vid)}/blend`, post({}));
/** Poll a variant's .blend export: "ready" once the file can be downloaded. */
export async function variantBlendStatus(id: string, vid: string): Promise<"building" | "ready" | "failed"> {
  const res = await fetch(variantBlendUrl(id, vid));
  if (res.status === 202) return "building";
  return res.ok ? "ready" : "failed";
}

// --- public library ---
export const publishJob = (id: string, body: PublishRequest) =>
  request<PublishedSimulation>(`/jobs/${encodeURIComponent(id)}/publish`, post(body));
export const unpublishJob = async (id: string) => {
  const res = await fetch(`/api/jobs/${encodeURIComponent(id)}/publish`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) throw new ApiError(res.status, `HTTP ${res.status}`);
};
export const getPublished = (id: string) => request<PublishedSimulation>(`/gallery/${encodeURIComponent(id)}`);
export const listGallery = (q = "", limit = 24, offset = 0) =>
  request<PublishedSimulation[]>(`/gallery?${new URLSearchParams({ q, limit: String(limit), offset: String(offset) })}`);
export const thumbnailUrl = (id: string) => `/api/jobs/${encodeURIComponent(id)}/thumbnail`;

export const listDocuments = () => request<LibraryDocument[]>("/documents");
export const getDocument = (id: string) => request<LibraryDocument>(`/documents/${encodeURIComponent(id)}`);
export const deleteDocument = (id: string) =>
  request<unknown>(`/documents/${encodeURIComponent(id)}`, { method: "DELETE" });

/** Multipart upload via XHR so progress can be reported (0..1). */
export function uploadDocument(file: File, onProgress?: (fraction: number) => void): Promise<LibraryDocument> {
  return upload<LibraryDocument>("/documents", file, onProgress);
}

/** A sketch/photo or voice note for the next job (not a library document). */
export const uploadInput = (file: File, onProgress?: (fraction: number) => void) =>
  upload<{ input_id: string; kind: InputKind }>("/inputs", file, onProgress);

function upload<T>(path: string, file: File, onProgress?: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    xhr.responseType = "text";
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error during upload"));
    xhr.onload = () => {
      let body: unknown = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* non-JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body as T);
      const d = (body as { detail?: unknown } | null)?.detail;
      reject(new ApiError(xhr.status, typeof d === "string" ? d : xhr.statusText || `HTTP ${xhr.status}`));
    };
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}
