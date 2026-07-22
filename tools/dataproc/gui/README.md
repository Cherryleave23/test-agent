# dataproc GUI 工作台

数据处理工具的人机界面：选仓库 → 树状管理资料（固定三类：产品资料 / 知识类文章 / 原料资料，可任意多层嵌套）→ 拖拽入当前文件夹 → 全量或选择性处理 → 查看已处理结构。复用 `tools/dataproc/` 引擎，产出 NDJSON bundle（产物契约），与 agent 端零耦合。

## 结构
- `backend/`：FastAPI 后端（仓库/树/上传/处理标记/触发引擎），零 `import src.*`
- `frontend/`：React + Vite SPA（仓库栏 / 树 / 拖拽区 / 处理面板 / 已处理面板）
- `src-tauri/`：Tauri v2 桌面壳（双击打开即本地进程）

## 运行模式

### 1) 本地 Web（最快验证，无需打包）
```bash
cd tools/dataproc/gui/frontend
pnpm install && pnpm rebuild esbuild && pnpm build   # 生成 dist/
cd ../..
PYTHONPATH=$PWD/tools DATAPROC_REPOS_BASE=/tmp/dp_repos \
  python3 -m uvicorn dataproc.gui.backend.main:app --port 8000
# 浏览器打开 http://localhost:8000  → 即 GUI
```
后端同源托管 `frontend/dist`，`/` 是 GUI，API（`/repos` `/tree` `/upload` `/process` `/bundle`）同端口。

### 2) Tauri 桌面应用（双击打开）
先 `pnpm build` 前端，再：
```bash
cd tools/dataproc/gui
pnpm tauri dev      # 开发预览
pnpm tauri build    # 产出平台安装包
```
> Tauri 打包需要系统依赖（Linux: `webkit2gtk-4.1` 等）与 `src-tauri/icons/icon.png`（请补一张图标）。
> 后端（FastAPI）需作为独立进程/侧车运行；前端经 `VITE_API_BASE` 指向其地址（或 Tauri 侧车注入）。
> 系统文件拖入走 Tauri 窗口 `dragDropEnabled` 事件（见 `DropZone.tsx`），浏览器/本地 Web 模式用原生 HTML5 拖放。

## 测试
后端行为由 `harness/test_gui_backend.py` 覆盖（G1 仓库 / G2 树嵌套 / G3 上传 / G4 标记去重 / G5 触发产 bundle），随主 harness 运行。
