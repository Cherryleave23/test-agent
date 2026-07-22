import React, { useEffect, useRef, useState } from "react";

interface Props {
  current: string;
  currentFolder: string;
  busy: boolean;
  onFiles: (files: File[]) => void;
  onOsPaths: (paths: string[]) => void;
}

export default function DropZone({ current, currentFolder, busy, onFiles, onOsPaths }: Props) {
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Tauri：系统拖入走 tauri://drag-drop 事件（浏览器 DnD 在 Tauri 中被拦截）
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    (async () => {
      try {
        const w = await import("@tauri-apps/api/window");
        const win = (w as any).getCurrentWindow();
        unlisten = await win.onDragDropEvent((e: any) => {
          if (e.payload.type === "drop") onOsPaths(e.payload.paths as string[]);
        });
      } catch {
        /* 非 Tauri 环境：忽略 */
      }
    })();
    return () => unlisten && unlisten();
  }, [onOsPaths]);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length) onFiles(files);
  };

  if (!current) return <div className="dropzone disabled">（请先选择仓库）</div>;

  return (
    <div
      className={"dropzone" + (over ? " over" : "")}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      onClick={() => inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          const files = Array.from(e.target.files || []);
          if (files.length) onFiles(files);
          e.target.value = "";
        }}
      />
      <div className="dz-title">拖拽文件到此处 → 放入「{currentFolder || "仓库根"}」</div>
      <div className="dz-sub">
        {busy ? "处理中…" : "点击也可选择文件（支持 md / 图片 / PDF 多选）"}
      </div>
    </div>
  );
}
