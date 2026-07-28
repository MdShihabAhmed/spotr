import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getProblem,
  ingestProblem,
  normalizeProblem,
  streamProblemStatus,
  type IngestRequest,
  type Problem,
} from "../lib/api";

// Ingestion is finished (for good or ill) at these; anything else is still in flight.
const TERMINAL: ReadonlySet<Problem["ingestion_status"]> = new Set(["ready", "failed"]);

export function useIngestProblem() {
  return useMutation({ mutationFn: (body: IngestRequest) => ingestProblem(body) });
}

/** Clean pasted text / a screenshot into structured fields for review (one LLM call). */
export function useNormalize() {
  return useMutation({
    mutationFn: (body: { text?: string; images?: string[] }) => normalizeProblem(body),
  });
}

/**
 * Fetch one problem by slug and keep it live while ingestion runs. Updates are pushed over
 * SSE (the backend LISTENs on Postgres and streams each status change) — no polling. If the
 * stream can't be opened (proxy buffering, transient failure), we fall back to a slow poll so
 * status still converges.
 */
export function useProblem(slug: string | null) {
  const queryClient = useQueryClient();
  const [streamFailed, setStreamFailed] = useState(false);

  const query = useQuery({
    queryKey: ["problem", slug],
    queryFn: () => getProblem(slug as string),
    enabled: slug != null,
    // Polling is the fallback only; while SSE works this stays off.
    refetchInterval: (q) => {
      if (!streamFailed) return false;
      const status = q.state.data?.ingestion_status;
      return status && !TERMINAL.has(status) ? 2000 : false;
    },
  });

  useEffect(() => {
    if (slug == null || streamFailed) return;
    const controller = new AbortController();
    // The server emits current status immediately and closes on a terminal one, so opening
    // unconditionally is safe. Each frame just refetches the full problem row.
    void streamProblemStatus(
      slug,
      {
        onStatus: () => queryClient.invalidateQueries({ queryKey: ["problem", slug] }),
        onError: () => setStreamFailed(true),
      },
      controller.signal,
    );
    return () => controller.abort();
  }, [slug, streamFailed, queryClient]);

  return query;
}
