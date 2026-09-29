import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig({
  plugins: [viteSingleFile()],
  build: {
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    emptyOutDir: false,
    modulePreload: false,
    outDir: "../src/voxbridge/ui",
    rollupOptions: {
      input: "audio-delivery-v1.html",
    },
  },
});
