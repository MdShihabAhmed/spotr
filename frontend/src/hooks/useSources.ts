import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addGithubSource,
  addPdfSource,
  deleteSource,
  listSources,
  type GroundingSource,
} from "../lib/api";

const TERMINAL: ReadonlySet<GroundingSource["status"]> = new Set(["ready", "failed"]);
const sourcesKey = ["grounding-sources"] as const;

/** List grounding sources. While any source is still processing, poll so its status + chunk
 *  count converge (cloning/embedding can take a while). */
export function useSources() {
  return useQuery({
    queryKey: sourcesKey,
    queryFn: listSources,
    refetchInterval: (q) =>
      q.state.data?.items.some((s) => !TERMINAL.has(s.status)) ? 2000 : false,
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
