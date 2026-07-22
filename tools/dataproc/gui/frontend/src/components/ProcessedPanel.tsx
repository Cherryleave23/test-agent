import React, { useState } from "react";
import ContextMenu from "./ContextMenu";

interface BundleData {
  total_files: number;
  total_corpus: number;
  product_kinds: Record<string, number>;
  structured: number;
  output_path?: string;
}

interface MarkerData {
  rel_path: string;
  path: string;
  status: string;
  kind: string;
  struct_type: string;
}

interface Props {
  markers: MarkerData[];
  bundle: BundleData | null;
  onPreviewFile?: (path: string) => void;
  onOpenExplorer?: (path: string) => void;
}

interface MenuState {
  visible: boolean;
  x: number;
  y: number;
  path: string;
}

export default function ProcessedPanel({ markers, bundle, onPreviewFile, onOpenExplorer }: Props) {
  const [menu, setMenu] = useState<MenuState>({
    visible: false, x: 0, y: 0, path: "",
  });

  const processedFiles = markers.filter((m) => m.status === "processed");
  const failedFiles = markers.filter((m) => m.status === "failed");

  const handleContextMenu = (e: React.MouseEvent, path: string) => {
    e.preventDefault();
    e.stopPropagation();
    setMenu({ visible: true, x: e.clientX, y: e.clientY, path });
  };

  const safePath = menu.path || "";
  const menuItems = [
    ...(onOpenExplorer ? [{ label: "在资源管理器中打开", action: () => onOpenExplorer(safePath) }] : []),
    ...(onPreviewFile && (safePath.toLowerCase().endsWith(".md") || safePath.toLowerCase().endsWith(".txt"))
      ? [{ label: "预览文件", action: () => onPreviewFile(safePath) }]
      : []),
  ];

  // 按路径分组
  const groupByFolder = (files: MarkerData[]) => {
    const groups: Record<string, MarkerData[]> = {};
    files.forEach((f) => {
      const rp = f.rel_path || f.path || "";
      const folder = rp.includes("/") ? rp.split("/").slice(0, -1).join("/") : "";
      if (!groups[folder]) groups[folder] = [];
      groups[folder].push(f);
    });
    return groups;
  };

  const renderFile = (f: MarkerData) => {
    const rp = f.rel_path || f.path || "";
    return (
      <div
        key={rp}
        className={`proc-tree-file ${f.status === "failed" ? "failed" : ""}`}
        onContextMenu={(e) => handleContextMenu(e, rp)}
        onDoubleClick={() => {
          if (onPreviewFile && (rp.toLowerCase().endsWith(".md") || rp.toLowerCase().endsWith(".txt"))) {
            onPreviewFile(rp);
          }
        }}
        title="双击预览"
      >
        <span className="proc-dot">{f.status === "failed" ? "✗" : "✓"}</span>
        <span className="mpath">{rp.split("/").pop()}</span>
        {f.kind && <span className="kind kind-{f.kind}">{f.kind}</span>}
      </div>
    );
  };

  return (
    <div className="processed">
      <div className="processed-head">已处理</div>
      {bundle && (
        <div className="bundle-info">
          <div className="bundle-title">📦 Bundle 摘要</div>
          <div className="bundle-stats">
            <span>文件: {bundle.total_files}</span>
            <span>语料: {bundle.total_corpus}</span>
            {bundle.structured > 0 && <span>结构化: {bundle.structured}</span>}
          </div>
          {bundle.output_path && (
            <div className="bundle-path">📁 {bundle.output_path}</div>
          )}
        </div>
      )}
      {processedFiles.length === 0 && failedFiles.length === 0 ? (
        <div className="empty">尚未处理任何文件</div>
      ) : (
        <div className="proc-tree">
          {/* 成功文件 */}
          {processedFiles.length > 0 && (
            <div className="proc-tree-section">
              <div className="proc-tree-folder">
                <span className="ficon">✓</span>
                <span>已处理 ({processedFiles.length})</span>
              </div>
              {Object.entries(groupByFolder(processedFiles)).map(([folder, files]) => (
                <div key={folder} className="proc-tree-files">
                  {folder && <div className="proc-tree-folder-label">{folder}</div>}
                  {files.map(renderFile)}
                </div>
              ))}
            </div>
          )}
          {/* 失败文件 */}
          {failedFiles.length > 0 && (
            <div className="proc-tree-section">
              <div className="proc-tree-folder">
                <span className="ficon">✗</span>
                <span>失败 ({failedFiles.length})</span>
              </div>
              {failedFiles.map(renderFile)}
            </div>
          )}
        </div>
      )}
      <ContextMenu
        visible={menu.visible}
        x={menu.x}
        y={menu.y}
        items={menuItems}
        onClose={() => setMenu({ visible: false, x: 0, y: 0, path: "" })}
      />
    </div>
  );
}
