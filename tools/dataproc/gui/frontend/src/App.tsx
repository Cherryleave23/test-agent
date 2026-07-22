import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import RepoBar from "./components/RepoBar";
import TreePanel from "./components/TreePanel";
import DropZone from "./components/DropZone";
import ProcessPanel from "./components/ProcessPanel";
import ProcessedPanel from "./components/ProcessedPanel";

interface RepoList {
  repos: { name: string; enterprise_id: string; namespace: string }[];
  current: string | null;
}
interface TreeData {
  path: string;
  folders: { name: string; path: string }[];
  files: { name: string; path: string; size: number }[];
  top_folders: string[];
}

export default function App() {
  const [repoList, setRepoList] = useState<RepoList>({ repos: [], current: null });
  const [current, setCurrent] = useState<string>("");
  const [tree, setTree] = useState<TreeData | null>(null);
  const [currentFolder, setCurrentFolder] = useState<string>("");
  const [selFiles, setSelFiles] = useState<Set<string>>(new Set());
  const [selFolders, setSelFolders] = useState<Set<string>>(new Set());
  const [markers, setMarkers] = useState<any[]>([]);
  const [bundle, setBundle] = useState<any>(null);
  // 已处理文件的相对路径集合（供 TreePanel 显示绿点）
  const processedPaths = new Set(
    markers.map((m: any) => m.rel_path || m.path || "").filter(Boolean)
  );
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const loadRepos = useCallback(async () => {
    const r = await api.listRepos();
    setRepoList(r);
    if (r.current && !current) {
      setCurrent(r.current);
    }
    return r;
  }, [current]);

  const loadTree = useCallback(async (name: string, path: string) => {
    const t = await api.getTree(name, path);
    setTree(t);
    setCurrentFolder(path);
  }, []);

  const loadProcessed = useCallback(async (name: string) => {
    try {
      const p = await api.processed(name);
      setMarkers(p.markers || []);
    } catch {
      /* ignore */
    }
    try {
      const b = await api.bundle(name);
      setBundle(b);
    } catch {
      setBundle(null);
    }
  }, []);

  useEffect(() => {
    loadRepos().then((r) => {
      const name = r.current || (r.repos[0] && r.repos[0].name);
      if (name) {
        setCurrent(name);
        loadTree(name, "");
        loadProcessed(name);
      }
    });
  }, []);

  const openRepo = async (name: string) => {
    await api.switchRepo(name);
    setCurrent(name);
    setSelFiles(new Set());
    setSelFolders(new Set());
    await loadTree(name, "");
    await loadProcessed(name);
    await loadRepos();
  };

  const createRepo = async (name: string, ns: string) => {
    await api.createRepo(name, ns);
    await loadRepos();
    await openRepo(name);
  };

  const navigate = (path: string) => loadTree(current, path);

  const toggleFile = (path: string) =>
    setSelFiles((s) => {
      const n = new Set(s);
      n.has(path) ? n.delete(path) : n.add(path);
      return n;
    });

  const toggleFolder = (path: string) =>
    setSelFolders((s) => {
      const n = new Set(s);
      n.has(path) ? n.delete(path) : n.add(path);
      return n;
    });

  const selectAllInCurrent = () => {
    if (!tree) return;
    setSelFiles((s) => {
      const n = new Set(s);
      tree.files.forEach((f) => n.add(f.path));
      return n;
    });
  };

  const onFiles = async (files: File[]) => {
    if (!current) return;
    setBusy(true);
    setMsg("上传中…");
    try {
      for (const f of files) {
        await api.upload(current, currentFolder, f);
      }
      setMsg(`已上传 ${files.length} 个文件到 ${currentFolder || "根目录"}`);
      await loadTree(current, currentFolder);
    } catch (e: any) {
      setMsg("上传失败：" + e.message);
    } finally {
      setBusy(false);
    }
  };

  const onOsPaths = async (paths: string[]) => {
    // Tauri OS 拖入：读取文件内容后走同一上传通道
    if (!current) return;
    setBusy(true);
    try {
      const mod = await import("@tauri-apps/plugin-fs");
      for (const p of paths) {
        const data = await mod.readFile(p);
        const blob = new Blob([new Uint8Array(data as any)]);
        const file = new File([blob], p.split(/[\\/]/).pop() || "file");
        await api.upload(current, currentFolder, file);
      }
      setMsg(`已从系统拖入 ${paths.length} 个文件`);
      await loadTree(current, currentFolder);
    } catch (e: any) {
      setMsg("系统拖入处理失败：" + e.message);
    } finally {
      setBusy(false);
    }
  };

  const doProcess = async (full: boolean) => {
    if (!current) return;
    const selection = full
      ? null
      : { files: [...selFiles], folders: [...selFolders] };
    setBusy(true);
    setMsg(full ? "全量处理中…" : "选择性处理中…");
    try {
      const sum = await api.process(current, selection);
      setMsg(
        `处理完成：${sum.processed_files?.length || 0} 个文件，` +
          `跳过 ${sum.skipped || 0} 个已处理`
      );
      await loadProcessed(current);
    } catch (e: any) {
      setMsg("处理失败：" + e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app">
      <RepoBar
        repoList={repoList}
        current={current}
        onOpen={openRepo}
        onCreate={createRepo}
      />
      <div className="msg">{msg}</div>
      <div className="main">
        <TreePanel
          tree={tree}
          currentFolder={currentFolder}
          selFiles={selFiles}
          selFolders={selFolders}
          processedPaths={processedPaths}
          onNavigate={navigate}
          onToggleFile={toggleFile}
          onToggleFolder={toggleFolder}
          onSelectAll={selectAllInCurrent}
        />
        <section className="center">
          <DropZone
            current={current}
            currentFolder={currentFolder}
            busy={busy}
            onFiles={onFiles}
            onOsPaths={onOsPaths}
          />
          <ProcessPanel
            busy={busy}
            hasSelection={selFiles.size > 0 || selFolders.size > 0}
            onProcess={doProcess}
          />
        </section>
        <ProcessedPanel markers={markers} bundle={bundle} />
      </div>
    </div>
  );
}
