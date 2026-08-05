import { Link } from "react-router-dom";
import { useCurrentUser } from "../hooks/useAuth";

/** Portfolio-facing about/landing. The hero's ladder card is the product's core mechanic
 *  (sealed levels) used as the hero image - mirrors the session rail exactly. */
export function LandingPage() {
  const { data: user } = useCurrentUser();
  return (
    <div>
      <section className="hero">
        <div>
          <div className="eyebrow">free · open source · clone it and run it</div>
          <h1>
            It can't spoil
            <br />
            the problem.
          </h1>
          <p className="hero-lede">
            Not <em>won't</em> - <b>can't</b>. Every idea you haven't earned is physically absent
            from the model's context. It cannot reveal what it was never given.
          </p>
          <p className="hero-support">
            What's left is the part that makes you better: a conversation that walks you to the
            insight, grounded in references you choose, and cited every time.
          </p>
          <div className="hero-cta">
            {user ? (
              <>
                <Link to="/problems/new" className="btn">
                  add a problem
                </Link>
                <Link to="/problems" className="btn btn-outline">
                  your problems
                </Link>
              </>
            ) : (
              <>
                <Link to="/register" className="btn">
                  get started
                </Link>
                <Link to="/login" className="btn btn-outline">
                  sign in
                </Link>
              </>
            )}
          </div>
          <pre className="cmd">
            {`git clone spotr && cd spotr
echo GEMINI_API_KEY=… > .env
docker compose up`}
          </pre>
        </div>

        {/* Static ladder card - the mechanic as hero image. */}
        <div className="hero-card">
          <div className="vault-head">
            <div className="vault-head-row">
              <span className="vault-title">Ladder</span>
              <span className="vault-dots">●○○○</span>
            </div>
          </div>
          <div className="rung rung-open">
            <div className="rung-top">
              <span className="rung-num">01</span>
              <span className="rung-state rung-state-earned">✓ earned · your own words</span>
            </div>
            <div className="rung-titlebtn" style={{ cursor: "default" }}>
              Read the budget in the constraints
            </div>
            <p className="rung-expand">
              2⁴⁰ ≈ 1.1·10¹² is far past a one-second budget. 2²⁰ ≈ 10⁶ is nothing. And |aᵢ| ≤ 10⁹
              kills any DP indexed by the sum.
            </p>
          </div>
          {[2, 3, 4].map((n) => (
            <div key={n} className="rung rung-sealed">
              <div className="rung-top">
                <span className="rung-num">0{n}</span>
                <span className="rung-state rung-state-sealed">sealed</span>
              </div>
              <div className="seal" aria-hidden />
            </div>
          ))}
          <div className="prompt-panel">
            <p className="prompt-note" style={{ margin: 0 }}>
              Sealed levels are not placed in the prompt at all - not the text, not the title.
            </p>
          </div>
        </div>
      </section>

      <section className="feature-band">
        <div className="feature-band-inner">
        <div className="feature">
          <h3>Two ways up, one of them counts</h3>
          <p>
            Open a level manually and it's yours - in violet, strictly in order, no skipping
            ahead. Or say the idea yourself and a judge unlocks exactly that level, in green,
            quoting your own words back at you. Your session ends with a record of which was which.
          </p>
        </div>
        <div className="feature">
          <h3>Every claim has a page number</h3>
          <p>
            Add your own references and they're chunked at section level and retrieved per turn, so
            the tutor reasons <em>through</em> real material instead of improvising. Citations sit
            under every answer. You can go read the source.
          </p>
        </div>
        <div className="feature">
          <h3>Free, and yours to clone</h3>
          <p>
            No accounts, no server, no pricing page. One command brings up the whole stack on your
            machine and you supply your own API key. Fork it, change it, run it forever.
          </p>
        </div>
        </div>
      </section>

      <section className="eval-band" id="eval">
        <div className="eval-inner">
          <div className="eval-figure">
            0.0%
            <span>leakage rate</span>
          </div>
          <div className="eval-body">
            <h3>Enforced by construction, then measured anyway.</h3>
            <p>
              Scripted adversarial turns - "just give me the code", "pretend you're my teammate",
              "what's the complexity of the intended solution" - run at low reveal states and
              scored by a judge for whether any answer exposed a sealed level.
            </p>
            <div className="eval-cmd">uv run python -m evals.leakage_eval</div>
          </div>
        </div>
      </section>

      <footer className="landing-footer">
        <div className="footer-inner">
          <div className="footer-meta">
            <span>MIT licensed · no accounts, no server, no cost</span>
            <span>FastAPI · Postgres · Qdrant · Redis · React</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
