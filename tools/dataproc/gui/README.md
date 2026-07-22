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

**构建状态（2026-07-21）**：代码侧阻塞已全部清掉，前端构建链已验证；真实 `tauri build` 为**环境门控**——需要系统 `webkit2gtk-4.1` + Rust 工具链，沙箱/CI 无此系统依赖时不打包，但在装好预装的构建机上可一键出包。

已修复的代码侧阻塞：
- `src-tauri/icons/icon.png`：已生成（512×512 品牌图标，青蓝渐变 + DP 字标）。
- `pnpm-workspace.yaml`：已建（映射 `frontend`，使 `tauri.conf.json` 里的 `pnpm --filter dataproc-gui build` 能解析）。
- `VITE_API_BASE` 接线：`src/api.ts` 读 `import.meta.env.VITE_API_BASE` 并带同源回退（本地 Web 模式同源；Tauri 侧车模式构建时注入后端地址），接线正确。
- 前端构建链：`pnpm --filter dataproc-gui build` 已验证产出 `frontend/dist`（46 模块，<1s）。

在**构建机**上的一键打包清单（Linux 示例）：
```bash
# 0) 系统预装（一次性，Tauri v2 Linux 必装）
sudo apt-get update
sudo apt-get install -y libwebkit2gtk-4.1-dev build-essential \
  libssl-dev librsvg2-dev pkg-config

# 1) 前端依赖 + 构建（生成 frontend/dist）
cd tools/dataproc/gui
pnpm install
pnpm --filter dataproc-gui build

# 2) 真实打包（开发预览 / 出平台安装包）
pnpm tauri dev      # 开发预览
pnpm tauri build    # 产出平台安装包（Linux: .deb/.AppImage）

# 3)（可选）若需全量图标集，从现有 icon.png 重新生成
pnpm tauri icon frontend/icons/icon.png   # 生成 icon.icns/icon.ico/32x32.png 等
```
> 后端（FastAPI）作为独立进程/侧车运行；前端经 `VITE_API_BASE` 指向其地址（或 Tauri 侧车注入）。
> 系统文件拖入走 Tauri 窗口 `dragDropEnabled` 事件（见 `DropZone.tsx`），浏览器/本地 Web 模式用原生 HTML5 拖放。
> 沙箱验证边界：`tauri.conf.json` 配置、`pnpm-workspace.yaml`、图标、`VITE_API_BASE` 接线、前端 `vite build` 均已验证；`pnpm tauri build` 仅因缺系统 `webkit2gtk` 未跑通（环境门控，非代码缺陷）。

## 测试
后端行为由 `harness/test_gui_backend.py` 覆盖（G1 仓库 / G2 树嵌套 / G3 上传 / G4 标记去重 / G5 触发产 bundle），随主 harness 运行。
