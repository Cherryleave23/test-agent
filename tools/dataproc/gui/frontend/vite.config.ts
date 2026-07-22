import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri 偏好固定端口；本地 Web 模式下由 FastAPI 同源托管 dist。
export default defineConfig({
  plugins: [react()],
  server: { port: 1420, strictPort: true },
  build: { outDir: "dist", emptyOutDir: true },
});
