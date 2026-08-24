// esbuild bundler for the sidepanel chat app (React + streamdown).
// Run: npm run build   (from chrome-extension/sidepanel-src)
import { build } from "esbuild";

await build({
  entryPoints: ["main.jsx"],
  bundle: true,
  minify: true,
  format: "iife",
  outfile: "../sidepanel.js",
  jsx: "automatic",
  loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"production"' },
  logLevel: "info",
});
console.log("bundled -> ../sidepanel.js");
