import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Frontend on 5177; proxy API + media to the FastAPI backend on 8077.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    strictPort: true,
    fs: { deny: ["**/.env*", "**/.local-token", "**/*.db", "**/*.{crt,pem}"] },
    port: 5177,
    proxy: {
      "/api":   { target: "http://127.0.0.1:8077", changeOrigin: false },
      "/media": { target: "http://127.0.0.1:8077", changeOrigin: false },
    },
  },
});
