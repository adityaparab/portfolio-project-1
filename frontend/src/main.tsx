/**
 App entry. Global styles exist exactly here: Mantine's stylesheets + the
 token sheet; everything else is CSS Modules (AGENTS.md frontend standard).
*/
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider, createTheme } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { BrowserRouter } from "react-router-dom";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@mantine/core/styles.css";
import "@mantine/dates/styles.css";
import "@mantine/notifications/styles.css";
import "~/styles/global.css";
import App from "~/App";
import { PersonaProvider } from "~/personas/PersonaContext";

// The theme reads the same tokens as global.css so both systems stay in sync.
const theme = createTheme({
  primaryColor: "teal",
  primaryShade: 7,
  defaultRadius: "var(--io-radius)",
  fontFamily:
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif",
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 10_000 },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider theme={theme}>
      <Notifications />
      <QueryClientProvider client={queryClient}>
        <PersonaProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </PersonaProvider>
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
);
