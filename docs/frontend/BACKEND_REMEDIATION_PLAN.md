# Phase 02 后端整改执行计划

基线为 2026-11-27 的后端 `0c0a064769a69a8474ce3dda20ba82c05916fd9d`。本轮同时核对前端 `d10c6fb5d0823f8d3a18f18a235e142221bb9d2b` 工作区。现行行为与已冻结的八组目标以 [接口基线](CONTRACT_BASELINE.md) 为准，原始证据和责任逐项见 [43 项差异台账](BACKEND_GAPS.md)，产品裁量见 [决策单](PRODUCT_DECISIONS.md)。下表的测试均是后续交付条件，本轮仅做静态核对和任务编排。路径相对 `/api/v1/`，源码模块相对 `backend/`。

## 第一批 · 前端精修的输入

第一批 1.1–1.8 的技术和产品输入已经冻结。Phase 03 可直接优化公共层，Phase 04 依现行接口开发列表、详情、受限态和 F 展示。目标字段可用于标明目标版本的契约样例。F 及响应注入可验交互，在使用真实数据签收对应功能前还须通过第二、三批。

| 任务 / 关联差异 | 交付给前端的契约与涉及模块 | 依赖、执行方式与验收 |
|---|---|---|
| 1.1 工作区与路由 · BG-023、BG-024 | PD-01/02 已确认 `GET me/capabilities/` 的 `workspaces` 与具名 `actions`、无安全 `next` 的落点、原生 Admin 的 active staff 门槛。M08/M09/M10 各自按具名动作和当前对象复核。依据 `accounts/profile.py`、`governance.py`、`restricted_admin.py`。 | 现行 active 用户固定有 `user`，有投影动作才加 `manage`。可按现行策略开发。正式账号始终有 user，沿用原生 Admin 与现有 REST 受众，管理专用和空区仅保留防御性注入验证。以普通、仅审阅、诊断、staff 有/无具名权及非可管目标验回跳、403 和清理。仅管理/空区若用注入须标注，与实际可创建的角色分开验。 |
| 1.2 普通观测分页与精确读 · BG-006、BG-009、BG-010、BG-011 | M03/M06 分别消费 `GET observations/query/` 的页一致性、`observations/current/` 的精确读版本及研究父版本。站址目录的请求 `at` 与响应锚点分开。后端涉及 `observations/detail.py`、`queries.py`、`s04_station.py`。 | `at` 原值续传，原省略则续页也省略，可直接修前端。普通页目标在签名游标内部绑定同一获权结果版本，变化回 `409/page_changed` 并丢弃旧页。实现前仅承诺单页或分批读取，不合成为同版整窗。精确行分开 UI 选择键、修订 PK、获权逻辑键及精确版本。验原请求省略/显式、非末行更正、变权与合法空日。 |
| 1.3 图点与 Pearson · BG-012、BG-013、BG-014 | M04/M05 → M06 按契约 §6 对接 `POST analysis/statistics/`、`analysis/compare/` 产生的图点如何沿原父查询和 `input_version` 读取 `analysis/details/`。点或段应披露 `support` 和精确半开 `bucket`，Pearson 另披露完整成对的列和值。模块 `observations/research_statistics.py`、`research_api.py`、`analysis_query.py`。 | 可选字段、空槽、压缩尾段、跨页配对与预算遵循契约 §6。现阶段可沿已有表格、桶、热图的明确支撑描述。不能由接近的桶、压缩最大最小或比较差值补点。完整散点/图点的真实接入须等 2.2。验同父版本、末段截断、P 不足、零方差、未获原值权。 |
| 1.4 质量证据、对象隐匿与写后恢复 · BG-029、BG-030、BG-031、BG-032、BG-034、BG-037 | 按冻结契约 §6 接入 `GET admin/quality-issues/{event_id}/` 的逐事件动作与版本、可裁剪的键/批次证据入口，`POST …/actions/` 首次/重复与本人原命令查证的响应。职责为 `observations/quality_governance.py`、`queries.py`、`accounts/management_api.py`、`s08_receipts.py`。 | 前后端先对同一事件走 open→in_review→resolved，确认两用户、证据过期、结构事件、跨键影响及丢首包的 DTO/错误样例。真实读写端隐匿、裁决和丢包后恢复范围须 2.5–2.7 上线到隔离测试环境后才可验。未获准证据保留待核，现有回执能证明的动作照实显示。 |
| 1.5 采集状态与失联 · BG-028、BG-033、BG-035、BG-036 | 交付 M08 的 `admin/collection-jobs/`、`admin/collection-runs/`、`admin/collection-windows/`、`admin/history-imports/` 读写分支以及 `POST admin/command-receipts/lookup/` 原信封恢复状态。依据 `collection/control.py`、`views.py`、`s07_admin.py`、`observations/history_status.py`。 | 已有 Job 或登记模板可开发。写后按原对象重读，失联有编号时查原号，无号时以原命令查证，待核时保留未知。运行用同一 Run/Window，导入用同一 Attempt。用一个 Job 暂停、一个重投、两个同期 stage 各丢一次首包演练。完全无号的真实导入恢复依 2.7。 |
| 1.6 导出范围与文件协议 · BG-015、BG-016、BG-017、BG-018、BG-043 | M06 确认窗应反映将送入 `POST me/exports/` 的规范父组、版本、来源、窗口、时区、`kind=aggregate/detail` 与字段。`fields:[]` 表示必备列。`support=observed` 不保证全部有效。票据 GET 仅返回四项元数据，下载 GET 独立鉴权。依据 `accounts/research_exports.py`。 | 当前默认列路径可先把只读预读放到最终确认之前，签发使用该父组。已变版则重读重确认。新版附件必备 `data_scope=formal/synthetic`，旧票按冻结的过期或安全失效路径重申。选择附加列、跨会话来源及 D 导出仅阻塞各自扩展。验 `summary` 拒绝、合法 no_data、默认列与签发 body/实际字节一致。 |
| 1.7 修订现行机器契约 · BG-011、BG-015、BG-016、BG-019、BG-020、BG-022、BG-033、BG-041 | 双方据实现冻结了现行 DTO 的分支边界。将研究 `statistics/compare/pearson/detail`、收藏可用/撤权、质量列表/详情/首次/重放、计划 GET/POST、Window 行和详情分开。`docs/api/openapi.v1.json`、`v1.md` 及相应处理器归后端。 | Phase 02 已校正 `SaveQuery/PatchQuery` 的三种可保存方法、`ExportIntent.groups` 的 1–2 组及收藏双降序和裁剪规格。其余 DTO 在使用生成客户端前交付纠错后的机器契约，或按现行字段显式解码，配获权样例验分支。新业务字段在实现前保存在基线的目标区。 |
| 1.8 已有能力先接前端 · BG-001、BG-004、BG-006、BG-007、BG-008、BG-011、BG-014、BG-015、BG-019、BG-020、BG-039 | M01 `GET overview/national/` 的 `q/geography/grade/order/focus_city` 与续页，M02/M03 站址期和可用时效，M06 精确入口，M07 收藏，M10 诊断均沿现行端点。模块 `observations/s04_national.py`、`s04_station.py`、`accounts/favorites.py`、`operations.py`。 | 前端 Phase 03/04 交付列表同条件续批、改筛选回首批和稳定 ID 下钻。将 200 unavailable 与记录 404 分开，拒绝后清旧摘要。站型/全域分母未知保持未知。D 只在获权时用 M06 地方日入口，诊断用已披露字段。验两页同刻、同域链接、200/404/204、空日和截断。后端无须为旧映射另建端点。 |

**前置实现边界。** Phase 03 的公共层直接消费现行能力。Phase 04 若对应页面必须接真实服务，按对应任务前移最小依赖。普通合页、仅 S 权发现及精确组合校验取 2.1；图点和 Pearson 取 2.2；新版附件取 2.3；对象隐匿、资格与证据取 2.5；原裁决获权影响取 2.6 的回执部分；导入只读查账与采集理由绑定取 2.7。其余默认在 Phase 08 集中整改。服务交付前分别保留单批、明确支撑入口、F 演示、只读审阅或影响待核。Phase 05 回归前端，06 双向审计，07 阶段性封版，09 联调，10 最终验收。

## 第二批 · Phase 08 后端集中整改

下表按页面和数据依赖排序。先稳定可见范围、版本和校验，再发布生产相同路径的隔离联调版本。随后交付依赖这些条件的附件、裁决和恢复。各后端任务由对应模块维护方负责，前端负责同版客户端验证。每组用当版代码、HTTP 样例和数据库断言交付，前端单独在对应页面回归。

| 任务 / 关联差异 | 目标接口、模块与实现建议 | 依赖与验收测试 |
|---|---|---|
| 2.1 读范围和快照 · BG-005、BG-006、BG-009、BG-010 | `observations/discovery.py` 为纯城市深链提供按城市裁剪的 S 发现或等价授权站址目录链接。`detail.read_detail` 将可见成员、顺序、修订及影响投影的权利纳入续页一致性。`queries.read_current` 按产品形态检查报告窗、标准、指标与时锚。保留地方日合法无报的 200/0，非法组合作 `400/invalid_request`。 | 依 1.2、现行用途权限。仅 S 权、空目录、无发现权分别测试。分页改已显示非末行、后续行和部分许可，重启不能回放旧权。仅时间流逝引起的 `age_seconds/evaluated_at` 改变须正常续页。隐藏行变化不造成可观察差异。C+日报、D+小时、错误系列、合法无报及失权各有断言。T02/T03/T06/T08。 |
| 2.2 可验证的图点与真实配对 · BG-012、BG-013、BG-014 | `research_statistics` 点/段沿用 `research_api.drilldown` 的 `support` 和 `bucket`。段的 `end` 截至父窗终点，保留列位置。Pearson 按 SQL 计算系数的同一 `expected_common AND paired` 样本生成显式配对（同一时槽、固定 x/y 列、父版本与来源权），并说明截断。现行 `paired` 支撑行还可能包括未知应到。 | 依 1.3、可显示原值权与统计权。测试行分页把两列拆开、尾段、仅一列缺失、未知应到但双有效、P<30、常数列和共同覆盖不足。将库内完整共同样本与 P、corr 对账。未截断时散点与该集合相同，截断时核有序前缀、截断标记及完整 P。独立验证 compare 的差值/变幅及现有 ranking/partial_summary、箱须/异常候选。18/24 各列而共同 12/24 不生成整窗排名。T04/T09。 |
| 2.3 导出与双域标识 · BG-015、BG-016、BG-017、BG-018、BG-043 | `accounts/research_exports.normalize/columns/data_rows/download_export` 在新版本 detail 与 aggregate 附件加入必备、不可移除的域标识，校验字段用途。新票按新格式版本再生并对照字节摘要。按共享契约 §6 让旧票到期或安全失效，再按当前权利重新申请。下载与签发分别核权。 | 依 1.6 及契约 §6 的旧票处理。中性来源名/署名、两域两 kind 确认实际 CSV 字节与摘要。无数据、跨域混组、版本漂移、失权下载拒绝。升级前有效票据验证。可选来源元数据/选择列和 D 格式走 2.10。T06/T07/T08/T09。 |
| 2.4 工具可消费的现行 OpenAPI · BG-011、BG-015、BG-016、BG-019、BG-020、BG-022、BG-033、BG-041 | 将 1.7 的验证样例落实到现行操作，随后随每个实现单独更新 `docs/api/` 和同版 operation 契约测试。研究 DTO 区分 `E_common/P/actual_pairs`、结论、partial、`pearson` 可空，收藏稳定次序 `-created_at,-id`、可裁剪字段，质量 `event_version/evidence` 与回执，导出组上限和保存三方法。检查整数/UUID/Decimal 与空、null、缺省。 | 可与 2.1 并行。对每个分支同时走 Django resolver、样例 schema 验证与实际 handler。`aggregate/detail/summary`、保存 detail 拒绝、收藏同创建时刻跨页、撤权最小形态、当前/候选修订及首次/重复均验证。抽生客户端或显式 TS 解码，不以开放 object 校验成功代替字段检查。T07/T08。 |
| 2.5 先保证质量读写隐匿，再披露资格 · BG-029、BG-030、BG-031、BG-032、BG-037 | `quality_urls.event`、`quality_governance.act/checks/project_refs`、`management_api.rejection` 统一资源级不可见/不存在的状态和体，保留独立身份、CSRF 与全局权限分支。按锁内同一套条件产生逐事件允许动作、匹配事由、证据要求及当前版本。非授权键/原值/候选链接不披露。`read_evidence` 仅接授权 `city_key/station_key/batch`。 | 依 1.1/1.4/1.7。正反角色矩阵含两审阅者、只审阅/可裁决、open/in_review/resolved、本人/他人、失权与过期。读写及关联 GET 对存在但不可见与不存在返回相同对象级拒绝。构造同版、有证/无证、动作理由错配、陈旧版本分别断言，拒绝无业务状态/生效审计变化。T06/T07/T08。 |
| 2.6 正式有据更正与影响 · BG-029、BG-031、BG-034 | `quality_governance.correction_evidence/mapping_candidate`、`catalog_governance.registered` 只对已登记且获准的正式来源增规则，绑定候选、规则版、事件、操作人、有效期及目标身份。`quality_governance.act` 与 `accounts/s08_receipts.quality` 用事务内的原动作/决定重建受影响旧/新组，按查证时权利裁剪。 | 依 2.5。无正式规则或证据则保留隔离。可以先交付 F 的跨键影响恢复。用原信封丢首次响应→lookup→核同一 `action_id`，本人/他人、无关组和撤权后分别验证。比较有效读、分析和旧票失效范围。普通 close 不解争议。正式源效果留 3.3。T06/T08。 |
| 2.7 采集与导入恢复 · BG-033、BG-034、BG-035、BG-037 | `collection/control.py`、`services.redeliver`、`s08_schedule.py`、`s07_admin.list_activity`、`observations/history.py` 的 `submit`、`observations/history_status.py` 的 `read` 及 `accounts/s08_receipts.py`。控制 POST 后 GET `targets`，重投后 GET 原 Run。将见证丢失与投递状态分开。扩展现有本人 `POST admin/command-receipts/lookup/`，以原 actor + stage UUID + 原提交内容确认唯一 `attempt_id`，新增查证分支处理 UUID，保留原请求兼容。不暴露他人暂存或派生新尝试。按动作检查理由集合，明确原 `pause` 简写兼容。 | 依 1.4/1.5。并发同请求只生一个动作/Run/Window 身份。同 ID 改命令冲突。两个同时暂存各自取回本人的原号，重放、到期/已清理/撤权、另账号查证分别验。503 有号沿号查，无号只查原身份，未核实保持未知。只有 `dispatch=manual`、`status=pending`、`request_state=unsent`、无运行或窗口 owner 且符合重试、窗口、版本与预算条件才允许重投，同 Run 不新增窗口。PG 事务、故障注入和 Worker 在 3.5 重验。T05/T07/T08。 |
| 2.8 已有诊断与关系 · BG-033、BG-036、BG-038、BG-039 | `GET admin/operations/health/`、`admin/operations/trends/` 沿获权窗口、源地方日日桶及 `include=first_valid_write` 取数。`accounts/operations.window_evidence`、`operations_first_write.py`、`collection/s07_admin.bucket` 固化时标、分子分母和裁剪。按已核 `product+window` 查质量队列。 | 依 1.5/1.8。测部分许可、空日、E 未知、零分母、首写三态和截断。`source_publication_delay=null` 保持未知。Window 可对应多个 Job，PD-08 优先采用当前获权的对象链接；额外人员披露独立排期。PD-10 的全局/逐时扩展转 2.10。T05/T08。 |
| 2.9 真实三链的联调候选版 · BG-025、BG-026 | 对 2.1–2.8 各自已准入的改动交付相同版本 Django/API/SPA 与隔离验收说明，列出依赖的测试账号能力、来源/用途、数据库、队列和对象数据。对纯文档任务保留与实现版本的映射。 | 按三条业务路径而非 API 计数验。复用 `tests/test_s04_selection.py`、`test_s05_statistics.py`、`test_s06_quality.py`、`test_s07_exact.py`、`test_s08_receipts*.py` 等，以新反例补测试。冻结 operationId 与旧票据/游标/会话的兼容边界。联调候选版本由 3.4/3.5 签收。 |
| 2.10 按 PD 决策独立排期的增量 · BG-002、BG-003、BG-004、BG-007、BG-008、BG-016、BG-018、BG-021、BG-023、BG-024、BG-028、BG-036、BG-038、BG-040、BG-042、BG-043 | 依决策单分开排期。目录/统计模块承接站型、覆盖日历、时空比较与地图/全国趋势。本人资源模块承接按稳定目标反查、来源回访及可选字段。采集模块承接独立 Job/原 Job 首挂与多 Job 关系。运维/账号/站址/备份模块仅承接确认的交接、权限及批量粒度。CSV 按获准 D 使用独立地方日报日与时区。 | PD-01/02 的现行账号模型可直接验。PD-04/05/06/07/08/10/11 保留各专项原编号及独立验收条件。分别验第 301 个收藏、跨会话来源/清理、同 Job 首挂且不启源、两 Job 共窗部分可见、批量部分失败、HTML/REST/主机签认权限分层、非 UTC 地方日报日的实际字节。各项在自身的范围、素材或当期用途证据齐备后独立交付。S09-01/02 的基本入口继续可用。 |

## 第三批 · Phase 09 专项验收

| 任务 / 关联差异 | 进入条件、涉及模块与取证方法 | 通过标准与可独立交付范围 |
|---|---|---|
| 3.1 正式地图 · BG-002、BG-004、BG-027 | 数据与地理素材权利方提供适用用途、地域、受众、期限和署名，确认城市 ID、城点来源、CRS 和版本。M01 同刻 `overview/national/` 列表与获权点位/底图投影使用同一过滤与锚点。 | 两批跨页、同刻、部分点位、失权、下钻同一城市及有效素材署名分别验。S09-01 基本路径用正式可搜索列表与授权下钻独立签收，保留地图专项。 |
| 3.2 全国正式趋势 · BG-003、BG-004、BG-027 | PD-04 已确定每时获权、有效且可比城市中的优良占比。取得适用发布许可后，统计模块按同系列和源时区提供有界时序。以获准的多时刻 L 为证，每一槽记录分母、缺报和未知。 | 抽算相邻多时刻，测标准未知、分母变动、缺槽和许可退出。独立签收全国趋势。单城市 T 按独立产品展示，F 继续覆盖设计交互。 |
| 3.3 正式 D/H、更正和来源准入 · BG-005、BG-007、BG-008、BG-010、BG-027、BG-029、BG-043 | 为每一适用产品记录获取、保留、展示、分析、导出、备份和再分发等实际授权，取获准 D 地方日报日、H 站址期及有出处的正式纠错样例。取证路径包括 `observations/current/`、`catalog/series/`、研究明细、获准 CSV、`admin/quality-issues/`、站址变更与版本账本。在线来源按 V3.0 §12.4 与实施方案 §9 分开受限试运行和正式在线复审。 | D 跨日有报/合法无报，H 同一授权数据集同城站址可比性，纠错前后资格水位/局部失效/撤权，对账及来源署名各自验。城市离线 T10、已授权城市在线 T11 和正式更正分别出结论。无当期样本或许可的项标 `待验证`。 |
| 3.4 同源宿主与路由 · BG-001、BG-023、BG-024、BG-025、BG-026 | 集成方提供同一发行版本的 SPA 和 Django，并确认真实 HTTP 宿主的路径职责。按 `air-quality-frontend:docs/ROUTES.md` 在浏览器首达、刷新、后退、过期回跳，保留 HTTP 状态、内容类型、Cookie/CSRF 和能力重查证据。 | 覆盖 `/`、正常及未知 `/app` 子路径、三条旧址、`/api/v1/`、`/admin/`、`/static/admin/` 和 `/health/ready/`。分别对 HTML、JSON、静态资源和探针断言。客户端路由回退与宿主响应分开取证。T07/T12。 |
| 3.5 隔离服务、三链与并发 · BG-009、BG-012、BG-013、BG-016、BG-017、BG-020、BG-026、BG-029、BG-030、BG-031、BG-032、BG-033、BG-034、BG-035、BG-037、BG-039、BG-041 | 使用可恢复的一次性 PostgreSQL/Redis、Django、Celery Worker 和单个 Beat、独立账户与会话。逐链核 M01→M02→M03/M06→M07、M08→M09→M10→用户重读、M04/M05→M06→M07→CSV。治理按 S09-02 从已有获权 Job 或登记模板起步。 | 事务交错、重复请求/同窗、丢首包、投递未确认、Worker 恢复、执行与窗口结算、另一用户不可见、会话撤权、旧票失效和实际 CSV 字节须有原记录与读回。保留构建 SHA、数据域、非敏感条件、预期/实际与未覆盖项。T01–T10、T12 按适用场景分别签收，T11 进入 3.3。 |

每个专项用 `PASS / FAIL / NOT RUN` 记录其证据类别（F、H、授权正式数据、注入故障或真实运行）。证据只用于已准许的留存范围。缺许可、缺环境或缺相应版本时，保留具体待验证条件。本轮完成的是文档与目标契约，真实同源、进程、正式数据和获准来源列为 Phase 09 的独立验收。

## 后端独立执行批次（Phase 02 冻结后）

起点为 `main` 的 `5b7ed5f`，工作区及暂存区干净。标签 `phase02-baseline-v1.0` 指向 `abbb45b`。以下状态补充原计划，历史验收记录保留。

| 任务 | 本轮实施条件和出口 |
|---|---|
| 1.1、1.8 | 工作区、权限、诊断和本人资源可先跑后端回归。实际路由回跳、页面清理和呈现待同版前端。 |
| 1.2、1.3、1.6 | 输入已冻结。2.1–2.3 在本轮提前实现并独立验证，页面消费留待联调。 |
| 1.4、1.5 | 2.5–2.7 可用隔离账本、两个会话和丢弃首包验证。正式来源更正依 PD-09。 |
| 1.7、2.4 | 先订正现行规格，再按已验证的接口发布增量和 schema 断言。 |
| 2.1、2.2、2.3 | 立即实施普通页与精确读、目录、研究点和配对、CSV 格式与权限。域、版本和字节分别验。 |
| 2.5、2.6、2.7 | 立即实施质量事件资格及隐匿、影响恢复、历史原命令和理由配对，依现有授权服务。 |
| 2.8、2.9 | 运行独立诊断、数据库和接口回归，汇集同版证据。联调候选的前端操作仍需最终页面。 |
| 2.10 | PD-04–11 所列增强沿原专项交付。附加导出列、D 附件、建 Job、首挂、批量、全局指标、反查与来源持久化均有独立边界。 |
| 3.1、3.2、3.3 | 正式地图需素材、点位与 CRS。全国趋势需逐时 L 发布权。D/H、正式纠错及在线验收分别需正式用途与来源证据。 |
| 3.4、3.5 | 同源 SPA、页面三链待正式联调。PG、Redis、Worker、单 Beat、会话、并发和文件可先在隔离环境验证。 |

### 独立验收结果

| 计划项 | 本轮交付及证据 |
|---|---|
| 1.1–1.8（8 项） | 公共层与页面可采用现行用户工作区、目录、父研究意图、动作投影和回执。八组后端接口已提前实现并独立验证。前端路由、确认窗、失联恢复交互及清页仍在同版浏览器验收。 |
| 2.1、2.2、2.3（3 项） | PASS。`test_s04_selection`、`test_s04_noninterference`、`test_s07_catalog`、`test_s07_exact` 覆盖完整获权分页投影、PG 锁交错及仅 S 权发现。`test_s05_statistics` 验图点真实边界、Pearson 系数样本、直接查询与保存方案的实际响应预算。`test_s05_private`、`test_s05_races` 验两种数据域和两种附件的字节、字段权和失效语义。正式域分支用隔离库自制样本。 |
| 2.4（1 项） | PASS。`test_s07_contract`、`test_s07_contract_user_http` 对照 OpenAPI 3.1、Django resolver、实际 HTTP 及负例。`v1-phase08` 包含 55 条路径、74 个操作。 |
| 2.5、2.6、2.7（3 项） | PASS。`test_s06_quality`、`test_s06_collection`、`test_s08_receipts_session` 验入口资格、证据链接、版本竞争、逐动作理由及本人查证。`test_s06_history_generation` 验原 stage 与 attempt 一一关联，清理、错人、撤权与重复读取。2.6 的正式来源订正仍依 PD-09。 |
| 2.8、2.9（2 项） | 已有诊断、关系读回和候选后端按隔离回归验证。S08 单 Beat 调度 `s07-b-843beb15fb1d`、S06 V16 重投及撤权 `s06-46b0e8ccd5e8`、独立恢复 33 项 `s06-4d5675ef8df2` 均 PASS。完整页面三链在 3.4/3.5 签收。 |
| 2.10（1 项） | PD-04 至 PD-11 各专项沿原决策排期，具体条件见 BG-002/003/007/008/016/018/021/028/036/038/040/042/043。 |
| 3.1–3.3（3 项） | NOT RUN。正式地图与全国趋势分别等待素材授权及逐时 L 发布权。正式 D/H、逐源更正、T11 城市在线各依自身许可、来源证据及运行期。 |
| 3.4、3.5（2 项） | 3.4 同版浏览器宿主与 3.5 页面三链 NOT RUN。3.5 中可独立验证的隔离进程、双会话、数据库并发、撤权、失联和文件部分已提前完成。 |

跨版实验在一个隔离 PG 库和同一 HTTP 会话内切换服务。Phase 02 签发的明细 v1 票可在原版下载（23 列、3 行），升级后收到 `409/method_changed`。只有旧授权时重签返回 403，追加 v2 对应授权后返回 201，新附件为 24 列、3 行、1,448 字节。旧普通页游标原版可续，升级后返回 `400/invalid_cursor`，重新读取首批可续。原始收据 `compat-research/receipt.json`。

独立复审 `/root/delivery_review` 初审提出 L3-01 隐藏事件上的传输错误、L3-02 协商缩进后的响应字节预算、L3-03 证据入口正式域参数。三项均已修复并经该评审定向复核关闭。对应真实 HTTP 回归在 `test_s06_quality`、`test_s05_statistics`、`test_s07_contract_user_http`。本轮无需业务模型迁移。最终代码源摘要为 `61fc8ec6c55f1cb82067b205af27660f9a329d20c27080173aedfd4df18aef2b`，功能与契约提交为 `d3248bcf91e241e4ff23d1bdf591fefda9bc4b05`。综合回归原始 shell 命令未保存在临时收据中，已在持久化索引中标记为 `NOT RECORDED`，不补写推测命令。

### 验证收据与适用范围（2026-11-27）

隔离样本来自 F、自制双域权限分支及注入故障。脱敏收据已持久化到 [`docs/verification/backend-independent-v1.0/`](../verification/backend-independent-v1.0/)，并按运行编号保留原始收据的 SHA-256。原始文件当前仍位于本机私有临时目录 `/tmp/aq-backend-check.nUBLRJTE`，该临时目录不是长期证据入口。

| 项目 | 结果 | 可核对的事实 |
|---|---|---|
| 综合回归 | PASS | 最终代码 `finalall.xml`、`finalall.log` 共 728 项，690 通过、0 失败、38 跳过，耗时 11 分 31 秒。通用环境跳过的 5 项成本测试及 33 项恢复测试均在各自专用环境通过。14 条警告来自 JUnit xunit2 与 `record_property` 的兼容提示。起点 `5b7ed5f` 同口径为 665 通过、38 跳过。 |
| 中间回归复验 | PASS | 定向组合中曾有 1 次登录请求收到本机 TCP reset（`acceptance.log`）。对应 HTTP 文件随即 8/8 通过（`transport.log`），最终综合回归亦通过该用例。 |
| 历史导入丢失首包 | PASS | `history-worker/receipt.json` 使用两个并发查账请求、专用 Redis、独立 Worker 和 PG。丢弃两份提交响应后，从原 stage 分别核实两条 `sent/pending` 记录。Worker 各投递一次，拒绝注入的无效归档并清理暂存。再次查账仍得原编号，共两条 attempt。 |
| 进程故障与恢复 | PASS | `s06-f46f2c937fe2` 验证 Worker、单 Beat 和实际 PG 写入，分别中断并恢复 Redis、Worker、Beat 和 PG。 |
| 跨版升级 | PASS | `s06-ef4adc2b37bb`、`s06-09784fb9d72a` 以旧库和原会话升级至本次后端，研究游标、保存方案、旧票及既有账本通过各自断言。Phase 02 至当前版的同会话文件实验另见上方收据。 |
| 百万 F 性能 | PASS | `s07-b-8a40f18fbf08/performance/result.json` 记录 100 万个逻辑小时、5 个并发会话，R1–R4 各 50 次全部正确，P95 依次为 283.10、368.65、825.85、1787.30 ms，对应预算 1000、1000、2000、2000 ms。 |
| 万行导出与恢复 | PASS | `s06-3e9a5c954f26` 的 5 项成本测试包含 10,000 行 CSV（准备 459 ms、下载 P95 582 ms）、上限拒绝及实际摘要，最终代码复跑 5/5 通过（`s06-45cb91c47595`）。`s06-4d5675ef8df2` 在专用恢复环境通过 33 项。 |
| 工程检查 | PASS | Django `check`、`makemigrations --check --dry-run`、`migrate --check` 均通过。Ruff 检查和格式检查覆盖 320 个文件，mypy 通过配置的 3 个源文件，`git diff --check` 通过。 |
| 正式验收 | NOT RUN | 同版浏览器宿主及三条页面链、正式地图、全国 L 趋势、正式 D/H、逐源更正和 T11 在线运行分别保留第 3 批的前置条件。 |
| 本轮新增数据库迁移 | N/A | 复用既有模型、唯一约束及账本，本轮没有新增或变更 migration。 |
