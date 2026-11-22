# S04 Implementation Plan｜统一查询与用户业务闭环

**2026-11-22 · GPT 独立 Plan Review 最终结论为 APPROVED。** §2 记录首次送审的基线。C01～C04 已关闭（见 §7），用户已授权实施 P1～P7。

## 1. 交付给用户的行为

普通用户登录后，在获准的全国 L 列表选择一个实际观测时刻，按城市或地域筛选，进入城市 C 七项、独立 T 趋势和所属站点目录，再进入一个确定站址期的最近值、历史与分页明细。用户可修改自己的资料和密码，收藏城市或站点，在“我的关注”复访同一实体。每次读取都按当时的会话、对象、用途和数据版本重新选数。

| 阶段依据 | S04 退出时的可观察结果 |
|---|---|
| M01/M02，D02、D09 | 相同城市和时刻可以有不同的 L/C/T 值。列表、排名与分布共用同一 L 快照，排名和分布仅取列表中获准汇总且同标准可比的城市。详情能说明七项和趋势各自来自哪里。 |
| M03/M06，D03～D05、D07 | 值能追到来源、产品、站址期、指标、单位、统计窗口、标准、真实时标、当前修订和用途依据。显式空窗保持为空。最近有效按指标标时。M04/M05 以后能按同样条件取明细。 |
| M07，D10、D11 | 资料和收藏归本人。并发收藏只产生一个意图。更名、迁址、停站和撤权后的复访有可解释的结果。退出、改密、停用和业务权限撤回可以用原 Cookie 验证。 |
| 后续阶段 | 同层比较、E/O/V/E共/P 统计结论、私有分析方案及 CSV 属 S05。全链失效、质量工作台、备份/恢复及生产采集故障衔接属 S06。百万行整体负载和独立交接属 S07。Vue、地图、ECharts、浏览器及整机验收属 S08/S09。D 日报维持独立准入。T11 两道准入和自然 168 小时另验。 |

硬依赖是 S01 身份和 S02 可查询记录。S03 的回执可补充采集血缘，用户查询只使用 PG 中获准的存量。设计依据见 [V3.0 §3、§6～10、§12～14](../design/城市空气质量监测数据分析平台_系统总体设计方案_V3.0.md) 和 [总体实施方案 §4、§5 S04、§7～10](../design/总体实施方案.md)。

## 2. 本地基线与证据边界

### 2.1 本轮核查

| 检查 | 2026-11-22 结果 | 对计划的影响 |
|---|---|---|
| Git / 指令 | `main@f02c7bb36a17ced5238d3ae3cae4d027c499c873`。`HEAD`、本地 `origin/main` 及只读 `git ls-remote origin refs/heads/main` 一致，左右差 `0/0`。起点 index、跟踪修改和非忽略未跟踪项均空。`docs/plans/` 无已有 S04 计划，相关目录无更深的工程指令。 | 根 `AGENTS.md` 适用。`.env`、`tmp/` 私有证据和本地 `.tasks/cnemc-integration/` 均属既有忽略项，后者记录 `complete`。本轮唯一仓库文件增量是此计划。 |
| 工具与配置 · PASS | `.python-version` / `.venv` Python 3.13.18，uv 0.13.6。`uv lock --check --offline` 通过。环境包为 Django 5.2.19、DRF 3.18.3、psycopg 3.3.9、Celery 5.7.0、pytest 9.2.0、pytest-django 4.14.0、Ruff 0.17.6、mypy 2.4.1。V3.0 SHA-256 与实施方案记录的 `4373fca3…79bf6` 一致。 | 复用 `pyproject.toml`、`uv.lock`、PG ORM/SQL 和现有工具。 |
| 运行拓扑 · PASS | Docker 客户端/引擎 29.9.3、Compose 5.7.0。标准项目 `air-quality-platform` 的 db/redis healthy，Web 在 `127.0.0.1:8000`。`/health/ready/` 实回 Web/PG `ok`。migrate 已退出，网关 exited，专用 worker created，标准项目无运行的 Worker/Beat，Web `CNEMC_MODE=off`。另有 `s03v3a85580b59ca` 验证栈仍运行、多个既有卷。 | 环境核查只使用目标项目。新测试用自有临时 PG，不复用任何现存卷。Compose 服务清单和实际运行态分别核对。 |
| 数据库与迁移 · PASS | 标准 Web 进程内带 `PGOPTIONS='-c default_transaction_read_only=on'` 执行 `manage.py check`、`migrate --check`、`makemigrations --check --dry-run`。`showmigrations` 显示 `sessions.0001`、`observations.0010`、`collection.0008` 已应用，`accounts` 无迁移。PG 16.16，`btree_gist` 1.7。Web 镜像与工作区的 `backend/**/*.py`、`tests/**/*.py` 两组相对路径与内容摘要分别一致。 | 有可供核验的当前模型和数据库。新迁移从此叶节点在隔离库演练。 |
| 标准库现存产品与授权 · PASS | 10:50 UTC 在应用连接以 `REPEATABLE READ, READ ONLY` 查目录、既有 Run 和血缘。正式产品目录当时仅见 C。北京 `cnemc/city-live-hour`，`110000`，观测 `2026-11-22T17:00:00+08:00`，原报告 Run `4592102d-dcf2-4fe0-b8a6-1399c154c2d9` 仍为 `finished/responded`、A03、attempt 1。该产品只有此 Run，Job 未启用且无到期排期。七指标逐一经 `policy.applicable` 检查 **当前** `retain_normalized + display` 的 `REQUIRED` 字段后，对应同 Run/Batch 的七个已接受格和当前 valid 修订均匹配，AQI 标准为 `unknown`。 | 可作为限定对象/时次的可选 C 读例。对同一 AQI 范围，`catalog`、`primary_pollutant`、`source_grade`、`source_description`、`collection_ledger` 均未同时满足两种用途。普通响应应省略相应字段。核查未导出测值、凭据或会话。 |
| 当前代码的隔离回归 · PASS | 以已缓存的项目 PG 镜像启动 `--network none`、PGDATA tmpfs 的一次性 PG 16.16。临时非超级用户拥有自己的数据库和建测试库权限。应用仅共享该回环网络。运行 `pytest -q -p no:cacheprovider`，覆盖 `tests/test_accounts.py`、`test_observation_{access,catalog,revisions,rules}.py`、`test_history_policy.py` 和 `tests/collection/test_cnemc_wire.py`，**105 passed**。关闭 cache 插件后镜像原有 `cache_dir` 配置产生 1 条 warning。 | 证据为既有 S01/S02/CNEMC 合成规则和真实 PG 回归。另在同一临时 PG 新迁移业务库，以 `tests/s02_support.py` 写自制 F 零值，通过回环 HTTP、真数据库 Session 验了匿名拒绝、无 CSRF、注册夹带权限、注册 USER、登录、`current/` 与 PG 零值对账、相邻小时空集、缺合成域拒绝、退出后重放旧 Cookie 被拒。自有容器已回收。 |
| NOT RUN | 本轮未在标准库登录、重启 PG 或重演 A03。S01～S03 全矩阵、真实 H 导入、S04 新接口、性能大测和 T11 亦为 NOT RUN。核查时当前 shell 无已提供且可读的 `S02_UCI_ZIP`，标准库无 H。 | H 原件和使用范围在实施时再核对。可先用隔离 F 做软件切片。H 专项缺输入时保留 NOT RUN，不以既有收据或 F 填写新版本 H 的结果。 |

初次只读库存探针误用了 `Run.state`，在读取账本结果前中断。按 `collection.models.Run.status` 修正探针后，以上同 Run/同指标的完整只读核对通过。

### 2.2 已验收阶段与当前工作树的关系

| 既有记录 | 本地复核与沿用边界 |
|---|---|
| S01 独立验收 PASS，P01～P07 / V01～V11，源码 `0656071` | `tmp/s01-evidence/` 两轮 `checks.json` 哈希均与验收报告一致。当前仍有原路由和登录/退出链。本轮回环 HTTP 是新增的窄范围实测。见 [S01 计划](S01_IMPLEMENTATION_PLAN.md) 和 [验收、交接](../verification/S01.md)。 |
| S02 独立验收 PASS，P01～P08 / V01～V16，源码 `ca8b3a2` | 本地 `S02_REVIEW_PACKAGE.zip` SHA-256 与报告一致。UCI H 两站、两窗和 CO 换算是该版本的证据。当前有目录、许可、修订、导入实现及可复用 F 写入服务。见 [S02 计划](S02_IMPLEMENTATION_PLAN.md) 和 [验收、交接](../verification/S02.md)。 |
| S03 独立验收 PASS，返修 `bd955c1`、交接 `746049b`、封版 `b8c30ae` | F01/F02 已有定向复验。按 `S03_evidence.json` 的 `runs` 重算原四组 checks/result 哈希，8/8 匹配。V20 沿用首次 H 证据。`b8c30ae..HEAD` 已改 `observations/{queries,policy,disposition}.py`、采集、迁移等，当前 `scripts/s01_runtime.py:source_hash()` 为 `ce39b0d82c297c94463bc35917b978cd391fb707fd518d9ca81df72f135cfddd`，与 S03 返修冻结指纹不同。旧 Worker 混跑等未覆盖项仍归原阶段。见 [S03 计划](S03_IMPLEMENTATION_PLAN.md)、[S04 交接](../verification/S03.md) 和 [证据索引](../verification/S03_evidence.json)。 |
| CNEMC 基础软件及北京 A03 单次实源 PASS | 接入提交 `6395e39`、血缘核对 `3324d48` 已在当前历史内。接入前核查和实源报告“请求前快照”早于该报告首节的单次入库/Session/重启冷读。当前只读 PG 支持该单次血缘结论。其他产品实源、跨期、长时间运行及可选端点另有准入条件。见 [接入计划](CNEMC_IMPLEMENTATION_PLAN.md)、[接入前核查](../verification/CNEMC_PRE_S04_AUDIT.md)、[软件验收](../verification/CNEMC_IMPLEMENTATION_ACCEPTANCE.md) 和 [单次实源验收](../verification/CNEMC_FIRST_REAL_COLLECTION_ACCEPTANCE.md)。 |

### 2.3 能力盘点

| 判定 | 源码、现有行为与缺口 |
|---|---|
| 已实现，可复用 | `accounts/views.py` 的 `credentials/register/sign_in/sign_out`，`accounts/api.py` 的 `CsrfSessionAuthentication`，`config/settings.py` 的 `IsAuthenticated`、有界 JSON、Session Cookie 和密码验证器。注册只接收用户名/密码。`policy.actor` 重新读 active 用户及具名权限，staff 标记本身不构成业务授权。现有 URLConf 未挂 Django Admin。 |
| 已实现，可复用 | `observations/models.py` 的 `Source.namespace`、`Product(source,code,family,shape,level,synthetic)`、`UseGrant`，`policy.locked_product/applicable/clock`。产品政策、城市/站点键与修订分表，`revisions.tables/key_scope`、`catalog.identity_instant/resolve`、`normalization.INDICATORS/WINDOWS` 均有实际调用者。`QualityIssue` 可挂观测键，也可只关联批次和作用域。 |
| 已实现，需适配 | `observations/queries.py:read_current(actor_id,params,purpose)` 在事务内锁产品，精确接收 product、city 或 site_epoch、indicator、report_kind、window_definition、standard_series 和一个 `observed_at` 或 `local_report_date`。F 强制 `scope=synthetic`。`dimensions` 按时标找有效代码，`applicable` 核留存与展示，查不退役、不争议的当前 valid 非空值，返回前按 PG 时钟复核。空格、争议和其他质量给 reason。`input_version` 可复用。`project_revision` 包含 `source_description` 并展开整份 provenance，面向普通用户需逐字段投影。它没有范围、最近、分页、一般用户的回执字段或完整获取时刻。`read_evidence` 和 `collection.queries.read_window` 是管理投影。 |
| 已实现，需适配 | L/C/T 通过 CityObservation，S/H 通过 StationObservation 和 SiteEpoch 精确读，F 以同形独立产品入正式观测表。`backend/samples` 的 S01 F 端点与这些表有独立契约。CNEMC 写端 `adapters/cnemc.py`、`writer.metadata/write_cell` 将有权留存的原始时间表述、AQI 源描述/等级/首污放入 `Receipt.collection_metadata`。`Batch.fetched_at` 与所选修订通过 Receipt 相连。重复、迟到也可能有 Receipt。 |
| 已实现，需适配 | 标准库 `pg_indexes` 实见 `cityobservation_instant/day`、`stationobservation_instant/day` 对应的四个索引，以及两张修订表的 current 索引、码期/站址期排斥索引。PG 按产品＋实体/站址期＋指标＋报告类型＋窗口＋标准＋时标/报告日有条件唯一索引，每键至多一个 `is_current` 修订。标识和站址有半开区间与排斥约束。管理证据有 `after` 等局部分页，`REST_FRAMEWORK` 未配置公共业务分页。`City.geography` 是来源登记的地域，不能假定为全国行政区划表。 |
| 尚未实现 | 统一批量授权选数、时段/最近/快照、普通用户质量明细、排名、城市/站点业务 API、批量关注摘要、本人资料/改密、收藏表和路由。无可据以统一判定“实时”的正式阈值。 |
| 待运行验证 / 冲突点 | H 在新版本的离线读、分页/索引代价、并发撤权和收藏删除路径见第 6 节。`disposition.reconcile_catalog` 会删除站点/部分城市，或将被冻结 Job 引用的城市匿名化。收藏若用保护删除的引用会阻断现有处置。真实 C 仅授部分字段、AQI 标准未确认。以上影响投影和验收证据，不阻塞制定计划。 |

## 3. 统一选数契约

### 3.1 先界定可见集合，再挑选记录

1. **视图只翻译请求。** 在 `observations` 内增加选数模块并由第一个可用的范围明细端点驱动。规范条件包含主体、产品与来源、对象层级和 ID、站址期、指标、报告类型/窗口、单位、标准、质量、时间模式及分页。只接受服务端绑定的用途。普通视图面向 `display/local_authenticated`，M01 派生汇总另按 §3.3 核平台计算用途。分析和导出的操作/受众供后续单独接入。`accounts` 只处理私有资源、会话和所有权，摘要批量调用同一选数服务。
2. **共享现有判定。** 将 `read_current()` 内的精确作用域鉴权、当前修订资格与安全投影提成内部共用操作。保留精确端点的参数白名单、日/小时锚、`count/results/reason`、版本与 400/403 契约。新服务直接批量读同一账本。使已有 `read_current(...,purpose='analyze')`、管理 evidence、S01 F、S03 `read_window`、`cnemc_verify_once --read-run` 的调用和权限保持兼容。
3. **按实际作用域裁剪。** 在 `transaction.atomic()` 中按 PK 递增锁定涉及的 Product，复核 `actor`。批取未撤回的 grants、产品来源、对象的全部有效码及站址期。`applicable(..., rows=...)` 假定调用方已按产品限定 rows。共用读端装载器从 `product.usegrant_set` 取得该产品的所有相关允许和拒绝，按 `product.pk` 分桶并断言每行 `grant.product_id` 匹配。按产品源时区的 `source_local_date`、地域、**行的 object_code**、指标、字段和当前用途生成可读范围。`retain_normalized(platform)` 和 `display(local_authenticated)` 分别需要一个覆盖整组必要字段的完整 allow。`valid_from` 含、`valid_until/retain_until` 不含，有交集的 deny/限制优先。保留 hard/recoverable 已有规则。标准值字段沿用 `REQUIRED={normalized,provenance}`。字段不齐的不同 grant 不能凑成一次完整授权。
4. **让 PG 在许可内做选择。** 用选定对象/产品的有效码、授权数据日边界和标识有效期形成有限区间。对每个具体地域、码、指标及字段组合，把 grant 的 `data_from/data_until` 切成判定不变的片段，批量预载后调用同一个 `policy.applicable`。把允许片段作为参数化关系/条件与键、当前修订做 `EXISTS`/半连接，同一行即使有多项许可也只出现一次。按键存的 `source_local_date` 判数据日，另按 `directory_at` 判目录时刻，已知右标签用末端前 1µs。同站多码必须同时匹配行码、来源、对象和时效。不以观测是否存在判权。最近查询以许可区间配索引求极值，无需逐日或逐历史行循环。先对照原单格决策再开放集合查询。
5. **在允许集内**选时、取最近值、筛选、排名、计数、分页和判下一页。全国快照先定锚，再按名称/地域筛选（§3.3）。按投影区分普通数值、可选元数据、目录和派生展示。用作筛选、排序或分母的字段本身也须可用。重取用户状态，结束前用新 `clock()` 复核影响结果的所有范围、可选字段和署名，包括支撑计数或空集判断的范围。遇到时钟越过许可边界，重算可见结果或返回无数据细节的拒绝。产品锁持有至普通 DTO、分页和统计投影完成。读者不用 Celery 或外部 HTTP。
6. **撤权语义。** 有权部分形成投影，响应中不列被排除对象、指标、原数或排除数。请求的范围完全无相应用途时统一 `403/use_denied`，产品不存在、猜错来源/对象、F 域不符采用同样的无枚举响应。元数据不明或未获准时用同一通用缺省，不揭示“字段实际存在”。新增私有/观测响应含 `Cache-Control: private, no-store`，包括权限失败。当前请求与后页都重新判权。内部沿用 `input_version`（键、修订、资格版本、权利版本和依据）。新公开投影只给本行允许披露的版本/署名，不公开全产品授权计数或后台清单。

### 3.2 时间、版本、质量与血缘

| 选择 | 对外语义与实现条件 |
|---|---|
| `mode=window` | 强制带偏移的 ISO `start,end`，按 `[start,end)` 比较记录真实 UTC 时标，逐项保留来源时区、标签位置及原窗口含义。标准不明仍标 `unknown`。整段有权但无格，返回 `200`、空数组及空窗原因。可见子区间稀疏就保留断档。显式时间不会调用最近选择器。S04 新通用入口提供小时/已登记滚动序列，日报地方日继续由既有精确读独立支持。 |
| `mode=latest_valid` | 输入同质系列及可选 `as_of`（首请求默认 PG 当前时刻），按 `observed_at <= as_of`。划分 `(product,city/site_epoch,indicator,report_kind,window_definition,unit,standard_series)`，在可读 valid 行按时间降序＋唯一键取首行。每指标分别算 `age_seconds = evaluated_at - observed_at`。`as_of` 是选数上限，`evaluated_at` 是本次展示时钟。旧值允许返回，显式以正在回访的站址期为限。 |
| 当前资格 | 每键 `retired=false`、当前修订 `is_current=true,candidate=false`。可靠值还须 `disputed=false, quality=valid, value IS NOT NULL`。真零用 Decimal 字符串。受限明细可选 `quality=all/valid/missing/censored/suspect/invalid/disputed`。缺测为 null，删失保留 `<` 和界限，待核峰值仅在显式质量明细按权披露且标为不可比较。争议只给已授权状态，隐藏候选值及原当前值。退役键排除。只有 Batch/QualityIssue/拒收事件而无观测键时没有读数行，不能制造 `invalid` 或零值。 |
| 单位/标准 | 字段沿用现有规范单位 `1`、`grade`、`mg/m³`、`µg/m³`。L 的 `*Level` 只作等级。O₃ 小时/滚动 8h/24h 各自成列。AQI/等级采用有来源或文档证据的标准与规范化结果。未知版可展示获准源描述/数值，等级推导、颜色、同标准排名均留空。六浓度的标准系列为 `not_applicable`。固定单系列的请求发现不相容参数返回 400，未指定的多系列分组具名返回。 |
| `fetched_at` 和可选来源字段 | 批取与当前 revision、同产品及键匹配、实际产生该修订的 Receipt（初次 `first`、订正 `content_changed`），稳定以该回执所指 `Batch.fetched_at` 标注获取/本地档案读取时间及依据。线上要求同一 Run/Batch、RunItem 码/指标/时标匹配。回执的 `duplicate/watermark_advanced/late/retired_mapping` 可指向当前 revision，不能拿最后回执的时间/文本冒充产生当前值的来源。人工裁决或映射修复无可证原始关联时返回 `null/unknown`。来源发布时间单独可空。执行时间、Revision.created_at 和请求时钟都不是获取时间。 |
| Receipt 和目录字段 | `writer.metadata` 各字段独立留存。源方 `source_grade/source_description/primary_pollutant/source_time` 只有当前修订的对应回执和对应字段的有效留存、展示依据同时成立才投影。同一修订仅有后来的不同描述而无法归属时，留空。规范化 `observed_at`、时区和窗口依据与回执里的原始时间文本分列。新旧普通读端共享白名单。旧 `project_revision()` 今天会展开修订上的 `source_time`、`source_description`，不能据此断言字段级拒绝已实现。兼容策略为保留原键名、已获准的规范化来源标签及空字符串类型，独立受限的原始标签/源描述在缺权时返回 `""`。这是针对读投影的安全收紧，要与未受限样例逐字段回归。治理端沿用独立授权。`Batch.fetched_at` 作为获准的规范来源信息投影，无法证实或披露时返回 null。管理 `review/collection_ledger`、原文、定位符、日志不进入用户响应。目录名/地域/原码/站址期显示按 `catalog` 做字段检查，坐标还要有允许用途、可信位置证据和已知 CRS。只有 `normalized/provenance` 时仍可用已知内部 ID 读授权观测。目录过滤及下钻不能旁路此区别。 |
| 新鲜度与故障 | 返回每值年龄和 `freshness=unknown` 直至该产品有实证阈值。确有产品阈值才给 `fresh/stale`。断源/结构异常是另一维 `source_status`，仅据获准披露的独立证据标注。S04 用显式标记的 F 故障场景测试“断源＋仍有旧值”，没有证据时为 `unknown`。已存行可读时正常响应，PG 不可用由 `config.errors.BoundaryErrors` 返回 `503/database_unavailable`。 |

### 3.3 三个业务读端

**全国概览（M01）。** `at` 精确指定 `snapshot_at`。省略 `at` 时，在同一 L 产品、小时窗口、显式 `standard_series`（若有）及主体实际获准逐值读取的范围内，取当前可靠 AQI 中 `observed_at <= as_of` 的最大时标。首请求省略 `as_of` 时取 PG 当前时刻。显式选择标准可以改变候选时刻。固定锚后才按获准目录匹配 `q` 和 `geography`，再判汇总资格、排序、分页。同一快照内更改名称或地域筛选时，沿用返回的 `snapshot_at` 作为 `at`。排序方式和汇总许可也不参与选锚。筛选仅命中更早时刻的城市时，有权请求在原锚返回空列表。较新时刻只有逐值展示权也选该时刻。有权但无可见值时，指定时刻保留 `at`，自动选时返回 `snapshot_at=null`。

- 默认 `order=city`。列表每个 City 一项，以 `City.pk` 排序，同城多个合格系列在行内具名返回。列表的 AQI、源等级/描述和首要污染物各按 §3.2 投影，L 污染物等级保留 `grade`。实际地理范围取有展示权的 `City.geography`。
- **派生计算与展示。** 三个城市数、排名、等级分布均属 `overview_summary`。每项输入先通过 §3.1 的规范值留存和逐值展示检查。聚合前另核同一范围的两项来源许可。`process_local/platform` 的条款须明确允许平台计算该概览，`display/local_authenticated` 的条款须明确允许向登录用户展示结果。每项操作各需一条覆盖 `REQUIRED` 且在 `fields` 中明列 `overview_summary` 的完整 allow。来源条款另需统计许可时，计算依据须包含该项许可。响应前复核两项用途。此用途限于 M01 即席汇总，普通用户沿用原有会话权限。
- `policy.applicable()` 的 allow 分支以 `"*" in grant.fields` 匹配任意请求字段，`effect="deny"` 或非空 `restrictions` 则按相交范围和字段否决。仅对上述两次派生判定，从 §3.1 的本产品分桶保留**全部 deny 和带限制的行**，其余仅取字面包含 `overview_summary` 的 allow，再以 `REQUIRED ∪ {overview_summary}` 分操作调用 `applicable()`。这样旧 `fields=["*"]` 仍适用于原有逐值用途，不能单独授权汇总。无需改写历史许可。
- `order=aqi_desc` 要求显式指定一个已确认标准，只对该组已获准汇总且可比较的城市分页。`dense_rank` 按 AQI 降序计算，数值相同并列，展示顺序再以 city ID 升序固定。`order=city` 中可在行内附加各自有权的分组名次。相同标准条件下两种顺序共享锚点。

| 固定快照、目录筛选后、分页前的计数 | 分母与披露条件（均按 City 去重） |
|---|---|
| `covered_city_count` 可见城市覆盖数 | 有可展示可靠 L AQI 的全部城市。能证实汇总许可覆盖全部可见城市时，才在获准集合上计算并返回该总数。部分城市仅可逐值展示时，此字段为 `null`。 |
| `summary_eligible_city_count` 派生汇总合格数 | 可见城市中，至少有一项可靠 AQI 同时获准计算和展示汇总的城市。未知标准可以计入此数。 |
| `ranked_city_count` 实际参与排名数 | 每个已确认标准组内，实际有该组 `comparable` 且获准汇总的可靠 AQI 的城市。每组等级分布的合计等于该组排名数。未知标准不推导颜色、等级或名次。 |

仅在可披露覆盖数且有独立可披露的预期名册时标注 `partial/complete`，依据不足时 `expected=null, completeness=unknown`。整项无汇总权时，摘要只返回 §4.1 约定的不可用状态。

**城市详情（M02）。** 单一来源 City ID，选择具体 C。展示同一 C **观测标时**的七个固定指标槽，默认选择可见 C 数据的最新报告时刻，可显式 `at`。该时标缺少的指标给统一不可用槽，不用更早 C、L/T 或站点数据补齐。AQI 按选定标准，六浓度按 `not_applicable`。若同一时刻有多个 AQI 标准且请求未指定，AQI 槽内按标准列出各自版本，六浓度仍只计一次。等级/首污按当前修订血缘。以单独 T 产品给近期 24 个小时槽，显式小时锚 `anchor` 或最近可见 T 观测小时，响应标明锚的依据。包含锚时 `t` 的区间为 `[t−23h,t+1h)`。以 T 自身观测键去重，重叠采集窗和回执不会增加点。C 历史由同一明细服务限定 C 和指定时间窗，T 与可选日产品不会混进 C 历史。站点入口另用获准的同来源、同城市 SiteEpoch 目录，无名单时也可单独读有权的 C/T。

**站点详情（M03）。** URL 使用 `Station.pk`，读数请求同时固定 product 与其所属 SiteEpoch，返回来源命名空间和获准观测的稳定内部 ID。站点原码、站名、城市名和期起止按 `catalog` 投影。仅有数值/来源权时允许按已知期 ID 查数，目录字段留空。当前目录按 `active_period @> at` 选择站址。指定历史 epoch 可直接读，跨迁址的大窗按明确选中的期分别请求。右标签恰在边界时沿用 `catalog.identity_instant`，跨期未获证实的窗保持空。相同站码的新实体、不同来源同名、A04 按城 S 和 A07 全国 S、同源不同 H 数据集均独立。H 只有六浓度，UCI reporting identity 只作历史身份，缺坐标或 AQI 就保持缺项。复访无现行期的站点可进入获准的历史期。逐指标最近值与指定窗走 §3.2。

## 4. API、账户与存储设计

### 4.1 读端请求和返回

以下均位于 `/api/v1/`，同源 Session。`product` 为具体 Product PK，服务端校验产品与对象来源、层级和形状。默认全国入口选登记的 `cnemc/city-list-hour`，已知 ID 的其他受权产品可显式指定。F 一律显式传测试产品和 `scope=synthetic`。导航链接传播对象、所选观测时刻或时窗和 F 域。L 行下钻到 C 或跳转到 T/站点目录时，以同一来源、同一 City 和目标形状查获准产品，链接使用**目标产品 ID**。没有可公开的目标就只保留当前视图。有多个合格 S 产品时给明确选项。请求 C、T 或目录的拒权只影响相应入口。

| 方法与拟增路径 | 必要参数或默认行为 | 成功返回 |
|---|---|---|
| `GET overview/national/` | L。`at`（精确）或可选 `as_of`（找最近同刻），`standard_series`、`window_definition`。定锚后应用 `q`、`geography`、`order`、分页 | 返回 `snapshot_at`、实际筛选条件、获准列表和城市入口。摘要按 §3.3 给出覆盖数、汇总合格数及分标准的排名数、分布和名次，翻页只切明细。 |
| `GET cities/{city}/` | C。`product`、可选 `at/as_of`、AQI 标准系列。`at` 与 `as_of` 互斥，同时传入返回 400 | `city`、`snapshot_at`、七项固定槽、产品、时标、等级/首污和可用的历史、T、站点入口。每栏独立说明可用性。 |
| `GET cities/{city}/trend/` | T。`product`、可选小时 `anchor`、`standard_series` | 带锚与区间的 T 点、缺口、产品/时标与来源，最多 24 个小时位置。缺口只以授权范围内的“无可见点”表达。 |
| `GET cities/{city}/stations/`，`GET stations/{station}/` | 目录指定 S 或 H 产品和目录时刻 `at`。详情指定 `product,site_epoch`，可传 `as_of,indicators`。稳定站点入口未指定期时只给该站有权的期选择或停用提示 | 目录/期列表稳定按实体 ID、期起点和期 ID 排序。详情含已核身份、每指标最近有效值和带同一 product/epoch 的历史链接。 |
| `GET observations/query/` | `product,mode,indicators,report_kind,window_definition`，以及 `cities` 或 `site_epochs` 的有界 ID 集合。可选 `source`（与产品核对）、`unit,standard_series,quality`。`window` 必填 `start,end`，`latest_valid` 可填 `as_of`。`scope,cursor,page_size` 依契约使用 | 回显有效条件、`evaluated_at`、实际可见覆盖/状态、具名系列、当前修订、逐行时间与质量，以及页信息。城市 C 历史、站点 S/H 历史与 M04/M05 带入的同一组条件均用此入口。 |

**请求边界。** 单值参数拒绝重复键、未知键、空/非法 ID、越界日期、无偏移时标、错层对象和模式混用。序列参数用一个键传逗号分隔的去重枚举或正整数 ID，不用客户端任意字段名建 ORM 排序。`window` 默认 `quality=valid`，允许显式选其他质量。`latest_valid` 只接受 valid。跨类型单位/标准应拆成兼容的系列请求。`at` 与 `as_of` 互斥，小时快照/趋势锚须与产品粒度匹配。起止相等或倒置、`as_of` 指向未来均返回 `400/invalid_request`。新读端拟定每页默认 50、上限 100 对象行。普通明细每次最多 5 个对象、7 个指标、30 天，`对象数 × 指标数 × 请求小时槽数 ≤ 3600`。超限 `400/query_too_large`，提示拆分。最近值按有限系列做索引搜索，不能因只支持 30 天明细而把旧的 H 当空。全国快照独立按单产品单刻在库内聚合并分页，无 5 城限制。控量看请求参数和允许范围，不能因私下多了受限行而改变错误或截断。

**排序和续页。** 明细固定 `(observed_at_utc DESC, observation_key_id DESC)`，单次查询只走一张层级表和一个产品。最近值先按系列取首行，再按稳定序分页。全国默认列表按 city ID。显式排名按该标准 AQI 降序、city ID 升序。收藏按本人的 `created_at DESC, pk DESC`。采用 keyset、授权集内 `limit+1`，默认不暴露未请求的总数。签名游标绑定路由、主体、规范条件、选定锚点、排序和最后一个已展示的键，有长度上限及有效期。签名只防篡改，游标不装明文测值或私有许可列表。条件、数据和授权集合静态时，各页按序拼接与独立 SQL 全集一致，无重无漏。

续页沿用首请求的观测锚点，按当前权限和修订重新查询。伪造、跨用户或异条件游标返回 `400/invalid_cursor`。合法续页整项失去用途时优先返回 `403/use_denied`。范围仍有权但上一页末行已不可见时返回 `409/page_changed`。AQI 游标另绑该末行修订、资格和排序键 `(AQI, City.pk)` 的摘要。其中一项改变也返回 409，客户端从首页刷新。固定 `snapshot_at` 只固定观测时刻。其他行可在两次请求间移过游标，末行校验仍可能通过，拼页可能漏行或重复。S04 采用这项局部校验，需要完整有序结果时在数据稳定后从首页重读。

**响应示例。** 以下为拟议的 F 合同示例，符号 ID 在隔离夹具中解析。实施验收用实际 HTTP 与 PG 比较这些字段。数值使用定点字符串，版号和署名随获准记录返回。

```http
GET /api/v1/observations/query/?product=17&scope=synthetic&site_epochs=9&indicators=CO&mode=window&report_kind=hourly&window_definition=hourly_label_unspecified&standard_series=not_applicable&start=2024-01-01T00:00:00Z&end=2024-01-01T01:00:00Z&page_size=1
```

```json
{
  "selection": {"source":"test.s04","product":17,"scope":"synthetic","site_epochs":[9],"indicators":["CO"],"mode":"window","report_kind":"hourly","window_definition":"hourly_label_unspecified","standard_series":"not_applicable","quality":"valid","start":"2024-01-01T00:00:00Z","end":"2024-01-01T01:00:00Z"},
  "evaluated_at":"2024-01-01T03:00:00Z","state":"ok","synthetic":true,
  "results":[{"key":41,"revision":43,"site_epoch":9,"indicator":"CO","value":"0.000000","unit":"mg/m³","quality":"valid","observed_at":"2024-01-01T00:00:00Z","source_timezone":"UTC","fetched_at":"2024-01-01T00:08:00Z","age_seconds":10800,"freshness":"unknown","attribution":"S04 synthetic fixture"}],
  "page":{"page_size":1,"next_cursor":null}
}
```

| 情形 | HTTP 与信息边界 |
|---|---|
| 有权，指定窗无记录 / 可证未覆盖 | `200`，`results:[]`，`state=no_data`。仅有独立可披露的覆盖范围证据时可给 `no_coverage`，不能从隐藏行、过期许可或暂停 Job 推断覆盖情况。部分可见时返回可见集，`partial` 也需独立、获准的依据。 |
| 全国列表有逐值权，汇总仅部分获准 | `order=city` 返回 `200`，按页列出匹配的可见城市。仅在合格子集上计算并返回汇总合格数、分组排名数、分布和名次。有仅可逐值展示的城市时 `covered_city_count=null`，仅逐值的行无名次。`order=aqi_desc` 只分页返回所选标准的实际排名行。分页不另给总数或排除数。 |
| 全国汇总无权 / 有权但无可比行 | 依固定锚的来源数据日和请求作用域判用途，自动无锚时依请求域判权。整项无汇总许可但有逐值权时，普通列表 `200`、`summary.state=unavailable`，省略三个计数、分布和名次。单取排名（`order=aqi_desc`）返回 `403/use_denied`。作用域有汇总许可但选中时刻或目录筛选后无可比行时，排名返回 `200`、空列表、该组 `ranked_city_count=0`。 |
| 正常值、已陈旧、断源、质量异常 | `200`，数值资格、年龄/新鲜度、来源状态分别编码。获准的诊断行可带质量原因。全是争议/待核时可靠视图可返回空和已授权的质量提示。无法披露时统一 `unavailable/unknown`。 |
| 未登录、用途拒绝、私有越权 | 沿用 SessionAuthentication 的 `403/not_authenticated`。角色/账号按现行会话为 `403/permission_denied` 或未认证。用途 `403/use_denied`。私有对象在本人 QuerySet 中不存在为 `404/not_found`。拒绝体形如 `{"code":"use_denied"}`，无 count、时间、suggestion、链接或受限状态。 |
| 无效输入 / CSRF / 限速 / 系统故障 | `400/invalid_request` 或上表的范围/游标码，`403/csrf_failed`，`429/rate_limited`，PG 连接故障 `503/database_unavailable`。不可预期内部故障为脱敏 5xx。只有获准旧值时，断源不把本地可读服务改报 503。 |

### 4.2 私有资源与迁移

| 路径（既有 auth 路径继续工作） | 写入与访问规则 |
|---|---|
| `GET/PATCH me/` | 仅 `request.user`，返回 username、first_name、last_name，更新只许后两项并使用现有 User 字段长度。拒绝 id、owner、角色、groups、is_staff/is_superuser/is_active、密码等未知字段。个人资料直接用 Django User，无需另建 Profile。本人以外的列表、按他人 ID 的资料路由和删号均无业务入口。 |
| `POST auth/password/` | 只接受 `old_password,new_password`，敏感操作按已登录主体限速，CSRF 必验。事务内锁本人的 User，复核旧密码、用现有 password validators 校验新密码、`set_password` 保存。建议改密成功主动 `logout`，返回 204，当前及其他旧会话随后均需重登。失败保留原密码和会话。账号停用由后端认证即时拒绝后续请求，撤业务权限则失去对应操作，来源撤权仅失去对应用途。 |
| `GET/POST me/favorites/` | GET 即“我的关注”，按本人和数据域分页，批量取可见摘要和安全状态。默认只含正式域意图，`scope=synthetic` 只含 F。POST 接受 `kind,target_id,product` 及必要 F 域，验证具体产品的用途、对象归属与可访问身份。`owner` 总由服务端决定。新增时即使目标 ID 可猜，也需证实该对象在首选产品有可读身份依据（获准目录，或该对象的一条获准规范观测）。读数缺失本身不充当目录依据。首次 201，同本人、同目标、同选择的重试返回现有项 200。若同目标但不同首选产品，返回仅本人可见的 `409/favorite_exists`，改选走 PATCH。 |
| `GET/PATCH/DELETE me/favorites/{favorite_id}/` | 先用 `owner=request.user` 和请求域取意图，直接 GET/PATCH/DELETE 也按此规则。GET 复访。PATCH 只允许显式改选同一目标可读的产品，并重验 scope、来源、对象/站址兼容。DELETE 204 取消本人的关注，即使源已撤权也能删除。禁止改 owner、kind、目标或写入摘要。另一用户或管理员使用这些私有入口时均无法读取或改动他人项。 |

新增 `accounts.Favorite` 一张表，含 `owner`（User 外键）、`kind`、可空 `city`/`station` 外键、可空 `preferred_product` 外键、服务端从首选产品记下的不可变 `synthetic_scope`、`created_at` 和自身 PK。按稳定的 City 或 Station 收藏，站址期用于读数。PG `CheckConstraint` 限合法 kind 与对应侧，禁止两侧同时有值或指向错误侧。服务层创建时要求选中的一侧恰有目标。`UniqueConstraint(owner,city) WHERE city IS NOT NULL` 和站点对称约束负责并发去重。在事务/内层 savepoint 内处理唯一冲突，再以本人和目标读回。两份真实 PG 连接验证。更新首选或去重时先按固定顺序锁涉及的产品，再在本人范围锁意图并复验目标，保持与处置先锁产品再删目录的顺序一致。

F 与正式目录由来源命名空间隔离。PATCH 不能改变意图所属域。即使产品或目标被处置，意图的域标记仍参与所有者过滤和分页。目标及首选产品引用用 `SET_NULL`，账号删除用 `CASCADE`。目录依法删除后允许两侧均为空，保留的仅是本人的意图 ID/种类，不保存第三方名称、原码、坐标、描述、时刻或数值副本，也不自动重挂新对象。目录匿名化或权利不明同样返回本人可删除的占位。合法更名沿稳定实体读取当前获准名。可信迁址显式给新旧期选择与标识，现况只取选中期。停用/待核仅在状态可披露时标注。产品撤权后收藏列表/详情仍能读自己的意图，但隐藏受限身份、测值、年龄和派生计数。分页计的是本人意图，和第三方观测覆盖分开。

首个 `accounts` 迁移依赖 `swappable_dependency(settings.AUTH_USER_MODEL)` 及所引目录模型的明确叶节点。先在隔离库应用现有叶、写已知 F 与会话，再迁到新叶，核旧 PK、用途、Session、S01 单格、S02 回读和 S03 账本一致。另测从空库全迁、第二次迁移无待办、删除目录时的现有 disposition 事务。测试库可逆向检查表依赖。已有个人意图时回退表迁移将丢失这些意图，实际回退优先停用新增入口、保留表。

**索引与控量。** 收藏列表新增与 `(owner,synthetic_scope,created_at DESC,id DESC)` 相合的索引。观测先复用现有唯一索引、外键索引和当前修订部分唯一索引。在隔离 F 的小规模及放大数据上测明细时间排序、逐系列最近值和 L 同刻/标准筛选的 `EXPLAIN (ANALYZE, BUFFERS)`、SQL 次数及实际页大小。若同刻查找成为热点，候选索引为 CityObservation 的 `(product,indicator,observed_at_utc DESC,city)` 小时子集。站点候选为 `(product,site_epoch,indicator,observed_at_utc DESC)`。先核现有索引左前缀、投影、查询计划和写入代价，再决定是否以独立迁移增补。S04 的依据是业务请求有界、无逐卡查询和可解释的计划。S07 单独核 V3.0 §10.2 的并发 HTTP P95。

## 5. 实施顺序与可提交成果

下表供正式开发获批后使用。P1～P7 在本节表示工程步骤。跨切片用例在每步先验证本步已有的入口，P7 再核完整矩阵。例如 P1 只核 V09 的明细撤权，关注复访在 P6 加入。每一步交付可调用的用户行为、失败反例和对应测试。所有拟增文件随真实调用出现，`backend/` 内沿现有 Django app 分工。

| 步骤与前置 | 用户行为、主要实现与预计文件 | 完成条件 / 可提交成果 |
|---|---|---|
| P1 读到一个授权的历史窗口。依赖 S01、S02 | 先给单产品 F/S 的 `observations/query/?mode=window` 提供正常、空窗、部分许可与拒绝响应。提取 §3.1 公共范围/投影，加入严格参数、错误、keyset 分页和禁止缓存。预计 `observations/{queries,policy,views}.py`、新增 `observations/selection.py` 或同等职责文件、`config/urls.py`、`tests/test_observation_access.py` 与范围测试。 | 真 PG、真 Session 的窗口与旧精确读逐键/修订一致。部分拒绝发生在计数/分页前。旧 API 响应、日锚和角色回归通过。交付可独立调用的明细端点。V01、V02、V07～V10、V11 的静态明细分页、V15。 |
| P2 从目录查指定站址的最近值和历史。依赖 P1 | 为现有选择器增加按系列取最近、批量回执和年龄。按时效码、SiteEpoch 与来源发布站点详情/获准目录。实现 F 断源状态输入的测试接缝，测试生产入口只消费可信状态。预计 `observations/{selection,queries,catalog,views}.py`、路由、站点/目录测试。 | S 或独立 H 可按同一 epoch 下钻到 P1 的历史。逐指标错时、停站、同码复用与迁址边界可验证。UCI H 可用已知内部 ID 读有权时段。H 目录另需独立权利。可先用显式授权 F 验证目录下钻和站点查询软件，真实 H 读数按 V16 在 P7 退出前对账。交付 M03 和 M07 后续可复用的站点摘要。V03、V04、V06、V07、V16。 |
| P3 从城市读取七项、趋势及目录。依赖 P1、P2 | C 同刻卡、独立 T 锚和 C 历史共用选择器。用当前修订的原始贡献回执投影首污/等级/获取时间，按权生成站点入口。预计 `observations/{selection,views}.py`、城市投影模块或现有 queries、`tests/test_observation_catalog.py` 及城市 API 测试。 | 一份城市样本能对齐七项和历史。不同产品同刻异值、重叠 T、缺 C 指标、撤去附加字段均可对账。交付 M02。V02、V05、V07、V10。 |
| P4 从全国同刻列表下钻城市。依赖 P1、P3 | 从获准逐值展示的 L 指定/选择时刻，固定锚后筛名称/地域。按 §3.3 的计算和展示许可分别求可见覆盖、汇总合格数及分标准排名数，按 §4.1 分页返回列表或排名、选中时刻与城市入口。预计 `observations/{selection,views}.py`、概览查询、路由和聚合测试。 | 两城错时、同城 L/C/T、标准未知、排名平局、仅逐值/部分汇总许可、整项撤权及续页中途修订均给预期。交付 M01 和全国→城市→站点→明细的后端链。V05、V07～V11。 |
| P5 本人资料和安全改密。仅依赖 S01，可独立验证 | 复用登录/CSRF 和 Django 密码服务，增加白名单资料、敏感操作限速和注销语义。预计 `accounts/{views,urls,limits}.py`，必要时 `accounts/services.py` 和 `tests/test_accounts.py`。 | 两个账户、同账户两个旧 Session、管理员角色和撤权路径经 HTTP 及 PG 状态核对。交付 M07 账户端点。V01、V12。 |
| P6 收藏后安全复访。依赖 P2～P5 | 新增 `accounts/models.py`、`accounts/migrations/0001_*.py`、收藏服务/路由、所有权/并发/处置测试。关注页批量调用同一授权读端，目录删除留占位，记录稳定意图。 | 并发新增一行、两用户全部方法隔离、迁址/停用/撤展/实际目录清理后复访合法。干净库和升级库回归通过。交付 M07 收藏及我的关注。V09、V12～V15。 |
| P7 形成阶段交接。依赖 P1～P6 | 复用 `scripts/s01_runtime.py` 的 Cookie 客户端和已有隔离验证基础，必要时增加小型 `scripts/verify_s04.py`。核对相同选择在卡片/摘要/分页明细的键、修订、时标与权利，记录 SQL 计划和资源隔离。增量维护 README/API 示例、已有夹具出处说明，新增一份阶段报告 `docs/verification/S04.md` 指向脱敏收据。 | 第 6 节逐项有输入、预期、实得、版本、证据类型和位置。S04 退出须含本版本 V16 真实 UCI H 子项的 PASS 证据。未覆盖单列。清楚交接查询服务入口、错误、授权、额度、迁移与复现命令，供 S05 消费、S07 复验。V01～V17。 |

## 6. 测试与验收矩阵

**实施期取证方法。** 在任务自有 PG 中分别验证隔离 F 和获准 H。先按各自输入写下预期，再经 S02 正规化/修订服务落库。用独立 SQL 连接核键、当前修订、回执和权限，用真实同源 HTTP、Cookie jar、Django Session、CSRF 对账。可沿用 pytest-django 做 PG 约束与并发断言。纯时间/单位/参数规则可单测。当前 §2 的 105 项是前序能力回归。下表每一项 S04 结果目前均为 **NOT RUN**。

| 编号 / 范围 | 输入及操作 | 可复核的预期与证据 |
|---|---|---|
| V01 身份入口 · T01/T07 | 未登录读业务端点。取 CSRF，缺/错 token 注册和登录，带 `role/is_superuser` 注册，再合法注册、登录和携带轮换后 token 写入。 | 匿名业务读和缺/错 token 的写请求被拒，状态码和 `code` 符合 §4。合法账号无 staff/superuser 和具名管理权。拒绝时 PG 用户/收藏无增量。用真实 HTTP 和独立 PG 查询证明。 |
| V02 显式窗口 · T02/T03 | F 在 t0、t2 各有一条同系列值。在有展示权的 `[t1,t2)` 查历史，再查 `[t2,t3)` 和精确 t2。另测同一瞬间的 `Z`/`+08:00`、来源地方日交界、右标签、滚动窗、未带偏移。 | 前者 `200 []/no_data`，后者仅有 t2，键/修订/单位与旧 `current/` 一致。按 `[start,end)` 和来源日分别验查询与许可。非法时标 400。授权目录能证实未覆盖的独立场景才给 `no_coverage`。 |
| V03 各指标最近值、断源 · T03 | 同站址 CO 在 t2 为缺测、t1 有效，PM2.5 在 t2 有效，旧站址 t3 有有效 CO。固定 `as_of=t3` 查最近和显式 t2 窗。标注 F 断源/结构异常，另设有依据的 F 阈值与无阈值两种情形。 | 同次响应 CO=t1、PM2.5=t2，年龄分别对 PG 展示时钟计算，旧站址 t3 不被选中。t2 窗无有效 CO。仍获准的旧值能读，阈值不明为 `unknown`，已知过期为 `stale`。有故障证据才附 `source_status`。不会生成新的 Run。 |
| V04 身份与迁址 · T03 | 两来源同名站、同源同码在不重叠时段归属不同站、一个 Station 将站址 E1 迁到 E2。构造边界时标、跨期/跨城标识，分别选按城 S、全国 S、独立 H 形状 F。 | 以 `(source,station,site_epoch,时标)` 对账。已知右标签恰在结束边界读 E1，左标签起点读 E2。访问错误 epoch/产品无可枚举信息，窗口不跨期拼接。对停用站只给同一身份获准的历史。无核准坐标不返回点。 |
| V05 L/C/T 分离与血缘 · T02 | 在同一 F 城同刻写三产品 AQI 40/70/90，站点 AQI 另给 110。C 写六浓度及有权首污，然后在另一时刻只写 AQI/CO。两个相邻 T 采集窗共享小时，另造一次重复和一次迟到 Receipt。 | 分别核 L=40、C=70、T=90，三组各只计自身。C 七槽来自 C，缺项留空，历史只有合法 C，T 仅含参考锚 24h 内各真实观测键一次。首污/获取时间对应产生该修订的回执。追加迟到回执后原返回不变。 |
| V06 质量、版本、兼容性 · T02/T03，引用 T06 内核 | F 的真零、`NA`、`/`、`<3`、待核峰、无效输入、无键事件、同键无序冲突、可信新版、映射退役键。显式切换 quality、单位、O₃ 小时/8h、`HJ633-2012`/`HJ633-2026`/`unknown`。 | 逐行核 Decimal、单位、删失方向/界限、质量和修订。争议无候选数值，拒收不制造格。新旧版按当前资格读，同值新版水位或重放不增加观测。不同标准和窗口分列。未知版只保留获准源描述。 |
| V07 许可的交集与部分覆盖 · T08 读取部分 | 同产品覆盖两城、两指标、跨两来源地方日。一条 allow 只覆盖城 A 的 CO 和首日，第二天有相交 deny。另设同一实体另一时效码、不同产品、字段分拆的 allow、错误受众、仅展示无留存。派生反例用一城一刻的可比 L 形状 F，保留基础逐值权，计算/展示分别给旧 `fields=["*"]` 或 §3.3 的显式 allow。 | 逐值读取按范围对比 `policy.applicable` 与集合返回键。只有完整满足两用途的切片可读，两个不完整的字段许可不能相加。同一城市另一产品的许可无效。无权具体请求统一 403，允许切片的页/摘要/导航均只含可见集。构造同一来源同一对象且日期/字段相同的两个 product，仅其中一个获权，验证预载分桶和旧/新入口的字段白名单。单独撤 source_time/source_description 后，两端均无受限原始文本，仍有观测 UTC 时标和已获准的规范来源信息。派生两端仅通配或任一端仅通配时，列表 `200`、摘要 `unavailable`、排名 403。两端均显式时三数均为 1。再单独叠加计算端通配 deny、计算端带非空 `restrictions` 的通配 allow、展示端对 `overview_summary` 的 deny，三次都只可逐值读。不相交 deny 保留原结果。 |
| V08 全国共同真实时刻 · T01/T02 | 同一 L 在 t0 为甲=90、乙=60、丙=60，t1 仅乙=80，丁只见于 C。给三城目录及完整汇总许可。测试 `at=t0`、`at=t1`、缺失 t2、自动最新。在自动请求分别筛甲的名称及仅含甲的地域，另设有/无独立可见名册。 | t0 三数 3/3/3，同标准名次 1/2/2，分布合计 3。自动锚 t1 只有乙。两种筛选都在 `snapshot_at=t1` 返回 `200 []`，显式 `at=t0` 同样筛选才见甲，三数均为 1。显式 t2 空，丁不出现。另验有权但无可见 L 时自动锚为 `null`。翻页不改变静态分布，`*Level` 单位 `grade`。独立场景将 t0/t1 置于不同来源数据日，t1 当日仅有逐值许可，更晚 t2 只见其他标准。显式选原标准时仍锚 t1，列表有乙、摘要不可用，排名 403。新鲜度、名册覆盖与无权分别核验。 |
| V09 撤权无侧漏 · T08 读取部分 | 保留 A 的许可，向 B 插入多行、较新时标、更高 AQI、更多元数据。只撤 B 的展示、保留 B 的留存，比较增删受限行前后的总览、搜索、趋势、历史、下一页和关注。再撤整个产品展示。 | 相同允许集与固定选择锚下，用户可见值、覆盖/分布、顺序、下钻、`has_more`/续页结果、首污、最新锚和错误体相同。归一化请求时钟/签名时间再比对。整项撤权后普通用户和管理员都拿不到受限数值、计数和派生信息，旧 Cookie/游标也一样。PG 仍有合法留存行。私有意图仍可删除。 |
| V10 附加字段、排名资格与到期竞争 · T02/T08 | 给 L 的 `catalog`、`overview_summary`、`primary_pollutant`、源等级/描述独立许可，并逐项撤回。固定快照的甲、乙、丙均有独立有效的基础留存和逐值展示许可。甲丙同一确认标准，丙 AQI 最高，乙标准未知。只给甲乙完整汇总许可，再补齐丙。以未筛标准的普通列表核三数，再核指定标准的排名行。另测标准冲突和获准数据日内指定的空时刻。一个读事务与撤权事务用两条 PG 连接配合，再使 grant 在投影中自然到期。 | 字段拒绝或不存在时同一缺省，未授权字段不影响搜索/排序。部分授权时列表有三城，`covered_city_count=null`，`summary_eligible_city_count=2`，已确认组 `ranked_city_count=1`，排名和分布仅含甲。补齐丙后三数为 3/3/2。撤尽汇总权后列表仍有三城，摘要 `unavailable`，响应无三个计数、分布或名次，排名 403。另在有汇总权的空时刻核排名 `200 []`、该组排名数 0。标准冲突导致不可比的值排除在对应分布和排名之外。锁线性化点前已提交的撤权立即生效，等待后读不得取旧资格。事务跨过截止时刻，结束复核阻止失效投影。 |
| V11 分页、参数、成本 · M06，T01/T02 的明细 | 多城/指标同一时刻，1/2/100 行分页。篡改游标、换用户/条件、翻页后订正锚或撤权。测 0、刚好上限、超过上限、重复参数、错单位/标准和倒置窗。增加同一许可内城市数及每卡指标数。 | 静态集合拼页与独立 SQL 有序全集吻合，无漏无重。跨条件 400，保持末行键有权，将其 AQI 修订到另一页，旧游标 409 刷新。仅末行失权也为 409，整项失权为 403。另固定时刻、标准和授权，甲 90、乙 80、丙 70，首取两行甲乙。仅将未展示的丙修订为 95，末行乙未变，旧游标返回 `200` 空尾页并漏过丙。修订稳定后从首页拼页得丙、甲、乙。越界不执行无界查询。记录关键 SQL/执行计划、HTTP 返回行数及耗时。同一组产品/许可片段下批量读取的 SQL 次数不随卡片数线性增长。 |
| V12 资料、密码和会话 · T07 | 用户 A/B 和有管理权用户各建会话，A 再登录第二次。请求资料列表、猜他人 ID、PATCH 注入他人 owner/权限。缺 CSRF、错旧密码、弱新密码、限速、成功改密及注销分别重放原 Cookie，再测试登录后停用账号及撤具名管理权。 | A 只能 GET/PATCH 本人允许字段。PG 中 B 和权限字段不变。他人详情/改/删无入口或无权。成功改密后两份旧 Cookie 都拒绝业务读，旧密码失效，新密码能重登。单会话退出只使该旧会话失效。停用立即拒读，撤管理权限后同 Cookie 拒管理操作，仍有权的普通读可用。 |
| V13 收藏归属与并发 · T01/T07 | A/B 收藏同一城市与同名异源站。用两个连接/请求同时为 A 新增同一个目标。B/管理员猜 A 的 ID 做列表过滤、GET、PATCH、DELETE 和改 owner，A 在本人项显式改选产品、取消收藏。 | SQL 分用户核唯一行，可区分首次和重试，响应指同一意图。跨主体验证 404/非法参数且写入无副作用。PATCH 仅接受同一身份允许的产品。重复 POST 不偷偷改选。只有所有者可取消，B 的意图独立保留。 |
| V14 复访与目录处置 · T03/T07/T08 读取部分 | 收藏后可信更名、迁址、关闭站址期、站码分配给新站、只撤展示、再对隔离 F 走已有的真实目录留存处置及城市匿名化。 | 复访用原 Station/City，名称和期变化按权显示，当前期无值时无前期回填。同一用户分别建立 F 和正式域的测试意图。正式域可用获准 H，缺合规正式输入时混域子项记 NOT RUN，F 仍可验证显式域与默认空列表。默认列表、详情与分页均无 F，显式 F 域才有 F 意图/页/摘要。撤权或目标和产品置空后，仍在原域持有和删除占位。不改指同名新站。展示撤回后占位仅含本人意图。目录真实删除能提交，空引用保留私人项，无复制的第三方数据、权限清单或测值。 |
| V15 升级与回归 · S01～S03 | 在隔离基线库保留旧账号/会话、F 产品/修订和 S03 窗口，再迁移。用原 Cookie、精确 selector、旧管理端点对账。另做新库安装及再次迁移。 | 默认正式许可和旧事实不被新增迁移改写。主键、单位、修订/Receipt/窗口和旧受控接口如前。运行 `manage.py check/migrate --check`、`makemigrations --check --dry-run` 及与改动相关的既有 pytest，记录实际套件和结果。 |
| V16 证据类别 · T02/T03 | 真实 H 必验。核对本地 UCI ZIP 的档案指纹、署名及现行用途，经 S02 导入隔离 PG，以真实登录会话通过本版 S04 站点详情和明细 API 读取两站两窗的最近值、指定窗明细及窗间的无权数据日。可选在实际有权条件下复用标准库存的北京单次 C（只读途径）。 | H 标明真实历史站点、归属与加工，现有 `uci_catalog.register_uci` 对所选两站、两个时段授予 `display(normalized,provenance)`，无 `catalog`。按已知内部站址期 ID，将获准原样、PG 当前键/修订与 HTTP 返回的时标、CO 换算（µg/m³ → mg/m³）、来源署名对账。响应中的期起止与站名等目录字段留空。两窗间选一个整段无展示授权的日期应 403，有权但无行才是 `200 []`，可在 F 单独造有权空槽。任何 F/H 都不算真实城市观测。H 子项独立判定并适用下述退出条件。真实 C 子项无可读条件时以 F 验 C 软件，记录真实证据待验。 |
| V17 端到端与故障 · T01/T02/T03/T07/T08 后端部分 | 在无外联、无调度的自有环境走“注册→登录→同刻全国→城市 C/T→站点→历史/明细→收藏→退出重登→复访”。比较卡片、关注摘要及同条件明细。另仅对自有测试服务注入 PG 连接失败。 | 同条件返回同一组资格和修订，汇总可回到可见支撑行。撤权、指定空窗、年龄未知/陈旧及 DB 503 有不同响应。记录实际 SQL/HTTP、输入类型和版本指纹。全程外部采集请求为零。 |

**证据与退出。** F 必须在 `family=F,synthetic=true` 的独立命名空间下，用自制小表标注对象、时次、等级标准、允许字段和预期结果。真实 H 使用 `observations.uci_catalog/import_uci` 的档案、署名和获准范围。故障证据显式标记 F/注入，定时和上游访问关闭。真实 C 只在验证当时仍满足授权时读取，收据限于可披露的断言，不提交响应原文、复原数据、Session 或密钥。

| V3.0 案例 | S04 交接的证据 | 后段取证 |
|---|---|---|
| T01 | V01、V08、V13、V17 登录至收藏复访的服务端链 | 浏览器主流程、合法地图及 T12 在 S08/S09。 |
| T02 | V02、V05～V11、V16 的产品/同刻/标准/字段对账 | D 独立产品、完整成品呈现及真实城市发布按各自准入。 |
| T03 | V02～V04、V06、V14、V16 的站址、逐指标旧值、空窗和断源 | S06 接真实任务账本，S07 复验故障传递，S08 核呈现。 |
| T07 | V01、V12～V15 的数据库会话、所有权、权限变化 | 分析方案/导出归属交 S05，浏览器安全交 S08。 |
| T08（读取部分） | V07、V09、V10、V14、V17 核当前权利、派生信息和复访。使用现有处置仅验证收藏兼容性 | 导出/下载、受控副本、备份恢复和完整到期处置留待 S05～S07。 |

T04 消费同一选数及明细，T05/T06 提供已有账本和当前资格，T09 使用性能基线，T10/T12 使用离线和展示契约，T11 单独准入。阶段报告只判已覆盖的后端条款。各切片通过相关测试后按 §5 递增交接。缺少合规 H 输入或适用许可时，F 软件链路可继续开发与验证，V16 真实 H 子项记 **NOT RUN**，S04 完整退出待验。H 实跑与预期不符记 **FAIL**。只有本版真实 H 经 PG 和 API 对账通过才能记该子项 **PASS**，F 结果与 S02 旧收据仅按各自范围记证。其他必需的权限或事务证据缺口也单列。

## 7. Codex 技术自审与 Plan Review 提交点

| 自审时发现的问题 | 写入本计划的修订 / 复核点 |
|---|---|
| 用现有精确 API 循环实现列表，会把逐次授权、页面计数、锚点和版本拆开，同站多码还有越权风险。 | P1 先交可调用的授权窗口，精确端点和批量端点共用同一 `applicable` 语义，授权关系进入 SQL 后再选锚、排名和分页。以 V07、V09、V11 对照单格和插入隐藏数据前后结果。 |
| 从最新回执取首污/获取时间，可能把迟到包的字段贴到当前修订，还会将管理用途误用为普通展示。 | §3.2 明确原始贡献回执和逐字段双用途，无法归属返回 unknown。V05、V10 检查两次读取与旧回执指向同一修订的反例。 |
| 仅有一条真实 C 就推导等级、地图、目录或全国覆盖，会超过现行字段授权和标准依据。 | 标准库按七指标和附加字段分别核权。全国、C、T、S/H 的输入在 §3.3 分开。真实 C 是可选读样，数据覆盖、目录和首污均有缺省语义。 |
| 站点收藏若绑码、名称或站址期会换目标。若用 `PROTECT` 则既有目录处置无法删除。 | 采用稳定实体、可置空引用和 PG 条件唯一约束。V13/V14 核竞争、真实删除及匿名化。本人意图和第三方摘要分别判权。 |
| 将登录墙当成所有权或会话撤销的证据，以及把断源当作 PG 故障，都会漏验退出标准。 | 在 V12 使用两用户、同人双 Session 和原 Cookie，在 V17 用可归因的故障区分仍可读旧值与数据库不可用。 |
| 旧验收与当前源码不同，H 原件和新索引收益尚无本轮证据。 | §2 记录可复核的历史/当前基线。P7 按真实输入重做 H/F/HTTP 对账，索引据计划和 SQL 实测选择。范围以总体方案的 S04 退出标准为准。 |

批量读专项复查还发现三处契约缺口，现已补入正文。授权预载按 product 分桶并检验每行归属（V07），AQI 游标校验上一页可见末行的修订与排序键（V11），收藏记录所属数据域，使目标置空后仍可隔离 F 与正式意图（V14）。V08/V10 覆盖仅允许逐值展示的新时刻，V16 划清 H 实测读数与目录权。

**GPT Plan Review 条件落实记录**

| 条件 | 契约、实施步骤与验收反例 |
|---|---|
| C01 派生汇总授权 | §3.1、§3.3、§4.1 → P4 → V07/V10。计算和展示分别要求明列 `overview_summary` 的许可。旧通配 allow、相交 deny 和非空限制逐一对照。现行判定见 [policy.applicable()](../../backend/observations/policy.py#L123)。 |
| C02 全国快照与覆盖数 | §3.3、§4.1 → P4 → V08～V10。先定锚后筛目录。三种计数分别对账，核部分授权、无汇总权和有权空排名。 |
| C03 跨页修订 | §4.1 → P1/P4 → V11。静态拼页对账有序全集。末行变化触发刷新，未展示行移过末行的反例验证动态分页的限制。 |
| C04 真实 H 验收 | §3.3、§4.1 的站点读端 → P2/P7 → V16 及 §6 退出条件。真实 UCI H 须在本版 PG 和会话 API 上对账，缺输入时 H 子项记 NOT RUN，阶段退出待验。 |

GPT 已确认本轮条件修订。参数上限和索引仍在实施期隔离验证。
