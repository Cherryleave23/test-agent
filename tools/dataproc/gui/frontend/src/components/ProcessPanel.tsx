import React, { useState } from "react";

interface Props {
  busy: boolean;
  hasSelection: boolean;
  outputDir: string;
  onProcess: (full: boolean, force: boolean) => void;
  onClearMarkers: () => void;
}

export default function ProcessPanel({ busy, hasSelection, outputDir, onProcess, onClearMarkers }: Props) {
  const [force, setForce] = useState(false);

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
      {outputDir && (
        <p className="hint">输出目录：{outputDir}</p>
      )}
      <p className="hint">
        已处理的文件（内容未变）会自动跳过。勾选「强制」或点击「清除标记」可重新处理。
      </p>
    </div>
  );
}
