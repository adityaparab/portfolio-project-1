/** Test harness: all providers the app needs, minus the router. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";
import type { ReactNode } from "react";
import { PersonaProvider } from "~/personas/PersonaContext";

export function TestProviders({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <MantineProvider>
      <QueryClientProvider client={queryClient}>
        <PersonaProvider>{children}</PersonaProvider>
      </QueryClientProvider>
    </MantineProvider>
  );
}
