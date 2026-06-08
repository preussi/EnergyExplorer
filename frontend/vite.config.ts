import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// During `npm run dev`, proxy API calls to the backend so the browser sees a
// single origin. In Docker, nginx does the equivalent proxying.
export default defineConfig({
  plugins: [svelte()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // Use 127.0.0.1, not "localhost": on Node 18+ localhost can resolve to
        // IPv6 (::1) while uvicorn listens on IPv4 only, breaking the proxy.
        target: process.env.BACKEND_URL ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
