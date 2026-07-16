const ACCESS_KEY = "cp_access_token";
const REFRESH_KEY = "cp_refresh_token";

// Tokens live in localStorage, which means an XSS bug exposes them. The alternative
// (httpOnly cookies) needs the backend to stop using OAuth2 bearer headers and to add
// CSRF protection. Documented as a known tradeoff in the README rather than pretended away.
export const tokens = {
  access: () => localStorage.getItem(ACCESS_KEY),
  refresh: () => localStorage.getItem(REFRESH_KEY),
  set(access: string, refresh: string) {
    localStorage.setItem(ACCESS_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};
