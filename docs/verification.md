# v0.1.0 验收记录

本文记录公开版本的可复现检查命令和实际结果。它不包含私有评测任务、隐藏测试、参考补丁、密钥或实例连接信息。

## 公开质量门禁

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

### 2026-08-09 实际结果

- Windows 开发环境：`pytest` 通过 73 项、因未授予创建符号链接权限跳过 2 项安全测试；Ruff、格式检查与 Mypy 全部通过。
- Linux Python 3.12 隔离容器：`pytest` 通过 75 项；仅有 FastAPI/Starlette 和 Authlib 的第三方弃用警告。
- WSL2 Linux Node 容器：`npm ci && npm run build` 成功，Next.js 生成 4 个静态页面。
- `uv.lock` 与 `apps/web/package-lock.json` 已提交；GitHub Actions 在 Linux 中执行同一组公开质量门禁。

## 私有评测包验证

完整评测包不进入公开仓库。发布前在本地私有包中完成了 58 个任务的结构和行为验证：58 个注入缺陷均被测试捕获，58 个参考修复均通过。该结果用于确认评测包自洽，不应宣传为真实 LLM 能力分数。

## Docker Compose 验收

```bash
cp .env.example .env
# 在 .env 中设置仅本地使用的 POSTGRES_PASSWORD
docker compose --env-file .env -f deploy/docker-compose.yml up --build -d
curl http://127.0.0.1:8080/healthz
curl -I http://127.0.0.1:3000/
```

控制台地址为 `http://localhost:3000`，API 健康检查为 `http://127.0.0.1:8080/healthz`，指标为 `http://127.0.0.1:8080/metrics`。

2026-08-09 的干净 Compose 验收使用独立数据卷完成：PostgreSQL、Redis、API 与 Web 均健康；API 创建、暂停、恢复、取消和 SSE 事件流均返回成功。一次性 Worker 在临时公开教学仓库中实际领取任务、应用代码补丁、通过受限沙箱测试并由 Reviewer 审核为 `completed`。

Worker 镜像显式使用项目虚拟环境 Python，并安装 Git 以支持受限的补丁/差异工作流。

## 沙箱边界

沙箱网关和受限 Python 镜像均位于 `deploy/`。启动和 WSL2 原生 Docker 的 bridge 配置见 [本地沙箱说明](local_sandbox.md)。探针必须确认：非 root 用户、禁网、只读根文件系统、无有效 Linux capabilities、`no_new_privileges`、PID/内存/CPU 限额和超时限制。

## 评测与模型口径

公开仓库展示安全执行、可恢复任务、多角色流程和本地控制面。真实模型推理或完整修复评测只能在本地私有评测包中运行；公开仓库不含答案、隐藏测试、参考补丁或真实模型成功率。因此不能将确定性离线流程的结果表述为真实 LLM 效果。
