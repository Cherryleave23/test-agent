import React, { useState } from "react";

interface Repo {
  name: string;
  enterprise_id: string;
  namespace: string;
  disk_path?: string;
  output_dir?: string;
}
interface Props {
  repoList: { repos: Repo[]; current: string | null };
  current: string;
  onOpen: (name: string) => void;
  onCreate: (name: string, ns: string, path?: string, outputDir?: string) => void;
}

export default function RepoBar({ repoList, current, onOpen, onCreate }: Props) {
  const [showModal, setShowModal] = useState(false);
  const [name, setName] = useState("");
  const [ns, setNs] = useState("b");
  const [path, setPath] = useState("");
  const [outputDir, setOutputDir] = useState("");

  const browse = async (setter: (v: string) => void) => {
    const w = window as any;
    if (w.showDirectoryPicker) {
      try {
        const handle = await w.showDirectoryPicker({ mode: "readwrite" });
        setter(handle.name);
        return;
      } catch (e) {
        if ((e as any).name === "AbortError") return;
      }
    }
    const input = document.createElement("input");
    input.type = "file";
    input.setAttribute("webkitdirectory", "");
    input.onchange = () => {
      const files = input.files;
      if (files && files.length > 0) {
        const relPath = (files[0] as any).webkitRelativePath as string;
        const dirName = relPath.split("/")[0];
        setter(dirName);
      }
    };
    input.click();
  };

  const submit = () => {
    if (!name.trim()) return;
    onCreate(name.trim(), ns, path.trim() || undefined, outputDir.trim() || undefined);
    setName("");
    setPath("");
    setOutputDir("");
    setShowModal(false);
  };

  const cancel = () => {
    setName("");
    setPath("");
    setOutputDir("");
    setShowModal(false);
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
      <button onClick={() => setShowModal(true)}>+ 新建仓库</button>
      {currentRepo && (
        <span className="cur-ent" title={currentRepo.disk_path || ""}>
          {currentRepo.enterprise_id}
          {currentRepo.disk_path && (
            <span className="disk-path"> | {currentRepo.disk_path}</span>
          )}
        </span>
      )}

      {/* 新建仓库弹窗 */}
      {showModal && (
        <div className="modal-overlay" onClick={cancel}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">新建仓库</div>
            <div className="modal-row">
              <label className="modal-label">仓库名称</label>
              <input
                className="modal-input"
                placeholder="如：企业A资料库"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </div>
            <div className="modal-row">
              <label className="modal-label">命名空间</label>
              <select
                className="modal-select"
                value={ns}
                onChange={(e) => setNs(e.target.value)}
              >
                <option value="b">企业自有</option>
                <option value="hq">总部共享库</option>
              </select>
            </div>
            <div className="modal-row">
              <label className="modal-label">仓库位置</label>
              <div className="path-picker">
                <input
                  className="modal-input path-input"
                  placeholder="留空=默认位置"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  title="指定仓库在磁盘上的位置，如 D:\资料库\企业A"
                />
                <button onClick={() => browse(setPath)} title="浏览选择文件夹">📁</button>
              </div>
            </div>
            <div className="modal-row">
              <label className="modal-label">输出位置</label>
              <div className="path-picker">
                <input
                  className="modal-input path-input"
                  placeholder="留空=仓库内 .dataproc/bundle"
                  value={outputDir}
                  onChange={(e) => setOutputDir(e.target.value)}
                  title="指定 bundle 产物的输出目录。每个仓库可独立配置。"
                />
                <button onClick={() => browse(setOutputDir)} title="浏览选择文件夹">📁</button>
              </div>
            </div>
            <div className="modal-hint">
              每个仓库拥有独立的输出目录，互不干扰。
            </div>
            <div className="modal-actions">
              <button className="modal-btn modal-cancel" onClick={cancel}>取消</button>
              <button className="modal-btn modal-ok" onClick={submit} disabled={!name.trim()}>创建</button>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
