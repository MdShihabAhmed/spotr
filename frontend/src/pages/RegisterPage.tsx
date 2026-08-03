import { useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useRegister } from "../hooks/useAuth";
import { ApiError } from "../lib/api";
import { tokens } from "../lib/auth";

const MIN_PASSWORD = 8; // mirrors UserCreate's Field(min_length=8) on the backend

function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "email_already_registered")
      return "That email is already registered. Try signing in.";
    // 422 from pydantic validation has a different body shape than ApiError's.
    if (error.status === 422) return `Password must be at least ${MIN_PASSWORD} characters.`;
    return error.message;
  }
  return "Could not reach the server. Is the backend running?";
}

export function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const signUp = useRegister();

  if (tokens.access()) return <Navigate to="/" replace />;

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD;

  return (
    <div className="auth-wrap">
      <form
        className="auth-card"
        onSubmit={(e) => {
          e.preventDefault();
          if (!tooShort) signUp.mutate({ email, password });
        }}
      >
        <h1>Create account</h1>
        <p className="lede">Start upsolving without spoilers.</p>

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
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <p className="composer-hint" style={{ color: tooShort ? "var(--bad)" : undefined }}>
            At least {MIN_PASSWORD} characters.
          </p>
        </div>

        {signUp.isError && <div className="error-box" style={{ marginBottom: 14 }}>{messageFor(signUp.error)}</div>}

        <button type="submit" className="btn" disabled={signUp.isPending || tooShort}>
          {signUp.isPending ? "creating…" : "create account"}
        </button>

        <p className="switch">
          Already have one? <Link to="/login">Sign in</Link>
        </p>
      </form>
    </div>
  );
}
