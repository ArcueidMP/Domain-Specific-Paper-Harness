import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), "");

  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": environment.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000",
        "/health": environment.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000",
      },
    },
    preview: {
      host: "127.0.0.1",
      port: 4173,
      strictPort: true,
    },
  };
});
