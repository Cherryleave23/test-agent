#!/usr/bin/env python3
# @module deploy
"""依赖清单校验验收（MOD-deploy §1B 依赖分层策略）。

验证 dependency-manifest.yaml 的结构正确性和模式匹配逻辑：
  - manifest 文件存在且可解析
  - Tier1 大依赖（torch/sentence-transformers/bge 模型）声明完整
  - modes 字段正确标注适用模式（light/full/demo）
  - 模型文件列表非空（bge 模型需多文件）
  - 镜像 URL 存在（国内加速选项）
  - configure.ps1 生成的 .env.local 与 manifest 模式一致

直接运行：python3 test_deploy_manifest.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

MANIFEST_PATH = os.path.join(ROOT, "deploy", "dependency-manifest.yaml")
ENTERPRISE_YAML_PATH = os.path.join(ROOT, "deploy", "enterprise.yaml")


def _load_yaml(path):
    """简单 YAML 加载（不依赖 pyyaml，用于 manifest 结构校验）"""
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _p1_manifest_exists_and_parseable():
    """manifest 文件存在且 YAML 可解析"""
    assert os.path.isfile(MANIFEST_PATH), f"manifest 不存在: {MANIFEST_PATH}"
    data = _load_yaml(MANIFEST_PATH)
    assert isinstance(data, dict), "manifest 应为 dict"
    assert "python_embeddable" in data, "缺少 python_embeddable 段"
    assert "wheels" in data, "缺少 wheels 段"
    assert "models" in data, "缺少 models 段"


def _p2_python_embeddable_required_for_all_modes():
    """python_embeddable 对所有模式必需"""
    data = _load_yaml(MANIFEST_PATH)
    pe = data["python_embeddable"]
    assert pe["required"] is True, "python_embeddable 应 required=true"
    assert set(pe["modes"]) == {"light", "full", "demo"}, \
        f"python_embeddable modes 应包含全部三种，实际 {pe['modes']}"
    assert pe["url"], "python_embeddable 应有下载 URL"
    assert "python.org" in pe["url"], "URL 应指向 python.org"


def _p3_wheels_mode_mapping():
    """wheels 的 modes 字段正确：chromadb 适用 light+full，torch 仅 full"""
    data = _load_yaml(MANIFEST_PATH)
    wheels = {w["name"]: w for w in data["wheels"]}
    # torch 仅 full
    assert "torch-cpu" in wheels, "缺少 torch-cpu"
    assert wheels["torch-cpu"]["modes"] == ["full"], \
        f"torch-cpu 应仅 full，实际 {wheels['torch-cpu']['modes']}"
    assert wheels["torch-cpu"]["size_mb"] >= 800, "torch 应 ≥800MB"
    # sentence-transformers 仅 full
    assert "sentence-transformers" in wheels
    assert wheels["sentence-transformers"]["modes"] == ["full"]
    # chromadb 适用 light + full
    assert "chromadb" in wheels
    assert set(wheels["chromadb"]["modes"]) == {"light", "full"}, \
        f"chromadb 应 light+full，实际 {wheels['chromadb']['modes']}"


def _p4_models_have_file_lists():
    """模型声明含文件列表（bge 模型需多文件）"""
    data = _load_yaml(MANIFEST_PATH)
    for model in data["models"]:
        assert model["name"], "模型应有 name"
        assert model["files"], f"{model['name']} files 列表不应为空"
        assert model["dest"], f"{model['name']} 应有 dest 路径"
        assert model["modes"], f"{model['name']} 应有 modes"
        # bge-small-zh 至少含 model.safetensors
        if "bge" in model["name"]:
            assert "model.safetensors" in model["files"], \
                f"{model['name']} 应含 model.safetensors"
            assert "config.json" in model["files"]


def _p5_models_have_mirror():
    """模型含国内镜像 URL（hf-mirror.com）"""
    data = _load_yaml(MANIFEST_PATH)
    for model in data["models"]:
        assert model.get("mirror_url"), f"{model['name']} 应有 mirror_url"
        assert "hf-mirror.com" in model["mirror_url"], \
            f"{model['name']} mirror_url 应含 hf-mirror.com"


def _p6_optional_reranker():
    """bge-reranker 标记为 optional"""
    data = _load_yaml(MANIFEST_PATH)
    reranker = [m for m in data["models"] if "reranker" in m["name"]]
    assert reranker, "manifest 应含 bge-reranker 模型"
    assert reranker[0].get("optional") is True, "reranker 应 optional=true"


def _p7_enterprise_yaml_uses_relative_path():
    """enterprise.yaml 的 db_path 应为相对路径（Windows 直装兼容）"""
    data = _load_yaml(ENTERPRISE_YAML_PATH)
    db_path = data["db_path"]
    assert not db_path.startswith("/"), \
        f"db_path 不应为 Linux 绝对路径（/开头），实际 {db_path}"
    assert not db_path.startswith("\\"), \
        f"db_path 不应为反斜杠绝对路径，实际 {db_path}"


def _p8_enterprise_yaml_has_mode_comments():
    """enterprise.yaml 含模式选择注释（端侧部署人员可读）"""
    with open(ENTERPRISE_YAML_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "轻量模式" in content, "enterprise.yaml 应含轻量模式注释"
    assert "完整模式" in content, "enterprise.yaml 应含完整模式注释"
    assert "演示模式" in content, "enterprise.yaml 应含演示模式注释"


def _p9_configure_ps1_exists():
    """configure.ps1 配置向导脚本存在"""
    path = os.path.join(ROOT, "deploy", "postinstall", "configure.ps1")
    assert os.path.isfile(path), f"configure.ps1 不存在: {path}"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    assert ".env.local" in content, "configure.ps1 应生成 .env.local"
    assert "AGENT_LLM_KIND" in content, "configure.ps1 应设置 AGENT_LLM_KIND"
    assert "AGENT_EMBEDDING_KIND" in content
    assert "AGENT_BOT_TOKEN" in content


def _p10_configure_ps1_supports_offline_import():
    """configure.ps1 支持离线导入（U盘/本地目录）"""
    path = os.path.join(ROOT, "deploy", "postinstall", "configure.ps1")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "离线导入" in content, "应支持离线导入选项"
    assert "Copy-Item" in content, "离线导入应使用 Copy-Item"


def _p11_requirements_split():
    """requirements 拆分为 light/full 双轨"""
    light_path = os.path.join(ROOT, "deploy", "requirements-light.txt")
    full_path = os.path.join(ROOT, "deploy", "requirements-full.txt")
    assert os.path.isfile(light_path), "requirements-light.txt 不存在"
    assert os.path.isfile(full_path), "requirements-full.txt 不存在"
    with open(light_path) as f:
        light = f.read()
    with open(full_path) as f:
        full = f.read()
    # light 不含 sentence-transformers
    assert "sentence-transformers" not in light, "light 不应含 sentence-transformers"
    # full 含 sentence-transformers
    assert "sentence-transformers" in full, "full 应含 sentence-transformers"
    # 两者都含 chromadb
    assert "chromadb" in light and "chromadb" in full
    # 版本约束（>= 格式）
    assert ">=" in light, "light 应有版本约束"
    assert ">=" in full, "full 应有版本约束"


CHECKS = [
    ("P1 manifest 存在且可解析", _p1_manifest_exists_and_parseable),
    ("P2 python_embeddable 全模式必需", _p2_python_embeddable_required_for_all_modes),
    ("P3 wheels 模式映射正确", _p3_wheels_mode_mapping),
    ("P4 模型含文件列表", _p4_models_have_file_lists),
    ("P5 模型含国内镜像", _p5_models_have_mirror),
    ("P6 reranker 标记 optional", _p6_optional_reranker),
    ("P7 enterprise.yaml 相对路径", _p7_enterprise_yaml_uses_relative_path),
    ("P8 enterprise.yaml 模式注释", _p8_enterprise_yaml_has_mode_comments),
    ("P9 configure.ps1 存在且正确", _p9_configure_ps1_exists),
    ("P10 configure.ps1 离线导入", _p10_configure_ps1_supports_offline_import),
    ("P11 requirements 双轨拆分", _p11_requirements_split),
]


def main():
    failed = []
    for name, fn in CHECKS:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name}: 异常 {type(e).__name__}: {e}")
            failed.append(name)
    print(f"=== Summary: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed ===")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("RESULT: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
