# DataPilot 企业数据分析 Agent 平台

面向业务人员提供自然语言查数、带业务口径和 SQL 证据的报告，以及审批后的站内共享。本分支提供新版的完整代码与独立部署配置。

## 启动

需要 Python 3.12、Node.js 22+、Docker Compose、DeepSeek API Key 和 Jina API Key。

1. 复制 `.env.example` 为 `.env`，为两个数据库设置本地密码，同步对应数据库连接串。模型 Key 写入本机环境变量或本地 `.env`。
2. 在项目根目录执行 `docker compose -p datapilot-v2 up -d --wait`。如果用 Ubuntu WSL 自带的 Docker，在 WSL 的项目目录执行同一命令。Windows 后端需要能连到映射的数据库端口（默认 55432 和 55433）。如果端口被占用，在本地 `.env` 调整对应的 `APP_DB_PORT` 或 `BUSINESS_DB_PORT`，同步修改该数据库连接串的端口后重新启动。如果退出 WSL 终端后引擎随发行版停掉，可以让该终端执行 `docker compose -p datapilot-v2 up -d --wait && sleep infinity`，保持运行到演示结束。
3. 在 PowerShell 执行以下命令。启动脚本可选用 `-UseExistingApiKeys` 从本机原配置映射已有 Key。

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python -m pip install -e '.[dev]'
   . .\infra\load-env.ps1
   .\.venv\Scripts\python -m datapilot.bootstrap
   .\.venv\Scripts\python -m uvicorn datapilot.api:app --host 127.0.0.1 --port 18091
   ```

4. 另开终端，在 `web` 目录执行 `npm ci` 和 `npm run dev`。打开 `http://127.0.0.1:5174`。使用初始化账号登录，或在仅供本机演示的 `.env` 中设 `DATAPILOT_LOCAL_DEMO=1` 后重启后端，直接选择分析员或报告接收人。

## 工作台

- **智能问数**以会话为单位保存多轮提问，点击示例即可填写问题。发出问题后，页面显示实际工具执行进度。结果支持表格、适用数据的柱状图、SQL 和 CSV 导出。
- **数据源**展示当前 PostgreSQL 数据源可读的表和字段。
- **知识库**支持发布规则、查看版本和切片，并用同一条混合检索链路试查资料。
- **共享报告**在聊天内审批，接收人在独立入口打开报告。可在侧栏底部切换本机演示角色。

## 演示与测试

以 analyst 登录，直接问「按渠道统计订单数量」，再追问「只看官网呢」。在想留档的那条回答下点「生成报告」，系统保存该轮的答案和 SQL 证据。展开「共享报告」，选择 reviewer 并确认。以 reviewer 登录后可从「共享报告」查看。样例订单的支付月份是业务库首次初始化的月份，按月演示时请使用该月份。

提问时可展开「指定计算规则（可选）」选用已发布的规则。模型发现指标有多种适用口径时，可以先提出选项并暂停；用户选择后，LangGraph 从原任务接着查数。普通查数可以直接提交。

- `python -m pytest -q` 运行本地回归用例。载入数据库环境后，测试也会验证 PostgreSQL 读取、检查点续接和站内共享。
- 配置两个演示密码及模型凭据后，可执行 `python -m tests.live_smoke` 复现一次功能验收。它发布两版演示规则，检查 SQL 证据，并在 API 进程重启后验证审批和共享。
- `npm run build` 构建网页。停止网页和 API 后，可用同一 Compose 项目执行 `docker compose -p datapilot-v2 stop`。
- 设计、面试讲法、版本切换和评测口径见[新版实现与评测说明](docs/DataPilot-项目学习与面试手册.md)。

## 代码与来源

`datapilot/agent.py` 是 LangGraph 执行主线，`datapilot/sql.py` 和 `datapilot/knowledge.py` 分别管理只读查询与检索。沿用 DataPilot 旧版的工具注册实现和 Jina 适配代码，许可见 [LICENSE](LICENSE)。
