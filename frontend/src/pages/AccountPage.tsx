import { useCurrentUser } from "../hooks/useAuth";

export function AccountPage() {
  const { data: user } = useCurrentUser();
  if (!user) return null;

  const rows: [string, React.ReactNode][] = [
    ["email", user.email],
    ["user id", user.id],
    ["member since", new Date(user.created_at).toLocaleDateString()],
    ["codeforces handle", user.cf_handle ?? <span key="cf" className="muted">not linked</span>],
  ];

  return (
    <div className="plain-page">
      <h1>Account</h1>
      <div style={{ marginTop: 24, border: "1px solid var(--b-hair)" }}>
        {rows.map(([k, v], i) => (
          <div
            key={k}
            className="prompt-row"
            style={{ padding: "14px 18px", borderTop: i ? "1px solid var(--b-hair)" : "none" }}
          >
            <span className="k">{k}</span>
            <span className="v" style={{ fontFamily: "var(--mono)", fontSize: 12.5 }}>
              {v}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
