import React, { useState, useEffect } from "react";
import { api } from "../api";

// LLM provider 类型（与后端 config.LLM_KINDS 对齐）
type LLMKind = "none" | "lmstudio" | "cloud" | "ollama";

interface LLMSettings {
  kind: LLMKind;
  base_url: string;
  model: string;
  api_key: string;
  temperature: number;
  max_tokens: number;
}

const KIND_OPTIONS: { value: LLMKind; label: string; hint: string }[] = [
  { value: "none", label: "不启用", hint: "规则抽取（无 LLM 补全）" },
  { value: "lmstudio", label: "本地 LMStudio", hint: "OpenAI 兼容，默认 http://localhost:1234/v1" },
  { value: "cloud", label: "云端 LLM", hint: "OpenAI 兼容端点（如 DeepSeek / 通义 / 官方）" },
  { value: "ollama", label: "本地 Ollama", hint: "默认 http://localhost:11434" },
];

const PRESET_BASE_URL: Record<LLMKind, string> = {
  none: "",
  lmstudio: "http://localhost:1234/v1",
  cloud: "",
  ollama: "http://localhost:11434",
};

const EMPTY: LLMSettings = {
  kind: "none",
  base_url: "",
  model: "",
  api_key: "",
  temperature: 0.2,
  max_tokens: 1024,
};

interface Props {
  onClose: () => void;
  onSaved?: (cfg: LLMSettings) => void;
}

export default function LLMSettingsPanel({ onClose, onSaved }: Props) {
  const [s, setS] = useState<LLMSettings>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<{
    ok: boolean; latency_ms: number; models: string[]; endpoint: string; error: string | null;
  } | null>(null);

  useEffect(() => {
    api.getLLMSettings().then((r: LLMSettings) => setS(r)).catch(() => {});
  }, []);

  const onKindChange = (kind: LLMKind) => {
    // 切换类型时自动填充预设 base_url（仅当用户未手动改过时更顺手：此处直接填预设）
    setS((prev) => ({
      ...prev,
      kind,
      base_url: PRESET_BASE_URL[kind] || prev.base_url,
    }));
  };

  const patch = (p: Partial<LLMSettings>) => setS((prev) => ({ ...prev, ...p }));

  const save = async () => {
    setSaving(true);
    try {
      // 直接发送（含 api_key="<set>" 脱敏值）；后端检测到 "<set>" 会保留原值
      const saved = await api.updateLLMSettings(s);
      // 回落脱敏后的 api_key 展示
      setS((prev) => ({ ...prev, api_key: saved.api_key === "<set>" ? prev.api_key : saved.api_key }));
      onSaved?.(saved);
    } catch (e: any) {
      alert("保存失败：" + e.message);
    }
    setSaving(false);
  };

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      const r = await api.testLLM(s);
      setTest(r);
    } catch (e: any) {
      setTest({ ok: false, latency_ms: 0, models: [], endpoint: "", error: e.message });
    }
    setTesting(false);
  };

  const needsKey = s.kind === "cloud";
  const showBaseUrl = s.kind === "lmstudio" || s.kind === "cloud" || s.kind === "ollama";

  return (
    <div className="llm-modal-mask" onClick={onClose}>
      <div className="llm-modal" onClick={(e) => e.stopPropagation()}>
        <div className="llm-header">
          <h2>LLM 配置</h2>
          <button className="llm-close" onClick={onClose}>✕</button>
        </div>
        <p className="llm-sub">
          配置用于「OCR 文字 → 结构化字段补全」的 LLM。支持本地 LMStudio（OpenAI 兼容）与云端 LLM。
        </p>

        {/* Provider 类型 */}
        <div className="settings-row">
          <label>Provider 类型</label>
          <div className="llm-kind-grid">
            {KIND_OPTIONS.map((o) => (
              <button
                key={o.value}
                className={"llm-kind-btn" + (s.kind === o.value ? " active" : "")}
                onClick={() => onKindChange(o.value)}
                title={o.hint}
              >
                {o.label}
              </button>
            ))}
          </div>
          <p className="settings-hint">
            {KIND_OPTIONS.find((o) => o.value === s.kind)?.hint}
          </p>
        </div>

        {s.kind !== "none" && (
          <>
            {showBaseUrl && (
              <div className="settings-row">
                <label>Base URL</label>
                <input
                  className="settings-input"
                  placeholder={PRESET_BASE_URL[s.kind] || "如 https://api.openai.com/v1"}
                  value={s.base_url}
                  onChange={(e) => patch({ base_url: e.target.value })}
                />
                <p className="settings-hint">
                  本地 LMStudio 默认 {PRESET_BASE_URL.lmstudio}；Ollama 默认 {PRESET_BASE_URL.ollama}
                </p>
              </div>
            )}

            <div className="settings-row">
              <label>模型名称</label>
              <input
                className="settings-input"
                placeholder="如 qwen2.5-7b-instruct / deepseek-chat"
                value={s.model}
                onChange={(e) => patch({ model: e.target.value })}
              />
              <p className="settings-hint">指定使用的本地或云端模型</p>
            </div>

            {needsKey && (
              <div className="settings-row">
                <label>API Key</label>
                <input
                  className="settings-input"
                  type="password"
                  placeholder="云端 LLM 的 API 密钥"
                  value={s.api_key}
                  onChange={(e) => patch({ api_key: e.target.value })}
                />
                <p className="settings-hint">仅云端需要；本地模型可留空</p>
              </div>
            )}

            <div className="settings-row llm-two-col">
              <div>
                <label>温度 (temperature)</label>
                <input
                  className="settings-input"
                  type="number" step="0.1" min="0" max="2"
                  value={s.temperature}
                  onChange={(e) => patch({ temperature: parseFloat(e.target.value) || 0.2 })}
                />
              </div>
              <div>
                <label>最大 Token (max_tokens)</label>
                <input
                  className="settings-input"
                  type="number" step="1" min="1"
                  value={s.max_tokens}
                  onChange={(e) => patch({ max_tokens: parseInt(e.target.value) || 1024 })}
                />
              </div>
            </div>

            <div className="llm-actions">
              <button className="llm-test-btn" onClick={runTest} disabled={testing || !s.model}>
                {testing ? "测试中…" : "测试连接"}
              </button>
              <button className="llm-save-btn" onClick={save} disabled={saving}>
                {saving ? "保存中…" : "保存"}
              </button>
            </div>

            {test && (
              <div className={"llm-test-result " + (test.ok ? "ok" : "fail")}>
                {test.ok ? (
                  <>
                    <strong>✓ 连接成功</strong>（{test.latency_ms} ms）
                    {test.models.length > 0 && (
                      <div className="llm-models">可用模型：{test.models.slice(0, 12).join("、")}</div>
                    )}
                  </>
                ) : (
                  <>
                    <strong>✗ 连接失败</strong>
                    <div className="llm-err">{test.error}</div>
                  </>
                )}
              </div>
            )}
          </>
        )}

        {s.kind === "none" && (
          <p className="settings-hint">未启用 LLM：抽取将仅使用规则解析（brand/name 等字段可能为空）。</p>
        )}
      </div>
    </div>
  );
}
