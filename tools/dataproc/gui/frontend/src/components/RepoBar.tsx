import React, { useState } from "react";

interface Repo {
  name: string;
  enterprise_id: string;
  namespace: string;
}
interface Props {
  repoList: { repos: Repo[]; current: string | null };
  current: string;
  onOpen: (name: string) => void;
  onCreate: (name: string, ns: string) => void;
}

export default function RepoBar({ repoList, current, onOpen, onCreate }: Props) {
  const [showNew, setShowNew] = useState(false);
  const [name, setName] = useState("");
  const [ns, setNs] = useState("b");

  const submit = () => {
    if (!name.trim()) return;
    onCreate(name.trim(), ns);
    setName("");
    setShowNew(false);
  };

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
          <button onClick={submit}>创建</button>
        </span>
      )}
      {current && (
        <span className="cur-ent">
          企业ID：{repoList.repos.find((r) => r.name === current)?.enterprise_id}
        </span>
      )}
    </header>
  );
}
