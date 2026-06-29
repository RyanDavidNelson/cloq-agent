import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy /api -> the FastAPI backend so the SPA can use same-origin relative URLs
// (in prod, nginx does the same proxy — see docker/nginx.conf).
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 8080,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
