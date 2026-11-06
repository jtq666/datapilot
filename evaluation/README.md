# 评测说明

本目录同时保存工程契约基准、冻结的业务题集和历史模型运行记录。阅读结果时先判断评测对象，不能把“SQL 执行器通过”当成“Agent 回答准确”。

## 评测资产

| 文件 | 用途 | 当前可支持的结论 |
|---|---|---|
| `fixtures/orders_eval_v2.db` | 冻结的九表业务样例数据库 | 用于业务题和 SQL 结果核对 |
| `fixtures/orders_eval_v2.manifest.json` | 数据库/题集哈希、结构和生成信息 | 用于确认评测输入版本 |
| `datasets/business_cases_v2.jsonl` | 40 道业务题及标准结果 | 题集完整性可验证；题数不等于模型成功题数 |
| `results/dataset_validation.json` | 题集校验结果 | 记录 40 道题、九张表、gold SQL 可执行及哈希匹配 |
| `run_local_benchmark.py` | 无模型调用的确定性工程基准 | 检查少量检索、SQL 正确执行和危险 SQL 拦截 |
| `results/local_benchmark.json` | 上述基准的保存结果 | 只能作为本地工程契约结果解释 |
| `results/business_eval_*.jsonl` | 分批 Agent 运行记录 | 保留单题结果和失败样例；不可未经核对直接拼成统一完整评测结论 |
| `fixtures/retrieval_corpus_v1/`、`datasets/retrieval_cases_v1.jsonl` | 新建的 19 份业务说明和 32 道单 gold 检索题 | 新题集已冻结；不是旧电脑 28/32 的原始题集 |
| `fixtures/retrieval_v1.manifest.json` | 新题集、语料 SHA-256 和检索配置 | 供运行前验证版本，不包含模型结果 |
| `validate_retrieval_dataset.py`、`run_retrieval_eval.py` | 离线校验和实际 Hybrid RAG 评测入口 | 前者不调用模型；后者需要 Jina Embeddings，当前尚未运行 |

## 四种结果不要混为一谈

1. **数据集完整性**：题目、数据库、标准 SQL 和哈希是否一致。
2. **SQL 执行结果正确性**：工具返回的列、行、分组和数值是否符合标准。
3. **最终回答正确性**：自然语言答案是否覆盖问题要求，且没有超出工具证据。
4. **工具错误恢复/中断续接**：异常是否真实触发、Agent 是否收到错误、是否修改后重试；中断后是否读取 checkpoint 并避免重复已完成动作。

SQL 正确不代表最终回答正确。错误未触发、模型一开始就生成正确 SQL，也不能计为“错误恢复成功”。

## 当前已有的本地工程基准

`results/local_benchmark.json` 对应 `local-contract-v1`，运行方式为确定性离线、无模型调用：

| 子项 | 当前结果 | 正确解释 |
|---|---:|---|
| 检索 | 16/16 命中前五，Recall@5 = 1.00，MRR@5 = 1.00 | 人工构造的一题一文测试语料，使用确定性哈希向量；不能代表真实语料检索质量 |
| SQL 有效查询 | 6/6 结果匹配 | 固定 SQLite 小样例上的执行契约检查 |
| 危险 SQL 拦截 | 6/6 被拒绝 | 固定拒绝用例检查，不代表对任意 SQL 攻击的完整防护证明 |

这些数字可用于说明“写了哪些确定性工程检查”，不应作为大模型端到端效果或生产安全率写在简历上。

## 运行命令

```powershell
cd F:\shixi\data-agent-platform
.\.venv\Scripts\python.exe -m evaluation.validate_eval_dataset
.\.venv\Scripts\python.exe -m evaluation.run_local_benchmark
```

真实模型评测由 `run_agent_eval.py` 调用当前默认 Ollama + Qwen Provider。执行前应确认模型、数据库、题集、代码版本一致，并为每次运行使用新的输出文件，避免覆盖已有记录。示例：

```powershell
.\.venv\Scripts\python.exe -m evaluation.run_agent_eval --start 0 --limit 1 --output evaluation/results/manual_smoke.jsonl
```

先用一题检查链路与输出，再决定是否运行更大的批次。业务脚本现在同时生成逐题 `.jsonl` 和同名 `.summary.json`：摘要保存模型名、输入/脚本/提示词哈希、日期、题数、分项计数和完整/子集标记；脚本拒绝覆盖已有文件。摘要不含 API Key，也不等于完整 Trace。模型评测会消耗本机算力和时间；本说明不表示本轮已执行这些命令，更不表示旧的 34/40 成绩由新增摘要文件证明。

新建 32 题检索集的操作顺序与业务题分开。先检查冻结资产，再决定是否调用外部 Embedding 服务；每次使用新的输出文件，避免覆盖旧报告：

```powershell
.\.venv\Scripts\python.exe -m evaluation.validate_retrieval_dataset
if (-not $env:JINA_API_KEY) { throw '请先在本机安全配置 JINA_API_KEY' }
.\.venv\Scripts\python.exe -m evaluation.run_retrieval_eval --smoke --output evaluation/results/retrieval_v1_smoke.json
.\.venv\Scripts\python.exe -m evaluation.run_retrieval_eval --output evaluation/results/retrieval_v1_full.json
```

第二、三条会调用 Jina Embeddings API；`--smoke` 只测一题，不能作为 32 题分数。示例输出文件名仅用于说明，运行前应换成带日期的唯一名称。脚本会先验证题集和语料哈希，再把临时向量索引建在项目结果目录所在磁盘；输出保留每题 gold、前五文档、名次和汇总 Recall@5/MRR@5。**本轮只运行了不联网的资产校验，未执行检索评测。**

逐步运行 40 道业务题、核对分母、区分自动评分与人工复核，以及复跑 32 道检索题所需资产，见[主手册第 7.5–7.7 节](../../学习文档/01-项目学习/数据智能平台-项目学习与面试手册.md#lesson-7-5)。

## 历史运行记录与作者异机实测汇总

`business_eval_smoke.jsonl`、`business_eval_stage1_8*.jsonl` 和 `business_eval_stage1_40_tail.jsonl` 是仓库保留的历史分批记录。它们可以用于查看具体 SQL、工具结果和失败答案，但只有在模型配置、代码版本、数据哈希、评分规则和题目范围完全一致时，才允许汇总成同一组准确率。不能把某个子集的结果外推到 40 道题。

项目作者确认在另一台电脑按与本项目一致的方案和判分口径完成评测，并提供端到端 `34/40（85%）` 与检索前五命中 `28/32（87.5%）` 的实测汇总。这两组数字**不是** `local_benchmark` 的结果，也不能由本仓库现有的历史分批文件直接推出。旧电脑的原始逐题记录、原 32 题和模型配置尚未提供给当前仓库；本轮新建的 `retrieval-v1` 是另一套题集，尚未运行，不能倒称它已得到 `28/32`。在取得旧原件或运行新题集前，本仓库不能独立复核旧成绩的失败案例或具体模型版本。

## 建议保存的正式评测元数据

每次正式运行至少记录：题集与数据库 SHA-256、代码版本、模型标签和量化方式、运行参数、开始时间、原始模型响应、工具轨迹、SQL 结果、评分依据、失败案例和人工复核。报告应并列给出题目分母以及 SQL 正确数、最终回答正确数、任务成功数、异常已触发数和恢复成功数。
