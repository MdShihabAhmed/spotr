import { useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useLogin } from "../hooks/useAuth";
import { ApiError } from "../lib/api";
import { tokens } from "../lib/auth";

function messageFor(error: unknown): string {
  // Branch on the machine-readable `code`, not the human text — that's the whole
  // point of the {detail, code} error schema.
  if (error instanceof ApiError) {
    if (error.code === "invalid_credentials") return "Incorrect email or password.";
    return error.message;
  }
  return "Could not reach the server. Is the backend running?";
}

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const login = useLogin();

  if (tokens.access()) return <Navigate to="/" replace />;

  return (
    <div className="centered">
      <form
        className="card auth-card"
        onSubmit={(e) => {
          e.preventDefault();
          login.mutate({ email, password });
        }}
      >
        <h1 className="auth-title">
          Forces<span className="brand-accent">AI</span>
        </h1>
        <p className="muted auth-sub">Upsolve Codeforces problems properly.</p>

        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />

        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        {login.isError && <p className="error">{messageFor(login.error)}</p>}

        <button type="submit" className="btn btn-primary" disabled={login.isPending}>
          {login.isPending ? "Signing in…" : "Sign in"}
        </button>

        <p className="muted switch">
          No account? <Link to="/register">Create one</Link>
        </p>
      </form>
    </div>
  );
}
