import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// FastAPI listens here (see apps/web/README.md for how to start it).
const API_ORIGIN = "http://127.0.0.1:8000";

// The browser only ever talks to the Vite server (same origin). Requests to
// /api/* are forwarded to FastAPI with the "/api" prefix removed, so the
// browser's /api/auth/login reaches FastAPI's /auth/login. This is a dev-time
// reverse proxy; nginx plays the same role in the Compose stack. Because the
// request is same-origin there is no CORS (ADR-025). The browser's Origin
// header is forwarded unchanged (changeOrigin stays false), which is what
// FastAPI's CSRF check compares against AUTH_TRUSTED_ORIGIN.
const apiProxy = {
  "^/api(/|$)": {
    target: API_ORIGIN,
    changeOrigin: false,
    rewrite: (path: string) => path.replace(/^\/api(?=\/|$)/, ""),
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Loopback only (ADR-025): never listen on the LAN until TLS exists.
  server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy: apiProxy },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true, proxy: apiProxy },
  test: { environment: "node", include: ["src/**/*.test.{ts,tsx}"] },
});
