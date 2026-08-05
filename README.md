# RepoEvo-Agent

> 一个面向小型 Python 仓库的软件维护智能体：安全执行、可恢复运行、全程可观测。

RepoEvo-Agent 接收代码维护需求，在受限工具和隔离沙箱中完成仓库理解、补丁生成、测试验证与结果审查。它的重点不是让模型拥有无限制的终端权限，而是把权限、状态、证据和恢复机制设计成可验证的工程系统。

## 项目展示

一次完整维护任务的主路径如下：

1. 用户在本地控制台创建任务；
2. Agent 分析需求和仓库上下文，生成受约束的修改方案；
3. 补丁只会应用到临时工作区；
4. 测试在 Docker 隔离沙箱中运行；
5. Reviewer 根据测试结果和变更范围决定是否批准；
6. FastAPI + SSE 实时展示任务状态、事件和证据。

演示视频展示了任务生命周期、沙箱隔离验证和 Docker Compose 健康检查。

### 演示视频

[打开项目演示视频](demo/RepoEvo-Agent-Demo.mp4)

视频对应本仓库的本地展示流程，覆盖控制台任务流转、隔离沙箱探针和 Compose 服务健康检查。

## 核心能力

- **安全执行**：不向模型暴露任意 Shell；补丁、文件读取、测试和 Git 操作均经过白名单与路径校验。
- **容器隔离**：非 root 用户、无网络、只读根文件系统、能力全部删除、PID/内存/CPU 限额。
- **可恢复任务**：Checkpoint、事件流、幂等键、暂停/恢复/取消和 Worker 重启恢复。
- **多角色协作**：Planner、Repository、Developer、Tester、Reviewer 各自拥有最小职责和权限。
- **可观测控制台**：FastAPI 提供任务 API、SSE 事件流、Prometheus 指标和受控 Artifact 下载；Next.js 提供本地控制台。
- **模型可替换**：支持 OpenAI 兼容接口；本地 vLLM 路径仅在需要本地 GPU 推理时启用。

## 系统结构

```text
Next.js 控制台
      │  HTTP / SSE
      ▼
FastAPI API ── 任务状态、事件、指标、Artifact
      │
      ▼
LangGraph Agent ── 规划 / 检索 / 修改 / 测试 / 审查
      │
      ▼
受限工具层 ── Git 补丁、MCP、测试配置
      │
      ▼
Docker 沙箱网关 ── 无网络、只读、资源限额
```

运行时可以使用 SQLite + 内存队列进行本地演示，也可以按配置切换到 PostgreSQL + Redis。

## 验证状态

- WSL2 Docker Compose 已验证 PostgreSQL、Redis、API 与 Web 服务健康；
- 本地控制台已验证创建、暂停、恢复、批准、取消与 SSE 审计事件；
- 完整修复评测仅在本地私有评测包中运行，公开仓库不包含隐藏测试、参考补丁或生成器。

公开仓库展示的是可复现的工程结构与安全边界；真实模型评测和完整基准答案均单独保留在本地，不作为公开能力分数。

## 快速运行

### 本地控制面（WSL2 / Docker）

```bash
cp .env.example .env
docker compose --env-file .env -f deploy/docker-compose.yml up --build -d
```

验证地址：

- 控制台：`http://localhost:3000`
- API 健康检查：`http://127.0.0.1:8080/healthz`
- 指标：`http://127.0.0.1:8080/metrics`

沙箱网关需要运行在 WSL2 主机上，并且只向 Docker bridge 暴露，不能绑定到公网地址。

### 开发检查

```bash
python -m pytest
python -m ruff check .
python -m mypy repoevo
```

## 算力模式

- **无卡模式**：API、前端、Docker Compose、沙箱、测试、MCP、队列、评测流程和控制台演示。
- **正常 GPU 模式**：仅在启动本地 vLLM 或进行真实本地模型推理时使用。

## 目录说明

```text
apps/          FastAPI 与 Next.js 控制台
repoevo/       Agent、运行时、工具层与安全逻辑
mcp_servers/   受限 MCP 工具服务
deploy/        Docker Compose、沙箱与 vLLM 配置
benchmarks/    公开任务清单与私有评测入口
fixtures/      教学用 Python 仓库
scripts/       构建、验证、评测与 Worker 脚本
```

## 当前边界

- 本仓库已完成本地 Compose 控制面、隔离沙箱和确定性工程流程验证；
- 真实模型指标不与确定性基线混用；
- 隐藏测试、参考补丁和基准生成器仅保留在本地私有评测包中；
- 任何将 API 或沙箱暴露到公网的部署，都必须补充认证、反向代理和网络隔离。
