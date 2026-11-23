# S07 交付包校验与独立重建

## 1. 从候选提交生成交付包

在项目根目录用 Python 3.13 指定待审 Git 提交的完整 SHA。生成器读取该提交的普通 Git blob，按源码、配置、锁、测试、F 样例及具名公开文档白名单组装 ZIP。`.git`、私有归档、原始收据、真实 H 和本机密钥保存在各自受控位置。提交者在生成前核定列入白名单的文档与证据摘要具备分发权。

```sh
python3.13 -B scripts/s07_handoff_checks.py
python3.13 -B scripts/s07_handoff.py build --commit "$(git rev-parse HEAD)"
```

构建输出位于 `tmp/s07-handoff/<commit>-<随机后缀>/`。`S07.zip` 内的 `S07_HANDOFF_MANIFEST.json` 记录提交、成员路径、大小、SHA-256 与 Git 模式。相邻的 `verification.json` 记录 ZIP 和清单摘要，用于向复核者登记预期值。签收时对照从可信渠道取得的提交和摘要。

## 2. 校验并提取

将 `AQ_S07_ZIP` 指向收到的 ZIP 的绝对路径。在可信的源工具处核对 CRC、成员集合、文件模式和全部内容摘要，再提取到短路径下的新目录。工具拒绝重复成员、符号链接、越界路径和已有目标目录，并复算提取后源码摘要。短路径便于 B 的 PostgreSQL 和 Redis 使用 Unix socket。

```sh
AQ_S07_CLEAN="$(mktemp -d /tmp/s07-clean.XXXXXXXX)"
python3.13 -B scripts/s07_handoff.py verify "$AQ_S07_ZIP"
python3.13 -B scripts/s07_handoff.py extract "$AQ_S07_ZIP" "$AQ_S07_CLEAN/app"
cd "$AQ_S07_CLEAN/app"
uv sync --locked --offline
```

A 和 B 在这个新目录通过提取后的清单核对候选提交和源码。`.venv`、运行收据和测试生成文件在候选源集合之外。复核者将这些运行收据放在本轮的 `tmp/s07-evidence`，另行登记允许分发的摘要。

## 3. 准备离线依赖与镜像

在同架构 Linux 或 WSL2 上先准备锁定的 Python 和依赖 wheel。在新提取目录用空的专用 UV 缓存运行 `UV_CACHE_DIR="$AQ_S07_CACHE" uv sync --locked`。断网前保留这一目录和匹配的 Python 3.13，复核时用同一缓存执行 `UV_CACHE_DIR="$AQ_S07_CACHE" uv sync --locked --offline`。用专用缓存收集本候选依赖。

按 `Dockerfile`、`compose.yaml` 和 `compose.host-infra.yaml` 准备 uv、Python、PostgreSQL 和 Redis 的 digest 引用。离线主机导入允许分发的镜像时通过 `docker load` 后逐项用完整 digest 核对可解析的镜像及目标架构。应用层先在已核验的独立 Compose 实例构建，并保留同一构建器的锁定依赖层和明确记录的应用镜像 ID。运行 V13 的机器需能按下面的入口从候选 Dockerfile 重建，V14 在所用镜像和依赖准备好后验证断网运行。

## 4. 由独立复核者运行 A 与 B

在提取的目录且仅在 V01 自动守卫可用的本地独立环境中运行。每个入口申请自有资源，核对空库与标签后执行业务测试，完成后清理本轮资源。A 和 B 顺序运行，性能测试时保留其独占资源条件。

```sh
.venv/bin/python -B scripts/verify_s07_host.py startup
.venv/bin/python -B scripts/verify_s07.py startup
.venv/bin/python -B scripts/verify_s07.py flows
.venv/bin/python -B scripts/verify_s07_host.py contract
.venv/bin/python -B scripts/verify_s07_host.py offline
.venv/bin/python -B scripts/verify_s07.py offline
.venv/bin/python -B scripts/verify_s07.py performance reverse
```

- B 的 `startup` 验证实际 `python main.py`、独立进程、拒绝场景与就绪恢复。日常启动、人工迁移和分组件恢复遵循宿主运维手册。 [S07 宿主启动与运维](S07.md)
- A 的 `flows` 用新会话走普通用户、研究和管理员三链，`contract` 的 B 实测覆盖管理员、真实 Worker 和 Redis。正例和拒绝均以实际 HTTP 与 PG 收据核对。 [v1 接口说明](../api/v1.md)
- 正式 WSGI 文件按 `compose.yaml`、`compose.offline.yaml` 排序，V01 再叠加 `scripts/s07_isolation.yaml`。核对 `performance/configuration.json` 的配置及指纹、依赖与镜像、实际 PID 1 参数及 Gunicorn 单 worker、gthread 和 8 线程。按 T09 对照百万小时键、五个真实登录会话、全四组 P95 和内容对账。 [T09 性能报告](../verification/S07_T09.md)
- 原有 S06/R 跨版测试需要本地冻结 Git 对象。以相同候选提交的干净 Git 副本执行相关升级入口，并按 S07 总报告复算专用证据。 [S07 验收记录](../verification/S07.md)

有权读取本轮原始收据的复核者可以在验收工作区逐字节复算 66 个 OpenAPI 操作的脱敏矩阵。原始收据位于该工作区的 `tmp`。 [V12 操作矩阵](../verification/S07_V12.json)

```sh
python3.13 -B scripts/s07_v12_index.py | cmp docs/verification/S07_V12.json -
```

独立报告记录实际提交、ZIP 摘要、成员复核、两入口的结果、配置指纹及热测成绩。按真实收据登记 PASS / FAIL / BLOCKED / NOT RUN，列出发现编号、复验位置及外部项。本轮真实 H 按现行授权单独核定。T11 继续 NO-GO，S07 封版及 S08 启动以 GPT 的独立阶段验收为前置条件。
