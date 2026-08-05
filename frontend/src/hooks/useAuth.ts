import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { getMe, login as loginRequest, logout as clearTokens, register } from "../lib/api";
import { tokens } from "../lib/auth";

export const meKey = ["me"] as const;

/**
 * The current user, or null when signed out. `enabled` gates on a token existing
 * so a signed-out visitor doesn't fire a guaranteed-401 on every page load.
 */
export function useCurrentUser() {
  return useQuery({
    queryKey: meKey,
    queryFn: getMe,
    enabled: Boolean(tokens.access()),
    staleTime: 5 * 60 * 1000,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      loginRequest(email, password),
    onSuccess: async () => {
      // The query was disabled while signed out; refetch now that a token exists.
      await queryClient.invalidateQueries({ queryKey: meKey });
      navigate("/");
    },
  });
}

export function useRegister() {
  const login = useLogin();

  return useMutation({
    // /register returns the user, not a token - sign in straight after so the user
    // isn't bounced to a login form they just filled in.
    mutationFn: async ({ email, password }: { email: string; password: string }) => {
      await register(email, password);
      await login.mutateAsync({ email, password });
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  return () => {
    clearTokens();
    queryClient.clear();
    navigate("/login");
  };
}
