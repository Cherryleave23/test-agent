import React, { useState, useEffect } from "react";
import { api } from "../api";

interface FieldRow {
  key: string;
  label: string;
  type: string;
  required: boolean;
}
interface SchemaEntry {
  name: string;
  label: string;
  kind: string;
  extends?: string | null;
  keywords: string[];
  fields: FieldRow[];
  builtin: boolean;
}

const KIND_OPTIONS = [
  { value: "milk", label: "奶粉系" },
  { value: "nutrition", label: "营养品系" },
  { value: "flex", label: "通用" },
];

const EMPTY_FIELD: FieldRow = { key: "", label: "", type: "text", required: false };

interface Props {
  onClose: () => void;
}

function keyToFields(schemas: Record<string, any>): SchemaEntry[] {
  return Object.entries(schemas || {}).map(([name, s]) => ({
    name,
    label: s.label || name,
    kind: s.kind || "flex",
    extends: s.extends ?? null,
    keywords: s.keywords || [],
    fields: (s.fields || []).map((f: any) => ({
      key: f.key || "",
      label: f.label || "",
      type: f.type || "text",
      required: !!f.required,
    })),
    builtin: !!s.builtin,
  }));
}

export default function SchemaSettingsPanel({ onClose }: Props) {
  const [entries, setEntries] = useState<SchemaEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api.getSchema()
      .then((r: any) => setEntries(keyToFields(r.schemas)))
      .catch((e: any) => setMsg("加载失败：" + e.message))
      .finally(() => setLoading(false));
  }, []);

  const patchEntry = (idx: number, p: Partial<SchemaEntry>) =>
    setEntries((prev) => prev.map((e, i) => (i === idx ? { ...e, ...p } : e)));

  const patchField = (ei: number, fi: number, p: Partial<FieldRow>) =>
    setEntries((prev) =>
      prev.map((e, i) =>
        i === ei ? { ...e, fields: e.fields.map((f, j) => (j === fi ? { ...f, ...p } : f)) } : e
      )
    );

  const addField = (ei: number) =>
    setEntries((prev) => prev.map((e, i) => (i === ei ? { ...e, fields: [...e.fields, { ...EMPTY_FIELD }] } : e)));

  const removeField = (ei: number, fi: number) =>
    setEntries((prev) =>
      prev.map((e, i) => (i === ei ? { ...e, fields: e.fields.filter((_, j) => j !== fi) } : e))
    );

  const addCategory = () => {
    const base = "custom";
    let name = base;
    let n = 1;
    const taken = new Set(entries.map((e) => e.name));
    while (taken.has(name)) name = `${base}${n++}`;
    setEntries((prev) => [
      ...prev,
      { name, label: "自定义类目", kind: "nutrition", extends: "nutrition", keywords: [], fields: [], builtin: false },
    ]);
  };

  const removeCategory = (idx: number) =>
    setEntries((prev) => prev.filter((_, i) => i !== idx));

  const save = async () => {
    setSaving(true);
    setMsg("");
    // 仅保存自定义类目（内置 milk/nutrition 不可改）
    const payload: Record<string, any> = {};
    for (const e of entries) {
      if (e.builtin) continue;
      payload[e.name] = {
        label: e.label || e.name,
        kind: e.kind,
        extends: e.extends || null,
        keywords: e.keywords,
        fields: e.fields
          .filter((f) => f.key.trim())
          .map((f) => ({ key: f.key.trim(), label: f.label || f.key, type: f.type, required: f.required })),
      };
    }
    try {
      const r: any = await api.updateSchema(payload);
      setEntries(keyToFields(r.schemas));
      setMsg("已保存（自定义类目已写入 conf.yaml）");
    } catch (e: any) {
      setMsg("保存失败：" + e.message);
    }
    setSaving(false);
  };

  return (
    <div className="llm-modal-mask" onClick={onClose}>
      <div className="llm-modal schema-modal" onClick={(e) => e.stopPropagation()}>
        <div className="llm-header">
          <h2>🏷️ 产品数据结构</h2>
          <button className="llm-close" onClick={onClose}>✕</button>
        </div>
        <p className="llm-sub">
          配置各产品类目的结构化字段。奶粉 / 营养品为内置默认；企业可增删自定义类目与字段，
          保存后由「OCR / 爬虫 → 结构化抽取」自动套用。
        </p>

        {loading ? (
          <p className="settings-hint">加载中…</p>
        ) : (
          <div className="schema-list">
            {entries.map((e, ei) => (
              <div className={"schema-card" + (e.builtin ? " builtin" : "")} key={e.name}>
                <div className="schema-card-head">
                  <strong>{e.label}</strong>
                  <span className="schema-tag">{e.builtin ? "内置" : "自定义"}</span>
                  {!e.builtin && (
                    <button className="schema-del-cat" onClick={() => removeCategory(ei)}>删除类目</button>
                  )}
                </div>
                {e.builtin ? (
                  <p className="settings-hint">内置类目（只读）：{e.fields.map((f) => f.label || f.key).join("、")}</p>
                ) : (
                  <>
                    <div className="schema-meta-row">
                      <label>类目名(key)</label>
                      <input className="settings-input" value={e.name} disabled />
                    </div>
                    <div className="schema-meta-row">
                      <label>显示名</label>
                      <input className="settings-input" value={e.label}
                        onChange={(ev) => patchEntry(ei, { label: ev.target.value })} />
                    </div>
                    <div className="schema-meta-row">
                      <label>入库类型</label>
                      <select className="settings-input" value={e.kind}
                        onChange={(ev) => patchEntry(ei, { kind: ev.target.value })}>
                        {KIND_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                    </div>
                    <div className="schema-meta-row">
                      <label>识别关键词(逗号分隔)</label>
                      <input className="settings-input" value={e.keywords.join("、")}
                        onChange={(ev) =>
                          patchEntry(ei, { keywords: ev.target.value.split(/[，,、]/).map((x) => x.trim()).filter(Boolean) })
                        } />
                    </div>
                    <div className="schema-fields">
                      <div className="schema-fields-head">
                        <span>字段</span>
                        <button className="schema-add-field" onClick={() => addField(ei)}>+ 添加字段</button>
                      </div>
                      {e.fields.map((f, fi) => (
                        <div className="schema-field-row" key={fi}>
                          <input className="settings-input sf-key" placeholder="key"
                            value={f.key} onChange={(ev) => patchField(ei, fi, { key: ev.target.value })} />
                          <input className="settings-input sf-label" placeholder="显示名"
                            value={f.label} onChange={(ev) => patchField(ei, fi, { label: ev.target.value })} />
                          <select className="settings-input sf-type" value={f.type}
                            onChange={(ev) => patchField(ei, fi, { type: ev.target.value })}>
                            <option value="text">text</option>
                            <option value="number">number</option>
                          </select>
                          <label className="sf-req">
                            <input type="checkbox" checked={f.required}
                              onChange={(ev) => patchField(ei, fi, { required: ev.target.checked })} /> 必填
                          </label>
                          <button className="schema-del-field" onClick={() => removeField(ei, fi)}>✕</button>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            ))}
            <button className="schema-add-cat" onClick={addCategory}>+ 新增自定义类目</button>
          </div>
        )}

        {msg && <p className="settings-hint schema-msg">{msg}</p>}

        <div className="llm-actions">
          <button className="llm-save-btn" onClick={save} disabled={saving || loading}>
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
