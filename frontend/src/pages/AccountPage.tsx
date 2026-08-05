import { useQuery } from "@tanstack/react-query";
import { useCurrentUser } from "../hooks/useAuth";
import { getMyStats } from "../lib/api";

export function AccountPage() {
  const { data: user } = useCurrentUser();
  const { data: stats } = useQuery({ queryKey: ["my-stats"], queryFn: getMyStats });
  if (!user) return null;

  const rows: [string, React.ReactNode][] = [
    ["email", user.email],
    ["user id", user.id],
    ["member since", new Date(user.created_at).toLocaleDateString()],
    ["handle", user.cf_handle ?? <span key="cf" className="muted">not linked</span>],
  ];

  const earnedPct =
    stats && stats.earned_ratio != null ? Math.round(stats.earned_ratio * 100) : null;

  return (
    <div className="plain-page">
      <h1>Account</h1>

      {stats && (
        <div className="stat-card">
          <div className="section-label">Your progress</div>
          {stats.levels_earned + stats.levels_opened === 0 ? (
            <p className="muted" style={{ margin: "10px 0 0" }}>
              Nothing unlocked yet. Earn hints by reasoning them out in a session.
            </p>
          ) : (
            <>
              <div className="stat-headline">
                You earned <b>{earnedPct}%</b> of your hints by your own reasoning.
              </div>
              <div className="stat-bar" aria-label={`${earnedPct}% earned`}>
                <span className="stat-bar-fill" style={{ width: `${earnedPct}%` }} />
              </div>
              <div className="stat-nums">
                <span>
                  <b>{stats.levels_earned}</b> earned
                </span>
                <span>
                  <b>{stats.levels_opened}</b> opened
                </span>
                <span>
                  <b>{stats.problems_started}</b> problems started
                </span>
              </div>
            </>
          )}
        </div>
      )}

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
