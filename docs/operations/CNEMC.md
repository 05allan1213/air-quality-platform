# CNEMC 小时采集与 S04 交接

登记和运行都使用已有 Django 应用、Celery 采集队列和 PostgreSQL。默认配置为 `CNEMC_MODE=off`。本次可复现输入使用 `cnemc.fixture` 合成来源。正式命名空间为 `cnemc`。

| 产品码 | 入口 | 已登记的小时投影 | 查询实体 |
|---|---|---|---|
| `city-list-hour` | A01 | 全国城市 AQI、六个污染物等级 | `city` |
| `city-live-hour` | A03 | 指定城市的 AQI、SO₂、NO₂、CO、O₃、PM₁₀、PM₂.₅ | `city` |
| `station-city-hour` | A04 | 指定城市的站点七项 | `site_epoch` |
| `city-trend-hour` | A05 | 指定城市近 24 个小时的 AQI | `city` |
| `station-national-hour` | A07、经认定的 A10 | 全国站点最新包和指定整点包，各站七项 | `site_epoch` |

## 从 S03 升级

停止旧版本的 Worker 和 Beat，在新版本应用环境中运行 `python backend/manage.py migrate`，随后启动 Web、Worker 和单个 Beat。此次新增 `observations.0010`、`collection.0007` 和 `collection.0008`。旧 S03 Run 保留 UUID 和状态，新端点与限定验证额度保持空值。迁移后的 CNEMC 来源与 Job 由操作者单独登记。旧库升级和一次性库回退、再升级的结果见[验收报告](../verification/CNEMC_IMPLEMENTATION_ACCEPTANCE.md)。

## 登记与运行

`python backend/manage.py cnemc_register /private/manifest.json --actor <用户主键>` 以一个事务登记来源、产品、用途声明、离线目录及禁用的 Job。操作者需要 `manage_grants` 和 `manage_collection`，执行身份需要 `run_collection`。命令返回产品和 Job 主键。个人本地单次测试的 `terms/terms_version` 可以如实记录操作者声明；不得将其描述为官方文件。其他实际适用限制仍须按请求范围落实。合成清单的可执行示例在 `tests/collection/test_cnemc_jobs.py` 的 `manifest()` 中。

| 清单字段 | 用途 |
|---|---|
| `namespace`、`collection_limits` | 来源和共用的最小间隔、最大并发、滚动周期、周期请求数 |
| `products[].code/evidence/attribution/grants` | 固定契约、证据、署名及逐用途授权。`acquire_online` 要明确列出 `endpoints` 和限额。限定验证用同一条有 `valid_until` 和 `max_total_requests` 的授权登记每份预留。跨清单或跨产品共用验证额度时，填写同一 `validation_allocation.basis` 和 `total`。数据库累计每份授权的分配额，含已撤回份额 |
| `cities[]`、`stations[]` | 提供所属产品、代码、名称、带时区的 `from`、可选排他 `until` 和证据。城市登记 `geography`，站点登记 `city_code`。`retain_normalized` 的 `catalog` 字段须覆盖目录有效期 |
| `jobs[].selection` | `{"mode":"include","objects":{"F-01":["AQI"]}}` 表示对象与指标白名单。`{"mode":"all"}` 要求整包处理及留存许可 |
| `expected`、`roster_basis` | 计算 E/O/V 的独立监测方案。例如 `{"F-01":["AQI","CO"]}`。分母未知时省略这两项，窗口显示 `E=null`。名单以外另有处理权的格可精确读取，已知分母的覆盖统计只计名册内格 |
| `standard` | 默认 `{}`。有标准依据时填 `series`、`basis`、`evidence`、地方日期半开区间 `from`/`until` |
| `executor`、`city_code`、`first_due`、`cadence` | 工作身份、按城入口的城市码、带时区的发布时间偏移及周期秒数。省略 `first_due` 即人工 Job。周期以整小时为单位，目标仍是源整点 |
| `validation_terms_version`、`backfill_policy` | 限定人工验证绑定授权版本。A10 使用独立的 `equivalence_basis`、`lookback_hours`、`max_windows_per_scan`、`max_requests_per_scan` 和 `operating_from`。回看上限 23 小时，锚取有权的 A07 实见时次，锚达到两小时后停止补采。`Job.backfill_cursor` 保存下一次扫描的整点，单页上限为 1 时也会越过无权日期 |

变更运行状态使用已登录、带 CSRF 的 `POST /api/v1/admin/collection-jobs/<id>/`。S06 起先对同一路径做受权 `GET`，读取 `targets.job/source/product.control_version`。提交 `action`、`request_id`（UUID）、`reason_code` 和实际目标的 `expected_version`：`pause`、`resume` 用 Job 版本，`resume_source` 用 Source 版本，`resume_product` 用 Product 版本。可用理由码包括 `operator_pause`、`operator_resume` 和 `incident_review`。仅紧急暂停兼容旧正文 `{"action":"pause"}`。遇 `409` 先重读状态，不能沿用旧版本强行恢复。

人工采集提交使用 `POST /api/v1/admin/collection-runs/`，传 `job`、`request_id`、`reason` 和带时区的整点 `target`。相同操作者重发原 UUID 可查原 Run；这不是重投任务。需要重投符合条件的原 Run 时，对 `collection-runs/<uuid>/` 做受权 `GET`，取 `target.control_version`，再以 `action=redeliver`、新 UUID、`reason_code=operator_recovery` 和该版本提交给同一地址。控制重放仍须通过会话、权限及用途检查。停止和恢复均以 PG 账本为准。`scheduled` 由现有 Beat 扫描器负责。

常规 Job 处置后如需保留预留时刻，`retain_normalized` 的 `request_ledger` 需覆盖该产品、全来源范围及窗口日期。其余预留进入 `disposed` 并清除时间字段。

限定验证另需 `retain_normalized` 的 `validation_ledger` 字段，覆盖本轮获取授权的对象、地域及日期。该权利允许保存请求预留及其授权关联。撤回后系统关闭该份额，清除 Job、Run 中的额度关联和预留时间。有单独留存及展示依据的观测可继续读取。已使用的预留被清除时，来源暂停至少 24 小时，覆盖可登记的最长滚动限额。另有更晚的 `Retry-After` 时沿用更晚时刻。操作者之后复核并恢复有权的 Job。

## 离线验收

```sh
python3.13 scripts/verify_cnemc.py
python3.13 scripts/verify_s03.py
```

CNEMC 验证器为每个用例分配独立 Compose 项目。`--case chain` 检验存取全链，`--case quota` 检验累计额度，`--case partial` 和 `--case capacity` 检验全国包，`--case pg` 检验定向数据库回归。收据写入 `tmp/s03-evidence/<项目>/cnemc-result.json`。

补充验收沿用相同的受控 HTTP、Worker 和 PG 测试环境。质量与留存探针核对 A10 空包、争议和受限字段。窗口探针让两个手动 Job 并发提交，检查 A10 拒绝的请求数，并在下一个实际整点发布第二个合成 T 包，通常需等待至下一整点。两份收据分别为 `projection-guard.json` 和 `window-boundaries.json`。

```sh
python3.13 .tasks/cnemc-integration/evidence/probe.py
python3.13 .tasks/cnemc-integration/evidence/window_boundaries.py
```

## 限定实源核验

### 首次 A03 的许可输入

针对个人开发环境的单次测试，项目操作者已声明取得数据使用许可并授权本次北京请求及规范化值留存。登记时把这项事实标为“项目操作者声明”，不得伪装成 CNEMC 出具的文件、条款或版本；不要求文件路径、官方条款编号或书面版本证明。该声明只覆盖此次 `cnemc / city-live-hour`、A03、北京市 `110000`、一次人工请求、规范化七项写入项目 PostgreSQL，以及认证 Session 的精确查询。它不自动开放其他城市/产品、周期采集、原始响应留存、导出、公开展示或再分发。若以后要扩大真实运行范围，按实际适用的外部限制重新确认。已有目录资料给出 `北京市`、`110000`，无需再查在线城市字典。

| 本地单次登记仍需明确的事实 | 私有登记清单中的位置与格式 |
|---|---|
| 声明内容与真实性 | `products[].evidence/attribution` 和各 `grants[].terms/terms_version` 写明“项目操作者声明”、声明日期和本次范围；例如 `terms_version=operator-declared-2026-11-22`。这只是操作者记录，不是外部条款引用。 |
| 来源、产品、端点、地域、城市码及数据范围 | `namespace=cnemc`、`products[].code=city-live-hour`、`endpoints=["A03"]`，城市 `110000` / `北京市`。A03 没有日期参数，获取授权的日期边界覆盖可能返回日期；处理与展示仍限定为本城七项。目录和地理字段只记录身份，不虚构坐标或官方证据。 |
| 请求频率、并发和本次绝对额度 | `collection_limits` 在来源与获取授权各填一份，均为四个整数 `min_interval_seconds`、`max_concurrent`、`period_seconds`、`max_requests`。本次两层并发均为 1，周期请求数为 1，获取授权 `max_total_requests=1`。来源累计额度不会因重试、重启或另建 Job 复原；不启动周期 Job。 |
| 七项规范化、溯源、目录与账本的本地留存 | `process_local/platform` 需 `normalized`。`retain_normalized/platform` 需覆盖 `normalized`、`provenance`、`collection_ledger`、`validation_ledger` 及登记目录所需的 `catalog`。只持久化规范化观测和必要溯源/任务事实，不保存原始响应。 |
| 谁能经 Session 查询和核对 | `display/local_authenticated` 需 `normalized`、`provenance`，本次只创建本地活动操作者账户。`review/operator` 需覆盖 `collection_ledger`，用于核同一个 Run 的 Window/Batch/Receipt。匿名读取和公开/导出用途不开放。 |

`jobs[]` 只建一个 Job。`city_code` 用获准城市码，`selection.mode="include"`，`selection.objects` 仅为该码列 `AQI, PM2.5, PM10, SO2, NO2, CO, O3`。`executor` 用本地获准执行账号主键，`validation_terms_version` 对应上述一次获取授权，省略 `first_due`。登记命令会返回禁用的 Job 主键。

### 验收项目与出口

首次真实 A03 使用仓库标准 Compose 项目 `air-quality-platform` 的开发数据库与 Redis，PG 卷为 `air-quality-platform_pgdata`。用 `-p air-quality-platform` 覆盖 `compose.cnemc.once.yaml` 中为旧隔离验收预设的项目名；不得使用 `cnemc-a03-once_pgdata` 或 S03 验收卷代替。根目录 `.env` 按 `.env.example` 提供本地秘密及可用的回环 `WEB_PORT`，常态 `CNEMC_MODE=off`。本次网关名为 `air-quality-platform_cnemc-egress`，由 Compose 按项目名创建。

| 网络路径 | 实际规则 |
|---|---|
| 默认 `worker`、PG、Redis | 只接 `backend`。验收项目中默认 Worker 副本为 0，专用 Worker 独占该项目的采集队列。 |
| `worker-cnemc` → `cnemc-gateway` | Worker 的两张网 `backend`、`cnemc-egress` 都是 IPv4 内网，使用 `isolated` bridge 模式。Worker 没有默认路由。Docker 仅向这个 Worker 提供 `air.cnemc.cn` 的网关别名，数据库和 Redis 名称仍由 Docker 解析。 |
| `cnemc-gateway` → 源服务 | HAProxy 仅监听私网地址 TCP 18007。收到 SNI 为 `air.cnemc.cn` 的 TLS ClientHello 后，才用 Docker DNS 查询固定上游 `air.cnemc.cn`，从 `cnemc-uplink` 走宿主既有 IPv4 出口到 TCP 18007。错误或缺失 SNI、明文、解析失败均拒绝。网关无宿主发布端口，关闭 IP 转发。 |
| TLS 与 HTTP | 网关透传 TLS。Worker 的 HTTPX 使用原有证书链和主机名校验，客户端和传输层均为 `trust_env=False`。URL、路径与请求预算仍由原采集契约控制。 |

`python3.13 scripts/verify_cnemc_egress.py` 使用自有应答器验证网络隔离，不请求 CNEMC。专用 Worker 对北京 A03 的真实 HTTP 200 和七项解析已单独验证，正式入库前不再消耗额度探路。[网关规则](../../docker/cnemc-egress.cfg)

离线登记、联网和停用共用同一份网络定义，切换时保留 `backend` 和 PG 卷。`base` 使用 `off`，`once` 固定 `limited` 并将 Beat 副本设为 0。以下命令均在仓库根目录执行。

```sh
base() { CNEMC_MODE=off docker compose --env-file .env -p air-quality-platform \
  -f compose.yaml -f compose.cnemc.yaml "$@"; }
once() { CNEMC_MODE=limited docker compose --env-file .env -p air-quality-platform \
  -f compose.yaml -f compose.cnemc.yaml -f compose.cnemc.once.yaml "$@"; }
base config -q
once config -q
CNEMC_EGRESS_NETWORK=$(once config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["networks"]["cnemc-egress"]["name"])')
printf '%s\n' "$CNEMC_EGRESS_NETWORK"
```

按项目操作者声明登记，不要求另交许可材料。先用 `base ps -a` 和数据库只读查询确认标准项目身份、迁移及是否已有业务数据；不操作 S03 或旧隔离项目。新库先按项目账号流程建立本地活动身份。`ACTOR_ID` 需有 `manage_grants` 和 `manage_collection`，执行身份需有 `run_collection`，Session 核验账号需有 `run_collection` 和 `review_observation`。常规 `worker` 与 `beat` 只在确认归属本项目且不会影响已有工作后才可调整。

```sh
base up -d --build db redis migrate web
base exec -T web python backend/manage.py cnemc_register /dev/stdin \
  --actor "${ACTOR_ID:?}" < "${A03_MANIFEST:?}"
```

核对单一 Job、单城七项、仅 A03、无排期、`max_total_requests=1`，以及本窗口七项的 `display` 和 `review` 权利。PG 额度账本绑定本 Job 的唯一预留；不得通过重建 Job 或重启服务恢复已用额度。确认 Beat 未运行后才接入出网 Worker。

```sh
other=$(once ps --status running -q beat worker) && [ -z "$other" ] && once up -d web worker-cnemc cnemc-gateway
```

启动成功后复核 `once ps`，用管理 Session 对本 Job 执行前述 `resume`。在预计已发布时选许可期内的当前整点（带时区），保存本次唯一的 UUID，显式提交一次。A03 只返回 latest；如果仍是上一小时，保留 `outside_target` 缺口，不改写时标，也不追加请求。Session 密码由终端读取。

```sh
once exec web python backend/manage.py cnemc_verify_once --username USER --job JOB \
  --target APPROVED_SOURCE_HOUR --request-id MANUAL_UUID
```

验收命令逐项通过真实 Session 查询 API，并核该 Run 的 Batch、七张 Receipt 与当前 Revision 的指标、值和单位；输出中列出七项值、单位、修订 ID 和 Window/Run/Batch 标识。Receipt 来自这一次 Worker HTTP 响应的规范化写入，因此对照的是同一响应处理所得的持久值，不另发第二个源请求，也不把原始响应存入日志或收据。

成功、超时或失败后均用管理 Session 对 Job 执行 `pause`，再停专用 Worker 和网关。核对原 Run、Batch、Receipt 及绑定授权的累计预留。以下步骤只重启标准项目 PostgreSQL，不删卷，不启动 Beat；常态 `.env` 保持 `CNEMC_MODE=off`。只重建 Web 不能证明数据库持久化。

```sh
once stop worker-cnemc cnemc-gateway
# 以 off 配置保持专用 Worker 停止，不启动它或其依赖。
base up --no-start --no-deps --no-build --pull never worker-cnemc
pg_started() {
  base exec -T db sh -ec 'PGPASSWORD="$APP_DB_PASSWORD" psql -XAtq -v ON_ERROR_STOP=1 \
    -h 127.0.0.1 -U "$APP_DB_USER" -d "$POSTGRES_DB" -c "SELECT pg_postmaster_start_time()"'
}
before=$(pg_started) && base restart --no-deps db && base up -d --no-deps --wait db &&
  after=$(pg_started) && test -n "$before" && test -n "$after" && test "$before" != "$after" || exit 1
base up -d --no-deps --force-recreate web
base exec web python backend/manage.py cnemc_verify_once --username USER --job JOB --read-run RUN_UUID
```

冷读使用原 Run 而非 `--request-id`。再次核对七个精确键的修订 ID、Batch、七张 Receipt 和预留数均未变；准许查看的相邻空窗仍应为空。不要启动整个 Compose 项目。

### 本机网络验证 · 2026-11-22

| 结果 | 实测证据 |
|---|---|
| **PASS · 专用出口** | WSL2 mirrored、Docker Engine 29.9.3、Compose 5.7.0。一次性项目 `egressfcb8416256d96` 的 Worker 从冷缓存发起首次合法握手，经网关访问另一张 bridge 上的自有服务 `172.22.0.1:18007`。HTTPX 0.28.1 校验测试证书和主机名后，以 TLS 1.3 收到 HTTP 200。原采集传输对同一测试证书返回 `transport_security`。 |
| **PASS · 拒绝路径与停用** | 替身的 18007、18008 都有服务。专用 Worker 直连替身 IP、访问网关 18008 均失败。网关拒绝错误 SNI、缺失 SNI 和明文，上游目标 DNS 与 TCP 计数均为 0。注入 NXDOMAIN 后上游 TCP 计数也未增加。停网关后 Worker 连接失败。 |
| **PASS · 默认隔离** | 基础 Worker 探针能连 PG/Redis，连不到网关和替身。PG/Redis 仅有内网路由，无宿主映射端口。离线登记、联网、停用三种配置运行在同一 PG/Redis 容器和 backend 网络上。Beat 启动次数为 0。 |
| **PASS · 实际 WSL 出口** | A03 网关通过 Docker DNS `127.0.0.11` 查询 `example.org` 得到 `198.18.0.136`。同一 A03 uplink 上的一次性诊断容器直连 `registry-1.docker.io:443`（解析为 `198.18.0.137`），完成证书链及主机名验证的 TLS 1.3 握手。仅执行握手，保留宿主既有路由。 |
| **PASS · 真实 A03 连接与解析** | 2026-11-22 北京时间 16:08，`cnemc-a03-once` 的专用 Worker 经 HAProxy 对 `110000` 发出 1 次 HTTPX 请求，HTTP 200、1 个城市对象、7 项解析通过。该次是 Worker 容器内的连通性验证，没有提交 Celery 业务 Run，也没有入库。证据见本机 `tmp/a03-httpx-MCE5m0uW/{receipt,runtime}.json`。 |

## S04 已有读接口

`GET /api/v1/observations/current/` 需要同源已登录 Session。用登记返回的 `product` 主键、`city` 或 `site_epoch` 主键、`indicator`、`report_kind=hourly`、`window_definition=hourly_label_unspecified`、`standard_series` 和明确带时区的 `observed_at` 查询。F 数据另传 `scope=synthetic`。AQI 和等级按实际标准系列选择，普通浓度取 `not_applicable`。CO 使用 mg/m³，其余浓度使用 µg/m³。本地 API 读操作只访问存储。`reason` 可区分显式空窗、争议和现行质量原因。

管理者可用 `GET /api/v1/admin/collection-windows/<id>/` 核 E/O/V、采集时刻、Run、Batch、格与回执。`object_code` 和 `indicator` 可缩小返回范围。获准的原时标、源等级、描述等可选元数据在有对应字段权利的管理回执返回。普通读者使用精确观测读权，现有分析用途另行核验。
