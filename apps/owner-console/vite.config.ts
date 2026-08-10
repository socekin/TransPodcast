import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "dist/client",
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (
            id.includes("react-markdown") ||
            id.includes("/unified/") ||
            id.includes("/remark-") ||
            id.includes("/rehype-")
          ) {
            return "markdown";
          }
          if (id.includes("lucide-react")) return "icons";
          if (id.includes("@base-ui")) return "base-ui";
          return undefined;
        }
      }
    }
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"]
  },
  server: {
    host: "127.0.0.1",
    port: 4173,
    allowedHosts: ["terminal.local"],
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/feed": "http://127.0.0.1:8000",
      "/media": "http://127.0.0.1:8000"
    }
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"]
  }
});
