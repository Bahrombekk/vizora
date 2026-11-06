import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev'da backend API va HLS proxy'siga so'rovlarni yo'naltiramiz.
// VIZORA_API — dev backend manzili (standart :8000).
const API = process.env.VIZORA_API || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": API,
      "/hls": API,
      "/whep": API,
      "/ws": { target: API.replace(/^http/, "ws"), ws: true },
    },
  },
  build: {
    outDir: "dist",
  },
});
