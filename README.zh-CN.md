[English](README.md) | [简体中文](README.zh-CN.md)

# Domain-Specific Paper Harness

Domain-Specific Paper Harness 是一个可自行托管的科研情报产品，用于持续发现、分析并关联已配置研究领域中的论文。它将仅使用 arXiv 的 Daily 发现流水线与基于原文的全文分析、历史研究、论文比较、支持溯源的知识图谱、确定性趋势以及只读 Web 产品结合在一起。

[使用指南](docs/USAGE.md) · [架构](docs/ARCHITECTURE.md) ·
[贡献指南](CONTRIBUTING.md) · [安全政策](SECURITY.md) ·
[Apache-2.0 许可证](LICENSE)

![Paper Harness 仪表板](docs/images/paper-harness-dashboard.png)

初始主题为 Broad LLM Agents、Brain-Computer Interfaces 和 World Models。每个主题独立拥有自己的查询、包含与排除规则、游标、筛选、报告、图谱、趋势和谱系。本项目是科研产品，而不是论文写作工具、通用搜索引擎或聊天机器人。

## 功能

- 使用重叠且幂等的发现窗口，维护规范化的 arXiv 身份和显式版本跟踪。
- 对选定论文进行结构化 DeepSeek 分析；GROBID 是唯一全文解析器，并显式记录分析溯源。
- 将论点和简短证据摘录关联到确切论文版本，并在可用时记录来源坐标。
- 通过认证的 Semantic Scholar 检索历史论文，使用有界的 PaSa 衍生搜索行为以及固定版本的 SPECTER2 embeddings。
- 提供与证据关联的比较、研究谱系、支持溯源的知识图谱，以及确定性的 7/30/90 天趋势。
- 如实呈现 `COMPLETE`、`PARTIAL`、`FAILED` 和 `NO_UPDATE` 状态，包括单项失败和 enrichment 可用性详情。
- 提供面向读取的 FastAPI API 和 React 界面，用于查看报告、论文、证据、比较、图谱、趋势、谱系和运行状态。

主要产品流程和全部可用截图请参阅[可视化使用指南](docs/USAGE.md)。

## 架构

本仓库采用 Ports-and-Adapters 模块化单体架构，包含三个可独立部署的运行单元：

1. **Web/API** 在 `/api/v1` 下提供 FastAPI 和生产 React 构建。它读取已持久化的结果，不运行后台科研任务。
2. **Daily** 为每个主题运行一条有界流水线，从 arXiv 发现一直到原子化产品发布。
3. **GROBID** 解析科研 PDF，并应仅对 Daily 运行单元保持私有。

唯一的持久化契约是带有 pgvector 的 PostgreSQL 15 或更高版本。Alembic migrations 需显式执行；API 启动时绝不会自行迁移。生产部署通过 Terraform 使用 Google Cloud Run 和 Cloud Run Jobs，其中 Web/API 受 IAP 保护，GROBID 保持私有，并在支持时将最小实例数设为零。

```text
external service -> adapter -> port -> application use case -> domain
```

更多详情请参阅[架构](docs/ARCHITECTURE.md)、[边界](docs/BOUNDARIES.md)和[失败政策](docs/FAILURE_POLICY.md)。

## 前置要求

- Git
- Windows PowerShell 5.1 或 PowerShell 7（主要支持的开发路径）
- [uv](https://docs.astral.sh/uv/) 和准确版本 CPython 3.13.13
- 带有 Corepack 的 Node.js 24；仓库会选择对应的 pnpm 版本
- 带有 Docker Compose 的 Docker Desktop
- 带有 pgvector 的 PostgreSQL 15+（本地由 Compose 提供）

仅云端部署需要 Terraform 和 Google Cloud CLI。第一方 Python 固定为 `>=3.13.13,<3.14`；不支持用其他 Python 版本代替。

## Windows 无密钥快速开始

此路径会启动一个空的本地数据库、只读 API 和 Web 界面。它不会访问 DeepSeek 或 Semantic Scholar，也不需要供应商或云端凭据。

```powershell
git clone https://github.com/ArcueidMP/Domain-Specific-Paper-Harness.git
Set-Location Domain-Specific-Paper-Harness
uv python install 3.13.13
corepack pnpm --version
.\scripts\dev.ps1
```

该脚本会同步锁定的 Python 和前端依赖，启动本地 pgvector 数据库，应用 Alembic migrations，并启动两个开发服务器：

- Web：<http://127.0.0.1:5173>
- API：<http://127.0.0.1:8000>
- OpenAPI：<http://127.0.0.1:8000/docs>

如果端口 5432、8000 或 5173 已被占用，请选择其他端口：

```powershell
.\scripts\dev.ps1 -PostgresPort 15432 -ApiPort 18000 -WebPort 15173
```

在 Linux 上，请在运行 `docker compose` 前设置 `POSTGRES_PORT=15432`，在
`DATABASE_URL` 中使用端口 15432，并分别向 uvicorn 和 Vite 传入端口 18000
和 15173。

按 `Ctrl+C` 停止 API 和 Web 进程。PostgreSQL 会继续在 Docker 中运行。新数据库尚无 Daily publication，因此在流水线首次完成前出现空报告和空论文状态是正常现象。

### 等效 Linux 命令

PowerShell 辅助脚本是主要支持路径。在 Linux 上，请从仓库根目录显式执行相同操作：

```bash
uv python install 3.13.13
uv sync --frozen --python 3.13.13
corepack pnpm install --frozen-lockfile
docker compose up --detach --wait db
export DATABASE_URL='postgresql+psycopg://paper_harness:paper_harness_local@localhost:5432/paper_harness'
uv run --frozen --python 3.13.13 alembic upgrade head
```

随后在两个终端中分别启动进程，并在 API 终端导出相同的 `DATABASE_URL`：

```bash
# Terminal 1
export DATABASE_URL='postgresql+psycopg://paper_harness:paper_harness_local@localhost:5432/paper_harness'
uv run --frozen --python 3.13.13 uvicorn paper_harness_api.main:app --host 127.0.0.1 --port 8000 --reload
```

```bash
# Terminal 2
corepack pnpm --filter @paper-harness/web dev --host 127.0.0.1 --port 5173
```

这些 Linux 命令使用与 CI 相同的锁定依赖。macOS 尚未验证，目前不是受支持的开发环境。

## 本地 API 示例

无密钥开发服务器运行后，在 Windows PowerShell 中使用 `curl.exe`：

```powershell
curl.exe http://127.0.0.1:8000/health/live
curl.exe http://127.0.0.1:8000/health/ready
curl.exe http://127.0.0.1:8000/api/v1/topics
curl.exe "http://127.0.0.1:8000/api/v1/papers?topic=broad-llm-agents&limit=10"
```

存在 publication 后：

```powershell
curl.exe "http://127.0.0.1:8000/api/v1/daily/latest?topic=broad-llm-agents"
curl.exe "http://127.0.0.1:8000/api/v1/trends?topic=broad-llm-agents&window=7D"
curl.exe "http://127.0.0.1:8000/api/v1/runs/latest?topic=broad-llm-agents"
```

在 Linux 上，请将 `curl.exe` 替换为 `curl`。

## 在本地运行完整流水线

完整流水线会发出实时请求，并需要由你自行持有的凭据。它还需要本地 GROBID 服务，以及经过明确准备并固定版本的 SPECTER2 artifact。切勿提交凭据或已准备的模型文件。

首先安装锁定的 SPECTER2 runtime 并准备一次模型。准备过程会下载准确的上游 revision、验证其 source hash，并将其转换为仅含 safetensors 的本地 artifact：

```powershell
uv sync --frozen --python 3.13.13 --extra specter2
$Specter2Path = Join-Path $env:LOCALAPPDATA "PaperHarness\models\specter2_base"
$Specter2Cache = Join-Path $env:LOCALAPPDATA "PaperHarness\cache\huggingface"
uv run --frozen --python 3.13.13 --extra specter2 python -m paper_harness.adapters.specter2.prepare `
  --output $Specter2Path `
  --cache-dir $Specter2Cache
```

如果该 artifact 已经存在，请保留它并跳过准备命令。启动 PostgreSQL 和本地 GROBID，然后设置仅对当前 session 生效的配置并执行一个主题：

```powershell
docker compose --profile analysis up --detach --wait db grobid
$env:APP_ENV = "development"
$env:DATABASE_URL = "postgresql+psycopg://paper_harness:paper_harness_local@localhost:5432/paper_harness"
$env:LLM_PROVIDER = "deepseek"
$env:LLM_MODEL = "deepseek-v4-flash"
$env:DEEPSEEK_API_KEY = "<your-deepseek-api-key>"
$env:SEMANTIC_SCHOLAR_API_KEY = "<your-semantic-scholar-api-key>"
$env:GROBID_URL = "http://127.0.0.1:8070"
$env:GROBID_AUTH_MODE = "none"
$env:SPECTER2_MODEL_PATH = $Specter2Path
uv run --frozen --python 3.13.13 alembic upgrade head
uv run --frozen --python 3.13.13 --extra specter2 paper-harness-daily run-pipeline `
  --topic-config configs/topics/broad-llm-agents.yaml
```

使用 `configs/topics/brain-computer-interfaces.yaml` 或 `configs/topics/world-models.yaml` 可运行另一个独立主题。CLI 会输出结构化终端事件，并在发生 run-level failure 时以非零状态退出。

等效的 Linux 准备和运行命令如下：

```bash
uv sync --frozen --python 3.13.13 --extra specter2
export SPECTER2_MODEL_PATH="${XDG_DATA_HOME:-$HOME/.local/share}/paper-harness/specter2_base"
uv run --frozen --python 3.13.13 --extra specter2 python -m paper_harness.adapters.specter2.prepare \
  --output "$SPECTER2_MODEL_PATH" \
  --cache-dir "${XDG_CACHE_HOME:-$HOME/.cache}/paper-harness/huggingface"
docker compose --profile analysis up --detach --wait db grobid
export APP_ENV=development
export DATABASE_URL='postgresql+psycopg://paper_harness:paper_harness_local@localhost:5432/paper_harness'
export LLM_PROVIDER=deepseek
export LLM_MODEL=deepseek-v4-flash
export DEEPSEEK_API_KEY='<your-deepseek-api-key>'
export SEMANTIC_SCHOLAR_API_KEY='<your-semantic-scholar-api-key>'
export GROBID_URL='http://127.0.0.1:8070'
export GROBID_AUTH_MODE=none
uv run --frozen --python 3.13.13 alembic upgrade head
uv run --frozen --python 3.13.13 --extra specter2 paper-harness-daily run-pipeline \
  --topic-config configs/topics/broad-llm-agents.yaml
```

之后的运行仅跳过模型准备命令；让 `SPECTER2_MODEL_PATH` 始终指向已准备的 artifact。

### 运行时配置

完整的本地模板位于 [.env.example](.env.example)。请将真实值保存在 session 环境变量或被忽略的本地密钥存储中，绝不要写入被跟踪的文件。

| 变量 | 使用者 | 用途 |
| --- | --- | --- |
| `DATABASE_URL` | Web/API 和 Daily | 使用 psycopg 3 driver 的 PostgreSQL 15+ 连接 |
| `LLM_PROVIDER=deepseek` | Daily | 选择唯一受支持的生产 LLM provider |
| `LLM_MODEL=deepseek-v4-flash` | Daily | 选择必需的 DeepSeek model |
| `DEEPSEEK_API_KEY` | Daily | 用户自行持有的凭据，用于生成分析和叙述 |
| `SEMANTIC_SCHOLAR_API_KEY` | Daily | 用户自行持有的凭据，用于历史及 related-work 操作 |
| `GROBID_URL` | Daily | 唯一全文解析器的 URL |
| `GROBID_AUTH_MODE` | Daily | 本地开发使用 `none`；生产环境需要 Google identity |
| `SPECTER2_MODEL_PATH` | Daily | 已准备、固定版本的离线 model artifact |

Web/API 不需要 DeepSeek 或 Semantic Scholar 凭据。浏览器绝不会接收到任何数据库或供应商密钥。

## 发布状态

- `COMPLETE` 表示当天可用的来源元数据已经发布。可选的 related-work、比较、图谱、趋势、谱系或证据 enrichment 仍可能被显式标记为不可用。
- `PARTIAL` 表示可用元数据已经发布，但至少一篇选定论文的核心元数据或来源分析处理失败。失败阶段和稳定错误码仍然可见。
- `NO_UPDATE` 是正常的完整 publication，表示该主题和逻辑日期没有新符合条件的论文。
- `FAILED` 仅用于 run-level 边界，例如无效配置、认证失败、数据库或 migration 失败、在获得可用输入前发生全局 arXiv 失败、无法持久化可用元数据，或 publication 失败。

某个独立项目失败不会抑制其他可用论文。缺少可选 enrichment 时会显示为不可用，而不会进行编造或阻止原本可用的 publication。

## 数据来源与信任边界

- **Daily 发现：**仅限 arXiv。
- **历史论文与 related work：**通过认证的 Semantic Scholar、已持久化的本地 corpus，以及有界的 PaSa 衍生 scholarly tool loop。
- **全文：**仅 arXiv 托管的 PDF 符合条件。非 arXiv 历史结果仅保留 bibliographic 或 abstract stub。
- **排除的行为：**不抓取出版商网站、不绕过付费墙、不下载出版商 PDF、不使用通用 Web 搜索、不进行隐藏的 provider 替换，也不修复格式错误的 model output。

论文标题、摘要、元数据、PDF 和摘录仍受其上游作者及供应商权利和条款约束。Apache-2.0 适用于仓库中的第一方源代码，不适用于第三方论文内容或模型输出。AI 生成的分析和推断关系带有溯源信息，且不会被标示为已经人工验证。

## 费用与外部服务

无密钥只读快速开始仅使用本地计算资源，不使用付费模型 API。完整运行可能会产生费用或消耗你所配置服务的配额，包括 DeepSeek、托管 PostgreSQL、云计算及出站流量，以及与 Semantic Scholar 相关的任何供应商方案。本地 GROBID 和 SPECTER2 也会消耗 CPU、内存、磁盘和网络带宽。本项目不会为你创建消费上限或配置托管数据库。

在运行流水线或启用定时任务前，请检查已配置的论文数量、搜索限制、重试、超时和模型价格。

## 验证

规范的 Windows 验证命令是：

```powershell
.\scripts\verify.ps1
```

它会检查冻结依赖、Python 和前端质量门槛、测试、生成的 API contracts、Docker Compose 和 runtime images、Terraform、干净的 Alembic migration 以及 PostgreSQL integration。默认验证不需要任何实时供应商或云端凭据。开发期间，应先运行覆盖所修改边界的聚焦检查，再运行规范发布检查。

## 部署

`infra/terraform` 下的 Terraform 定义了受支持的 Google Cloud topology：私有 Web/API Cloud Run service、按主题划分的 Cloud Run Jobs、私有 GROBID、Secret Manager references，以及每个主题一个 Cloud Scheduler target。默认计划时间为 `Asia/Kuala_Lumpur` 时区的 20:00、20:20 和 20:40。

部署需要现有 Google Cloud project、外部管理且带有 pgvector 的 PostgreSQL 15+ 数据库、immutable runtime images，以及用户自行持有的 secret values。应用任何 Terraform plan 前都应检查计费与计划中的每一项变更。具体 operator workflow 请参阅[生产运行手册](docs/RUNBOOK.md)。默认情况下，没有任何部署命令会授予公共 endpoint。

## 当前限制

- 此源码 release 不包含托管的公开 Demo，也不提供对维护者任何生产环境的访问权限。
- 本项目不会配置兼容的托管 PostgreSQL 数据库。
- 全文分析需要 GROBID；不存在解析器 fallback。
- DeepSeek、通过认证的 Semantic Scholar、已准备的 SPECTER2 和 PostgreSQL 都没有隐式生产替代项。
- 受支持的本地开发路径是 Windows PowerShell。所提供的 Linux 手动命令与 CI 一致；macOS 尚未验证。
- 趋势和周期报告的实用性取决于持久化 corpus 是否有足够历史。数据不足会保持可见，而不会被包装为趋势结论。
- 超过已配置 ingestion bound 的 arXiv PDF 仍会成为单项分析失败，但其可用来源元数据仍可能发布。

## 贡献与安全

我们以 best-effort 维护方式欢迎 bug reports 和范围明确的 pull requests。在更改主题行为、数据边界或生成的 contracts 前，请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。请按照 [SECURITY.md](SECURITY.md) 中的说明私下报告漏洞；不要在公开 issue 中放置凭据或敏感诊断信息。

## 许可证

第一方源代码采用 [Apache License 2.0](LICENSE) 许可。第三方致谢和集成边界记录在 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 [docs/reuse-register.yaml](docs/reuse-register.yaml) 中。
