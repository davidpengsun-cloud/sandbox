import { defineConfig, transformWithOxc } from "vite";
import react from "@vitejs/plugin-react";

const jsxInJs = {
  name: "jsx-in-js",
  enforce: "pre",
  async transform(code, id) {
    if (!/\/src\/.*\.js$/.test(id.split("?")[0])) return null;
    const { code: out, map } = await transformWithOxc(code, id, { lang: "jsx", jsx: { runtime: "automatic" } });
    return { code: out, map };
  },
};

export default defineConfig({
  plugins: [jsxInJs, react()],
  optimizeDeps: { rolldownOptions: { moduleTypes: { ".js": "jsx" } } },
  define: { "process.env.REACT_APP_ASK_FEEDBACK_URL": JSON.stringify(process.env.REACT_APP_ASK_FEEDBACK_URL) ?? "undefined" },
  build: { outDir: "build" },
  test: {
    environment: "happy-dom",
    setupFiles: "./src/setupTests.js",
    coverage: {
      provider: "v8",
      include: ["src/**"],
      exclude: [
        "src/**/*.test.js",
        "src/setupTests.js",
        "src/index.js",
        "src/reportWebVitals.js",
        "src/**/*.css",
        "src/assets/**",
      ],
      reporter: ["text-summary", "text"],
      // Floors: lines/statements 92, branches 85, functions 90 — about three
      // points under the measured figures, so they hold without flaking.
      thresholds: { lines: 92, statements: 92, branches: 85, functions: 90 },
    },
  },
});
