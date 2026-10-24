import type { CreateJobBody, JobView, ReviewDecision } from "./types";

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

export async function fetchJson(url: string): Promise<unknown> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, `${url}: HTTP ${res.status}`);
  return res.json();
}

/** Text body, or null when the resource does not exist. */
export async function fetchText(url: string): Promise<string | null> {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new ApiError(res.status, `${url}: HTTP ${res.status}`);
  return res.text();
}
