import React, { useEffect, useRef } from "react";

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
  status: ProcessStatus | null;
}

export default function LogPanel({ status }: Props) {
  const logEndRef = useRef<HTMLDivElement>(null);

  // 自动滚动到最新日志
  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollTop = logEndRef.current.scrollHeight;
    }
  }, [status?.logs]);

  const showLogs = status && status.logs && status.logs.length > 0;

  return (
    <div className="log-panel">
      <div className="process-head">日志</div>
      {showLogs ? (
        <div className="process-logs log-panel-logs" ref={logEndRef}>
          {status!.logs.map((log, i) => (
            <div
              key={i}
              className={`log-line ${log.startsWith("✗") ? "log-error" : log.startsWith("✓") ? "log-ok" : ""}`}
            >
              {log}
            </div>
          ))}
        </div>
      ) : (
        <div className="empty">暂无日志</div>
      )}
      {status && status.status === "done" && (
        <div className="process-done">处理完成</div>
      )}
      {status && status.status === "error" && (
        <div className="process-error">处理失败: {status.error}</div>
      )}
    </div>
  );
}
