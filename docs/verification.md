# v0.1.0 验收记录

本文件记录公开版本的可复现验收命令与结果。它不包含私有评测任务、隐藏测试、参考补丁、密钥或实例连接信息。

## 本地质量门禁

在项目根目录执行：

```bash
uv sync --all-groups --frozen
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy repoevo
```

前端构建：

```bash
cd apps/web
npm ci
npm run build
```

### 2026-08-05 本机结果

- `uv sync --all-groups --frozen`：通过。
- `uv run pytest`：73 通过、2 跳过。跳过项只在当前 Windows 未授予创建符号链接权限时触发；Linux CI 会实际执行这两项安全测试。
- `uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy repoevo`：均通过。
- 前端 `npm ci && npm run build`：作为 CI 发布门禁保留，发布前必须在 Linux 环境中通过，不以本机缓存结果替代。

## Docker Compose 验收

```bash
cp .env.example .env
docker compose --env-file .env -f deploy/docker-compose.yml up --build -d
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:19090/healthz
curl -X POST http://127.0.0.1:19090/v1/sandbox/probe
```

控制台地址为 `http://localhost:3000`。演示时应验证任务创建、暂停、恢复、取消、SSE 事件流和 Worker 完成公开示例任务。

## 隔离边界

沙箱探针应确认：非 root 用户、无网络、只读根文件系统、无有效 Linux capabilities、`no_new_privileges`、进程/内存/CPU 限额和超时限制。

## 评测边界

真实模型与完整修复评测只能在本地私有评测包中运行。公开仓库只公开评测入口和任务清单，不公开答案、隐藏测试或真实模型分数。
