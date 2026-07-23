"""Phase B 回归：dataproc 对「静默忽略/延迟处理」给出可见反馈（防资料凭空丢失）。

- 标准总文件夹之外的文件（真人拖错位置）被忽略时，必须 emit WARNING 并记入 manifest.skipped_files
- OCR 未启用时图片内容延迟（空占位 ocr_pending），必须 emit WARNING 并记入 manifest.counts.ocr_pending
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

HERE = "/workspace"
for p in (os.path.join(HERE, "src"), os.path.join(HERE, "tools"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402
from dataproc.build import build_bundle  # noqa: E402


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []
    def emit(self, record):
        self.records.append(record.getMessage())


def _make_repo(repo_dir: Path):
    (repo_dir / ".dataproc").mkdir(parents=True, exist_ok=True)
    (repo_dir / ".dataproc" / "repo.json").write_text(json.dumps({
        "name": "t", "enterprise_id": "ent_sim", "namespace": "b",
        "created_at": "2026-07-23T00:00:00+08:00",
    }, ensure_ascii=False), encoding="utf-8")
    (repo_dir / "产品资料").mkdir(parents=True, exist_ok=True)
    (repo_dir / "产品资料" / "正常产品.md").write_text(
        "---\nname: 正常产品A\nreg_number: 国食注字YP20990001\n---\n# 正常产品A\nok\n",
        encoding="utf-8")
    # 错名文件夹：真人拖错位置
    (repo_dir / "产品资料wrong").mkdir(parents=True, exist_ok=True)
    (repo_dir / "产品资料wrong" / "被误放的文档.md").write_text(
        "# 错名文件夹里的文档\n放错位置了。\n", encoding="utf-8")
    # 图片无 OCR：内容延迟
    (repo_dir / "原料资料").mkdir(parents=True, exist_ok=True)
    (repo_dir / "原料资料" / "原料标签.png").write_text("", encoding="utf-8")


def test_dataproc_feedback_on_skipped_and_ocr_pending():
    tmp = Path(tempfile.mkdtemp(prefix="dc_fb_"))
    repo = tmp / "repo"
    _make_repo(repo)
    bundle = tmp / "bundle"

    handler = _ListHandler()
    logging.getLogger("dataproc.build").addHandler(handler)
    try:
        r = build_bundle(str(repo), str(bundle))
    finally:
        logging.getLogger("dataproc.build").removeHandler(handler)

    manifest = r["manifest"]
    logs = "\n".join(handler.records)

    # 错名文件夹文件被记入 skipped_files
    assert manifest["counts"]["skipped_files"] >= 1, manifest["counts"]
    assert any("产品资料wrong" in s for s in manifest.get("skipped_files", [])), manifest.get("skipped_files")
    # 图片 OCR 延迟被计数
    assert manifest["counts"]["ocr_pending"] >= 1, manifest["counts"]
    # 操作者必须看到可见警告（不再静默）
    assert ("忽略" in logs or "skipped" in logs.lower() or "OCR" in logs), logs
    assert "OCR" in logs, f"应针对 ocr_pending 发出警告: {logs}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
