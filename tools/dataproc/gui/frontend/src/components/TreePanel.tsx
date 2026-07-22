import React, { useState, useCallback, useRef, useEffect } from "react";
import ContextMenu from "./ContextMenu";

const LS_KEY = "dataproc_tree_expanded";

interface TreeData {
  path: string;
  folders: { name: string; path: string }[];
  files: { name: string; path: string; size: number }[];
  top_folders: string[];
}

interface MenuState {
  visible: boolean;
  x: number;
  y: number;
  path: string;
  isFolder: boolean;
}

interface Props {
  tree: TreeData | null;
  currentFolder: string;
  selFiles: Set<string>;
  selFolders: Set<string>;
  processedPaths: Set<string>;
  onToggleFile: (path: string) => void;
  onToggleFolder: (path: string) => void;
  onSelectAll: () => void;
  onSetCurrentFolder: (path: string) => void;
  onMkdir: (parentPath: string, folderName: string) => void;
  onRmdir: (folderPath: string) => void;
  onDeleteFile?: (filePath: string) => void;
  onPreviewFile?: (path: string) => void;
  onOpenExplorer?: (path: string) => void;
  onMoveItem?: (srcPath: string, dstFolder: string) => void;
}

function formatSize(n: number) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / (1024 * 1024)).toFixed(1) + " MB";
}

/** 构建嵌套树结构（基于完整数据） */
function buildTree(
  folders: { name: string; path: string }[],
  files: { name: string; path: string; size: number }[]
): { children: any[]; files: any[] } {
  const root: any = { name: "", path: "", children: [], files: [] };
  const map: Record<string, any> = { "": root };

  // 先创建所有文件夹节点
  (folders || []).forEach((f) => {
    if (!f || !f.path) return;
    if (!map[f.path]) {
      map[f.path] = { name: f.name, path: f.path, children: [], files: [] };
    }
  });

  // 建立父子关系
  (folders || []).forEach((f) => {
    if (!f || !f.path) return;
    const parts = f.path.split("/");
    const parentPath = parts.slice(0, -1).join("/");
    if (!map[parentPath]) {
      map[parentPath] = { name: "", path: parentPath, children: [], files: [] };
    }
    map[parentPath].children.push(map[f.path]);
  });

  // 挂文件
  (files || []).forEach((f) => {
    if (!f || !f.path) return;
    const parts = f.path.split("/");
    const parentPath = parts.slice(0, -1).join("/");
    if (!map[parentPath]) {
      map[parentPath] = { name: "", path: parentPath, children: [], files: [] };
    }
    map[parentPath].files.push(f);
  });

  return { children: root.children, files: root.files };
}

export default function TreePanel({
  tree,
  currentFolder,
  selFiles,
  selFolders,
  processedPaths,
  onToggleFile,
  onToggleFolder,
  onSelectAll,
  onSetCurrentFolder,
  onMkdir,
  onRmdir,
  onDeleteFile,
  onPreviewFile,
  onOpenExplorer,
  onMoveItem,
}: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    try {
      const saved = localStorage.getItem(LS_KEY);
      return saved ? new Set(JSON.parse(saved)) : new Set();
    } catch {
      return new Set();
    }
  });
  const [menu, setMenu] = useState<MenuState>({
    visible: false,
    x: 0,
    y: 0,
    path: "",
    isFolder: false,
  });
  const [dragSrc, setDragSrc] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<string | null>(null);
  const [mkdirName, setMkdirName] = useState("");
  const [showMkdir, setShowMkdir] = useState(false);

  const treeRef = useRef<HTMLDivElement>(null);

  // 持久化展开状态
  useEffect(() => {
    localStorage.setItem(LS_KEY, JSON.stringify(Array.from(expanded)));
  }, [expanded]);

  const toggleExpand = useCallback((path: string) => {
    setExpanded((s) => {
      const n = new Set(s);
      n.has(path) ? n.delete(path) : n.add(path);
      return n;
    });
  }, []);

  const handleContextMenu = (e: React.MouseEvent, path: string, isFolder: boolean) => {
    e.preventDefault();
    e.stopPropagation();
    setMenu({ visible: true, x: e.clientX, y: e.clientY, path, isFolder });
  };

  const menuItems = [
    ...(menu.isFolder && onOpenExplorer
      ? [{ label: "在资源管理器中打开", action: () => onOpenExplorer(menu.path) }]
      : []),
    ...(onOpenExplorer && !menu.isFolder
      ? [{ label: "在资源管理器中打开", action: () => onOpenExplorer(menu.path) }]
      : []),
    ...(menu.isFolder
      ? [{ label: "删除文件夹", action: () => onRmdir(menu.path), danger: true }]
      : onDeleteFile
      ? [{ label: "删除文件", action: () => onDeleteFile(menu.path), danger: true }]
      : []),
  ];

  const handleDragStart = (e: React.DragEvent, path: string) => {
    setDragSrc(path);
    e.dataTransfer.setData("text/plain", path);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDragOver = (e: React.DragEvent, folderPath: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (dragSrc && dragSrc !== folderPath && !dragSrc.startsWith(folderPath + "/")) {
      setDragOver(folderPath);
    }
  };

  const handleDrop = (e: React.DragEvent, folderPath: string) => {
    e.preventDefault();
    setDragOver(null);
    const src = e.dataTransfer.getData("text/plain") || dragSrc;
    if (src && src !== folderPath && !src.startsWith(folderPath + "/") && onMoveItem) {
      onMoveItem(src, folderPath);
    }
    setDragSrc(null);
  };

  const handleDragLeave = () => {
    setDragOver(null);
  };

  const handleFolderClick = (path: string, hasChildren: boolean, e: React.MouseEvent) => {
    // Ctrl+单击 = 仅切换选中，不展开
    if (e.ctrlKey || e.metaKey) {
      onToggleFolder(path);
      onSetCurrentFolder(path);
      return;
    }
    // 普通单击 = 设置当前文件夹 + 切换选中 + 展开/折叠
    onSetCurrentFolder(path);
    onToggleFolder(path);
    if (hasChildren) toggleExpand(path);
  };

  const handleFileClick = (path: string, e: React.MouseEvent) => {
    if (e.ctrlKey || e.metaKey) {
      onToggleFile(path);
      return;
    }
    onToggleFile(path);
    if (onPreviewFile) onPreviewFile(path);
  };

  const renderFolder = (f: any, depth: number) => {
    const isExpanded = expanded.has(f.path);
    const isDragOver = dragOver === f.path;
    const hasChildren = f.children.length > 0 || f.files.length > 0;
    const isSelected = selFolders.has(f.path);

    return (
      <li key={f.path}>
        <div
          className={`tree-row folder-row ${isDragOver ? "drag-over" : ""}`}
          style={{ paddingLeft: depth * 18 + 6 }}
          draggable
          onDragStart={(e) => handleDragStart(e, f.path)}
          onDragOver={(e) => handleDragOver(e, f.path)}
          onDrop={(e) => handleDrop(e, f.path)}
          onDragLeave={handleDragLeave}
          onContextMenu={(e) => handleContextMenu(e, f.path, true)}
        >
          <span
            className={`tree-arrow ${hasChildren ? "" : "invisible"} ${isExpanded ? "open" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              if (hasChildren) toggleExpand(f.path);
            }}
          />
          <span
            className={`tree-name ${isSelected ? "selected" : ""}`}
            onClick={(e) => handleFolderClick(f.path, hasChildren, e)}
          >
            {f.name}
          </span>
        </div>
        {isExpanded && hasChildren && (
          <ul className="tree-children">
            {f.children.map((child: any) => renderFolder(child, depth + 1))}
            {f.files.map((file: any) => renderFile(file, depth + 1))}
          </ul>
        )}
      </li>
    );
  };

  const renderFile = (f: any, depth: number) => {
    const isProcessed = processedPaths.has(f.path);
    const isSelected = selFiles.has(f.path);
    return (
      <li key={f.path}>
        <div
          className="tree-row file-row"
          style={{ paddingLeft: depth * 18 + 24 }}
          draggable
          onDragStart={(e) => handleDragStart(e, f.path)}
          onContextMenu={(e) => handleContextMenu(e, f.path, false)}
        >
          <span
            className={`tree-name ${isSelected ? "selected" : ""}`}
            onClick={(e) => handleFileClick(f.path, e)}
            title="单击预览"
          >
            {f.name}
            {isProcessed && <span className="processed-dot" title="已处理">●</span>}
          </span>
          <span className="tree-size">{formatSize(f.size)}</span>
        </div>
      </li>
    );
  };

  const treeData = tree ? buildTree(tree.folders, tree.files) : { children: [], files: [] };

  return (
    <div className="tree" ref={treeRef}>
      <div className="tree-head">
        <span>资料树</span>
        <div className="tree-actions">
          <button onClick={onSelectAll}>全选</button>
          <button onClick={() => setShowMkdir(true)}>新建文件夹</button>
        </div>
      </div>
      {showMkdir && (
        <div className="mkdir-bar">
          <input
            placeholder="文件夹名"
            value={mkdirName}
            onChange={(e) => setMkdirName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && mkdirName.trim()) {
                onMkdir(currentFolder, mkdirName.trim());
                setMkdirName("");
                setShowMkdir(false);
              }
            }}
            autoFocus
          />
          <button
            onClick={() => {
              if (mkdirName.trim()) {
                onMkdir(currentFolder, mkdirName.trim());
                setMkdirName("");
                setShowMkdir(false);
              }
            }}
          >
            创建
          </button>
          <button onClick={() => { setShowMkdir(false); setMkdirName(""); }}>取消</button>
        </div>
      )}
      {tree ? (
        <ul className="tree-list">
          {treeData.children.map((f: any) => renderFolder(f, 0))}
          {treeData.files.map((f: any) => renderFile(f, 0))}
        </ul>
      ) : (
        <div className="empty">暂无仓库</div>
      )}
      <ContextMenu
        visible={menu.visible}
        x={menu.x}
        y={menu.y}
        items={menuItems}
        onClose={() => setMenu({ visible: false, x: 0, y: 0, path: "", isFolder: false })}
      />
    </div>
  );
}
