# 参考实现目录

`dbgpt-upstream/` 是从 DB-GPT Git 提交 `953da6b23fb0be5cfde490944d8f735cd16ac586` 提取的本地历史参考源码快照，未随本公开仓库发布。运行根目录的 `data_platform` 主程序不需要这份快照。

完整 DB-GPT 入口会直接运行这份快照；`data_platform` 的独立代码仍在仓库根目录，用于补充和对照 Harness、状态、只读 SQL 等实现。两层能力和代码归属在项目文档中分开记录。

## 许可证与来源

DB-GPT 上游代码按其仓库中的 MIT 许可证分发。若另行获取、使用或重新分发参考源码，请保留该源码随附的版权及许可证。当前公开仓库只保留参考信息，不包含这份源码及其许可证副本。

上游项目：<https://github.com/eosphoros-ai/DB-GPT>

## 本地运行

参考版的依赖环境与目标项目分开。若本机已有 `F:\shixi\DB-GPT\.venv`，可执行：

```powershell
cd F:\shixi\data-agent-platform
.\scripts\run_dbgpt_reference.ps1
```

如果 5670 已被已有参考服务占用，可用临时端口启动：

```powershell
.\scripts\run_dbgpt_reference.ps1 -Port 5671
```

没有现成环境时，先按 `vendor/dbgpt-upstream/pyproject.toml` 安装依赖，再运行脚本。密钥必须通过当前 PowerShell 会话的环境变量提供，不能写入配置文件或仓库。

脚本会在缺少 `DEEPSEEK_API_KEY` 时立即停止，不会把“网页启动”误认为“模型对话可用”。

目标项目的本地实现仍使用：

```powershell
cd F:\shixi\data-agent-platform
py -3.12 -m pytest -q
```
