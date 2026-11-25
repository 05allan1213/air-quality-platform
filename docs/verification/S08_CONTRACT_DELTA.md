# S08 API 兼容差集

本记录对应原实施计划 §6、§6.1、§6.2 已落地的后端能力。原正式前端清退后，OpenAPI 与 `b12f33f717ce5f1ccb18407b4e56ff9849aa4024` 字节一致。以下 API 路径省略 `/api/v1/` 前缀，并保留末尾斜杠。接口细节以本次对照的 OpenAPI 和处理函数为准。[S08 实施计划](../plans/S08_IMPLEMENTATION_PLAN.md#6-冻结契约差异与已确认的取舍与增强规格) · [接口文档](../api/v1.md) · [OpenAPI](../api/openapi.v1.json)

| 对照项 | S07 冻结基线 | S08 对照快照 |
| --- | --- | --- |
| 版本 | `s07-backend-v1` → `a3fde5b2239fbd55e83e54ad0006e034b26f57a1` | 当前工作树的 `docs/api/openapi.v1.json` |
| OpenAPI SHA-256 | `187e7d27b976442ded2524cc7248fd2abe66f388d64b47e4fb4ae8ea028f7e26` | `0584f4ad2d7512f3430774366b60f4d4cc1d547df347bb59a30b454b65530136` |
| 显式 HTTP 路径和操作 | 49 条路径、66 个操作 | 55 条路径、74 个操作。新增 8 个操作，移除 0 个 |

按 `(HTTP 方法, 路径)` 比较，66 个原操作中 57 个对象直接相等，9 个对象有差异。展开本地 `$ref` 后，6 个差异只涉及请求体定义复用。其余 3 个是全国总览、运行健康和账号直接授权枚举。组件新增 28 个 schema、1 个错误响应，修改 `NationalResult` 和 `Health`，原组件无删除。普通目录的 L 链接勘误不改变 OpenAPI 操作对象。操作数只包含 OpenAPI 显式声明的方法。[原 V12 矩阵](S07_V12.json) · [S07 封版报告](S07.md)

## §6 · 展示、选数和运行证据

新增读使用当前 Session 和私有 `no-store` 响应。两个目录每次选定一个用途和数据域，省略 `scope` 时为正式域，F 使用 `scope=synthetic`。目录和对象审计分页默认 25、最多 50。签名游标绑定用户和选择条件，最长有效期一小时，页尾只考虑可披露记录。缺功能权限或会话失效返回 403。目录在允许发现但无可披露项时返回空集，输入或游标非法返回 400。写操作及只读 POST 使用 Session/CSRF，JSON 上限为 16 KiB。状态码及应用错误码以各操作的允许范围为准。[Session 与游标](../../backend/accounts/research_session.py) · [游标编码](../../backend/observations/s04_http.py) · [JSON 请求限额](../../backend/accounts/parsers.py)

| 操作 | 原调用与显式增量 | 权限、拒绝及成功投影 |
| --- | --- | --- |
| **新增** `GET me/capabilities/` | 无查询参数。原登录、注册和 `GET/PATCH me/` 的返回结构沿用。 | 活跃 Session 可得闭合 `{workspaces,actions}`，无具名权限时为 `['user']` 和空动作。动作来自当前明确授予的组或直接权限。裁决及站址维护还需 `review_observation`，账号与字典入口还需 active staff。对象授权在具体操作中复核。带参数 400，失效会话 `403/not_authenticated`。[能力处理](../../backend/accounts/profile.py) |
| **新增** `GET catalog/series/` | `catalog/objects/` 继续提供原展示目录。新增 `purpose=analysis` 和 `method=statistics/compare/pearson`，`start/end` 为带时区半开区间，最长 30 天。可选 `scope/product/indicator/page_size/cursor`，对象选择最多一项 `city` 或 `site_epoch`。 | SQL 先按 C/S/H 可分析系列、确证身份、授权地域、字段及时间筛选，再按稳定键分页。核留存、分析和 LOCAL 派生展示用途，原始明细另核。`200 {results,page:{page_size,next_cursor}}`，每项为 `source/product/shape`、一个对象 ID、`indicator/unit/report_kind/window_definition/standard_series/source_timezone/permitted_from/permitted_until`。缺分析依据 403，获权结果变化可返回 409。发现阶段过滤不兼容候选，获准但无候选时返回 200 空列表。根据 §6，组合校验由 S05 分析端点负责，分析提交可返回 `422/incompatible_series`。[入口](../../backend/observations/s08_series.py) · [选数与分页](../../backend/observations/s08_series_selection.py) |
| **新增** `GET admin/catalog/products/` | 原 Job 列表和 `catalog/objects/` 仍用于各自用途。新增 `purpose=execute/configure/ledger/quality/operations`，共用 `scope/product/page_size/cursor`。`execute` 要求整点 `target` 及 `reason=manual/review/backfill`。`ledger/quality/operations` 要求源地方日 `[start,end)`，最长 30 天。`configure` 只接公共参数。 | `200 {results,next_cursor}`。执行项仅 `{product,job,reason,target}`，核操作者与登记执行者的执行权、用途、端点、时效、预算、计划归属、暂停与补采规则，`review` 另需审阅权。目录读不登记窗口、预占配额或派发。账本/质量需要审阅权及各自的用途证明，运维发现还需 `accounts.diagnose_operations` 与可披露时段。后三种只返回 `{product,source,shape,links}`，分别给 `windows/runs`、`quality_issues`、`trends`。配置投影见下节。非法目标可返回 `400/invalid_target`，撤权或会话失效 403，限流 429。[管理目录](../../backend/collection/s08_management_catalog.py) · [采集准入](../../backend/collection/admission.py) |
| **增强** `GET overview/national/` | 原城市顺序、`aqi_desc`、筛选、投影及游标分支继续可用。增加 `order=aqi_asc` 和 `grade=1..6`。`home=1` 使用独立游标，接受 `rank_preview=aqi_desc/aqi_asc`、正 int64 `focus_city` 和 `map_limit=1..400`。正式省略产品时仍读 `cnemc/city-list-hour`，F 须显式给 L 产品。`at/as_of` 互斥，小时窗只接受 `hourly_label_unspecified/hourly_left_label/hourly_right_label`。 | 保留 `selection/synthetic/snapshot_at/evaluated_at/state/summary/results/page`。首页同一快照增加最多五城的 `ranking:{state,rows}` 及 `focused`（一行或 null），表与榜独立排序，AQI 同值并列且真实 0 参与排名。`map_limit` 当前只得 `{state:'unavailable',items:[],has_more:false}`。显式 AQI 排序、分级或榜顺序缺已核标准为 400，排序或分级缺汇总权为 403。使用普通城市序且榜缺标准或汇总依据时可以返回表和不可用榜。`q` 的别名增量见 §6.2。原 L 目录链接保留实际小时窗与已知标准，T 趋势仍来自同城独立 T 产品。[全国总览](../../backend/observations/s04_national.py) · [目录链接](../../backend/observations/discovery.py) |
| **增强** `GET admin/operations/health/` | 旧请求保持 `{components,window}`。仅指定 `window` 时接受 `include=first_valid_write`，仍可带 `object_code/indicator`。原趋势、覆盖和运行列表入口沿既有契约。 | 单看组件需 `accounts.diagnose_operations`。增强窗还需 `review_observation` 与所选范围的审阅、留存用途。`window` 同时附 `first_valid_write_state/first_valid_write_page_truncated/latest_first_valid_write_at/oldest_visible_pending_created_at/latest_visible_fetched_at/visible_errors`。从可披露键的首个有证据有效转换取最晚时刻，`known` 有时刻，`none` 和 `unavailable` 均为 null。Run 错误只取可见页的 `error_code/result/request_state`。无窗或错误选项 400，无权 403，按原窗口拒绝规则处理不可披露对象。[运行健康](../../backend/accounts/operations.py) · [首次有效写入](../../backend/accounts/operations_first_write.py) |

### NAT-01 / NAT-02 · L 目录与全国快照

普通目录按观测时刻优先。在最新可披露时刻有多条候选时，L 优先选获准 AQI，再以观测键排序。AQI 的 `link` 打开 `overview/national/` 并保留 `at`、实际 `window_definition` 和已核 `standard_series`。未知系列省略全国接口的 `standard_series` 筛选。选中的记录是污染物等级时，`link` 等于 `history_link`，保留原指标、系列、小时窗和半开时段。这个分支也适用于等级时刻比 AQI 更新的情况。真实 Session 与 PostgreSQL 的同刻优先级、仅等级、已知和未知系列锚点往返见 `s06-b37bebf8410a`。[目录实现](../../backend/observations/discovery.py) · [定向测试](../../tests/test_s08_national_contract.py)

S07 签发的 `catalog/objects/` 游标若以 L 记录为页尾，升级后重投影可能改变该行指纹。首次使用受影响的旧游标可收到 `409/page_changed`。客户端保留查询条件并移除 `cursor`，从第一页取得新游标。下述升级收据分别验证旧目录游标与旧研究明细游标。

## §6.1 · 周期计划及主机交接

新增独立具名权限 `observations.configure_collection_schedule`。模板与相应计划须有目标范围的 `retain_normalized/review` 账本用途。配置权覆盖模板摘要和计划命令。执行权、账本审阅、以及 Job/来源/产品的恢复动作分别由原服务判定。主机登记模板和版本，锁定数据来源、产品、地域、执行者、profile 和来源时钟。执行者独立通过 `run_collection`、在线取得/本地处理/留存、适配器和预算的门禁，CNEMC 验证许可仍不支持登记周期。[计划接口](../../backend/collection/s08_schedule.py) · [采集服务](../../backend/collection/services.py) · [来源时钟与登记](../../backend/collection/cnemc_jobs.py)

| 调用 | 输入 | 成功及关键拒绝 |
| --- | --- | --- |
| `GET admin/catalog/products/?purpose=configure` | 可选 `scope/product/page_size/cursor`。 | 200 模板页。项含 `template/template_version/product/source/contract_version/scope_label/allowed_cadences/delivery_minute/plan_state/job`，计划引用可为 null。未授配置权 403，获准发现但无项为空页。 |
| **新增** `POST admin/collection-jobs/` | `action=create_draft`、`template`、`expected_version`（模板版）、`cadence_seconds`、未来带时区 `first_due_at`、`reason_code=schedule_create`、规范 UUID `request_id`。 | 201 草稿与 `action_id`。频率须命中模板批准值，且为 3,600–86,400 秒内整小时。活跃范围须走后继流程。 |
| **新增** `GET admin/collection-jobs/{pk}/schedule/` | 路径 Job ID，无查询参数。 | 200 `job/state/version/template/template_version/current_template_version/cadence_seconds/first_due_at/predecessor/predecessor_version/pending_successor/effective_from/effective_until/preview`。预览为 `boundary/previous_last_due_at/first_due_at/gap_seconds/conflict`。带查询参数返回 `400/invalid_request`。 |
| **新增** `POST admin/collection-jobs/{pk}/schedule/` | `propose` 以链尾为入口，传 `expected_version/template_version/cadence_seconds/first_due_at`。`activate/discard` 以原草稿为入口，传 `expected_version/template_version/expected_predecessor_version`，无前驱时末项为 null。三者均带 `request_id`，理由依次 `schedule_change/schedule_activate/schedule_discard`。 | `propose` 201，`activate/discard` 200，回计划投影及 `action_id`。非法命令 400，权限/用途/CSRF 拒绝 403，版本、幂等意图、边界或占窗冲突 409，限流 429。 |

草稿未启用时不参与投递。启用使用分界目标小时 B 切分旧、新计划，分界前仍由旧计划拥有，分界起按新计划归属。既存 Run、窗口及历史恢复继续按原业务窗口和计划范围裁决。后继流程复用手动触发、扫描和恢复的归属检查，包含继承暂停、拒绝预占与防止同槽双归属。来源/产品解除暂停与历史导入阶段登记依然使用现有专用入口。许可撤回继续通过主机交接目标、`GET admin/grants/{grant_id}/actions/` 核认、原 POST `withdraw` 和主机处置流程执行。[分段与暂停](../../backend/collection/schedule_segments.py) · [恢复](../../backend/collection/recovery.py) · [许可撤回](../../backend/observations/disposition_control.py)

## §6.2 · 字典、本人回执、对象审计

- `POST admin/accounts/{pk}/actions/` 的 `grant/revoke` `permission` 枚举新增 `accounts.maintain_city_search_alias`、`observations.configure_collection_schedule`。旧 permission 值、`grant_role/revoke_role` 枚举和请求信封继续有效。旧账号操作继续核验 active staff 和 `manage_accounts`。旧角色组未被自动赋予这两项权限。[账号治理](../../backend/accounts/governance.py)
- 受限 Admin HTML 目录为 `GET /admin/observations/city-search-alias/`，单对象 `GET/POST /admin/observations/city-search-alias/{product}/{city}/`。字典需 active staff、`accounts.maintain_city_search_alias` 和该 L 产品/城市的 `catalog` 审阅与留存范围，与账号管理各自核权。GET 读绑定对象、现值与版本。表单 POST 经 CSRF 校验，接 `set/clear`、`search_alias`、`reason_code=search_correction`、规范 UUID、`expected_version`。别名经 NFC 和首尾空白处理，设置为 1–32 个准许字符，清除后版本仍递增。无权或不可见返回 403/404，非法表单 400，旧版或重放意图冲突 409。别名按 `(product,city)` 绑定，只参加 `home=1&q=…` 中获准展示并留存的目录匹配。表、汇总和名录使用同一匹配口径，响应始终显示正式名。旧 `q`、正式登记、观测身份和 S05 输入版本沿原逻辑，留存结束时按目录处置规则处理独立别名。[受限 Admin](../../backend/accounts/restricted_admin.py) · [别名规则](../../backend/observations/city_search_alias.py) · [目录处置](../../backend/observations/disposition.py)
- **新增** `POST admin/command-receipts/lookup/`。只读信封 `{family,target,command}` 的 `command` 为原完整命令，包括原请求 UUID。支持 `manual_run/job_control/redeliver/schedule/quality/station_change/grant_withdraw`。`target` 是原入口（重投为原 Run UUID，其余为正 int64）。计划创建用原模板，提案用原前驱，启用/放弃用原草稿。来源或产品控制仍以原入口 Job 匹配，参数指纹由服务端重建。只查当前 actor 的记录，按原动作复核权限和数据范围，重投另核原 Run。纯执行者可查本人受理状态，GET Run 明细仍按原审阅边界。信封结构错误为 400，目标值非法、原命令不匹配、无权、未提交和他人的记录同为 `404/not_found`，会话/CSRF 403，限流 429，数据库不可用 503。[本人回执](../../backend/accounts/s08_receipts.py)
- 回执 200 的治理基础投影为 `{state,action,recorded_at,action_id,version}`。`schedule` 加原结果 `job`，`redeliver` 加 `run_id/window_id/dispatch/status`。`manual_run` 为 `{state:'submitted',action,recorded_at,run_id,window_id,dispatch,status}`。原六个提交操作 `submitCollectionRun`、`redeliverCollectionRun`、`changeCollectionJob`、`adjudicateQualityIssue`、`applyStationChange`、`withdrawGrant` 仅复用请求 schema，原请求约束与响应保持兼容。旧 Job 快捷命令仍由原接口兼容，回执查找要求带 UUID 的完整信封。[OpenAPI](../api/openapi.v1.json) · [原账本命令匹配](../../backend/accounts/governance.py)
- **新增** `GET admin/object-audit/`。输入 `kind=job/run/quality_issue/station/grant`、单一 `id`（Run 为规范 UUID，其余正 int64）、带时区半开 `[start,end)`（≤30 天），以及可选 `page_size/cursor`。200 为 `{results,page:{page_size,next_cursor}}`，记录闭合 `{at,action,outcome,actor_relation,before_version,after_version}`，版本可为 null，`actor_relation` 限 `self/other/system`。按时间及记录键倒序，从对象追溯原控制、治理、裁决或目录变更，末端复核对象、记录及用于判定页尾的额外获权项。Job 需对应动作的管理或配置权及用途。计划启用记录另核原分界起的完整接管范围，范围内撤权会从结果和页尾排除该条记录。Run/质量需审阅和各自的范围证据，站址还需目录管理，grant 需现行许可管理和有效交接。无会话/无权/对象不可披露 403，非法参数/游标 400，超出采集管理工作台的逐用户限额返回 `429/rate_limited`。全局按人、UUID 搜索及保管资料由主机处理。[对象审计](../../backend/accounts/s08_object_audit.py) · [逐对象授权](../../backend/accounts/s08_object_audit_scope.py)

### HTML 路由边界

清退移除 `/`、`/app/` 与 `/app/<route>` 的 HTML 壳路由及旧 `/static/app/` 资源集成。Django Admin 及字典入口保留。根路径和旧壳路径回到 Django 404。后端的显式 API、Admin 和健康探针分别校验。[路由表](../../backend/config/urls.py) · [契约路由测试](../../tests/test_s07_contract.py) · [实际启动与静态验证](S08.md)

## 增量 V12 和本地收据

[增量 V12](S08_V12.json) 将 8 个新增操作、3 个 OpenAPI 增强操作和 1 项 L 目录行为勘误映射到测试节点及隔离收据。HTML 字典、原写信封复用、路由和升级各有收据。总体为 **PARTIAL**。`PASS` 适用于已列行为。当期获权 H 的同一数据集双站验证待适用许可与样本。F 已覆盖 H 形态请求不支持指标时的合法空态。S07 已封版的 V12 继续记录原操作覆盖。

以下历史后端 run 的汇总取自 `tmp/s06-evidence/<run>/pytest.json`，隔离与清理状态取自同一 run 的 `V01/result.json`。表中各定向运行均为 `guard=PASS`、`cleanup=true`、`NOT RUN=0`。收据对应各自代码快照，测试计数有重叠。

| 覆盖范围 | Run | 结果 |
| --- | --- | --- |
| 导航给权、双会话及路由回归 | `s06-a2b704315583` | 9 PASS，0 FAIL |
| 研究目录、真实 Session DTO、合法空态、游标、权外变化、OpenAPI 响应集合及路由 | `s06-e0e124763b42` | 7 PASS，0 FAIL |
| NAT-01/02 全国旧调用、首页 OpenAPI、等级、榜和 L 目录锚点往返 | `s06-b37bebf8410a` | 33 PASS，0 FAIL |
| 字典单权与深链、版本、撤权、名录范围及处置 | `s06-ea602791108c` | 9 PASS，0 FAIL |
| 执行、账本、质量和运维发现，游标与并发锁范围 | `s06-c1ef3ced8ede` | 8 PASS，0 FAIL |
| 管理目录及计划配置分支 | `s06-5b10bc11ce2f` | 14 PASS，0 FAIL |
| 调度、旧采集、PostgreSQL、CNEMC 与恢复回归 | `s06-e072ed02c168` | 122 PASS，0 FAIL，覆盖权外未来窗、只读/拒绝写不结算交接、事务末端复核执行权。较早 `s06-0df1c658d523` 的交接用例曾失败 |
| 窗口首次有效写入、旧健康、质量及契约 | `s06-978e9c803557` | 29 PASS，0 FAIL。较早 `s06-904b0e3d579c` 联合回归为 27 PASS，1 FAIL，失败项为首次有效写入 |
| 本人回执、真实 Session、运维、计划和路由 | `s06-b7a2ca3f7b1c` | 40 PASS，0 FAIL |
| 对象审计、本人回执、管理目录与路由联合回归 | `s06-834af2005bc5` | 28 PASS，0 FAIL |
| 调度 GET 非法查询、对象审计共用限流、接管范围部分撤权、健康末端复核 | `s06-e2acd338648a` | 41 PASS，0 FAIL |
| 账号治理明确授予及撤销两个新增权限。原主体 Session 刷新能力，权限不足的代理请求返回 403，审计动作和版本对应 | `s06-85514b391833` | 90 PASS，0 FAIL |

**S07 → S08 前向升级 PASS。** `tmp/s06-evidence/s06-075b16c61f77/{upgrade.json,V01/result.json}` 记录从 `s07-backend-v1` 停旧写者后同库迁移两遍，保留 56 张旧表、2 个 legacy Job、原窗口和两次尝试。四种旧 Session 仍按原能力读本人资料和获权对象，计划入口均拒绝。旧研究明细游标、保存方案重跑和有效导出票据分别返回 200，票据为 1,060 字节且 SHA-256 相符。[升级脚本](../../scripts/s08_contract_upgrade.py)

- **全国总览 `/overview/national/`。** S07 签发的默认城市序和 AQI 降序游标均在 S08 沿用旧 Session 续页，返回 200。比较时排除动态的 `evaluated_at` 和 `age_seconds`。将同一游标传给 `home=1` 返回 `400/invalid_cursor`。
- **普通目录 `/catalog/objects/`。** 旧游标尾行的 L 链接经勘误后指纹发生变化，升级后的同一续页请求返回 `409/page_changed`。清除游标重读首页后取得新版游标，续页返回 200。两页对象、排序和其他字段一致。

原正式前端的壳、浏览器联调与视觉证据见 `refs/archive/s08-formal-frontend:docs/verification/S08.md`。当前后端回归另有清退后收据。[验证与证据索引](S08.md)

调度复核 SCH-01、SCH-02、SCH-03 均已关闭。HTTP 激活和主机 `--phase` 纳管均先核完整开放接管段 `[B,+∞)` 的审阅与留存许可，再查冲突。仅覆盖有限数据日期时拒绝且零写。在有效期内覆盖整段的许可可以纳管。真实 PostgreSQL 回归 `s06-f311710660fe` 通过 131/131 项，另核只读与拒绝写、事务末端撤权回滚及审计的部分撤权。地图和当期 H 仍需保管方提供适用材料。T11 维持 NO-GO。[当前验收](S08.md) · [总体阶段边界](../design/总体实施方案.md)

### 复核方法

以 `git show s07-backend-v1:docs/api/openapi.v1.json` 读取基线，与工作树 JSON 按路径和显式 HTTP 方法比较。JSON 对象按键比较，数组按原顺序比较。递归展开两侧本地 `#/components/…` 引用，再比较共享操作的请求、响应及参数。原六个写信封展开后相等，账号仅扩展直接权限枚举。展开 `Health` 可观察响应 schema 对可选增强字段的约束，即使该操作的直接 `responses` 对象未变。静态核对同时检查当前 SHA-256、resolver 入口以及已引用的测试和收据文件。
