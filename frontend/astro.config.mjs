// @ts-check
import { defineConfig } from "astro/config";

import tailwindcss from "@tailwindcss/vite";
import node from "@astrojs/node";

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
    plugins: [tailwindcss()],
    server: {
      allowedHosts: true,
    },
  },

  adapter: node({
    mode: "standalone",
  }),
});
