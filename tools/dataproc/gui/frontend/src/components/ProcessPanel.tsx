import React from "react";

interface Props {
  busy: boolean;
  hasSelection: boolean;
  onProcess: (full: boolean) => void;
}

export default function ProcessPanel({ busy, hasSelection, onProcess }: Props) {
  return (
    <div className="process">
      <div className="process-head">处理</div>
      <button
        className="primary"
        disabled={busy}
        onClick={() => onProcess(true)}
        title="处理整个仓库的全部资料"
      >
        全量处理仓库
      </button>
      <button
        disabled={busy || !hasSelection}
        onClick={() => onProcess(false)}
        title="仅处理左侧勾选的文件/文件夹"
      >
        处理选中（{hasSelection ? "已选" : "未选"}）
      </button>
      <p className="hint">
        已处理的文件（内容未变）会自动跳过，避免重复。点击上方按钮开始。
      </p>
    </div>
  );
}
