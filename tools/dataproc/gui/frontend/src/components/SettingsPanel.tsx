import React, { useState, useEffect } from "react";
import { api } from "../api";

interface Settings {
  ocr_enabled: boolean;
  run_real_ocr: boolean;
  output_dir: string;
  repos_base: string;
}

interface Props {
  onSettingsChange?: (s: Settings) => void;
}

export default function SettingsPanel({ onSettingsChange }: Props) {
  const [open, setOpen] = useState(false);
  const [showOcrGuide, setShowOcrGuide] = useState(false);
  const [settings, setSettings] = useState<Settings>({
    ocr_enabled: false,
    run_real_ocr: false,
    output_dir: "",
    repos_base: "",
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
          {/* 仓库根目录 */}
          <div className="settings-row">
            <label>仓库默认存储位置</label>
            <input
              className="settings-input"
              placeholder="如 D:\资料库（留空=程序默认位置）"
              value={settings.repos_base}
              onChange={(e) => setSettings({ ...settings, repos_base: e.target.value })}
              onBlur={() => update({ repos_base: settings.repos_base })}
              title="新建仓库（不指定路径时）的默认存储位置。修改后需要刷新页面生效。"
            />
            <p className="settings-hint">未指定磁盘路径的新仓库会创建在此目录下</p>
          </div>

          {/* Bundle 输出目录 */}
          <div className="settings-row">
            <label>Bundle 输出目录</label>
            <input
              className="settings-input"
              placeholder="留空=仓库默认 (.dataproc/bundle)"
              value={settings.output_dir}
              onChange={(e) => setSettings({ ...settings, output_dir: e.target.value })}
              onBlur={() => update({ output_dir: settings.output_dir })}
            />
            <p className="settings-hint">处理后 bundle 的输出位置</p>
          </div>

          <div className="settings-divider" />

          {/* OCR 设置 */}
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
            <button
              className="ocr-guide-btn"
              onClick={() => setShowOcrGuide((v) => !v)}
            >
              {showOcrGuide ? "收起" : "查看"}安装引导
            </button>
          </div>
          {showOcrGuide && (
            <div className="ocr-guide">
              <div className="ocr-guide-title">PaddleOCR 3.x 安装引导</div>
              <ol>
                <li>
                  <strong>安装 PaddlePaddle 3.x（CPU 版）</strong>
                  <pre>pip install paddlepaddle</pre>
                  <p className="settings-hint">GPU 版请参考 PaddlePaddle 官方文档</p>
                </li>
                <li>
                  <strong>安装 PaddleOCR 3.x</strong>
                  <pre>pip install paddleocr</pre>
                  <p className="settings-hint">需 Python 3.9+（表格识别需 3.9+）</p>
                </li>
                <li>
                  <strong>安装表格识别依赖（可选）</strong>
                  <pre>pip install "paddleocr[doc-parser]"</pre>
                  <p className="settings-hint">用于 PP-StructureV3 表格识别，不需要表格识别可跳过</p>
                </li>
                <li>
                  <strong>安装辅助依赖</strong>
                  <pre>pip install pymupdf Pillow</pre>
                  <p className="settings-hint">PDF 扫描件 OCR 需要 PyMuPDF</p>
                </li>
                <li>
                  <strong>验证安装</strong>
                  <pre>python -c "from paddleocr import PaddleOCR; print('OK')"</pre>
                  <p className="settings-hint">首次运行会自动下载模型（约 300MB），请确保网络畅通</p>
                </li>
                <li>
                  <strong>启用 OCR</strong>
                  <p className="settings-hint">勾选上方「启用 OCR」和「运行真实 OCR」，然后处理图片或 PDF 文件即可</p>
                </li>
              </ol>
              <div className="ocr-guide-note">
                注意：OCR 处理需要较高 CPU 资源，大文件可能耗时较长。
                如遇到内存不足，请逐个处理文件而非全量处理。
                Windows 环境已自动配置 engine=paddle_static + run_mode=paddle 以避免兼容性问题。
              </div>
            </div>
          )}

          {saving && <p className="settings-saving">保存中…</p>}
        </div>
      )}
    </div>
  );
}
