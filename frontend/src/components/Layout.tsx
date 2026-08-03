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
          <span className="nav-glyph" aria-hidden />
          <span className="nav-word">
            spot<em>r</em>
          </span>
        </Link>

        <NavLink to="/problems/new" className="nav-item">
          add problem
        </NavLink>
        <NavLink to="/" end className="nav-item">
          about
        </NavLink>

        <div className="nav-right">
          <span>free · your key · your machine</span>
          {user && (
            <Link to="/account" className="nav-signout" title={user.email}>
              account
            </Link>
          )}
          <button type="button" className="nav-signout" onClick={logout}>
            sign out
          </button>
          <span className="nav-dot" aria-hidden />
        </div>
      </header>
      <main>{children}</main>
    </>
  );
}
