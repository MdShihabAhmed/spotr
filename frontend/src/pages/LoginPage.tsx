import { useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useLogin } from "../hooks/useAuth";
import { ApiError } from "../lib/api";
import { tokens } from "../lib/auth";

function messageFor(error: unknown): string {
  // Branch on the machine-readable `code`, not the human text - that's the whole
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
    <div className="auth-wrap">
      <form
        className="auth-card"
        onSubmit={(e) => {
          e.preventDefault();
          login.mutate({ email, password });
        }}
      >
        <h1>
          spot<span style={{ color: "var(--earned)" }}>r</span>
        </h1>
        <p className="lede">Upsolve competitive programming problems properly.</p>

        <div className="field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            className="input"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>

        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        {login.isError && <div className="error-box" style={{ marginBottom: 14 }}>{messageFor(login.error)}</div>}

        <button type="submit" className="btn" disabled={login.isPending}>
          {login.isPending ? "signing in…" : "sign in"}
        </button>

        <p className="switch">
          No account? <Link to="/register">Create one</Link>
        </p>
      </form>
    </div>
  );
}
