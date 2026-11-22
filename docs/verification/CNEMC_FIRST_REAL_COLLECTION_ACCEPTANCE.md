# CNEMC 首次真实数据采集验收报告

## 最新验收结论：北京 A03 单次真实链路 PASS（2026-11-22）

本次依据项目操作者对一次本地技术测试及规范化值留存的声明，只执行一份 `cnemc / city-live-hour`、北京市 `110000`、A03 人工请求；未登记或声称 CNEMC 出具的许可文件、条款号或书面版本证明。

| 检查 | 实际结果及证据 |
|---|---|
| CNEMC 真实请求与解析 | **PASS**。2026-11-22 17:30:19（北京时间）由专用 Celery Worker 经 HAProxy/HTTPX 请求 A03；适配器成功路径仅接受 HTTP 200。读取目标为 `2026-11-22T17:00:00+08:00`，解析北京市一个 JSON 对象和 AQI、PM2.5、PM10、SO2、NO2、CO、O3 七项。适配器关闭重试；没有发送第二次请求。未留存原始响应。 |
| PostgreSQL 任务账本与规范化入库 | **PASS**。项目标准 Compose PostgreSQL 中 Source=`cnemc`、Product=`city-live-hour`、`synthetic=false`；Window 1、Run `4592102d-dcf2-4fe0-b8a6-1399c154c2d9`、Batch 1。Run 为 `finished/responded`、A03、attempt 1、一次 Celery delivery、manifest 7；7 个 RunItem 均 accepted，对应 7 个 current/valid CityRevision 与 Receipt 1–7。 |
| 登录 Session API 一致性 | **PASS**。真实 Session 查询的七项数值和单位逐项等于同 Run、同城市码、同观测时次、同指标的 Receipt 所指向当前修订。各 Revision ID 与 Receipt ID 一一对应。 |
| PostgreSQL 重启后持久性 | **PASS**。重启前 `pg_postmaster_start_time()` 为 `2026-11-22 09:06:33.175014+00`，重启后为 `2026-11-22 09:36:26.967887+00`；新 Web 进程以原 Session 对原 Run 执行 `--read-run`，七项、Batch、Receipt 和 Revision 仍全部匹配。冷读没有请求上游。 |
| 周期采集与收尾 | **PASS**。Beat 和普通 Worker 未启动；Job 已经 Session `pause`，`next_due_at` 为空；专用 Worker 与网关已停止，标准环境恢复 `CNEMC_MODE=off`。请求总数为 1，没有重试。 |

本次规范化数据可供 S04 查询/API 工作复用，满足“接收一条真实城市观测”的数据前置条件；单城单时次不代表 S04 全部分析验收通过，也不支持全国排名、长期趋势或监管结论。尚未授权和启动周期采集；A01/A07 等范围须按各自用途、地域和频率另行确认。实际七项值仅在最终交付报告，不写入本文件；持久数据位于上述项目 PostgreSQL。

## 请求前核查快照（历史状态，已由上方结果取代）

以下保留首次正式请求前的检查记录；其中许可待补、零请求及 `NOT RUN` 仅描述当时状态。

### 前期结论：A03 出口已验证，首次真实入库仍未执行

2026-11-22，应用基线 `1e302cd`。本轮新增真实请求 **0**、PG 请求预留 **0**。阻塞点是尚无可用于正式登记的许可条款明细，不是网络、解析器或数据库故障。没有把早先的响应回放入库，也没有把 F 标作 C。

| 检查 | 结果及证据 |
|---|---|
| 既有真实 Worker → 北京 A03 | **PASS（复用）**。`cnemc-a03-once` 专用 Worker 经已验证的 HAProxy，于北京时间 16:08 发出一次 `cityCode=110000` 的 HTTPX 请求，HTTP 200。`cnemc.parse` 解析 1 个城市对象，7 项通过。**15:00 是上轮现场确认的观测时次，不是取得时刻。** 私有[连接收据](../../tmp/a03-httpx-MCE5m0uW/receipt.json)只记运行结果，不含七项数值；[运行记录](../../tmp/a03-httpx-MCE5m0uW/runtime.json)显示探测前后均无业务 Run 或城市观测。本轮没有重测 DNS、TLS、网关或 A03。 |
| 实源库与登记输入 | **PASS（离线检查）**。复用原 `cnemc-a03-once_pgdata` 和私有部署配置；应用数据库账号以 `BEGIN READ ONLY` 核实 `collection.0008`、`observations.0010` 已应用。独享 PG 实际重启、启动时间改变，原容器及卷不变。[只读计数](../../tmp/a03-preflight-zTkGQ30L/dedicated-db.json)：Source、Product、UseGrant、Job、Window、Run、预留、Batch、Receipt、城市观测和修订全部为 0。专库本轮未登记身份或来源。私有 Compose 文件只提供运行配置；现有登记示例属于 `cnemc.fixture`，研究方案也不含可登记的许可条款。 |
| 本轮真实 Celery → PG → 已登录 Session | **NOT RUN**。没有创建真实 Job 或提交请求，因此没有真实 Run、Batch、修订或 Receipt 可引用，没有可与本次 API 逐项对照的源响应，也不能证明真实观测重启后可读。未据此判定采集失败。 |
| 不出网的人工链与断源冷读 | **PASS（仅 F）**。独立项目 `s03vdc66eff0a675` 使用现有应用镜像、`cnemc_register /dev/stdin`、真实单并发 Celery、Redis、PG 和登录 Session。限定一城、仅 A03、总额 1、无排期；内网 HTTP 服务实际收到 1 次，SQL 核 1 Window / 1 Run / 1 预留 / 1 Batch / 7 城市键 / 7 修订 / 7 Receipt。七个 API 键的值、单位逐项等于同一份 F 响应；未授权的原值未留存，匿名读取及普通读者查看管理窗口均被拒绝。暂停 Job、停止 Worker 和内网源后重启 PG，`pg_postmaster_start_time()` 从 08:22:44 UTC 变为 08:23:17 UTC；原 Session 读回同七个修订 ID，显式空窗仍为空，预留仍为 1。Beat 未启动，一次性项目已回收。[演练收据](../../tmp/s03-evidence/s03vdc66eff0a675/checks.json) |
| 安全收尾 | **PASS**。专用 Worker 未启动且配置为 `off`，网关停止，验收项目没有运行的 Beat 或其他 Worker。私有部署文件恢复 `CNEMC_MODE=off`，其余条目和 `0600` 权限不变；原 PG 卷保留。未启用定时采集、其他端点或 168 小时试运行。 |

### 当时认为恢复真实验收所缺的输入

**只需提供已有许可材料的可读私有路径，或带引用和版本的适用条款摘录。** 已知端点、北京城码、七项映射、单并发、单次人工触发、专用项目和出口不需再次提供。登记时还需从材料落实三项事实：

- **获取及剩余额度**：条款引用、版本、署名、有效期，允许 Worker 取北京 A03 的完整 latest 对象及实际频控。A03 无日期参数，获取权必须覆盖可能返回的数据日期；不等于长期使用这些数据。若前次探测消耗同一份额，要确认仍有本次所需的 1 次。
- **处理与留存**：七项允许处理的观测日期；七项规范化值及溯源、本地 PG、北京目录有效期、采集账本和限定额度账本各自的留存依据及到期/撤权处置。不能把缺失期限填成永久，也不能把硬删除条款擅改为 `recoverable`。
- **读取与复核**：本地登录受众可看哪些字段，哪类操作者可核七项回执，及本次允许保存的最小验收记录。原包、分析、导出、再分发和备份均非这次的默认授权。

取得上述资料后按[单次运行手册](../operations/CNEMC.md#验收项目与出口)登记及核权，再开启出口。选预期已发布的当前目标整点，只提交一个 UUID；若返回旧时次，不移窗、不重试。`cnemc_verify_once` 证明的是 API 与对应批次修订的一致性，源字段须按获准方式与**同一次**响应核对。关闭出口后须重启原 PG 并用 `--read-run` 读同一 Run，不以重启 Web 代替。

**S04 可复用**现有的 C 七项采集契约，以及 `GET /api/v1/observations/current/` 按产品、城市、指标、观测整点、小时窗口及标准系列的 Session 精确查询。AQI 单位为 `1`，CO 为 mg/m³，其余五种浓度为 µg/m³；O₃ 取小时字段，不取滚动 8 小时值。该接口读取授权存量，不触发采集。**当前没有可以交给 S04 的真实 CNEMC 库存**，没有开发 S04 功能，T11 仍为 **NO-GO**。

### 历史记录（早于 16:08 的真实连接验证）

以下保留首次准入检查原文；其中“出口待验证”和“0 次请求”仅描述当时那一轮，不代表当前链路状态。

2026-11-22 · 基线 `main@1088711bc921d0eab4ec1ebe0267f070c52e543a`

**本轮结论为 NOT RUN，真实 CNEMC 请求 0 次。** 已收到用户的许可声明。本轮可查材料尚缺能登记到 `Source`、`UseGrant` 和人工 `Job` 的许可条款。A03 及后续端点的实源采集暂停，既有步骤 1–6 的软件验收仍以原报告为准。[接入计划](../plans/CNEMC_IMPLEMENTATION_PLAN.md) · [基础软件验收](CNEMC_IMPLEMENTATION_ACCEPTANCE.md)

### 已核对的环境和证据

- **PASS · 工作区与材料。** 核对了接入计划、软件验收、运维手册、接口映射，以及 Windows 研究目录的接口目录、数据产品、采集策略、验证报告和探测代码。研究报告记载 2026-11-22 13:18:26（北京时间）使用 Windows Python Requests 取得 A03 单城对象。它提供接口形状与时标依据。本轮没有使用该回执作数据种子或 Worker 验收证据。[接口映射](../design/CNEMC_ENDPOINT_MAPPING.md) · [研究材料位置](CNEMC_PRE_S04_AUDIT.md#核查基线与材料)
- **PASS · 本地运行环境识别。** 2026-11-22 07:06–07:07 UTC（北京时间 15:06–15:07），用 `docker compose ls`、`docker ps`、`docker inspect`、`docker network inspect` 与容器内版本命令核对现有 `s03v3a85580b59ca` 项目。宿主为 WSL2，Worker 镜像为 `linux/amd64`，运行 Python 3.13.18、HTTPX 0.28.1、Celery 5.7.0。项目有 PostgreSQL 16.16 容器。两个 Worker 仅连接 `internal=true` 的 `s03v3a85580b59ca_backend`，其配置没有开启 CNEMC 实源模式。该项目保留原配置和运行状态。
- **NOT RUN · 目标部署与 Worker 连通。** 本轮未找到已确认供实源验收使用的受控出口。未从 Worker 发起 CNEMC DNS、TCP、TLS 或 HTTP 探测，连通性待证。`docker compose ps` 为 **FAIL（当前终端缺部署环境变量）**。现有容器信息来自直接检查。[实源 Compose 覆盖](../../compose.cnemc.yaml) · [运维手册](../operations/CNEMC.md#限定实源核验)

### 首次请求前待登记的输入

| 待确认的许可或环境信息 | 登记与核对位置 |
|---|---|
| 可核条款版本、私有材料引用、适用起止和署名。是否允许 `A03` / `C`（`city-live-hour`）从指定 Worker 自动化客户端发起本次人工采集，以及是否覆盖该城市的完整 latest 响应 | `Product.evidence/attribution`、`UseGrant.terms/terms_version/valid_from/valid_until`、`acquire_online` 的端点、地域、对象及数据日期范围。A03 无日期参数，现实现按完整返回范围检查获取权，许可生效期与响应可能覆盖的数据日期分别登记。 |
| 获准的一座城市及其 CNEMC 码、全称、地域、目录有效期和来源依据 | `cities[]`、`Job.city_code` 与七项指标的 `selection`。A03 无站点范围，A04 及 A07/A10 的站点范围留到相应端点获准后确认。 |
| 适用的最小请求间隔、最大并发、滚动周期和周期请求数、本轮可用累计额度及其是否与其他产品共享 | `Source.collection_limits` 与 `acquire_online.collection_limits`、`max_total_requests`、`Job.validation_terms_version`。共享时登记 `validation_allocation`。首轮拟收紧至单并发、总额 1 次，失败或发送结果不明的预留同样计入。人工 Job 无 `first_due`。 |
| 处理与持久化的字段、证据范围、留存期限、到期或撤权后的处置。需要确认七项规范化值与溯源、城市目录、采集账本和限定验证额度账本均可保留 | `process_local`、`retain_normalized` 及其字段 `normalized`、`provenance`、`catalog`、`collection_ledger`、`validation_ledger`。单独确认原时标、原值、原包、摘要及报告可保留的范围。现行判权拒绝带 `retain_until` 的留存授权，也拒绝有 `valid_until` 且 `expiry_mode` 非 `recoverable` 的留存授权。若实际条款要求硬删除期限，现有持久化路径无法直接使用。 |
| 获准的登录展示与管理复核受众，分析及其他用途的范围或禁用状态 | 首次验收需要本地登录读者的 `display` 及验收人员的 `review`。`analyze`、截图、导出、公开展示、再分发、备份与恢复逐项判权，未确认的用途保持关闭。 |
| 实际验收环境的受控 Worker 出口、目标地址和端口白名单，以及获准的 PostgreSQL 与私有证据保存位置 | 复核 `CNEMC_EGRESS_NETWORK`，以 `CNEMC_MODE=limited` 运行。使用现有登记权限、执行身份和 Session 权限，凭据仅经私有渠道输入。 |

[登记实现](../../backend/collection/registration.py) · [任务准入](../../backend/collection/admission.py) · [端点额度](../../backend/collection/cnemc_jobs.py) · [用途判权](../../backend/observations/policy.py)

### 真实链路验收结果（请求前快照）

| 检查项 | 本轮结果 |
|---|---|
| A03 单次人工采集 | **NOT RUN**。0 次请求，无请求时刻、目标城市或源响应。未创建实源 Job 或提交 Run。 |
| Worker 规范化、质量验证、修订及 PostgreSQL 入库 | **NOT RUN**。本轮未写入任何实源观测。七项实际值待核。 |
| 已登录 API 与源响应逐项核对 | **NOT RUN**。待对照同一 A03 响应、PG 修订及精确查询键。`cnemc_verify_once` 核对 API 与本批 PG 修订和 Receipt 的对应关系。源响应对照仍需在许可覆盖的范围内独立核验。 |
| 时间、单位和产品 | **PASS（契约核对）／NOT RUN（实源样本）**。A03 归属 C。无偏移 `TimePoint` 按 `Asia/Shanghai` 解释，观测时次与取得时刻分别记录。AQI 无量纲，CO 为 mg/m³，SO₂、NO₂、O₃、PM₁₀、PM₂.₅ 为 µg/m³，O₃ 使用小时 `O3`。七个精确键使用 `hourly`、`hourly_label_unspecified` 与各自的标准系列。 |
| Window、Run、Batch、Receipt 与额度 | **NOT RUN**。本轮没有相应标识，新增实源请求预留为 0。待核目标窗口、请求端点、保守预留、响应状态、批次、七项回执和额度消耗。 |
| 关闭实源出口后冷启回读 | **NOT RUN**。待用 `--read-run` 与仍获准的 Session 重查同键，复核显式空窗及无新增请求预留。 |
| A01、A07、A04 及 A10 | **NOT RUN**。各 0 次。A03 通过后再按各自许可及剩余额度决定。A01、A07 需要全国整包获取权。A04 需要获准城市及适用站点范围。A10 还需要 A07/A10 双端点许可、同产品口径证据和有界补采配置。 |

本轮确认的障碍为准入条款及实源部署出口未落实。未取得可复现的实现缺陷，业务代码与配置无修改，既有软件测试未重跑。四份原有未跟踪文档保留。[单次核验命令](../../backend/collection/management/commands/cnemc_verify_once.py) · [Worker 执行路径](../../backend/collection/services.py) · [写入与回执](../../backend/collection/writer.py) · [精确查询](../../backend/observations/queries.py)

### 定时采集与 S04 判定（请求前快照）

- **定时采集维持禁用。** 启用前需要落实周期采集许可、各产品和地域范围、配额与停止条件，取得本轮城市链证据、获准的同样本重放与断源冷启证据，以及三地区各至少两个真实发布周期的核查结果。随后冻结周期、成功率、新鲜度和缺口阈值，另行批准受限试运行。T11 当前为 **NO-GO**，正式验收还需试运行自然经过 168 小时并通过正式复审。[接入计划 §6](../plans/CNEMC_IMPLEMENTATION_PLAN.md#6-实源验证风险与阶段衔接) · [总体实施方案 §9](../design/总体实施方案.md)
- **具备使用合法 H 与隔离 F 开展 S04 的技术条件。** 依据是总体实施方案规定的 S01/S02 依赖和既有软件验收。S04 的真实 CNEMC 数据接入仍缺本轮实源证据。本轮未实施 S04 功能，未启动长期任务，也未执行 Git Push。[总体实施方案 S04](../design/总体实施方案.md#s04-统一查询与用户业务闭环) · [基础软件验收](CNEMC_IMPLEMENTATION_ACCEPTANCE.md)

### 2026-11-22 首次请求前复验

- **PASS · Docker 本地隔离。** 项目 `a03probe1b28bd9de2` 使用真实 Celery Worker、PG、Redis 和只在测试内网监听 18007 的 TLS 服务。基础 Worker 访问该服务失败，验收 Worker 通过 Docker DNS 和 TCP 访问成功。进程内测试 CA 的 HTTPS 检查通过，采集传输按预期拒绝这张未受信证书。仅验收 Worker 接入测试出口，PG/Redis 的宿主端口映射均为空。Beat 副本数为 0。私有环境文件的出口优先于终端残留值，已有运行中 Beat 的阻断分支用 `sleep` 替身验证。测试容器、卷和网络均已回收。
- **PASS · 离线登记演练。** 在一次性 PG 上用 `cnemc.fixture` 和最小字段授权经 `cnemc_register /dev/stdin` 登记了一个禁用的单城七项人工 Job，累计预算 1。对现有 `s03v3a85580b59ca` 项目做只读计数，实源 `cnemc` 的 Source、Product、UseGrant、Job、Run 各为 0。研究资料已确认候选北京市城码 `110000`。许可登记仍等待条款原件的私有引用或相关摘录。[许可字段与格式](../operations/CNEMC.md#首次-a03-的许可输入)
- **NOT RUN · 真实 A03 及 Session 回读。** 待核 `CNEMC_EGRESS_NETWORK` 对应的目标白名单与 DNS 策略，以及人工单次的适用许可。真实请求数 0，实源请求预留 0。[操作步骤](../operations/CNEMC.md#验收项目与出口) · [本地测试收据](../../tmp/a03-egress-WXbpX4XV/result.json)
