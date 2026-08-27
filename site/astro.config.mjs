// @ts-check
import { defineConfig } from "astro/config";

// A project page is served from a subpath, so `base` is not optional: without
// it every asset and internal link resolves against the domain root and 404s.
export default defineConfig({
  site: "https://anubhabbehera.github.io",
  base: "/healing-hertz",
  markdown: {
    shikiConfig: {
      // Shiki's own themes each paint their own background, which would sit a
      // second opaque rectangle inside the pit that `pre` is drawn as. The
      // css-variables theme emits `var(--shiki-*)` instead, so the highlighting
      // is driven from Synth's palette in site/src/styles/wiki.css.
      theme: "css-variables",
      wrap: false,
    },
  },
});
