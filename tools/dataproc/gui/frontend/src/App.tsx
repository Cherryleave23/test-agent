import React, { useCallback, useEffect, useState, useRef } from "react";
import { api } from "./api";
import RepoBar from "./components/RepoBar";
import TreePanel from "./components/TreePanel";
import DropZone from "./components/DropZone";
import ProcessPanel from "./components/ProcessPanel";
import LogPanel from "./components/LogPanel";
import ProcessedPanel from "./components/ProcessedPanel";
import PreviewPanel from "./components/PreviewPanel";
import SettingsPanel from "./components/SettingsPanel";
import LLMSettingsPanel from "./components/LLMSettingsPanel";
import SchemaSettingsPanel from "./components/SchemaSettingsPanel";

interface RepoList {
  repos: { name: string; enterprise_id: string; namespace: string; output_dir?: string }[];
  current: string | null;
}
interface TreeData {
  path: string;
  folders: { name: string; path: string }[];
  files: { name: string; path: string; size: number }[];
  top_folders: string[];
}
interface Settings {
  ocr_enabled: boolean;
  run_real_ocr: boolean;
  output_dir: string;
  repos_base: string;
}
interface ProcessStatus {
  status: string;
  total: number;
  processed: number;
  skipped: number;
  current_file: string;
  logs: string[];
  error: string;
  elapsed: number;
}

export default function App() {
  const [repoList, setRepoList] = useState<RepoList>({ repos: [], current: null });
  const [current, setCurrent] = useState<string>("");
  const [currentOutputDir, setCurrentOutputDir] = useState<string>("");
  const [tree, setTree] = useState<TreeData | null>(null);
  const [currentFolder, setCurrentFolder] = useState<string>("");
  const [selFiles, setSelFiles] = useState<Set<string>>(new Set());
  const [selFolders, setSelFolders] = useState<Set<string>>(new Set());
  const [markers, setMarkers] = useState<any[]>([]);
  const [bundle, setBundle] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [procStatus, setProcStatus] = useState<ProcessStatus | null>(null);
  const [settings, setSettings] = useState<Settings>({
    ocr_enabled: false,
    run_real_ocr: false,
    output_dir: "",
    repos_base: "",
  });
  const [llmOpen, setLlmOpen] = useState(false);
  const [schemaOpen, setSchemaOpen] = useState(false);
  // 文件预览状态
  const [previewName, setPreviewName] = useState<string | null>(null);
  const [previewContent, setPreviewContent] = useState<string>("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");

  const pollRef = useRef<number | null>(null);

  const processedPaths = new Set(
    markers.map((m: any) => m.rel_path || m.path || "").filter(Boolean)
  );

  const loadRepos = useCallback(async () => {
    const r = await api.listRepos();
    setRepoList(r);
    if (r.current && !current) {
      setCurrent(r.current);
    }
    return r;
  }, [current]);

  const loadTree = useCallback(async (name: string) => {
    const t = await api.getTreeFull(name);
    setTree(t);
  }, []);

  const loadProcessed = useCallback(async (name: string) => {
    try {
      const p = await api.processed(name);
      setMarkers(p.markers || []);
    } catch { /* ignore */ }
    try {
      const b = await api.bundle(name);
      setBundle(b);
    } catch {
      setBundle(null);
    }
  }, []);

  const updateOutputDir = useCallback((repoName: string) => {
    const repo = repoList.repos.find((r) => r.name === repoName);
    if (repo?.output_dir) {
      setCurrentOutputDir(repo.output_dir);
    } else {
      setCurrentOutputDir(settings.output_dir || "");
    }
  }, [repoList.repos, settings.output_dir]);

  useEffect(() => {
    loadRepos().then((r) => {
      const name = r.current || (r.repos[0] && r.repos[0].name);
      if (name) {
        setCurrent(name);
        loadTree(name);
        loadProcessed(name);
        const repo = r.repos.find((rp: any) => rp.name === name);
        setCurrentOutputDir(repo?.output_dir || "");
      }
    });
  }, []);

  // 处理状态轮询
  useEffect(() => {
    if (!busy) {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      api.processStatus().then(setProcStatus).catch(() => {});
      return;
    }
    const poll = async () => {
      try {
        const s = await api.processStatus();
        setProcStatus(s);
        if (s.status !== "running") {
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        }
      } catch {}
    };
    poll();
    pollRef.current = window.setInterval(poll, 1500);
    return () => {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [busy]);

  const openRepo = async (name: string) => {
    await api.switchRepo(name);
    setCurrent(name);
    setSelFiles(new Set());
    setSelFolders(new Set());
    await loadTree(name);
    await loadProcessed(name);
    await loadRepos();
    updateOutputDir(name);
  };

  const createRepo = async (name: string, ns: string, path?: string, outputDir?: string) => {
    try {
      await api.createRepo(name, ns, path, outputDir);
      await loadRepos();
      await openRepo(name);
      setMsg(`仓库「${name}」已创建`);
    } catch (e: any) {
      setMsg("创建仓库失败：" + e.message);
    }
  };

  const toggleFile = (path: string) =>
    setSelFiles((s) => { const n = new Set(s); n.has(path) ? n.delete(path) : n.add(path); return n; });

  const toggleFolder = (path: string) =>
    setSelFolders((s) => { const n = new Set(s); n.has(path) ? n.delete(path) : n.add(path); return n; });

  const setActiveFolder = (path: string) => setCurrentFolder(path);

  const selectAllInCurrent = () => {
    if (!tree) return;
    setSelFiles((s) => { const n = new Set(s); tree.files.forEach((f) => n.add(f.path)); return n; });
  };

  const onMkdir = async (parentPath: string, folderName: string) => {
    if (!current) return;
    try {
      await api.mkdir(current, parentPath, folderName);
      setMsg(`文件夹「${folderName}」已创建`);
      await loadTree(current);
    } catch (e: any) { setMsg("创建文件夹失败：" + e.message); }
  };

  const onRmdir = async (folderPath: string) => {
    if (!current) return;
    try {
      await api.rmdir(current, folderPath);
      setMsg(`文件夹已删除`);
      await loadTree(current);
    } catch (e: any) { setMsg("删除文件夹失败：" + e.message); }
  };

  const onDeleteFile = async (filePath: string) => {
    if (!current) return;
    try {
      await api.deleteFile(current, filePath);
      setMsg(`文件已删除`);
      await loadTree(current);
      if (previewName === filePath) { setPreviewName(null); setPreviewContent(""); }
    } catch (e: any) { setMsg("删除文件失败：" + e.message); }
  };

  const onPreviewFile = async (path: string) => {
    if (!current) return;
    setPreviewLoading(true);
    setPreviewError("");
    setPreviewName(path);
    try {
      const data = await api.fileContent(current, path);
      setPreviewContent(data.content || "");
    } catch (e: any) {
      setPreviewError(e.message || "预览失败");
      setPreviewContent("");
    } finally {
      setPreviewLoading(false);
    }
  };

  const onOpenExplorer = async (path: string) => {
    if (!current) return;
    try {
      await api.openExplorer(current, path);
    } catch (e: any) { setMsg("打开资源管理器失败：" + e.message); }
  };

  const onMoveItem = async (srcPath: string, dstFolder: string) => {
    if (!current) return;
    try {
      await api.moveItem(current, srcPath, dstFolder);
      setMsg(`已移动 ${srcPath} → ${dstFolder}`);
      await loadTree(current);
      if (previewName === srcPath) setPreviewName(null);
    } catch (e: any) { setMsg("移动失败：" + e.message); }
  };

  const onFiles = async (files: File[]) => {
    if (!current) return;
    setBusy(true);
    setMsg("上传中…");
    try {
      let uploaded = 0;
      for (const f of files) {
        const folder = currentFolder
          ? currentFolder + (f.name.includes("/") ? "/" + f.name.split("/").slice(0, -1).join("/") : "")
          : (f.name.includes("/") ? f.name.split("/").slice(0, -1).join("/") : "");
        await api.upload(current, folder, f);
        uploaded++;
      }
      setMsg(`已上传 ${uploaded} 个文件到 ${currentFolder || "根目录"}`);
      await loadTree(current);
    } catch (e: any) { setMsg("上传失败：" + e.message); }
    finally { setBusy(false); }
  };

  const onOsPaths = async (paths: string[]) => {
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
      await loadTree(current);
    } catch (e: any) { setMsg("系统拖入处理失败：" + e.message); }
    finally { setBusy(false); }
  };

  const doProcess = async (full: boolean, force: boolean) => {
    if (!current) return;
    const selection = full ? null : { files: [...selFiles], folders: [...selFolders] };
    setBusy(true);
    setMsg(full ? "全量处理中…" : "选择性处理中…");
    try {
      const resp = await api.process(current, selection, force, currentOutputDir || "");
      if (resp.status === "started") {
        setMsg(`处理已启动：${resp.total} 个文件（跳过 ${resp.skipped} 个已处理）`);
        const poll = async (): Promise<void> => {
          const s = await api.processStatus();
          if (s.status === "running") { await new Promise((r) => setTimeout(r, 1500)); return poll(); }
        };
        await poll();
        const final = await api.processStatus();
        if (final.status === "error") { setMsg("处理失败：" + final.error); }
        else { setMsg(`处理完成：${final.processed || 0} 个文件，跳过 ${final.skipped || 0} 个`); }
      } else if (resp.status === "done") {
        setMsg(`全部跳过：${resp.skipped} 个文件已处理且内容未变。`);
      } else {
        setMsg(`处理完成：${resp.processed_files?.length || 0} 个文件，跳过 ${resp.skipped || 0} 个`);
      }
      await loadProcessed(current);
    } catch (e: any) { setMsg("处理失败：" + e.message); }
    finally { setBusy(false); }
  };

  const onClearMarkers = async () => {
    if (!current) return;
    try {
      const r = await api.clearMarkers(current);
      setMsg(`已清除 ${r.cleared} 条处理标记`);
      setMarkers([]);
    } catch (e: any) { setMsg("清除标记失败：" + e.message); }
  };

  return (
    <div className="app">
      <RepoBar
        repoList={repoList}
        current={current}
        onOpen={openRepo}
        onCreate={createRepo}
      />
      <div className="msg-bar">
        <span className="msg">{msg}</span>
        <button className="llm-nav-btn" onClick={() => setLlmOpen(true)}>
          🧠 LLM 配置
        </button>
        <button className="llm-nav-btn" onClick={() => setSchemaOpen(true)}>
          🏷️ 产品数据结构
        </button>
        <SettingsPanel onSettingsChange={setSettings} />
      </div>
      {llmOpen && (
        <LLMSettingsPanel onClose={() => setLlmOpen(false)} />
      )}
      {schemaOpen && (
        <SchemaSettingsPanel onClose={() => setSchemaOpen(false)} />
      )}
      <div className="main">
        <TreePanel
          tree={tree}
          currentFolder={currentFolder}
          selFiles={selFiles}
          selFolders={selFolders}
          processedPaths={processedPaths}
          onToggleFile={toggleFile}
          onToggleFolder={toggleFolder}
          onSelectAll={selectAllInCurrent}
          onSetCurrentFolder={setActiveFolder}
          onMkdir={onMkdir}
          onRmdir={onRmdir}
          onDeleteFile={onDeleteFile}
          onPreviewFile={onPreviewFile}
          onOpenExplorer={onOpenExplorer}
          onMoveItem={onMoveItem}
        />
        <ProcessedPanel
          markers={markers}
          bundle={bundle}
          onPreviewFile={onPreviewFile}
          onOpenExplorer={onOpenExplorer}
        />
        <PreviewPanel
          name={previewName}
          content={previewContent}
          loading={previewLoading}
          error={previewError}
        />
      </div>
      <div className="bottom-row">
        <div className="bottom-left">
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
            outputDir={currentOutputDir}
            status={procStatus}
            onProcess={doProcess}
            onClearMarkers={onClearMarkers}
          />
        </div>
        <div className="bottom-right">
          <LogPanel status={procStatus} />
        </div>
      </div>
    </div>
  );
}
