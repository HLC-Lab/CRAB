import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// Build output goes straight into the Python package's static dir so it ships
// in the wheel and is served by FastAPI (see src/crab/web/server.py).
export default defineConfig({
  plugins: [vue()],
  base: "/",
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: fileURLToPath(new URL("../web/static", import.meta.url)),
    emptyOutDir: true,
  },
  server: {
    // Dev: proxy API calls to the running `crab web` backend.
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
});
