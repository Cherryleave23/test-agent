// Tauri v2 应用入口。前端（React SPA）由 tauri.conf.json 的 frontendDist 提供；
// 系统文件拖入经窗口 dragDropEnabled 事件在前端处理（见 DropZone.tsx）。
// 后端（FastAPI）需作为独立进程/侧车运行，前端经 VITE_API_BASE 指向其地址。
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running dataproc-gui");
}
