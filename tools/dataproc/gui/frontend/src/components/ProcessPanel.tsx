import React, { useState, useEffect, useRef } from "react";
import { api } from "../api";

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

interface Props {
  busy: boolean;
  hasSelection: boolean;
  outputDir: string;
  onProcess: (full: boolean, force: boolean) => void;
  onClearMarkers: () => void;
}

export default function ProcessPanel({ busy, hasSelection, outputDir, onProcess, onClearMarkers }: Props) {
  const [force, setForce] = useState(false);
  const [status, setStatus] = useState<ProcessStatus | null>(null);
  const pollRef = useRef<number | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  // 轮询处理进度
  useEffect(() => {
    if (!busy) {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      // 最后拉一次状态
      api.processStatus().then(setStatus).catch(() => {});
      return;
    }

    // 每 1.5 秒轮询
    const poll = async () => {
      try {
        const s = await api.processStatus();
        setStatus(s);
        if (s.status !== "running") {
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
        }
      } catch {}
    };
    poll();
    pollRef.current = window.setInterval(poll, 1500);

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [busy]);

  // 自动滚动到最新日志
  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollTop = logEndRef.current.scrollHeight;
    }
  }, [status?.logs]);

  const pct = status && status.total > 0
    ? Math.round(((status.processed + status.skipped) / status.total) * 100)
    : 0;

  const showProgress = busy || (status && status.status === "running");
  const showLogs = status && status.logs && status.logs.length > 0;

  return (
    <div className="process">
      <div className="process-head">处理</div>
      <button
        className="primary"
        disabled={busy}
        onClick={() => onProcess(true, force)}
        title="处理整个仓库的全部资料"
      >
        全量处理仓库
      </button>
      <button
        disabled={busy || !hasSelection}
        onClick={() => onProcess(false, force)}
        title="仅处理左侧勾选的文件/文件夹"
      >
        处理选中（{hasSelection ? "已选" : "未选"}）
      </button>
      <label className="force-label" title="忽略已处理标记，重新处理所有文件">
        <input
          type="checkbox"
          checked={force}
          onChange={(e) => setForce(e.target.checked)}
        />
        强制重新处理（忽略已处理标记）
      </label>
      <button
        className="clear-btn"
        disabled={busy}
        onClick={onClearMarkers}
        title="清除所有处理标记，使文件可被重新处理"
      >
        清除处理标记
      </button>

      {/* 进度条 */}
      {showProgress && (
        <div className="progress-bar-container">
          <div className="progress-info">
            <span>
              {status!.processed}/{status!.total} 已处理
              {status!.skipped > 0 && `（跳过 ${status!.skipped}）`}
            </span>
            <span>{pct}%</span>
            {status!.elapsed > 0 && <span className="elapsed">{status!.elapsed}s</span>}
          </div>
          <div className="progress-bar-track">
            <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
          </div>
          {status!.current_file && (
            <div className="progress-current" title={status!.current_file}>
              正在处理: {status!.current_file}
            </div>
          )}
        </div>
      )}

      {/* 实时日志 */}
      {showLogs && (
        <div className="process-logs" ref={logEndRef}>
          {status!.logs.map((log, i) => (
            <div key={i} className={`log-line ${log.startsWith("✗") ? "log-error" : log.startsWith("✓") ? "log-ok" : ""}`}>
              {log}
            </div>
          ))}
        </div>
      )}

      {/* 完成状态 */}
      {!busy && status && status.status === "done" && (
        <div className="process-done">处理完成</div>
      )}
      {!busy && status && status.status === "error" && (
        <div className="process-error">处理失败: {status.error}</div>
      )}

      {outputDir && (
        <p className="hint">输出目录：{outputDir}</p>
      )}
      <p className="hint">
        已处理的文件（内容未变）会自动跳过。勾选「强制」或点击「清除标记」可重新处理。
      </p>
    </div>
  );
}
