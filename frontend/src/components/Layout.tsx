import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useCurrentUser, useLogout } from "../hooks/useAuth";

export function Layout({ children }: { children: ReactNode }) {
  const { data: user } = useCurrentUser();
  const logout = useLogout();

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          Forces<span className="brand-accent">AI</span>
        </div>

        <nav className="nav">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/ingest">Ingest</NavLink>
          <NavLink to="/account">Account</NavLink>
        </nav>

        <div className="topbar-right">
          <span className="muted email">{user?.email}</span>
          <button type="button" className="btn btn-ghost" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>

      <main className="content">{children}</main>
    </div>
  );
}
