# 本地沙箱网关

RepoEvo 的 API 不直接执行模型生成的 Shell 命令。它只向本地网关发送受大小限制的文本文件和白名单测试命令；网关再用一次性 Docker 容器运行测试。

## 启动

在 WSL2 或 Linux 中、项目根目录执行：

```bash
docker build -t repoevo-sandbox:py311 -f deploy/Dockerfile.sandbox .
python3 deploy/sandbox_gateway.py
```

默认只监听 `127.0.0.1:19090`。另开一个终端验证：

```bash
curl http://127.0.0.1:19090/healthz
curl -X POST http://127.0.0.1:19090/v1/sandbox/probe
```

探针必须显示：`uid=10001`、`network=BLOCKED`、`rootfs=READ_ONLY`、`CapEff` 全为零，以及 `NoNewPrivs: 1`。

## 与 Compose 连接

Docker Desktop 通常可在 `.env` 中使用：

```dotenv
REPOEVO_SANDBOX_URL=http://host.docker.internal:19090
```

若 Docker Engine 运行在 WSL2 内部，容器不能通过 `host.docker.internal` 访问 WSL 的 `127.0.0.1`。先启动 Compose（不启动 worker），再让网关只绑定该 Compose bridge 的宿主地址：

```bash
export COMPOSE_PROJECT_NAME=repoevo
docker compose --env-file .env -f deploy/docker-compose.yml up -d
BRIDGE_GATEWAY="$(docker network inspect "${COMPOSE_PROJECT_NAME}_default" --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}')"
REPOEVO_SANDBOX_HOST="$BRIDGE_GATEWAY" python3 deploy/sandbox_gateway.py
```

将 `.env` 的 `REPOEVO_SANDBOX_URL` 改为 `http://<BRIDGE_GATEWAY>:19090`，然后重建 API 环境变量：

```bash
docker compose --env-file .env -f deploy/docker-compose.yml up -d --force-recreate api
```

不要将网关绑定到 `0.0.0.0`，也不要把 19090 映射到公网。桥接地址只供同一台机器上对应的 Compose 网络访问。
