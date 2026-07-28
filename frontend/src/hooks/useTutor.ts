import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getTutorSession, revealNextLevel } from "../lib/api";

/** The tutor session for a problem (unlocked rungs, history, status). Static once loaded —
 *  the chat turn streams outside React Query and updates local component state. */
export function useTutorSession(slug: string | null) {
  return useQuery({
    queryKey: ["tutor", slug],
    queryFn: () => getTutorSession(slug as string),
    enabled: slug != null,
  });
}

/** Manual sequential reveal. Invalidates the session so refetch reflects the new rung. */
export function useReveal(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => revealNextLevel(slug),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tutor", slug] }),
  });
}
