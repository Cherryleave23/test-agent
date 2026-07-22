import React, { useEffect, useRef, useState } from "react";

interface Props {
  current: string;
  currentFolder: string;
  busy: boolean;
  onFiles: (files: File[]) => void;
  onOsPaths: (paths: string[]) => void;
}

/** 递归读取拖入的目录项（webkitGetAsEntry），返回所有文件的 File 对象。 */
async function readDirEntries(
  entry: any,
  path: string = "",
  acc: File[] = []
): Promise<File[]> {
  if (entry.isFile) {
    return new Promise<File[]>((resolve) => {
      entry.file((file: File) => {
        // 如果在子目录中，修改 file.name 为相对路径以保留结构
        if (path) {
          const renamed = new File([file], path + "/" + file.name, {
            type: file.type,
            lastModified: file.lastModified,
          });
          acc.push(renamed);
        } else {
          acc.push(file);
        }
        resolve(acc);
      });
    });
  } else if (entry.isDirectory) {
    const reader = entry.createReader();
    const entries = await new Promise<any[]>((resolve) => {
      const all: any[] = [];
      const readBatch = () => {
        reader.readEntries((batch: any[]) => {
          if (!batch.length) {
            resolve(all);
          } else {
            all.push(...batch);
            readBatch();
          }
        });
      };
      readBatch();
    });
    const subPath = path ? path + "/" + entry.name : entry.name;
    for (const e of entries) {
      await readDirEntries(e, subPath, acc);
    }
    return acc;
  }
  return acc;
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

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);

    // 优先尝试 webkitGetAsEntry（支持文件夹递归）
    const items = e.dataTransfer.items;
    if (items && items.length > 0) {
      const allFiles: File[] = [];
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        const entry = item.webkitGetAsEntry?.();
        if (entry) {
          await readDirEntries(entry, "", allFiles);
        }
      }
      if (allFiles.length) {
        onFiles(allFiles);
        return;
      }
    }

    // 回退：普通文件拖入
    const files = Array.from(e.dataTransfer.files).filter((f) => f.size > 0);
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
      <div className="dz-title">拖拽文件或文件夹到此处 → 放入「{currentFolder || "仓库根"}」</div>
      <div className="dz-sub">
        {busy ? "处理中…" : "支持 md / 图片 / PDF，可拖入整个文件夹（自动保留目录结构）"}
      </div>
    </div>
  );
}
