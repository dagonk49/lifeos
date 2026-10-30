"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ServiceWorkerRegistration } from "@/components/ServiceWorkerRegistration";
import { isApiError } from "@/lib/api";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            refetchOnWindowFocus: true,
            retry: (count, error) => !isApiError(error) && count < 2,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <ServiceWorkerRegistration />
      {children}
    </QueryClientProvider>
  );
}
