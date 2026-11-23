# S07 交付包校验与独立重建

S07 已 `CLOSED / PASS`。本地 annotated tag `s07-backend-v1` 标记最终文档封版。归档验收使用原软件包与 H 补充包，两个包仍按各自的历史提交和签收摘要核对。提交、摘要、签收记录和保留边界见 [S07 封版报告](../verification/S07.md#受审版本)。

## 1. 从候选提交生成交付包

在项目根目录用 Python 3.13 指定待审 Git 提交的完整 SHA。生成器读取该提交的普通 Git blob，按源码、配置、锁、测试、F 样例及具名公开文档白名单组装 ZIP。`.git`、私有归档、原始收据、真实 H 和本机密钥保存在各自受控位置。提交者在生成前核定列入白名单的文档与证据摘要具备分发权。 `README`、S07 运维、v1 API 和本轮验收步骤的相对链接在包内可用。`S07_PRECHECK.md` 保留指向完整仓库旧阶段材料的历史链接，总体实施方案保留指向原设计审查材料的引用。

```sh
python3.13 -B scripts/s07_handoff_checks.py
python3.13 -B scripts/s07_handoff.py build --commit "$(git rev-parse HEAD)"
```

构建输出位于 `tmp/s07-handoff/<commit>-<随机后缀>/`。`S07.zip` 内的 `S07_HANDOFF_MANIFEST.json` 记录提交、成员路径、大小、SHA-256 与 Git 模式。相邻的 `verification.json` 记录 ZIP 和清单摘要，用于向复核者登记预期值。签收时对照从可信渠道取得的提交和摘要。

正式测量后，性能报告、收据索引和独立复核报告随一次文档提交进入交付包。验收人从 `S07_HANDOFF_MANIFEST.json` 取得报告版提交，从 `docs/verification/S07_T09.json` 的 `modes.heat.extraction` 取得实测提交和包摘要。两者的全部运行文件、模式与字节在包外签收时逐项核对，最终 ZIP 的摘要随包单独交付。在含对应 Git 对象的完整仓库中可用 `git diff --name-status <实测提交> <交付提交>` 核对文档变更。

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

复验四入口 Beat 竞争时使用 `singleton-compose`。B 运行器给公开源码制作临时镜像快照。一次 `umask 077` 包装运行在基础 Beat 获取 PG 锁前超时。以下执行条件通过了四入口竞争、Worker 消费和窗口对账。该 subshell 只为本次验证设置文件创建掩码。实例根目录仍为 `0700`，宿主私有配置仍为 `0600`。

```sh
(umask 022 && .venv/bin/python -B scripts/verify_s07_host.py singleton-compose)
```

- B 的 `startup` 验证实际 `python main.py`、独立进程、拒绝场景与就绪恢复。日常启动、人工迁移和分组件恢复遵循宿主运维手册。 [S07 宿主启动与运维](S07.md)
- A 的 `flows` 用新会话走普通用户、研究和管理员三链，`contract` 的 B 实测覆盖管理员、真实 Worker 和 Redis。正例和拒绝均以实际 HTTP 与 PG 收据核对。 [v1 接口说明](../api/v1.md)
- 正式 WSGI 文件按 `compose.yaml`、`compose.offline.yaml` 排序，V01 再叠加 `scripts/s07_isolation.yaml`。核对 `performance/configuration.json` 的配置及指纹、依赖与镜像、实际 PID 1 参数及 Gunicorn 单 worker、gthread 和 8 线程。按 T09 对照百万小时键、五个真实登录会话、全四组 P95 和内容对账。 [T09 性能报告](../verification/S07_T09.md)
- 原有 S06/R 跨版测试需要本地冻结 Git 对象。以相同候选提交的干净 Git 副本执行相关升级入口，并按 S07 总报告复算专用证据。 [S07 验收记录](../verification/S07.md)

有权读取本轮原始收据的复核者可以在验收工作区逐字节复算 66 个 OpenAPI 操作的脱敏矩阵。原始收据位于该工作区的 `tmp`。 [V12 操作矩阵](../verification/S07_V12.json)

```sh
python3.13 -B scripts/s07_v12_index.py | cmp docs/verification/S07_V12.json -
```

独立报告记录实际提交、ZIP 摘要、成员复核、两入口的结果、配置指纹及热测成绩。按真实收据登记 PASS / FAIL / BLOCKED / NOT RUN，列出发现编号、复验位置及外部项。真实 H 补充包须与原包一同核对，见[历史送审说明与校验命令](../verification/S07_H/REVIEW_HANDOFF.md)。GPT 已签收 S07 后端。T11 继续 NO-GO，浏览器、真实地图与整机恢复保留各自的验收责任。

## 受控备份和隔离恢复

备份操作者须有 `accounts.manage_backups`，权利保管人须由包外信任配置指定并有 `observations.manage_grants`。操作者在每个段的产品、地区、对象、指标、日期和字段范围内核 `backup` 及 `retain_normalized`，恢复时再核 `restore`。应用使用自有 `S06_BACKUP_DIR`，只读访问 `S06_AUTHORITY_DIR`。保管人另行持有现行条款、可信锚、主体映射、签发记录及离线私钥。原段、签认及身份凭证留在各自受控介质上。

1. 在已验证归属的实例中，操作者用已登录会话和 CSRF 向 `/api/v1/admin/backup-products/<product_id>/segments/` POST `{"scope":{"geography":"F-city","object_code":"001","indicator":"CO","start":"2024-01-01","end":"2024-01-02"}}`。`201` 返回 `operation_id`，准备中的段尚不可读取。公开 F 样例使用规范化数据。
2. 保管人用独立 Session 读 `/api/v1/admin/backup-authority/publish/<segment_id>/`，对照本次操作、范围、权利链和当前时刻。保管人通过 `scripts/s06_restore_sign.py` 在受控目录离线签发限时证明。原操作者在有效期内向 `/api/v1/admin/backup-segments/<segment_id>/publish/` POST `{}`，核 `200`、`available`、分段清册、依赖及发布标记。
3. 恢复须使用新建的、单独标记的空目标库。持 `accounts.restore_backup` 的操作者使用自己的有效 Session，向 `/api/v1/admin/backup-segments/<segment_id>/restore-intents/` POST `{"target":"<本次空目标 UUID>"}`。`201` 返回短期 `operation_id`。保管人从 `/api/v1/admin/backup-authority/restore/<operation_id>/` 复核并为本次意图签认。在守卫核准的环境执行 `python backend/manage.py s06_restore "$S06_RESTORE_OPERATION"`。命令核目标、主体、原 Session、当前授权和段依赖，意图仅可消费一次。
4. 现行撤权先提交时，一次性读取被拒。合法读取先持锁时，权威事务将锁持至回环正文写完，撤权随后生效。最后核权或写入失败时检查原意图是否已消费和目标库是否回滚。仍有资格的操作者用空目标及新意图重试。留存或备份权中断、发布缺标记、证明过期或段依赖失配时，按 PG 的处置待办定位原卷与副本，核实删除后再核销。仅丧失展示权按受影响展示与导出失效处理，按各自仍有效的依据核留存和备份。其他合法独立段继续单独核权。

可从包内运行下列专项。运行器在 R 的隔离边界内创建 F 保管材料和空目标，验证真实 Session、PG 提交顺序、撤权拒绝、恢复清空及本轮资源清理。机器收据在 `tmp/s06-evidence/<run>/`。本次 UCI H 的依据、受控恢复及再次运行的前提见 [H 权利记录](../verification/S07_H/RIGHTS.md)。

```sh
.venv/bin/python -B scripts/verify_s07_host.py restore
```
