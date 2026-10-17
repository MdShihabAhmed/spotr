import { tokens } from "./auth";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8001";

/** Mirrors the backend's {detail, code} error schema so callers branch on `code`. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, detail: string) {
    super(detail);
    this.status = status;
    this.code = code;
  }
}

/** In-flight refresh, shared so N concurrent 401s trigger one refresh, not N. */
let refreshing: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const refresh_token = tokens.refresh();
  if (!refresh_token) return false;

  const resp = await fetch(`${BASE_URL}/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token }),
  });

  if (!resp.ok) {
    tokens.clear();
    return false;
  }
  const body = await resp.json();
  tokens.set(body.access_token, body.refresh_token);
  return true;
}

function ensureRefreshed(): Promise<boolean> {
  refreshing ??= refreshTokens().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

async function send(path: string, init: RequestInit): Promise<Response> {
  const access = tokens.access();
  return fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      ...init.headers,
      ...(access ? { Authorization: `Bearer ${access}` } : {}),
    },
  });
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  let resp = await send(path, init);

  // Access tokens are short-lived by design; a 401 mid-session is expected, not exceptional.
  if (resp.status === 401 && tokens.refresh()) {
    if (await ensureRefreshed()) {
      resp = await send(path, init);
    }
  }

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new ApiError(resp.status, body.code ?? "unknown_error", body.detail ?? resp.statusText);
  }
  return resp.status === 204 ? (undefined as T) : resp.json();
}

export const api = {
  get: <T>(path: string) => apiFetch<T>(path),
  post: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
};

/** Mirrors backend UserOut. */
export type User = {
  id: number;
  email: string;
  cf_handle: string | null;
  created_at: string;
};

export const getMe = () => api.get<User>("/me");

/** Personal progress: rungs earned by your own reasoning vs opened manually. */
export type UserStats = {
  problems_started: number;
  levels_earned: number;
  levels_opened: number;
  earned_ratio: number | null;
};

export const getMyStats = () => api.get<UserStats>("/me/stats");

export const register = (email: string, password: string) =>
  api.post<User>("/register", { email, password });

export function logout(): void {
  tokens.clear();
}

export type SampleTest = { input: string; expected_output: string };

/** A user-uploaded diagram tagged with the section it belongs to. */
export type Figure = { data_uri: string; section: "statement" | "note" };

export type IngestionStatus =
  | "pending"
  | "normalizing"
  | "generating_levels"
  | "ready"
  | "failed";

/** Mirrors backend ProblemOut. Addressed by `slug` ("cf-1846C"), not an int id. */
export type Problem = {
  id: number;
  slug: string;
  source: string;
  external_id: string;
  url: string | null;
  title: string;
  rating: number | null;
  tags: string[];
  statement_text: string;
  constraints_text: string | null;
  sample_tests: SampleTest[];
  // CF "Note" (explains the samples); shown after them.
  note_text: string | null;
  // User-uploaded diagrams, rendered inline in their section.
  figures: Figure[];
  time_limit_ms: number | null;
  memory_limit_mb: number | null;
  editorial_url: string | null;
  // The raw editorial never leaves the server (spoiler); we only learn if one exists.
  has_editorial: boolean;
  is_interactive: boolean;
  has_multiple_answers: boolean;
  ingestion_status: IngestionStatus;
  ingestion_error: string | null;
  created_at: string;
  // Number of graduated ladder levels generated (varies by problem, typically 2-6). Content
  // is served one level at a time by the tutor's revealed-set flow, never all at once.
  level_count: number;
};

export type IngestRequest = {
  source: string;
  url: string;
  statement_text?: string;
  constraints_text?: string;
  sample_tests?: SampleTest[];
  note_text?: string;
  figures?: Figure[];
  time_limit_ms?: number;
  memory_limit_mb?: number;
  editorial_url?: string;
  editorial_text?: string;
};

export const ingestProblem = (body: IngestRequest) =>
  api.post<Problem>("/problems/ingest", body);

export const getProblem = (slug: string) => api.get<Problem>(`/problems/${slug}`);

/** A slim row for the problems list (mirrors backend ProblemSummary). */
export type ProblemSummary = {
  slug: string;
  title: string;
  source: string;
  external_id: string;
  rating: number | null;
  tags: string[];
  ingestion_status: IngestionStatus;
  created_at: string;
  level_count: number;
};

export type ProblemList = {
  items: ProblemSummary[];
  total: number;
  page: number;
  page_size: number;
};

export const listProblems = (page = 1, pageSize = 20) =>
  api.get<ProblemList>(`/problems?page=${page}&page_size=${pageSize}`);

export type ProblemStatusHandlers = {
  onStatus: (status: IngestionStatus, error: string | null) => void;
  onError: (detail: string) => void;
};

/** Subscribe to live ingestion-status changes via SSE (server LISTENs on Postgres and pushes
 *  each transition), replacing status polling. The server closes the stream on a terminal
 *  status; pass an AbortSignal to close early (component unmount). */
export async function streamProblemStatus(
  slug: string,
  h: ProblemStatusHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let resp: Response;
  try {
    resp = await authorizedFetch(`/problems/${slug}/events`, { signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") return; // intentional close, not a failure
    h.onError("Lost the status connection.");
    return;
  }
  if (!resp.ok || !resp.body) {
    h.onError("Couldn't open the status stream.");
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const line = frame.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const evt = JSON.parse(line.slice(5).trim());
        if (evt.type === "status") h.onStatus(evt.status, evt.error ?? null);
      }
    }
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    h.onError("The status stream dropped.");
  }
}

/** Cleaned, structured problem returned by /problems/normalize. */
export type NormalizedProblem = {
  statement: string;
  constraints: string | null;
  samples: SampleTest[];
  note: string | null;
  time_limit_ms: number | null;
  memory_limit_mb: number | null;
};

/** Send pasted text and/or screenshot data-URIs; get back clean structured fields.
 *  Multiple images let a long problem be captured across several screenshots. */
export const normalizeProblem = (body: { text?: string; images?: string[] }) =>
  api.post<NormalizedProblem>("/problems/normalize", body);

// --- Tutor (grounded Socratic chat + revealed-set disclosure) ----------------
/** A grounding chunk the tutor cited (provenance for the "Grounded by" credit). */
export type Citation = { source: string; title: string; url: string; page: number | null };

export type TutorMessage = {
  // Present on server-persisted messages; absent on optimistic local ones.
  id?: number;
  role: "user" | "assistant";
  content: string;
  citations: Citation[] | null;
  created_at: string;
};

/** How a rung was unlocked: "chat" = earned (green, quotes your words), "manual" = opened (violet). */
export type UnlockVia = "chat" | "manual";

/** An UNLOCKED ladder rung. Locked rungs are never sent - that's the no-leakage guarantee.
 *  `earned_text` is the student's own words the judge matched (chat unlocks only). */
export type RevealedLevel = {
  level: number;
  content: string;
  via: UnlockVia;
  earned_text: string | null;
  earned_message_id: number | null;
  unlocked_at: string | null;
};

export type TutorSession = {
  slug: string;
  status: "active" | "solved" | "gave_up";
  revealed_levels: number[];
  revealed: RevealedLevel[];
  level_count: number;
  messages: TutorMessage[];
};

export type RevealResult = {
  level: number;
  content: string;
  via: UnlockVia;
  revealed_levels: number[];
};

export const getTutorSession = (slug: string) => api.get<TutorSession>(`/tutor/${slug}`);

export const revealNextLevel = (slug: string) =>
  api.post<RevealResult>(`/tutor/${slug}/reveal`);

/** Bearer-authorized fetch that transparently refreshes once on a 401, returning the raw
 *  Response - used for the SSE stream, which `apiFetch` (JSON-only) can't handle. */
export async function authorizedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let resp = await send(path, init);
  if (resp.status === 401 && tokens.refresh()) {
    if (await ensureRefreshed()) resp = await send(path, init);
  }
  return resp;
}

export type TutorStreamHandlers = {
  onToken: (text: string) => void;
  onUnlock: (level: number, via: UnlockVia, matchedMessage: string) => void;
  onDone: (info: {
    revealed_levels: number[];
    unlocked: number | null;
    citations: Citation[];
    chunk_count: number;
  }) => void;
  onError: (detail: string) => void;
};

/** POST a chat turn and consume the Server-Sent-Events stream, dispatching each frame.
 *  (EventSource can't send auth headers or a POST body, so we read the stream by hand.) */
export async function streamTutorMessage(
  slug: string,
  content: string,
  h: TutorStreamHandlers,
): Promise<void> {
  const resp = await authorizedFetch(`/tutor/${slug}/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!resp.ok || !resp.body) {
    const body = await resp.json().catch(() => ({}));
    h.onError(body.detail ?? "The tutor request failed.");
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE frames are separated by a blank line; keep any partial trailing frame buffered.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      const evt = JSON.parse(line.slice(5).trim());
      if (evt.type === "token") h.onToken(evt.text);
      else if (evt.type === "unlock") h.onUnlock(evt.level, evt.via, evt.matched_message);
      else if (evt.type === "done") h.onDone(evt);
      else if (evt.type === "error") h.onError(evt.detail);
    }
  }
}

// --- Grounding sources (bring your own references) ---------------------------
export type GroundingSourceStatus =
  | "pending"
  | "cloning"
  | "embedding"
  | "ready"
  | "failed"
  | "retrying";

export type GroundingSource = {
  id: number;
  kind: "github" | "pdf";
  label: string;
  source_key: string;
  location: string;
  status: GroundingSourceStatus;
  error: string | null;
  chunk_count: number; // embedded so far
  chunks_total: number; // total to embed (0 = not known yet)
  created_at: string;
  // When a `retrying` source becomes eligible again (e.g. after the daily quota resets).
  retry_at: string | null;
};

export const listSources = () => api.get<{ items: GroundingSource[] }>("/grounding/sources");

/** Retry now - skip a scheduled wait (network blip, or you've freed up quota). Resumes from
 *  what's already embedded. */
export const retrySource = (id: number) =>
  api.post<GroundingSource>(`/grounding/sources/${id}/retry`);

export const addGithubSource = (body: { label: string; repo_url: string; license_ack: boolean }) =>
  api.post<GroundingSource>("/grounding/sources/github", body);

/** PDF upload is multipart, so it bypasses the JSON `api` helper. */
export async function addPdfSource(
  file: File,
  label: string,
  licenseAck: boolean,
): Promise<GroundingSource> {
  const form = new FormData();
  form.append("file", file);
  form.append("label", label);
  form.append("license_ack", String(licenseAck));
  // No Content-Type header - the browser sets the multipart boundary itself.
  const resp = await authorizedFetch("/grounding/sources/pdf", { method: "POST", body: form });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok)
    throw new ApiError(resp.status, body.code ?? "unknown_error", body.detail ?? resp.statusText);
  return body as GroundingSource;
}

export const deleteSource = (id: number) =>
  apiFetch<void>(`/grounding/sources/${id}`, { method: "DELETE" });

/** /login is form-encoded (OAuth2PasswordRequestForm), unlike every other endpoint. */
export async function login(email: string, password: string): Promise<void> {
  const resp = await fetch(`${BASE_URL}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ username: email, password }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new ApiError(resp.status, body.code ?? "unknown_error", body.detail ?? resp.statusText);
  }
  const body = await resp.json();
  tokens.set(body.access_token, body.refresh_token);
}
