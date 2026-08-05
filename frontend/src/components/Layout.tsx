import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { useCurrentUser, useLogout } from "../hooks/useAuth";

/** The fixed 54px dark nav on every screen. The mark is a half-revealed ladder (top half
 *  lit): the product's mechanic as its logo. "free · your key · your machine" is positioning,
 *  and it stands in for the account menu a hosted app would put there. */
export function Layout({ children }: { children: ReactNode }) {
  const { data: user } = useCurrentUser();
  const logout = useLogout();

  return (
    <>
      <header className="nav-bar">
        <Link to="/" className="nav-mark">
          <svg className="nav-glyph" viewBox="0 0 32 32" aria-hidden="true">
            <rect x="7" y="4" width="2.4" height="24" fill="#6b6675" />
            <rect x="22.6" y="4" width="2.4" height="24" fill="#6b6675" />
            <rect x="7" y="5.6" width="18" height="2.8" fill="#6b6675" />
            <rect x="7" y="12.1" width="18" height="2.8" fill="#6b6675" />
            <rect x="7" y="18.6" width="18" height="2.8" fill="#34c46a" />
            <rect x="7" y="25.1" width="18" height="2.8" fill="#57e089" />
          </svg>
          <span className="nav-word">
            spot<em>r</em>
          </span>
        </Link>

        {user ? (
          <>
            <NavLink to="/problems" end className="nav-item">
              problems
            </NavLink>
            <NavLink to="/problems/new" className="nav-item">
              add problem
            </NavLink>
            <NavLink to="/grounding" className="nav-item">
              sources
            </NavLink>
          </>
        ) : (
          <NavLink to="/" end className="nav-item">
            about
          </NavLink>
        )}

        <div className="nav-right">
          <span>free · your key · your machine</span>
          {user ? (
            <>
              <Link to="/account" className="nav-signout" title={user.email}>
                account
              </Link>
              <button type="button" className="nav-signout" onClick={logout}>
                sign out
              </button>
            </>
          ) : (
            <>
              <Link to="/login" className="nav-signout">
                sign in
              </Link>
              <Link to="/register" className="nav-signout">
                get started
              </Link>
            </>
          )}
          <span className="nav-dot" aria-hidden />
        </div>
      </header>
      <main>{children}</main>
    </>
  );
}
