# CNEMC 小时采集接入验收

本次范围为 S04 前接入计划第 4 章步骤 1–6。基线为 `main@b8c30ae7bac22db03bc2d11ed27e4862b390571a`。隔离验证使用自制 F、注入故障、PostgreSQL、Redis、单 Beat、两个 Celery Worker、内网 HTTP 服务和已登录的 Django Session。最终应用和测试源码 SHA-256 为 `15d62d2248e125fd22ac045ac1438e7aa2b72b6a2592cb13e822c93bbeec5ef4`。各 CNEMC 进程用例同时记录构建前后摘要及实际 Worker 镜像 ID。

**交付结论。** 基础采集软件的按城模式和全国模式均为 PASS。限定真实城市采集为 NOT RUN，真实 CNEMC 请求数为 0。长期在线运行及 T11 为 NOT RUN / NO-GO。

## 产品与精确读取

| 产品 | 启用的契约 | 通过受控 HTTP 获取的内容 |
|---|---|---|
| L | `city-list-hour` / A01 | 同刻城市 AQI 和六种污染物等级。 |
| C | `city-live-hour` / A03 | 按城七项。CO 为 mg/m³，另五项浓度为 µg/m³，AQI 无量纲。 |
| 按城 S | `station-city-hour` / A04 | 按城取站点七项，按观测时次的站码与站址期入账。 |
| 全国 S | `station-national-hour` / A07、A10 | A07 取全国包。满足双端点授权和同口径依据时，用 A10 读取合格缺口的指定整点。 |
| T | `city-trend-hour` / A05 | 指定城市近期小时 AQI，按滚动窗口对账。 |

S04 使用 `GET /api/v1/observations/current/`，按产品主键、`city` 或 `site_epoch`、指标、时标、统计窗口与标准系列读单格。F 另传 `scope=synthetic`。管理窗口接口提供 E/O/V、Run、Batch、Receipt 和获准的源描述。精确读可以区分空窗、缺报、待核和争议。登记格式、命令和参数见[操作说明](../operations/CNEMC.md)。

`chain` 在 F 城市 `999999`、北京时间 2026-11-22 14:00 的 A03 把七个精确键查回。AQI 为 43，CO 为 0.31 mg/m³，SO₂、NO₂、O₃、PM₁₀、PM₂.₅ 依次为 12、17、28、21、8 µg/m³。各值与受控包及 API 单位逐项比对。对应 Run 为 `7a9881b4-c9e4-4857-b32d-15438e1c901c`，Window 为 `1`。这组数值来自合成数据。[链路断言](../../scripts/verify_cnemc.py)

## 验收矩阵

| 计划验收项 | 结论 | 核验内容 |
|---|---|---|
| 端点与投影、时间、单位、缺测 | PASS | 固定路径、GET、空体 POST、A10 表单，ISO 与毫秒标签、时区冲突、跨日、空值、`NA`、`/`、删失和零。响应超过行数或字节限额时整包拒收，gzip 返回 `unsupported_encoding`。L 的等级、C 的浓度、T 的 AQI 按产品读取。 |
| 来源登记与出网前判权 | PASS | 空库零 Job，真实 `cnemc_register` 命令两次登记 F 清单得到同一个禁用的 Job，重复 JSON 键遭拒。`off` 下的单次验证命令返回 `limited_mode_required`，新增 Run 为 0。非法目录使整份登记回滚。PG 用例验证全国包遇到仅地方获取权、缺 A07 端点或仅许可部分日期时在恢复 Job 阶段拒绝。S03 V16 的受控源计数显示窄授权的 L、S 和跨日 T 申请均为 HTTP 403，外采增量为 0。 |
| 城市与站点身份 | PASS | A07 交叉核城码，A04/A10 依站码有效期和站址期。站点迁址前后 AQI 分别精确读到 21、34，失去留存权后两期均已处置。 |
| HTTPX、Worker、PG 与 Session | PASS | `chain` 的 A03 由 Beat 触发。一共 6 次内部 HTTP，现有 API 读回 A03 七项。L/C/T 同城同刻 AQI 分别为 44、43、45。停止两个 Worker 和内网 HTTP 服务并重启 Web 后，仍可读原 C 的 AQI，查询更早的显式空窗返回 `no_observation_at_time`。 |
| 幂等、修订与质量 | PASS | A07 首次 `14/7/7`，A10 同窗补成 `14/14/14`。重复 UUID 沿用 Run。等价时标重读只添回执。另一受控用例中 `NA` 计入 O，无序异值使精确读返回 `unresolved_dispute`。旧 F 的 v1 → v3 → v2 用例保留 v3 水位。 |
| 不同处理选择和应到分母 | PASS | 两个主键不同的人工 Job（2、3）在 `E=null` 且选择相同的情况下并发提交，得到一个窗口、一份预留、一次 HTTP 和一个认可格。异选择在预留前拒绝。仅授权一站 AQI 时 E/O/V 为 `null/1/1`。另有独立名册时，确有处理权的名册外格可精确读取，名册内覆盖保持 `1/0/0`。 |
| 趋势窗和等级 | PASS | 在北京时间 14:00、15:00 两个实际整点的相邻 T 窗口，12:00 的精确读从 `no_observation_at_time` 变为 AQI 51。该修订在两批的回执数分别为 0、1。两次 HTTP 得到 25 个唯一小时键，原窗覆盖仍为 `24/23/23`，新窗为 `24/24/24`。源等级冲突写 `source_grade_conflict`，该格 V 为 0。标准未知时源标签按回执权可读，平台颜色为空。 |
| 有限补采与请求恢复 | PASS | 在 A07 新鲜锚和两小时回看策略下，超限三小时的 A10 Run 为 `backfill_unavailable`。保留 A07 获取权、撤回 A10 后，窗内目标收到 HTTP 403 `use_denied`。两种拒绝的 A10 HTTP 和 PG 预留增量均为 0。A10 混入错时行时整包拒收。空包保留缺口，重复申请的 A10 请求数为 0。跨地方日的 PG 用例设置单页上限为 1，首轮拒绝无权窗，次轮从已存游标接续并仅登记获准窗。注入的 HTTP 适配器记录该窗一次 A10 调用，随后三轮新增调用为 0。断档游标、停机跨槽、A04/A07 模式互斥有 PG 用例。 |
| 请求额度与故障 | PASS | `quota` 的首轮 503、后继重试、同 UUID 重投、新 UUID、Worker 重启和跨 5 秒滚动周期，合计 1 次内部 HTTP 和 1 份 PG 预留。耗尽绝对总额的后继 Run 与新手动 Run 均返回 `validation_budget_exhausted`。 |
| 撤权、到期与冷启动 | PASS | PG 实测部分读权、展示与留存的分别撤回、跨产品目录、到期清理、额度关联失权。全国全量测试在关闭两个 Worker 后撤回留存，冷启一台 Worker，9.72 秒完成处置。旧 Job 已禁用，所撤回的观测、目录、回执及预留数均为 0。 |
| 全国全量及局部处理 | PASS | 1,288,805 字节，1,601 站 × 7 项，1 次 HTTP，88 块，11,207 个认可格、观测键和回执。全量 E/O/V 为 `11207/11207/11207`，213.62 秒完成。在窗口租约更新处测得最长 PG 事务 6.445 秒，最长续租间隔 6.748 秒，两个 Worker 中的最高 cgroup 内存为 233,877,504 字节。测量区间内 PG 调用 393,943 次，其中目录查询 60,946 次。另一个同规模局部授权用例仅写一格。 |
| 数据库迁移 | PASS | 定向 PG 用例从 `collection.0006` / `observations.0009` 创建待投递 Run 和既有授权，升级后 UUID、状态及条款保留，新端点为空、无验证预留。一次性库回退恢复旧冻结触发器，再升级成功。S03 V01 同时演练带历史数据升级与独立空库回退再升级。 |
| S03 进程回归 | PASS | V01–V19 全部通过。主项目 `s03v8a6e96ef4040` 记录 197 个检查点，产品子项目 `s03vb71ae3d99c90` 记录 173 个。涵盖 Beat 和人工竞争、Redis 丢投、Worker 失租约与 ACK 前退出、分块断点、限流等待、撤权和独立复核。两个项目的运行首尾源码摘要相同，自有资源已回收。 |
| V20 真实 H 档案回归 | NOT RUN | V20 使用独立的 S02 验证器，本轮未提供它所需的获准 UCI 原始档案。本次 S03 回归证据范围为 F/I。 |
| 单元、PG 与静态检查 | PASS | `python -m pytest -q tests` 通过 311 项，耗时 174.67 秒。CNEMC 的 `pg` 用例在 Compose 中另执行 50 项定向测试。Ruff、121 个文件格式、mypy 配置所列的 3 个文件、`uv lock --check --offline`、Django system check、迁移漂移和 Compose 出口拓扑检查均通过。 |
| 独立实现与验收审查 | PASS | 六项实现问题已返修，三项矩阵缺口已通过受控进程探针补证。独立审查者在同一冻结源码上关闭 CNEMC-R01 至 R09。[独立终审](../../.tasks/cnemc-integration/reviews/final.md) |
| 真实 CNEMC 单次城市链 | NOT RUN | 实源请求 0 次。已有接口研究及许可声明。可登记的条款版本、本轮获准端点与地域、用途和受众、留存及处置、出口、访问频率与累计额度仍待落实。实际 Worker 出口的 DNS、TLS 与端口核验为 NOT RUN。A07/A10 的实源同产品认定另需证据。 |
| 三地区跨期验证与 T11 | NOT RUN / NO-GO | 三地区各两个真实发布周期、独立批准的试运行和自然 168 小时对账尚待执行。 |
| 可选第 7 步 | NOT RUN | 日产品 A02/A06、单站趋势 A08 和在线目录 A11/A12 留给有相应用途及权利的后续工作。A09、长期档、导出、备份和地图分别遵从后续阶段边界。 |

## 实施差异

- `observations.0010` 在获取授权上记录端点、单份额度、跨清单分配及单向关闭状态。`collection.0007` 冻结 Job、Window 和 Run 契约，允许受控脱敏。`collection.0008` 增加停机跳过区间和 A10 跨轮游标，从 PG 恢复扫描进度。
- 配额记录的留存按 `validation_ledger` 或 `request_ledger` 核验。丧失相应权利时清除关联或预留时间，封存失权的限定份额。清除已用额度的预留后，来源冷却至少 24 小时，覆盖可登记的最长滚动周期。
- 已知名册只计算名册内覆盖。窗口另行标识有处理权的名册外格。原 S03 的 F 契约及调用方式继续复用。
- 验收入口为 `scripts/verify_cnemc.py`。补充用例核对 A10 空包、质量冲突，并检查受限合成标记在 PG、Redis、服务日志和测试收据中的命中数，四处均为 0。[补充探针](../../.tasks/cnemc-integration/evidence/probe.py)

各产品的 Run 和 Window ID、实际镜像、HTTP 次数、PG 计数和内存用量见[冻结版本验收收据](../../.tasks/cnemc-integration/evidence/acceptance.json)。

相同选择并发、A10 拒绝及 T 重叠小时的精确查询可用[窗口探针](../../.tasks/cnemc-integration/evidence/window_boundaries.py)复跑。
