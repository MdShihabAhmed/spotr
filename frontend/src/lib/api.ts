import { tokens } from "./auth";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

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
  // Number of graduated ladder levels generated (0–4). Content is served one level at
  // a time by the tutor's revealed-set flow, never all at once.
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
  role: "user" | "assistant";
  content: string;
  citations: Citation[] | null;
  created_at: string;
};

/** An UNLOCKED ladder rung. Locked rungs are never sent — that's the no-leakage guarantee. */
export type RevealedLevel = { level: number; content: string };

export type TutorSession = {
  slug: string;
  status: "active" | "solved" | "gave_up";
  revealed_levels: number[];
  revealed: RevealedLevel[];
  level_count: number;
  messages: TutorMessage[];
};

export type RevealResult = { level: number; content: string; revealed_levels: number[] };

export const getTutorSession = (slug: string) => api.get<TutorSession>(`/tutor/${slug}`);

export const revealNextLevel = (slug: string) =>
  api.post<RevealResult>(`/tutor/${slug}/reveal`);

/** Bearer-authorized fetch that transparently refreshes once on a 401, returning the raw
 *  Response — used for the SSE stream, which `apiFetch` (JSON-only) can't handle. */
export async function authorizedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let resp = await send(path, init);
  if (resp.status === 401 && tokens.refresh()) {
    if (await ensureRefreshed()) resp = await send(path, init);
  }
  return resp;
}

export type TutorStreamHandlers = {
  onToken: (text: string) => void;
  onUnlock: (level: number) => void;
  onDone: (info: { revealed_levels: number[]; unlocked: number | null; citations: Citation[] }) => void;
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
      else if (evt.type === "unlock") h.onUnlock(evt.level);
      else if (evt.type === "done") h.onDone(evt);
      else if (evt.type === "error") h.onError(evt.detail);
    }
  }
}

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
