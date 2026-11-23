# S07 本地工程基线与实施边界

2026-11-23 规划预检现场快照。GPT 独立 Plan Review 结论为 **APPROVED WITH CONDITIONS**，修订稿待条件落实复核及用户实施授权。下列 Git、环境与历史收据检查均归属原预检。[遗留台账](S07_LEGACY_LEDGER.md) · [执行计划](../plans/S07_IMPLEMENTATION_PLAN.md)

## 1. 需求、版本与工作区

先核对仓库 `AGENTS.md`、V3.0 和总体实施方案，再从阶段 Plan、验收、机器索引回到实现。V3.0 SHA-256 `4373fca30fd71061c4e795ebd02b73190e39d451dd7830c0dc5bf01e58379bf6`，总体实施方案 `2107ff37d0bb00f3f1d393c4f81a028bdac746745cfccc50c637bb1afe2e81a6`。前者 §5–10、§12–14 管架构、领域、预算与准入，后者 §5–10 管阶段、三项重点 P1 和完成状态。本轮补充宿主机启动与后端历史闭环，通过 S07 才能启动 S08。[总体设计](../design/城市空气质量监测数据分析平台_系统总体设计方案_V3.0.md) · [总体实施方案](../design/总体实施方案.md)

| 只读检查 | 2026-11-23 现场结果 |
|---|---|
| `git status --porcelain=v2 --branch --untracked-files=all`、`git diff`、`git diff --cached` | 起点 `main`，HEAD 可达 40 个提交、285 个已跟踪路径，暂存 0、已跟踪未暂存 0、非忽略的未跟踪 0。无已见用户修改需合并。终点新增本轮三份规划稿，均未暂存。 |
| `git rev-parse HEAD`、`git for-each-ref`、`git ls-remote --exit-code origin refs/heads/main` | 本地 HEAD、上游 `origin/main`、远端实查 `main` 和用户给定参考完全相同，`8de867853c470393617bc7ee14bb692a97fe8c5c`，ahead/behind 0/0。未 fetch，无引用变更。 |
| `git diff 079ba2a..HEAD --name-only`、`scripts/s01_runtime.py:source_hash()` | S06 工程至 HEAD 仅文档变更。当前工作区复算 S06 工程指纹 `598e00c2db1d39918b2e5eecb64f35956de26c38c4ebed944401c34caa66c632`。该函数覆盖七个根配置及 backend/tests/scripts/docker，未涵盖 CNEMC overlays 或拟增根入口。S07 需扩大证据清单。 |
| 忽略区 | `.env` 存在，权限 `0600`，只查文件属性。`.venv`、`.tasks/{S04,S05,S06,cnemc-integration}`、`tmp/`、缓存均为本地资产。Git 干净不代表不存在私有数据。 |

### 已提交阶段与资产定位

以下 SHA 为可在本仓库解析的提交。`docs/plans/S0n_IMPLEMENTATION_PLAN.md` 与 `docs/verification/S0n.md` 按同号对应。报告中的历史 PASS 只属于相应基线、输入和范围。

| 阶段 | 工程与封版提交 | 现有验证入口、收据与本轮取证深度 |
|---|---|---|
| S01 | `0656071` → `07711ee` | `verify_s01.py`、`s01_runtime.py`、`tests/test_accounts.py` / `test_persistence.py`。`tmp/s01-evidence/{s01v89c9ad22bced,s01va391b5974faf}` 两个独立项目各 228 检查、42 tests。报告与结果文件在位。 |
| S02 | `ca8b3a2` → `d7c5d8b` | `verify_s02.py`、历史/处置检查、`tests/test_observation_*` / `test_history_*`。`tmp/s02-evidence/s01v953a31d36d6c` 253 项，三方 H 及隔离 PG。S02 C01–C03 与后续派生物分期核验。 |
| S03 | `3f1faa0`、`32abe4a` → `bd955c1`、`746049b` → `b8c30ae` | `verify_s03.py`、`verify_s03_products.py`、`scripts/s03_compose.yaml`、`tests/collection/`。`S03_evidence.json` 有首轮及 remediation。`tmp/s03-evidence` 和 `tmp/s03-f01-f02` 均在。 |
| CNEMC 独立支线 | `6395e39`、`ee876ae`、`1e302cd`、`3324d48` → `f02c7bb` | 先看 `CNEMC_PRE_S04_AUDIT.md`、`CNEMC_PLAN_REVIEW.md`、`CNEMC_IMPLEMENTATION_ACCEPTANCE.md`，按日期读 `CNEMC_FIRST_REAL_COLLECTION_ACCEPTANCE.md` 顶部最终结果。`docs/design/CNEMC_ENDPOINT_MAPPING.md`、`docs/operations/CNEMC.md`、`verify_cnemc{,_egress}.py` 记录产品和网络边界。私有探针、包与样本仅核存在性。 |
| S04 | `acb76ed` 起，`02958fc` 至 `1617c2c`、`9854811` → `f02a549` | `verify_s04.py`、`s04_checks.py`、`tests/test_s04_*`、`S04_evidence.json` 和 `tmp/s04-evidence`，`docs/operations/S04.md`。 |
| S05 | `9b04e74`、`323a453`、`90e61e0`、`c50667f`、`270fa8f`、`a5ced8a` → `be66812` | `verify_s05.py`、`s05_v16*.py`、`tests/test_s05_*`、`tests/s05_{http,volume}.py`。`S05_evidence.json`、`tmp/s05-evidence`，`docs/operations/S05.md`。V16 的真实 H 研究授权有效至 **2026-11-22 18:06:40.223343 UTC**。原件在场、许可证名称或旧票据均不能直接开启新一轮操作。 |
| S06 | `079ba2a` → `dd2304a` → `8de8678` | `verify_s06.py` 及 `s06_{guard,sandbox,collection_runtime,restore_verify,upgrade,evidence_index}.py`、`tests/test_s06_*`。`S06_evidence.args`、`S06_evidence.json`、`S06_reviews/l3-{review,recheck}.md`、运维手册及 `tmp/s06-evidence/`。详见下文只读复算。 |

脚本短名均在 `scripts/`。本地 `tmp/` 含 S01、S02、S03（首版及 F01/F02）、S04、S05（工程及 V16）共 7 个审查 ZIP。只读专项核对 S01–S04 五包 CRC，其中四包的 `SHA256SUMS` 分别匹配 59、140、155、204 项。S02 原包及 S05 V16 包的整体摘要与各自验收报告相符。按仓内约定位置未见 `S06_ACCEPTANCE_PACKAGE.zip`。包内私有内容、S06 包的外部副本及旧静态日志的真实性均未据此背书。历史复核材料仅用作判定依据，不复制成分发夹具。

## 2. 运行时实况与隔离约束

在 Linux x86_64 / WSL2 核对本机命令、`.venv` 包元数据、`uv.lock` 和本地镜像配置。镜像版本是镜像元数据，数据库实际迁移状态和服务健康需在获准环境单独检查。

| 层 | 当前值与路径 | 对 S07 的影响 |
|---|---|---|
| 工具与包 | 宿主 Python 与 `.venv` 都是 3.13.18，uv 0.13.6。Django 5.2.19，DRF 3.18.3，Celery 5.7.0，django-celery-beat 2.9.0，psycopg 3.3.9，redis-py 8.1.0，HTTPX 0.28.1。pytest 9.2.0/pytest-django 4.14.0、Ruff 0.17.6、mypy 2.4.1 见锁。`pyproject.toml` 将 mypy 限定于三份纯规则文件。 | 锁内无 Gunicorn、完整 OpenAPI 工具、Pandas 或 NumPy。现有统计走 PG/SQL，后两者不是启动依赖。 |
| 容器 | Docker client/engine 29.10.0，Compose 5.8.0。PG 镜像 16.16，Redis 服务镜像 7.4.12，tag 与 digest 固定于 `compose.yaml`。本机 `redis-server`/`redis-cli` 7.0.15，PATH 未见 `psql`，有 bwrap、openssl。 | 本机命令与容器版本分别入证。`Dockerfile` 固定 uv、Python 基镜像，`uv sync --locked`，非 root UID 10001。已有应用镜像 tag 不证明含当前源码。 |
| 迁移 | 项目迁移文件 accounts 6、collection 9、observations 17、samples 2。叶分别为 `0006_worker_subscription`、`0009_control_versions`、`0017_backup_preparation_dependencies`、`0002_local_grants`。S06 最终隔离收据另含 sessions `0001`、auth `0012`、admin `0003`、contenttypes `0002`、django_celery_beat `0019`。 | S06 历史升级收据从 S05 应用 14 项，二次待迁移 0。共享库叶状态本轮 NOT RUN。 |
| 启动配置 | `.env.example` 给出三个待本地设置的密钥类变量及 DB 名、用户、主机白名单、Web 端口、Cookie、CNEMC。`manage.py` 仅选择 Django settings。`settings.py` 要求 DB 名/用户/口令与 Django secret。PG `POSTGRES_HOST` 默认 `db`，`POSTGRES_PORT=5432`，Broker 写死 `redis://redis:6379/0`。 | `.env` 由 Compose 注入，宿主 Python 不会自动照搬。启动恢复会写业务数据。测试 settings 的 Unix socket 和私有路径仅属专用运行器。 |
| 服务与网络 | base 的 migrate 完毕才开 Web。Web 是 `runserver --noreload`，回环发布 8000、连 front/backend。PG 只在内部网挂 `pgdata`，Redis 内网、tmpfs 且关闭持久化，两者都没有宿主端口。Worker 单并发、三队列，先 `s02_dispose_due` 再 `s03_reconcile_due`，后由 Celery 消费。Beat 独立，用 `config.scheduler:ObservedScheduler` 在 PG 写 tick。 | Web readiness 只测 PG，Worker 订阅及成功消费、Beat 近期 tick 来自 `config/pulses.py` / `accounts/operations.py`。目前无跨两种启动方式的 Beat 排他凭证。`runserver`、进程存在和 Redis PING 各自只能证明有限事实。 |
| 存储与出口 | Worker 的 `S02_STAGE_DIR=/run/s02-history` 为私有 tmpfs，32 MiB、0700。`S06_BACKUP_DIR`、`S06_AUTHORITY_DIR` 可选且后者隔离，卷代次、私有属主和清单决定可用性。`compose.cnemc.yaml` 有专用 Worker 与网关，普通 Worker 缩为 0。`.once.yaml` 限单次、Beat 0。base 默认 `CNEMC_MODE=off`。 | 独立模式的网络白名单和授权不能通过只改一个环境变量移植到宿主。宿主路线见计划，仅提供离线业务。 |

**Docker 只读盘点**（`docker ps -a` 选择项目/服务/状态/端口，`docker volume/network ls` 选择项目标签，未读业务表或容器日志）。

| 标记项目 | 当前可见现场 | 归属判断 |
|---|---|---|
| `air-quality-platform` | Web Exited (255)，db/redis/migrate/gateway Exited (0)，worker-cnemc Created。Web 曾绑定 `127.0.0.1:8000`。`air-quality-platform_pgdata`、backend/front/egress/uplink 网络在。 | S06 运维手册记录的共享业务环境，有真实 CNEMC 和待处置义务。以停机保护处理。卷内现在的行数、权限、待办数未知，由数据保管人另行核实。 |
| `cnemc-a03-once` | 专用 worker Created，其余已见组件 Exited，卷 `cnemc-a03-once_pgdata` 及网络仍在。 | 独立实源探针遗留。专用也不是 S07 的可销毁资源。 |
| `s03v3a85580b59ca` | 两个 Worker、Beat、origin、Web、db、redis、migrate 均已退出。项目卷及网络仍在。 | 历史测试资源，未获本轮处理权。不能凭名称或已停机判定可回收。 |

未启动或进入上述容器，未读 `.env` 值、私有签名、源回复或用户数据。项目标签下未见运行中 Beat，只能判定本次 Docker 快照。共享数据库内容和宿主进程全局唯一性没有在线核验。实施应新建有随机运行标记、独占 PG/角色/卷、Redis、网络及回环端口的环境，显式排除以上三项目。详情见 [S06 运维手册](../operations/S06.md)。

## 3. 本轮只读结果与实施前提

| 检查 | 实得 | 适用范围 |
|---|---|---|
| 复算四份已提交 S03–S06 JSON 索引可解析。用 `python -B scripts/s06_evidence_index.py @docs/verification/S06_evidence.args` 输出经 `cmp` 比对已提交文件 | **PASS**。S06 15 次隔离运行的脱敏重建逐字节相同，SHA-256 `a07570928efb760ba21d7f518938bc722535a1da12de4bcd99bccb43055c764b`。 | 核索引与原始收据对应，不是新执行 15 次。 |
| 原始节点集合交叉 | **PASS**。`s06-8bb305073ef4` 544 PASS、33 NOT RUN。`s06-5153d698b193`、`s06-26f3fb56609e` 各自 33 PASS，两个集合分别与前者 33 个未运行节点完全相同，去重 577。`s06-a8f813ec1eac` 为 543 PASS、1 FAIL、33 NOT RUN，单节点 `s06-d023852110d6` 为 1 PASS。 | S06 同指纹 F/I。首个 CSV 非 201 的实际状态码未知。 |
| 实际 URL resolver 对计划 §3.1 | **PASS**。用 `.venv` 中的 Django 加载当前 URLConf，在数据库连接、网络访问与文件写入均被拦截的独立解释器内展开回调。**44 个 v1 路径、59 个显式方法**逐行与计划相符。 | 本次仅核路由及方法，Session HTTP、序列化与错误仍须 V12 实测。 |
| 当前技术链 | **静态确认**。Django/PG/队列/采集账本/查询/处置/恢复均有代码、测试和历史证据。北京 A03 最终报告记录一次正式技术链、七项同批 Session、PG 重启回读。此前 16:08 的连通探针单列。此次现场测试以操作者的限定声明为依据。 | 准入仍依 V3.0 §12.4。未取得本轮真实 H 条款，D 的业务启用、地图权利、城市跨期及 T11 分项保留。 |
| 本轮业务运行、迁移/处置、实源请求、T09、离线冷启、S07 契约实测 | **NOT RUN**。规划授权限于只读。 | 正式计划分配环境、用例与证据。不得以历史 PASS 或依赖已安装替代这些检查。 |

规划稿已对账 41 条台账、10 项实施任务、17 组验收、T01–T12 及文档内相对链接。本机足以制定可执行计划。三项技术决策与 C01–C04 的判据见执行计划的 Review Decisions / Conditions。GPT 复核条件落实、用户授权实施后，须先通过可销毁隔离环境的归属检查，方可推进 F/I 验证。真实 H 离线查询及真实研究仅能在当前用途核准后验收，受限项未完成时按计划的退出条件报告。

### 审查资料入口

| 阶段 | 实施依据与工程结果 | 机器索引或运维边界 |
|---|---|---|
| S01 | [计划](../plans/S01_IMPLEMENTATION_PLAN.md) · [验收](S01.md) | 两套隔离 Compose 原件定位见上表 |
| S02 | [计划及附加条件](../plans/S02_IMPLEMENTATION_PLAN.md) · [验收](S02.md) | H 和处置矩阵、收据范围见报告 |
| S03 | [计划及 C01–C04](../plans/S03_IMPLEMENTATION_PLAN.md) · [验收与返修](S03.md) | [机器索引](S03_evidence.json) |
| CNEMC | [专项审计](CNEMC_PRE_S04_AUDIT.md) · [Plan Review](CNEMC_PLAN_REVIEW.md) · [工程验收](CNEMC_IMPLEMENTATION_ACCEPTANCE.md) | [北京 A03 历史现场](CNEMC_FIRST_REAL_COLLECTION_ACCEPTANCE.md) · [运维手册](../operations/CNEMC.md) |
| S04 | [计划](../plans/S04_IMPLEMENTATION_PLAN.md) · [验收](S04.md) | [索引](S04_evidence.json) · [运维手册](../operations/S04.md) |
| S05 | [计划](../plans/S05_IMPLEMENTATION_PLAN.md) · [验收与 H 期限](S05.md) | [索引](S05_evidence.json) · [运维手册](../operations/S05.md) |
| S06 | [计划](../plans/S06_IMPLEMENTATION_PLAN.md) · [验收](S06.md) · [初审](S06_reviews/l3-review.md) · [返修复核](S06_reviews/l3-recheck.md) | [索引](S06_evidence.json) · [选择器](S06_evidence.args) · [运维手册](../operations/S06.md) |
