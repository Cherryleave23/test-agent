<#
.SYNOPSIS
  门店端侧配置向导 — 交互式选择 LLM/embedding 模式，拉取大依赖，生成 .env.local

.DESCRIPTION
  读取 dependency-manifest.yaml，按选择的模式拉取 Tier1 大依赖（torch/模型权重），
  生成 .env.local 环境变量文件。端侧部署人员无需编辑 yaml 即可切换模式。

  三种模式：
    demo  : mock LLM + mock embedding — 无外部依赖，演示用
    light : cloud LLM + mock embedding — 仅需 chromadb，~300MB RAM
    full  : ollama/cloud LLM + bge embedding — 需 torch+模型，~2GB RAM

  依赖拉取方式：
    online : 按 manifest 中 URL 从 PyPI/HuggingFace 下载
    offline: 指定本地目录（U盘/共享），跳过下载
    skip   : 已有依赖，跳过

.NOTES
  生成文件：.env.local（被 run-agent.ps1 加载后设为环境变量）
#>

param(
    [string]$InstallDir = $PSScriptRoot | Split-Path | Split-Path,
    [string]$ManifestPath = "",
    [string]$Mode = "",
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
$ManifestPath = if ($ManifestPath) { $ManifestPath } else { Join-Path $InstallDir "deploy\dependency-manifest.yaml" }
$EnvFile = Join-Path $InstallDir ".env.local"

function Write-Step($msg) { Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[X] $msg" -ForegroundColor Red }

# ── 1. 选择模式 ──────────────────────────────────────────────
Write-Host @"
========================================
  母婴智能顾问 — 端侧配置向导
========================================
请选择部署模式：

  [1] 演示模式 (demo)
      LLM: mock (内置)
      嵌入: mock (内置)
      RAM: ~200MB | 无外部依赖
      适用: 产品演示、功能验证

  [2] 轻量模式 (light)  ← 推荐低配门店
      LLM: 云端 (DeepSeek/OpenAI)
      嵌入: mock (内置)
      RAM: ~300MB | 需联网调用云 LLM
      适用: 4-8GB 内存的门店电脑

  [3] 完整模式 (full)
      LLM: 本地 Ollama 或 云端
      嵌入: bge-small-zh (本地真实语义)
      RAM: ~2GB | 需下载 ~1.3GB 依赖
      适用: 8GB+ 内存的门店电脑
"@ -ForegroundColor White

if (-not $Mode) {
    if ($NonInteractive) { $Mode = "light" }
    else {
        do {
            $choice = Read-Host "`n请输入选项 (1/2/3) [默认 2]"
            switch ($choice) {
                "1" { $Mode = "demo" }
                "3" { $Mode = "full" }
                default { $Mode = "light" }
            }
        } until ($choice -in @("1","2","3",""))
    }
}
Write-Ok "已选择模式: $Mode"

# ── 2. 解析 manifest（简单 YAML 解析，不依赖 pyyaml） ───────
Write-Step "读取依赖清单: $ManifestPath"
if (-not (Test-Path $ManifestPath)) {
    Write-Err "依赖清单不存在: $ManifestPath"
    exit 1
}
$manifestContent = Get-Content $ManifestPath -Raw
Write-Ok "依赖清单已加载"

# ── 3. 按模式拉取大依赖 ─────────────────────────────────────
$needWheels = $Mode -eq "full"
$needModels = $Mode -eq "full"

if ($needWheels -or $needModels) {
    Write-Host @"
`n依赖拉取方式：
  [1] 在线拉取 (从 PyPI / HuggingFace 下载)
  [2] 离线导入 (指定本地目录，如 U 盘路径)
  [3] 跳过 (已有依赖)
"@ -ForegroundColor White

    if ($NonInteractive) { $fetchChoice = "1" }
    else {
        do {
            $fetchChoice = Read-Host "请选择 (1/2/3) [默认 1]"
            if (-not $fetchChoice) { $fetchChoice = "1" }
        } until ($fetchChoice -in @("1","2","3"))
    }

    switch ($fetchChoice) {
        "1" {
            Write-Step "在线拉取依赖..."
            $pipIndex = if ($NonInteractive) { "https://pypi.org/simple" }
                        else {
                            Write-Host "  PyPI 镜像选项：" -ForegroundColor White
                            Write-Host "  [1] 官方 PyPI (pypi.org)"
                            Write-Host "  [2] 清华镜像 (pypi.tuna.tsinghua.edu.cn)" -ForegroundColor White
                            Write-Host "  [3] 阿里镜像 (mirrors.aliyun.com/pypi)" -ForegroundColor White
                            $mirrorChoice = Read-Host "  选择 [默认 2]"
                            switch ($mirrorChoice) {
                                "1" { "https://pypi.org/simple" }
                                "3" { "https://mirrors.aliyun.com/pypi/simple" }
                                default { "https://pypi.tuna.tsinghua.edu.cn/simple" }
                            }
                        }

            if ($needWheels) {
                Write-Step "下载 Python wheels (torch + sentence-transformers)..."
                $wheelsDir = Join-Path $InstallDir "wheels"
                New-Item -ItemType Directory -Path $wheelsDir -Force | Out-Null
                $pythonExe = Join-Path $InstallDir "runtime\python\python.exe"
                if (-not (Test-Path $pythonExe)) {
                    $pythonExe = "python"
                }
                & $pythonExe -m pip download torch sentence-transformers `
                    -d $wheelsDir `
                    -i $pipIndex `
                    --extra-index-url "https://download.pytorch.org/whl/cpu"
                if ($LASTEXITCODE -ne 0) {
                    Write-Warn "wheel 下载部分失败，可稍后重试或改用离线导入"
                } else {
                    Write-Ok "wheels 下载完成 -> $wheelsDir"
                }
            }

            if ($needModels) {
                Write-Step "下载模型权重 (bge-small-zh-v1.5)..."
                $modelsDir = Join-Path $InstallDir "models\bge-small-zh-v1.5"
                New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
                $hfBase = if ($NonInteractive) { "https://huggingface.co" }
                          else {
                              Write-Host "  HuggingFace 镜像选项：" -ForegroundColor White
                              Write-Host "  [1] 官方 (huggingface.co)"
                              Write-Host "  [2] 国内镜像 (hf-mirror.com)" -ForegroundColor White
                              $hfChoice = Read-Host "  选择 [默认 2]"
                              if ($hfChoice -eq "1") { "https://huggingface.co" }
                              else { "https://hf-mirror.com" }
                          }
                $modelFiles = @("config.json","model.safetensors","tokenizer_config.json","vocab.txt","modules.json","sentence_bert_config.json")
                foreach ($f in $modelFiles) {
                    $url = "$hfBase/BAAI/bge-small-zh-v1.5/resolve/main/$f"
                    $dest = Join-Path $modelsDir $f
                    Write-Host "  下载 $f..." -NoNewline
                    try {
                        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 120
                        Write-Host " OK" -ForegroundColor Green
                    } catch {
                        Write-Host " FAIL" -ForegroundColor Red
                        Write-Warn "  $f 下载失败: $_"
                    }
                }
                Write-Ok "模型下载完成 -> $modelsDir"
            }
        }
        "2" {
            $localPath = if ($NonInteractive) { "" }
                         else { Read-Host "请输入本地依赖目录路径 (如 E:\deps)" }
            if ($localPath -and (Test-Path $localPath)) {
                Write-Step "从本地导入依赖: $localPath"
                $wheelsSrc = Join-Path $localPath "wheels"
                $modelsSrc = Join-Path $localPath "models"
                if (Test-Path $wheelsSrc) {
                    Copy-Item $wheelsSrc (Join-Path $InstallDir "wheels") -Recurse -Force
                    Write-Ok "wheels 已导入"
                }
                if (Test-Path $modelsSrc) {
                    Copy-Item $modelsSrc (Join-Path $InstallDir "models") -Recurse -Force
                    Write-Ok "models 已导入"
                }
            } else {
                Write-Err "路径不存在: $localPath"
                Write-Warn "跳过依赖导入，可稍后手动复制"
            }
        }
        "3" {
            Write-Ok "跳过依赖拉取（假定已存在）"
        }
    }
} else {
    Write-Ok "模式 $Mode 不需要大依赖，跳过拉取"
}

# ── 4. 收集配置参数 ─────────────────────────────────────────
Write-Step "配置参数收集"

$enterpriseId = if ($NonInteractive) { "demo" }
                else { Read-Host "企业 ID [默认 demo]" }
if (-not $enterpriseId) { $enterpriseId = "demo" }

$botToken = if ($NonInteractive) { "" }
            else { Read-Host "iLink Bot Token (从 iLink 开放平台获取)" }
if (-not $botToken) { $botToken = "<placeholder>" }

$llmKind = switch ($Mode) {
    "demo"  { "mock" }
    "light" { "cloud" }
    "full"  {
        if ($NonInteractive) { "ollama" }
        else {
            Write-Host "  LLM 选择：" -ForegroundColor White
            Write-Host "  [1] 本地 Ollama (不出网，需 16GB+ RAM)"
            Write-Host "  [2] 云端 DeepSeek (出网，低延迟)" -ForegroundColor White
            $llmChoice = Read-Host "  选择 [默认 2]"
            if ($llmChoice -eq "1") { "ollama" } else { "cloud" }
        }
    }
}

$llmBaseUrl = ""
$llmApiKey = ""
$llmModel = ""
if ($llmKind -eq "cloud") {
    $llmBaseUrl = if ($NonInteractive) { "https://api.deepseek.com/v1" }
                  else {
                      Write-Host "  云 LLM 提供商：" -ForegroundColor White
                      Write-Host "  [1] DeepSeek (api.deepseek.com)"
                      Write-Host "  [2] OpenAI (api.openai.com)" -ForegroundColor White
                      Write-Host "  [3] 自定义" -ForegroundColor White
                      $provChoice = Read-Host "  选择 [默认 1]"
                      switch ($provChoice) {
                          "2" { "https://api.openai.com/v1" }
                          "3" { Read-Host "  base_url" }
                          default { "https://api.deepseek.com/v1" }
                      }
                  }
    $llmModel = if ($NonInteractive) { "deepseek-chat" }
                else {
                    if ($llmBaseUrl -like "*deepseek*") { "deepseek-chat" }
                    elseif ($llmBaseUrl -like "*openai*") { "gpt-4o-mini" }
                    else { Read-Host "  model 名称" }
                }
    $llmApiKey = if ($NonInteractive) { "" }
                 else { Read-Host "  API Key (sk-xxx)" }
}

$embeddingKind = switch ($Mode) {
    "demo"  { "mock" }
    "light" { "mock" }
    "full"  { "bge-small-zh-v1.5" }
}

# ── 5. 生成 .env.local ─────────────────────────────────────
Write-Step "生成配置文件: $EnvFile"

$envContent = @"
# 端侧配置（由 configure.ps1 生成，勿手动编辑）
# 模式: $Mode
# 生成时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

AGENT_ENTERPRISE_ID=$enterpriseId
AGENT_DB_PATH=./data/instance.db
AGENT_LLM_KIND=$llmKind
AGENT_LLM_MODEL=$llmModel
AGENT_LLM_BASE_URL=$llmBaseUrl
AGENT_LLM_API_KEY=$llmApiKey
AGENT_EMBEDDING_KIND=$embeddingKind
AGENT_BOT_TOKEN=$botToken
"@

$envContent | Out-File -FilePath $EnvFile -Encoding UTF8 -Force
Write-Ok "配置已写入: $EnvFile"

# ── 6. 汇总 ────────────────────────────────────────────────
Write-Host @"

========================================
  配置完成
========================================
  模式:       $Mode
  LLM:        $llmKind $(if ($llmModel) { "($llmModel)" })
  嵌入:       $embeddingKind
  企业 ID:    $enterpriseId
  数据目录:   $InstallDir\data\
  配置文件:   $EnvFile

下一步:
  1. 运行 register-service.ps1 注册开机自启
  2. 运行 run-agent.ps1 启动服务
  3. 扫描控制台输出的 iLink 登录二维码

"@ -ForegroundColor Green
