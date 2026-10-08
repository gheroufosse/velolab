import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { coordinator } from "./auth/browser";
import "./index.css";

// TanStack Query caches server responses. It must never outlive a session:
// the cache is wiped whenever the auth generation changes (login, logout,
// recovery, another tab switching account). The access token itself is not
// stored here; it stays inside the coordinator.
const queryClient = new QueryClient({
  // Auth failures are handled by the coordinator; never loop on retries.
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});
let seenGeneration = coordinator.getState().generation;
coordinator.subscribe(() => {
  const { generation } = coordinator.getState();
  if (generation !== seenGeneration) {
    seenGeneration = generation;
    queryClient.clear();
  }
});

// Called once, outside React: StrictMode runs component effects twice in
// development, which would otherwise start the session restore twice.
void coordinator.start();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
