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

使用独立项目 `cnemc-a03-once`，其 PG 卷、Redis 队列和单并发 Worker 仅供本次验收。先核对项目归属和存量预留，重启和回读沿用同一 PG 卷。`A03_ENV_FILE` 指向操作者的私有环境文件，按 `.env.example` 填本地秘密及可用的回环 `WEB_PORT`。正式出口名称写入该文件的 `CNEMC_EGRESS_NETWORK`。

网络管理员需确认出口对业务流量仅放行 `air.cnemc.cn` 的 TCP 18007，并允许 Worker 经获准的 DNS 解析器查询该域名。`external: true` 只引用 Docker 网络，目的地址与端口过滤由实际出口执行。HTTPX 直连并验证服务端证书链和主机名，`trust_env=False` 同时用于客户端与传输层。若组织要求显式代理，当前传输须等待获准的兼容出口。首次对目标的 DNS、TCP、TLS 和 HTTP 访问随获准的一次业务请求进行。

在仓库根目录使用下列两套命令。`base` 用于离线登记。`once` 复用已有出网覆盖，并锁定 `limited`、Beat 副本数 0。独立项目名对两者相同。

```sh
base() { docker compose --env-file "${A03_ENV_FILE:?}" -p cnemc-a03-once -f compose.yaml "$@"; }
once() (
  unset CNEMC_EGRESS_NETWORK
  CNEMC_MODE=limited docker compose --env-file "${A03_ENV_FILE:?}" -p cnemc-a03-once \
    -f compose.yaml -f compose.cnemc.yaml -f compose.cnemc.once.yaml "$@"
)
base config -q
once config -q
once config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["networks"]["cnemc-egress"]["name"])'
```

将显示的网络名与管理员核准的出站规则对照。许可材料齐备且获准使用此库后，用 `base ps -a` 确认服务归属。在该验收项目停 Worker 和 Beat，再做离线登记。新库先按项目账号流程建立本地活动身份。`ACTOR_ID` 需有 `manage_grants` 和 `manage_collection`，执行身份需有 `run_collection`，暂停和恢复 Job 的账号需有 `manage_collection`。Session 核验账号需有 `run_collection` 和 `review_observation`。

```sh
base stop worker beat && base up -d --build web redis
base exec -T web python backend/manage.py cnemc_register /dev/stdin \
  --actor "${ACTOR_ID:?}" < "${A03_MANIFEST:?}"
```

核对单一 Job、单城七项、仅 A03、无排期、剩余本轮额度为 1 及实际出口规则。确认 Beat 已停止后才接入出网 Worker。

```sh
beat_id=$(base ps --status running -q beat) && [ -z "$beat_id" ] && once up -d web worker
```

启动成功后复核 `once ps`，用管理 Session 对本 Job 执行前述 `resume`。选许可期内的当前整点（带时区），保存本次唯一的 UUID，显式提交一次。Session 密码由终端读取。

```sh
once exec web python backend/manage.py cnemc_verify_once --username USER --job JOB \
  --target APPROVED_SOURCE_HOUR --request-id MANUAL_UUID
```

验收命令经同源 Session 查回七项，逐项核 PG 修订、数值、单位与该批回执。记录获准的 Run、Window 和字段数。返回异常或超时也应先暂停 Job、停止专用 Worker，核对原 Run 和额度。使用原卷在基础网络上重建 Web 后可只读复核。按条款保管收据，执行到期和撤权处置。

```sh
once stop worker
base up -d --no-deps --force-recreate web
base exec web python backend/manage.py cnemc_verify_once --username USER --job JOB --read-run RUN_UUID
```

## S04 已有读接口

`GET /api/v1/observations/current/` 需要同源已登录 Session。用登记返回的 `product` 主键、`city` 或 `site_epoch` 主键、`indicator`、`report_kind=hourly`、`window_definition=hourly_label_unspecified`、`standard_series` 和明确带时区的 `observed_at` 查询。F 数据另传 `scope=synthetic`。AQI 和等级按实际标准系列选择，普通浓度取 `not_applicable`。CO 使用 mg/m³，其余浓度使用 µg/m³。本地 API 读操作只访问存储。`reason` 可区分显式空窗、争议和现行质量原因。

管理者可用 `GET /api/v1/admin/collection-windows/<id>/` 核 E/O/V、采集时刻、Run、Batch、格与回执。`object_code` 和 `indicator` 可缩小返回范围。获准的原时标、源等级、描述等可选元数据在有对应字段权利的管理回执返回。普通读者使用精确观测读权，现有分析用途另行核验。
