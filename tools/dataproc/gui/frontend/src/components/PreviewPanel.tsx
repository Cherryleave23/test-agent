import React from "react";

interface Props {
  name: string | null;
  content: string;
  loading: boolean;
  error: string;
}

export default function PreviewPanel({ name, content, loading, error }: Props) {
  if (loading) {
    return (
      <aside className="preview">
        <div className="preview-head">文件预览</div>
        <div className="preview-loading">加载中…</div>
      </aside>
    );
  }
  if (error) {
    return (
      <aside className="preview">
        <div className="preview-head">文件预览</div>
        <div className="preview-error">{error}</div>
      </aside>
    );
  }
  if (!name) {
    return (
      <aside className="preview">
        <div className="preview-head">文件预览</div>
        <div className="preview-empty">点击资料树或已处理列表中的文件进行预览</div>
      </aside>
    );
  }

  const isMd = name.toLowerCase().endsWith(".md");

  return (
    <aside className="preview">
      <div className="preview-head" title={name}>
        <span className="preview-name">{name}</span>
      </div>
      <div className="preview-body">
        {isMd ? (
          <div className="preview-md">
            {content.split("\n").map((line, i) => (
              <div key={i} className="md-line">{line || <br />}</div>
            ))}
          </div>
        ) : (
          <pre className="preview-txt">{content}</pre>
        )}
      </div>
    </aside>
  );
}
