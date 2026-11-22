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

取得本轮城市、端点、完整响应获取范围、字段与受众、留存处置、出口和绝对总额的依据后，由操作者私下登记人工 A03 Job。`compose.cnemc.yaml` 仅用于已有受控出口的环境。`CNEMC_EGRESS_NETWORK` 应指向已经核验的目标地址、TLS 和端口白名单网络，覆盖文件只把 Worker 接入该网络。设 `CNEMC_MODE=limited`，验证前复核服务端 Job、用户及授予总额。

在本地 Web 容器中显式执行单次命令，Session 密码从终端读取。以下是命令格式，替换为本次实际登记值。

```sh
python backend/manage.py cnemc_verify_once --username USER --job JOB \
  --target APPROVED_SOURCE_HOUR --request-id MANUAL_UUID
python backend/manage.py cnemc_verify_once --username USER --job JOB --read-run RUN_UUID
```

第一条经同源 Session 提交并查回七项，在获准的回执范围内逐项核 PG 修订、数值和单位。第二条用于关闭采集出口、冷启后的回读。命令只输出 Run、Window 和字段数。复核异常、缺测或已有争议时返回非零退出码。实源收据的保管遵从本次私有许可。

## S04 已有读接口

`GET /api/v1/observations/current/` 需要同源已登录 Session。用登记返回的 `product` 主键、`city` 或 `site_epoch` 主键、`indicator`、`report_kind=hourly`、`window_definition=hourly_label_unspecified`、`standard_series` 和明确带时区的 `observed_at` 查询。F 数据另传 `scope=synthetic`。AQI 和等级按实际标准系列选择，普通浓度取 `not_applicable`。CO 使用 mg/m³，其余浓度使用 µg/m³。本地 API 读操作只访问存储。`reason` 可区分显式空窗、争议和现行质量原因。

管理者可用 `GET /api/v1/admin/collection-windows/<id>/` 核 E/O/V、采集时刻、Run、Batch、格与回执。`object_code` 和 `indicator` 可缩小返回范围。获准的原时标、源等级、描述等可选元数据在有对应字段权利的管理回执返回。普通读者使用精确观测读权，现有分析用途另行核验。
