import React from "react";

interface Props {
  markers: { path: string; status: string; bundle_ref: string }[];
  bundle: any;
}

const STATUS_LABEL: Record<string, string> = {
  processed: "已处理",
  pending: "待处理",
  failed: "失败",
};

export default function ProcessedPanel({ markers, bundle }: Props) {
  const counts = bundle?.counts || {};
  const cbk = counts.corpus_by_kind || {};

  return (
    <aside className="processed">
      <div className="processed-head">已处理结构</div>
      {bundle ? (
        <div className="bundle-sum">
          <div>
            企业：<b>{bundle.enterprise_id}</b>
          </div>
          <div>
            产品 {counts.products ?? 0} · 语料 {counts.corpus ?? 0} · HQ{" "}
            {counts.hq_products ?? 0}
          </div>
          <div className="kinds">
            {Object.entries(cbk).map(([k, v]) => (
              <span key={k} className={"kind kind-" + k}>
                {k}: {v}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <div className="empty">（尚未生成 bundle）</div>
      )}
      <ul className="markers">
        {markers.map((m) => (
          <li key={m.path} className={"marker " + m.status}>
            <span className="dot" />
            <span className="mpath">{m.path}</span>
            <span className="mstatus">{STATUS_LABEL[m.status] || m.status}</span>
          </li>
        ))}
        {!markers.length && <li className="empty">（无处理记录）</li>}
      </ul>
    </aside>
  );
}
