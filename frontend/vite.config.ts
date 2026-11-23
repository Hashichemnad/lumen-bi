import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs on :8000; in Docker, Caddy serves both from one origin.
// The API, and the MCP server with its OAuth endpoints (for AI apps such as Claude)
const API_PROXY = Object.fromEntries(
  ["/api", "/mcp", "/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server", "/oauth/token", "/oauth/register", "/oauth/revoke"]
    .map((p) => [p, "http://localhost:8000"]),
);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy: API_PROXY },
  preview: { proxy: API_PROXY },
  // The chart builder (ECharts) and the world map are loaded on demand, so large chunks are expected
  build: { chunkSizeWarningLimit: 900 },
});
