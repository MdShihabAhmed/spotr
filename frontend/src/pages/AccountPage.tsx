import { useCurrentUser } from "../hooks/useAuth";

export function AccountPage() {
  const { data: user } = useCurrentUser();
  if (!user) return null;

  return (
    <div className="stack">
      <h1>Account</h1>

      <section className="card">
        <dl className="details">
          <dt>Email</dt>
          <dd>{user.email}</dd>

          <dt>User ID</dt>
          <dd>
            <code>{user.id}</code>
          </dd>

          <dt>Member since</dt>
          <dd>{new Date(user.created_at).toLocaleDateString()}</dd>

          <dt>Codeforces handle</dt>
          <dd>
            {user.cf_handle ?? <span className="muted">Not linked</span>}
            <p className="hint">
              Linking a handle lets recommendations use your real rating and skip problems you've
              already solved. Verification ships with step 7.
            </p>
          </dd>
        </dl>
      </section>
    </div>
  );
}
