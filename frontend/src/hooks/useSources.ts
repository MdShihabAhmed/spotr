import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addGithubSource,
  addPdfSource,
  deleteSource,
  listSources,
  type GroundingSource,
} from "../lib/api";

const ACTIVE: ReadonlySet<GroundingSource["status"]> = new Set([
  "pending",
  "cloning",
  "embedding",
]);
const sourcesKey = ["grounding-sources"] as const;

/** List grounding sources. Poll fast (2s) while a source is actively processing so its status +
 *  chunk count converge; poll slowly (30s) while one is only waiting on a scheduled retry (a
 *  quota wait can be ~24h — no point hammering); otherwise stop. */
export function useSources() {
  return useQuery({
    queryKey: sourcesKey,
    queryFn: listSources,
    refetchInterval: (q) => {
      const items = q.state.data?.items ?? [];
      if (items.some((s) => ACTIVE.has(s.status))) return 2000;
      if (items.some((s) => s.status === "retrying")) return 30_000;
      return false;
    },
  });
}

export function useAddGithubSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { label: string; repo_url: string; license_ack: boolean }) =>
      addGithubSource(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: sourcesKey }),
  });
}

export function useAddPdfSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, label, licenseAck }: { file: File; label: string; licenseAck: boolean }) =>
      addPdfSource(file, label, licenseAck),
    onSuccess: () => qc.invalidateQueries({ queryKey: sourcesKey }),
  });
}

export function useDeleteSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteSource(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: sourcesKey }),
  });
}
