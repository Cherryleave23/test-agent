import React, { useState, useEffect } from "react";
import { api } from "../api";

interface Settings {
  ocr_enabled: boolean;
  run_real_ocr: boolean;
  output_dir: string;
}

interface Props {
  onSettingsChange?: (s: Settings) => void;
}

export default function SettingsPanel({ onSettingsChange }: Props) {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<Settings>({
    ocr_enabled: false,
    run_real_ocr: false,
    output_dir: "",
  });
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const s = await api.getSettings();
      setSettings(s);
      onSettingsChange?.(s);
    } catch { /* ignore */ }
  };

  useEffect(() => { load(); }, []);

  const update = async (patch: Partial<Settings>) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    setSaving(true);
    try {
      const saved = await api.updateSettings(patch);
      setSettings(saved);
      onSettingsChange?.(saved);
    } catch { /* ignore */ }
    setSaving(false);
  };

  return (
    <div className="settings-panel">
      <button className="settings-btn" onClick={() => setOpen((v) => !v)}>
        ⚙ 设置
      </button>
      {open && (
        <div className="settings-dropdown">
          <div className="settings-row">
            <label>
              <input
                type="checkbox"
                checked={settings.ocr_enabled}
                onChange={(e) => update({ ocr_enabled: e.target.checked })}
              />
              启用 OCR
            </label>
            <p className="settings-hint">开启后图片/PDF扫描件将尝试文字识别</p>
          </div>
          <div className="settings-row">
            <label>
              <input
                type="checkbox"
                checked={settings.run_real_ocr}
                disabled={!settings.ocr_enabled}
                onChange={(e) => update({ run_real_ocr: e.target.checked })}
              />
              运行真实 OCR（需安装 PaddleOCR）
            </label>
            <p className="settings-hint">关闭则仅标记 ocr_pending，不实际识别</p>
          </div>
          <div className="settings-row">
            <label>Bundle 输出目录</label>
            <input
              className="settings-input"
              placeholder="留空=仓库默认 (.dataproc/bundle)"
              value={settings.output_dir}
              onChange={(e) => update({ output_dir: e.target.value })}
              onBlur={() => update({ output_dir: settings.output_dir })}
            />
            <p className="settings-hint">处理后 bundle 的输出位置</p>
          </div>
          {saving && <p className="settings-saving">保存中…</p>}
        </div>
      )}
    </div>
  );
}
