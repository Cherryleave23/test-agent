import React, { useState } from "react";

interface Marker {
  rel_path?: string;
  path?: string;
  chash?: string;
  processed_at?: number;
  source_type?: string;
}

interface Props {
  markers: Marker[];
  bundle: any;
}

/** 从 markers 列表构建树形结构 */
interface TreeNode {
  name: string;
  path: string;
  children: Map<string, TreeNode>;
  files: Marker[];
  isFolder: boolean;
}

function buildTree(markers: Marker[]): TreeNode | null {
  if (!markers.length) return null;
  const root: TreeNode = {
    name: "仓库根",
    path: "",
    children: new Map(),
    files: [],
    isFolder: true,
  };

  for (const m of markers) {
    const p = m.rel_path || m.path || "";
    if (!p) continue;
    const parts = p.split("/").filter(Boolean);
    let cur = root;
    // 最后一部分是文件名
    for (let i = 0; i < parts.length - 1; i++) {
      const part = parts[i];
      const childPath = cur.path ? cur.path + "/" + part : part;
      if (!cur.children.has(part)) {
        cur.children.set(part, {
          name: part,
          path: childPath,
          children: new Map(),
          files: [],
          isFolder: true,
        });
      }
      cur = cur.children.get(part)!;
    }
    const fileName = parts[parts.length - 1];
    if (fileName) {
      cur.files.push(m);
    }
  }
  return root;
}

function TreeItem({ node, depth }: { node: TreeNode; depth: number }) {
  const [open, setOpen] = useState(depth < 2); // 默认展开前2层

  const subFolders = Array.from(node.children.values()).sort((a, b) =>
    a.name.localeCompare(b.name, "zh")
  );

  return (
    <div className="proc-tree-node" style={{ marginLeft: depth * 16 }}>
      {subFolders.length > 0 && (
        <div className="proc-tree-children">
          {subFolders.map((child) => (
            <div key={child.path}>
              <div
                className="proc-tree-folder"
                onClick={() => setOpen(!open)}
                style={{ cursor: "pointer" }}
              >
                <span className="ficon">{open ? "📂" : "📁"}</span>
                <span>{child.name}</span>
                <span className="proc-count">
                  ({child.files.length + countAllFiles(child)})
                </span>
              </div>
              {open && <TreeItem node={child} depth={depth + 1} />}
            </div>
          ))}
        </div>
      )}
      {node.files.length > 0 && (
        <div className="proc-tree-files">
          {node.files.map((f, i) => {
            const p = f.rel_path || f.path || "";
            const name = p.split("/").pop() || p;
            return (
              <div key={i} className="proc-tree-file">
                <span className="ficon">📄</span>
                <span>{name}</span>
                <span className="proc-dot">✓</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function countAllFiles(node: TreeNode): number {
  let count = node.files.length;
  for (const child of node.children.values()) {
    count += countAllFiles(child);
  }
  return count;
}

export default function ProcessedPanel({ markers, bundle }: Props) {
  const tree = buildTree(markers);

  return (
    <aside className="processed">
      <div className="proc-head">
        <span>已处理 ({markers.length})</span>
      </div>

      {bundle && (
        <div className="bundle-info">
          <div className="bundle-title">最新 Bundle</div>
          <div className="bundle-stats">
            <span>产品: {bundle.counts?.products || 0}</span>
            <span>语料: {bundle.counts?.corpus || 0}</span>
            <span>原料: {bundle.counts?.raw || 0}</span>
          </div>
          {bundle.bundle_dir && (
            <div className="bundle-path">{bundle.bundle_dir}</div>
          )}
        </div>
      )}

      {tree ? (
        <div className="proc-tree">
          <TreeItem node={tree} depth={0} />
        </div>
      ) : (
        <div className="empty">（尚无已处理文件）</div>
      )}
    </aside>
  );
}
