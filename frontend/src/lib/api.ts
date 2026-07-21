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
