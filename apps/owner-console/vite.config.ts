import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Point the dev server at another API instance, e.g. a worktree build.
const apiTarget = process.env.TRANSPODCAST_API_URL ?? "http://127.0.0.1:8000";

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
      "/api": apiTarget,
      "/feed": apiTarget,
      "/media": apiTarget
    }
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"]
  }
});
