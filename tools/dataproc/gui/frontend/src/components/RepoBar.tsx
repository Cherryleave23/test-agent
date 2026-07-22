import React, { useState } from "react";

interface Repo {
  name: string;
  enterprise_id: string;
  namespace: string;
  disk_path?: string;
}
interface Props {
  repoList: { repos: Repo[]; current: string | null };
  current: string;
  onOpen: (name: string) => void;
  onCreate: (name: string, ns: string, path?: string) => void;
}

export default function RepoBar({ repoList, current, onOpen, onCreate }: Props) {
  const [showNew, setShowNew] = useState(false);
  const [name, setName] = useState("");
  const [ns, setNs] = useState("b");
  const [path, setPath] = useState("");

  const browse = async () => {
    // 优先使用 File System Access API（Chrome/Edge 86+）
    const w = window as any;
    if (w.showDirectoryPicker) {
      try {
        const handle = await w.showDirectoryPicker({ mode: "readwrite" });
        setPath(handle.name); // 只能拿到目录名，实际路径需后端拼接
        // 在 Web 环境中无法获取完整磁盘路径，提示用户手动确认
        return;
      } catch (e) {
        if ((e as any).name === "AbortError") return; // 用户取消
        // 降级到手动输入
      }
    }
    // 降级：用 <input webkitdirectory> 让用户选择目录
    const input = document.createElement("input");
    input.type = "file";
    input.setAttribute("webkitdirectory", "");
    input.onchange = () => {
      const files = input.files;
      if (files && files.length > 0) {
        // webkitRelativePath 包含目录名路径
        const relPath = (files[0] as any).webkitRelativePath as string;
        const dirName = relPath.split("/")[0];
        setPath(dirName);
      }
    };
    input.click();
  };

  const submit = () => {
    if (!name.trim()) return;
    onCreate(name.trim(), ns, path.trim() || undefined);
    setName("");
    setPath("");
    setShowNew(false);
  };

  const currentRepo = repoList.repos.find((r) => r.name === current);

  return (
    <header className="repobar">
      <div className="brand">数据处理工作台</div>
      <select
        value={current}
        onChange={(e) => onOpen(e.target.value)}
        disabled={!repoList.repos.length}
      >
        <option value="">— 选择仓库 —</option>
        {repoList.repos.map((r) => (
          <option key={r.name} value={r.name}>
            {r.name}
            {r.namespace === "hq" ? "（总部共享库）" : ""}
          </option>
        ))}
      </select>
      <button onClick={() => setShowNew((v) => !v)}>+ 新建仓库</button>
      {showNew && (
        <span className="newrepo">
          <input
            placeholder="仓库名（如 企业A资料库）"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <select value={ns} onChange={(e) => setNs(e.target.value)}>
            <option value="b">企业自有</option>
            <option value="hq">总部共享库</option>
          </select>
          <span className="path-picker">
            <input
              className="path-input"
              placeholder="磁盘路径（留空=默认位置）"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              title="指定仓库在磁盘上的位置，如 D:\资料库\企业A。也可点击右侧按钮浏览选择。"
            />
            <button onClick={browse} title="浏览选择文件夹">📁 浏览</button>
          </span>
          <button onClick={submit}>创建</button>
        </span>
      )}
      {currentRepo && (
        <span className="cur-ent" title={currentRepo.disk_path || ""}>
          {currentRepo.enterprise_id}
          {currentRepo.disk_path && (
            <span className="disk-path"> | {currentRepo.disk_path}</span>
          )}
        </span>
      )}
    </header>
  );
}
