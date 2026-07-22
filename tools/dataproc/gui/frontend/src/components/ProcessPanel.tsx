import React, { useState } from "react";

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
  status: ProcessStatus | null;
  onProcess: (full: boolean, force: boolean) => void;
  onClearMarkers: () => void;
}

export default function ProcessPanel({ busy, hasSelection, outputDir, status, onProcess, onClearMarkers }: Props) {
  const [force, setForce] = useState(false);

  const pct = status && status.total > 0
    ? Math.round(((status.processed + status.skipped) / status.total) * 100)
    : 0;

  const showProgress = busy || (status && status.status === "running");

  return (
    <div className="process">
      <div className="process-buttons">
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
      </div>
      <label className="force-label" title="忽略已处理标记，重新处理所有文件">
        <input
          type="checkbox"
          checked={force}
          onChange={(e) => setForce(e.target.checked)}
        />
        强制重新处理
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
      {showProgress && status && (
        <div className="progress-bar-container">
          <div className="progress-info">
            <span>
              {status.processed}/{status.total}
              {status.skipped > 0 && `（跳过 ${status.skipped}）`}
            </span>
            <span>{pct}%</span>
            {status.elapsed > 0 && <span className="elapsed">{status.elapsed}s</span>}
          </div>
          <div className="progress-bar-track">
            <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
          </div>
          {status.current_file && (
            <div className="progress-current" title={status.current_file}>
              {status.current_file}
            </div>
          )}
        </div>
      )}

      {outputDir && (
        <p className="hint" title={outputDir}>输出：{outputDir}</p>
      )}
    </div>
  );
}
