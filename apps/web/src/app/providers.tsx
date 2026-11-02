"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

import { ServiceWorkerRegistration } from "@/components/ServiceWorkerRegistration";
import { isApiError } from "@/lib/api";
import { redirectToLogin } from "@/lib/session";

/**
 * The query client of the app. Any request answered `unauthenticated` (session expired,
 * revoked from another device or from the server) opens the login page, which empties the
 * cache once shown and brings the owner back here afterwards; on the public pages that
 * answer is expected and left to the page.
 */
export function createQueryClient(navigate: (path: string) => void): QueryClient {
  const onError = (error: unknown) => {
    if (isApiError(error, "unauthenticated")) redirectToLogin(navigate);
  };
  return new QueryClient({
    queryCache: new QueryCache({ onError }),
    mutationCache: new MutationCache({ onError }),
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: true,
        retry: (count, error) => !isApiError(error) && count < 2,
      },
    },
  });
}

export function Providers({ children }: { children: ReactNode }) {
  // The App Router's router is stable for the lifetime of the app.
  const router = useRouter();
  const [client] = useState(() => createQueryClient((path) => router.replace(path)));
  return (
    <QueryClientProvider client={client}>
      <ServiceWorkerRegistration />
      {children}
    </QueryClientProvider>
  );
}
