import { useMutation, useQuery } from "@tanstack/react-query";
import {
  getProblem,
  ingestProblem,
  normalizeProblem,
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
    mutationFn: (body: { text?: string; image?: string }) => normalizeProblem(body),
  });
}

/**
 * Fetch one problem by slug, polling every 1.5s while ingestion is still running and
 * stopping the moment it reaches a terminal status — the client half of the backend's
 * 202-accept-then-poll pattern.
 */
export function useProblem(slug: string | null) {
  return useQuery({
    queryKey: ["problem", slug],
    queryFn: () => getProblem(slug as string),
    enabled: slug != null,
    refetchInterval: (query) => {
      const status = query.state.data?.ingestion_status;
      return status && !TERMINAL.has(status) ? 1500 : false;
    },
  });
}
