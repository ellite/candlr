// @ts-check
import { defineConfig } from "astro/config";

import tailwindcss from "@tailwindcss/vite";
import node from "@astrojs/node";
import { paraglideVitePlugin } from "@inlang/paraglide-js";

// https://astro.build/config
export default defineConfig({
  output: "server",

  devToolbar: {
    enabled: false,
  },

  security: {
    checkOrigin: false,
  },

  server: {
    port: 4258,
    host: "0.0.0.0",
  },

  vite: {
    plugins: [
      tailwindcss(),
      // UI translations. The language is a user preference, not part of the URL.
      // Resolution order: the signed-in user's account language (injected by the
      // custom-account strategy in src/lib/ui-locale.ts), then the candlr_language
      // cookie - also where client-side scripts read it from, and where a
      // signed-out visitor's pick lands - then English. The browser's language is
      // deliberately not consulted.
      paraglideVitePlugin({
        project: "./project.inlang",
        outdir: "./src/paraglide",
        strategy: ["custom-account", "cookie", "baseLocale"],
        cookieName: "candlr_language",
      }),
    ],
    build: {
      rollupOptions: {
        // A misspelled key (m.nav_hom()) only makes Rollup warn that it is not
        // exported by the generated messages; the page then throws at render
        // time. Fail the build instead.
        onwarn(warning, warn) {
          if (warning.code === "MISSING_EXPORT" && /[\\/]paraglide[\\/]/.test(warning.exporter ?? "")) {
            throw new Error(`Unknown message key: ${warning.message}`);
          }
          warn(warning);
        },
      },
    },
    server: {
      allowedHosts: true,
    },
  },

  adapter: node({
    mode: "standalone",
  }),
});
