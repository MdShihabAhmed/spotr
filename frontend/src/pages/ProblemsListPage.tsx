import { useState } from "react";
import { Link } from "react-router-dom";
import { useProblemList } from "../hooks/useProblems";
import type { IngestionStatus } from "../lib/api";

const PAGE_SIZE = 20;

// Only surface a badge when the problem isn't a plain ready session — a ready one needs no
// annotation, it just opens.
const STATUS_LABEL: Partial<Record<IngestionStatus, string>> = {
  pending: "queued",
  normalizing: "working…",
  generating_levels: "working…",
  failed: "failed",
};

export function ProblemsListPage() {
  const [page, setPage] = useState(1);
  const { data, isPending, isError } = useProblemList(page, PAGE_SIZE);

  const total = data?.total ?? 0;
  const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="plain-page">
      <div className="list-head">
        <h1>Problems</h1>
        <Link to="/problems/new" className="btn">
          add a problem
        </Link>
      </div>

      {isPending && <p className="muted">Loading…</p>}
      {isError && <div className="error-box">Couldn't load the problems list.</div>}

      {data && data.items.length === 0 && (
        <div className="list-empty">
          <p className="muted">No problems yet.</p>
          <Link to="/problems/new" className="btn btn-outline">
            add the first one
          </Link>
        </div>
      )}

      {data && data.items.length > 0 && (
        <ul className="prob-list">
          {data.items.map((p) => {
            const badge = STATUS_LABEL[p.ingestion_status];
            return (
              <li key={p.slug}>
                <Link to={`/problems/${p.slug}`} className="prob-row">
                  <span className="prob-title">{p.title || p.external_id}</span>
                  <span className="prob-meta">
                    <span className="prob-source">{p.source}</span>
                    {p.rating != null && <span className="prob-rating">{p.rating}</span>}
                    {badge && (
                      <span className={`prob-status prob-status-${p.ingestion_status}`}>{badge}</span>
                    )}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      {total > PAGE_SIZE && (
        <div className="pager">
          <button
            className="btn btn-outline"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            ← newer
          </button>
          <span className="pager-at">
            page {page} of {lastPage}
          </span>
          <button
            className="btn btn-outline"
            disabled={page >= lastPage}
            onClick={() => setPage((p) => Math.min(lastPage, p + 1))}
          >
            older →
          </button>
        </div>
      )}
    </div>
  );
}
