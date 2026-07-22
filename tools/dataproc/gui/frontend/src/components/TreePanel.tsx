import React, { useState } from "react";

interface TreeData {
  path: string;
  folders: { name: string; path: string }[];
  files: { name: string; path: string; size: number }[];
  top_folders: string[];
}

interface Props {
  tree: TreeData | null;
  currentFolder: string;
  selFiles: Set<string>;
  selFolders: Set<string>;
  processedPaths: Set<string>;
  onNavigate: (path: string) => void;
  onToggleFile: (path: string) => void;
  onToggleFolder: (path: string) => void;
  onSelectAll: () => void;
  onMkdir: (parentPath: string, folderName: string) => void;
}

function breadcrumb(path: string): { name: string; path: string }[] {
  const parts = path.split("/").filter(Boolean);
  const out: { name: string; path: string }[] = [];
  let acc = "";
  for (const p of parts) {
    acc = acc ? acc + "/" + p : p;
    out.push({ name: p, path: acc });
  }
  return out;
}

export default function TreePanel({
  tree,
  currentFolder,
  selFiles,
  selFolders,
  processedPaths,
  onNavigate,
  onToggleFile,
  onToggleFolder,
  onSelectAll,
  onMkdir,
}: Props) {
  const [showMkdir, setShowMkdir] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");

  if (!tree) return <aside className="tree">（请先选择仓库）</aside>;

  const crumbs = [{ name: "仓库根", path: "" }, ...breadcrumb(currentFolder)];

  const submitMkdir = () => {
    if (!newFolderName.trim()) return;
    onMkdir(currentFolder, newFolderName.trim());
    setNewFolderName("");
    setShowMkdir(false);
  };

  return (
    <aside className="tree">
      <div className="tree-head">
        <span>资料树</span>
        <div className="tree-actions">
          <button onClick={() => setShowMkdir((v) => !v)} title="在当前文件夹下新建子文件夹">
            + 文件夹
          </button>
          <button onClick={onSelectAll} disabled={!tree.files.length}>
            全选当前
          </button>
        </div>
      </div>
      {showMkdir && (
        <div className="mkdir-bar">
          <input
            placeholder="新文件夹名"
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitMkdir()}
          />
          <button onClick={submitMkdir}>创建</button>
        </div>
      )}
      <nav className="crumbs">
        {crumbs.map((c, i) => (
          <span key={c.path}>
            {i > 0 && " / "}
            <a onClick={() => onNavigate(c.path)}>{c.name}</a>
          </span>
        ))}
      </nav>
      <ul className="folders">
        {tree.folders.map((f) => (
          <li key={f.path} className="folder">
            <input
              type="checkbox"
              checked={selFolders.has(f.path)}
              onChange={() => onToggleFolder(f.path)}
              title="选中整个文件夹（递归处理）"
            />
            <span className="ficon">📁</span>
            <a onClick={() => onNavigate(f.path)}>{f.name}</a>
          </li>
        ))}
        {!tree.folders.length && <li className="empty">（无子文件夹）</li>}
      </ul>
      <ul className="files">
        {tree.files.map((f) => {
          const isProcessed = processedPaths.has(f.path);
          return (
            <li key={f.path} className="file">
              <input
                type="checkbox"
                checked={selFiles.has(f.path)}
                onChange={() => onToggleFile(f.path)}
              />
              <span className="ficon">📄</span>
              <span>{f.name}</span>
              {isProcessed && (
                <span className="processed-dot" title="已处理">●</span>
              )}
              <span className="fsize">{(f.size / 1024).toFixed(1)}KB</span>
            </li>
          );
        })}
        {!tree.files.length && <li className="empty">（无文件）</li>}
      </ul>
    </aside>
  );
}
