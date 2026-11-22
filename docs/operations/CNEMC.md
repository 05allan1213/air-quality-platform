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

`python backend/manage.py cnemc_register /private/manifest.json --actor <用户主键>` 以一个事务登记来源、产品、许可、离线目录及禁用的 Job。操作者需要 `manage_grants` 和 `manage_collection`，执行身份需要 `run_collection`。命令返回产品和 Job 主键。配置文件中的许可条款使用可核的版本和私有材料引用。合成清单的可执行示例在 `tests/collection/test_cnemc_jobs.py` 的 `manifest()` 中。

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

变更运行状态使用已登录、带 CSRF 的 `POST /api/v1/admin/collection-jobs/<id>/`，正文的 `action` 可取 `resume`、`pause`、`resume_source` 或 `resume_product`。人工提交使用 `POST /api/v1/admin/collection-runs/`，传 `job`、`request_id`、`reason` 和带时区的整点 `target`。相同操作者重投原 UUID 可查原 Run。停止和恢复均以 PG 账本为准。`scheduled` 由现有 Beat 扫描器负责。

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

可提供仓库外的许可文件路径，或附条款编号的摘录。登记方从材料中填写下表。产品固定为 `cnemc / city-live-hour`，入口为 `POST /CityData/GetAQIDataPublishLiveInfo?cityCode=…`。若许可覆盖北京，已有目录资料给出 `北京市`、`110000`，无需再查在线城市字典。

| 需从许可确认的事实 | 私有登记清单中的位置与格式 |
|---|---|
| 可核的条款引用、版本、署名、生效与终止时间 | `products[].evidence/attribution` 和 `grants[].terms/terms_version/valid_from/valid_until`。引用和版本填文本，时间填带时区的 ISO 8601。限定获取授权必须有终点。 |
| 获准城市、目录使用区间、这次人工触发的 Worker 是否可获取该城完整 latest 响应，以及可处理的观测日期 | `cities[].geography/from/until/evidence`、`grants[].geography/object_code/data_from/data_until`。`acquire_online/platform` 取 `endpoints=["A03"]`、`indicator="*"`、`fields=["*"]`，地域和城市码限定到获准的一城。数据日期用 `YYYY-MM-DD` 半开区间。A03 没有日期参数，现行获取检查覆盖该城所有可能返回日期（`0001-01-01` 至 `9999-12-31`）。若条款只许获取某日或裁剪后的字段，停在登记前。处理与展示仍可按日、按指标收窄。周期权限可记为未授予或未确认，本次仅登记人工任务。 |
| 来源和 A03 的允许速率、并发、本轮剩余额度，额度是否与其他采集共享 | `collection_limits` 在清单顶层和获取授权各填一份，均为四个整数 `min_interval_seconds`、`max_concurrent`、`period_seconds`、`max_requests`。本次两层并发均取 1，获取授权 `max_total_requests=1`。共享时按原依据登记 `validation_allocation={"basis":…, "total":…}` 并核剩余份额。保留已用额度，包括发送结果不明的预留。 |
| 七项规范化、溯源、目录与账本可保留到何时，许可允许的 PG 存放位置，到期及撤权后如何处置 | `process_local/platform` 需 `normalized`。`retain_normalized/platform` 需覆盖 `normalized`、`provenance`、`collection_ledger`。`catalog` 需取 `indicator="*"` 并覆盖该城目录期，`validation_ledger` 需覆盖获取授权的完整范围。可对账本单列授权。只有实际允许恢复后处置时才填 `expiry_mode="recoverable"`。现有判权会拒绝含 `retain_until` 的留存授权，也会拒绝带 `valid_until` 的硬截止留存。此类条款须先解决处置能力。 |
| 谁可经 Session 查询七项、谁可核对回执，允许查看哪些字段 | `display/local_authenticated` 需 `normalized`、`provenance`，面向该实例全部已登录用户。若许可限于具名人员，须先确认该实例的访问及注册范围仅涵盖这些人员。`review/operator` 还需 `collection_ledger`。原值、原包、截图、分析、导出、公开展示、再分发、备份各按已确认条款登记，均非本次必填。 |

`jobs[]` 只建一个 Job。`city_code` 用获准城市码，`selection.mode="include"`，`selection.objects` 仅为该码列 `AQI, PM2.5, PM10, SO2, NO2, CO, O3`。`executor` 用本地获准执行账号主键，`validation_terms_version` 对应上述一次获取授权，省略 `first_due`。登记命令会返回禁用的 Job 主键。

### 验收项目与出口

CNEMC 覆盖文件的默认项目名为 `cnemc-dedicated`。首次 A03 使用独立项目 `cnemc-a03-once`。`CNEMC_EGRESS_NETWORK` 的实际值为 `cnemc-a03-once_cnemc-egress`，由 Compose 按项目名创建。私有环境文件只需按 `.env.example` 填本地秘密及可用的回环 `WEB_PORT`，旧文件中的出口名称会被忽略。保持验收项目独享 PG 卷和 Redis 队列，重启和回读沿用原卷。

| 网络路径 | 实际规则 |
|---|---|
| 默认 `worker`、PG、Redis | 只接 `backend`。验收项目中默认 Worker 副本为 0，专用 Worker 独占该项目的采集队列。 |
| `worker-cnemc` → `cnemc-gateway` | Worker 的两张网 `backend`、`cnemc-egress` 都是 IPv4 内网，使用 `isolated` bridge 模式。Worker 没有默认路由。Docker 仅向这个 Worker 提供 `air.cnemc.cn` 的网关别名，数据库和 Redis 名称仍由 Docker 解析。 |
| `cnemc-gateway` → 源服务 | HAProxy 仅监听私网地址 TCP 18007。收到 SNI 为 `air.cnemc.cn` 的 TLS ClientHello 后，才用 Docker DNS 查询固定上游 `air.cnemc.cn`，从 `cnemc-uplink` 走宿主既有 IPv4 出口到 TCP 18007。错误或缺失 SNI、明文、解析失败均拒绝。网关无宿主发布端口，关闭 IP 转发。 |
| TLS 与 HTTP | 网关透传 TLS。Worker 的 HTTPX 使用原有证书链和主机名校验，客户端和传输层均为 `trust_env=False`。URL、路径与请求预算仍由原采集契约控制。 |

`python3.13 scripts/verify_cnemc_egress.py` 使用自有应答器验证网络隔离，不请求 CNEMC。专用 Worker 对北京 A03 的真实 HTTP 200 和七项解析已单独验证，正式入库前不再消耗额度探路。[网关规则](../../docker/cnemc-egress.cfg)

离线登记、联网和停用共用同一份网络定义，切换时保留 `backend` 和 PG 卷。`base` 使用 `off`，`once` 固定 `limited` 并将 Beat 副本设为 0。以下命令均在仓库根目录执行。

```sh
base() { CNEMC_MODE=off docker compose --env-file "${A03_ENV_FILE:?}" -p cnemc-a03-once \
  -f compose.yaml -f compose.cnemc.yaml "$@"; }
once() { CNEMC_MODE=limited docker compose --env-file "${A03_ENV_FILE:?}" -p cnemc-a03-once \
  -f compose.yaml -f compose.cnemc.yaml -f compose.cnemc.once.yaml "$@"; }
base config -q
once config -q
CNEMC_EGRESS_NETWORK=$(once config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["networks"]["cnemc-egress"]["name"])')
printf '%s\n' "$CNEMC_EGRESS_NETWORK"
```

许可材料齐备且获准使用此库后，用 `base ps -a` 确认服务归属和当前运行状态。只在该验收项目停用采集进程后离线登记。新库先按项目账号流程建立本地活动身份。`ACTOR_ID` 需有 `manage_grants` 和 `manage_collection`，执行身份需有 `run_collection`，暂停和恢复 Job 的账号需有 `manage_collection`。Session 核验账号需有 `run_collection` 和 `review_observation`。

```sh
base stop worker worker-cnemc beat cnemc-gateway && base up -d --build web redis
base exec -T web python backend/manage.py cnemc_register /dev/stdin \
  --actor "${ACTOR_ID:?}" < "${A03_MANIFEST:?}"
```

核对单一 Job、单城七项、仅 A03、无排期、`max_total_requests=1`，以及本窗口七项的 `display` 和 `review` 权利。若先前的连通性请求与本轮共用许可额度，按条款扣除后仍须至少剩 1 次；PG 中零预留并不能证明这点。确认 Beat 已停止后才接入出网 Worker。

```sh
other=$(base ps --status running -q beat worker) && [ -z "$other" ] && once up -d web worker-cnemc
```

启动成功后复核 `once ps`，用管理 Session 对本 Job 执行前述 `resume`。在预计已发布时选许可期内的当前整点（带时区），保存本次唯一的 UUID，显式提交一次。A03 只返回 latest；如果仍是上一小时，保留 `outside_target` 缺口，不改写时标，也不追加请求。Session 密码由终端读取。

```sh
once exec web python backend/manage.py cnemc_verify_once --username USER --job JOB \
  --target APPROVED_SOURCE_HOUR --request-id MANUAL_UUID
```

验收命令逐项核 Session 返回值、PG 修订、单位和本批 Receipt；它本身不独立比对 HTTP 原包。按实际授权对照**同一次响应**：若已获单字段原值的留存和复核权，可从该 Batch 的 `GET /api/v1/admin/observation-evidence/` 读取；若原包只准瞬时处理，须在释放该响应之前在 Worker 内存中核对七个源字段与规范化投影。不再取第二包来对账。收据只记获准的运行标识、计数与核对结论，不复制响应或凭据。

成功、超时或失败后均用管理 Session 对 Job 执行 `pause`，再停专用出口。核对原 Run、Batch、Receipt 及绑定授权的累计预留。下列冷读步骤**只用于独享的 `cnemc-a03-once` 项目**，不删卷，不启动 Beat。确认私有环境文件的常态值为 `CNEMC_MODE=off`；只重建 Web 不能证明数据库持久化。

```sh
once stop worker-cnemc cnemc-gateway
# 停用时让已停止的专用 Worker 也回到 off，不启动它或其依赖。
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
