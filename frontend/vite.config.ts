/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Ports can be moved (the end-to-end tests run a second copy next to `npm run dev`).
const webPort = Number(process.env.VIEWER_WEB_PORT ?? 5173);
const apiPort = Number(process.env.VIEWER_API_PORT ?? 8000);

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: webPort,
    strictPort: true,
    proxy: { "/api": `http://127.0.0.1:${apiPort}` },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "scripts/**/*.test.mjs"],
  },
});
