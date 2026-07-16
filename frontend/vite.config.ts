import { defineConfig } from "vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import tsConfigPaths from "vite-tsconfig-paths";

export default defineConfig(async ({ command }) => ({
  server: {
    port: 8080,
  },
  resolve: {
    dedupe: [
      "react",
      "react-dom",
      "react/jsx-runtime",
      "react/jsx-dev-runtime",
      "@tanstack/react-query",
      "@tanstack/query-core",
    ],
  },
  plugins: [
    tailwindcss(),
    tsConfigPaths({ projects: ["./tsconfig.json"] }),
    tanstackStart({
      // Route the bundled server entry through src/server.ts (SSR error wrapper).
      server: { entry: "server" },
    }),
    // nitro drives the production server build only (Node target by default).
    ...(command === "build" ? [(await import("nitro/vite")).nitro()] : []),
    viteReact(),
  ],
}));
