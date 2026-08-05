import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useCurrentUser } from "../hooks/useAuth";
import { tokens } from "../lib/auth";

/**
 * Gates a route on a *verified* session, not merely on a token existing -
 * a stale or revoked token still fails /me, and must not render the app shell.
 */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const hasToken = Boolean(tokens.access());
  const { data: user, isPending, isError } = useCurrentUser();

  if (!hasToken) return <Navigate to="/login" replace />;
  if (isPending) return <div className="centered muted">Loading…</div>;
  if (isError || !user) return <Navigate to="/login" replace />;

  return <>{children}</>;
}
