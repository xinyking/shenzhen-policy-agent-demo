import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/v1": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyReq", (outgoing, incoming) => {
            if (incoming.headers.origin === "http://127.0.0.1:5173")
              outgoing.setHeader("origin", "http://127.0.0.1:8000");
          });
        },
      },
      "/health": "http://127.0.0.1:8000",
    },
  },
});
