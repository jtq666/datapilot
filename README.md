# DataPilot｜企业数据分析 Agent 平台

**开发与维护：江添琦**

> 基于 Ollama + Qwen 的自然语言数据分析个人项目，提供工具调用、只读 SQL 查询、知识检索、任务状态与运行追踪。使用方式和工程边界见下文，评测方法见 [evaluation/README.md](evaluation/README.md)。

## 项目解决什么问题

业务人员用自然语言提问，Agent 根据需要读取 SQLite 数据库结构、调用只读查询工具，并将结果整理成回答。一次简单请求按顺序经过：

1. 网页把问题和所选数据源提交给 FastAPI；后端检查访问范围。
2. AgentRunner 通过 Provider 请求 Ollama/Qwen；模型提出结构化工具请求。
3. ToolRegistry 检查工具名和参数，调用绑定当前数据源的 SQLite 只读工具。
4. AgentRunner 把工具结果交回模型；模型生成最终回答，后端返回网页。

自研 Agent 的核心只有四个职责：Provider 联系 Ollama/Qwen；AgentRunner 控制模型与工具的循环；ToolRegistry 校验并执行工具；State 保存本次运行的消息、待执行工具、轮次和答案。SQL 安全约束查询工具，知识检索以可选工具接入，Trace 记录执行过程。流式展示和有限恢复是附加工程能力；`workflows/` 是独立固定流程，不属于 Agent 循环。

## 项目边界

- `data_platform/`、`web/`、`tests/` 和 `evaluation/` 是本项目学习主线，手册围绕这些代码逐步讲解。
- 本仓库公开 `data_platform` 项目主体，不包含本地单独保存的 DB-GPT 上游参考代码快照；本地参考目录的说明见 [`vendor/README.md`](vendor/README.md)。
- 默认模型路径是 Ollama + Qwen，默认模型标签为 `qwen2.5:14b`。Agent 只维护原生 Tool Calling；旧客户端显式提交 `protocol=react` 会收到 422，不会静默切换。
- 当前没有接入运行时的权威 JSON 指标目录。评测题中的 gold SQL 是评分标准，不是 Agent 运行时指标治理功能。

## 本地启动

使用 Python 3.12 或更新版本、Node.js 及 Ollama。先在仓库根目录安装后端依赖，模型文件由 Ollama 管理：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

启动本地模型：

```powershell
ollama serve
ollama pull qwen2.5:14b
$env:MODEL_PROVIDER = 'ollama'
$env:OLLAMA_MODEL = 'qwen2.5:14b'
$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434/v1'
```

另开终端启动 API：

```powershell
cd F:\shixi\data-agent-platform
.\.venv\Scripts\python.exe -m data_platform.api bootstrap-user --username demo
.\.venv\Scripts\python.exe -m data_platform.api serve --port 18090
```

再启动网页：

```powershell
cd F:\shixi\data-agent-platform\web
New-Item -ItemType Directory -Force 'F:\shixi\_resources\npm-cache', 'F:\shixi\_resources\tmp' | Out-Null
$env:npm_config_cache = 'F:\shixi\_resources\npm-cache'
$env:TEMP = 'F:\shixi\_resources\tmp'
$env:TMP = 'F:\shixi\_resources\tmp'
npm ci
npm run dev
```

网页地址为 `http://127.0.0.1:5173/`，API 文档为 `http://127.0.0.1:18090/docs`。网页和 API 都需要运行；真实模型问答还需要 Ollama 服务和已下载的模型。

## 检查与本地评测

```powershell
cd F:\shixi\data-agent-platform
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m evaluation.validate_eval_dataset
.\.venv\Scripts\python.exe -m evaluation.run_local_benchmark
```

`run_local_benchmark` 不调用模型，检查的是固定小样本上的检索排序与 SQL 执行/拦截契约，不代表 Ollama + Qwen 的回答准确率。评测口径和已有结果见 [`evaluation/README.md`](evaluation/README.md)。

## 目录地图

| 路径 | 用途 |
|---|---|
| `data_platform/agents/` | Agent 消息合同、工具注册和执行循环 |
| `data_platform/model_gateway/` | Ollama + Qwen 模型适配 |
| `data_platform/data_sources/` | SQLite Schema 读取和只读查询 |
| `data_platform/knowledge/` | 文档切块、关键词/向量检索和知识库工具 |
| `data_platform/runtime/` | Run、Event、Checkpoint 和状态持久化 |
| `data_platform/conversations/` | 用户可见的会话历史 |
| `data_platform/workflows/` | 独立的固定工作流；不是 Agent 核心循环，主线学习可跳过 |
| `web/` | React 本地工作台 |
| `tests/` | 不依赖真实模型结果的工程测试 |
| `evaluation/` | 冻结数据集、离线基准和评测记录 |

## 当前限制

- 项目作者确认异机评测采用与本项目一致的方案和判分口径，实测汇总为：40 道业务题端到端成功 34 道（85%），32 道检索题前五命中 28 道（87.5%）。旧电脑原始逐题记录尚未提供给本仓库，当前不能独立复核失败题与具体模型版本。仓库现已另建 `retrieval-v1` 的 32 题、19 份语料、Manifest 和评测脚本，**这是未运行的新题集，不是 28/32 的原始证据**；方法和口径见[项目学习与面试手册](../学习文档/01-项目学习/数据智能平台-项目学习与面试手册.md#lesson-7-2)。
- SQL 安全边界针对本地 SQLite 开发场景，不是任意数据库的生产级沙箱；项目也没有生产级部署与运维保障。
- 当前本地检索基准使用确定性测试向量和构造语料，结果只证明测试代码路径，不证明真实语料或真实模型效果。
- 当前未实现运行时 JSON 指标目录，因此简历和讲解都不应声称已经完成动态业务口径治理。

## 建议阅读顺序

从本页的一次请求流程开始，再阅读 `data_platform/agents/runner.py`、`agents/tools.py` 和 `runtime/store.py`，了解工具执行与状态保存。检索与业务题评测见 [evaluation/README.md](evaluation/README.md)。

能力状态速查见[功能对照矩阵](docs/功能对照矩阵.md)；五个简历工作点的模块、数据合同、实施顺序与验收门槛见[工程设计方案](docs/简历五点工程设计与验收方案.md)；DB-GPT 上游边界见[参考版说明](docs/参考版复现说明.md)。设计方案中的待办不是已实现功能，本轮未执行模型评测。
