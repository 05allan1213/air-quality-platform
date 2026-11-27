# Phase 02 前后端接口契约基线

**Phase 02 v1.0（2026-11-27）｜联合冻结。** §2–5 记录现行接口，§6 冻结八组目标线协议和验收条件，§7 说明交付边界。正式维护 `air-quality-platform:docs/frontend/CONTRACT_BASELINE.md`，前端逐字节同步至 `air-quality-frontend:docs/CONTRACT_BASELINE.md`。改动先核两端，再从正式来源复制并校验 SHA-256。产品选择见 `air-quality-platform:docs/frontend/PRODUCT_DECISIONS.md`，实施主体见 `air-quality-platform:docs/frontend/BACKEND_REMEDIATION_PLAN.md`。

## 1. 版本、来源与使用方法

前端 `main@d10c6fb5d0823f8d3a18f18a235e142221bb9d2b`、后端 `main@0c0a064769a69a8474ce3dda20ba82c05916fd9d` 为本轮两个工作树的提交基点，基线文档以工作树冻结内容为准。本轮直接核查两端，实现证据可按下列仓库相对标识定位。前端传输分支由 `air-quality-frontend:src/api/analysis.ts` 等按 `mockMode` 选择真实 HTTP 或 `src/mocks/` 的 F 适配器。F、注入和旧浏览器收据保留其原证据等级。

| 上轮输入（基于 `air-quality-platform:docs/frontend/` 解析） | 上轮 SHA-256（历史证据） |
|---|---|
| `BACKEND_GAPS.md` 原始 90315 字节，追加前 | `662f1326006ef2e49fe41854fa84952ee9d8d4414a1605c8dec51be5c8919676` |
| `CONTRACT_REVIEW.md` | `4405f3d30f60c668e8786b2a94859d3c48e46ad28c64f7531ba3a9723b168a5a` |
| `PAGE_REQUIREMENTS.md` | `87f806a2122617b6882c7e7b28815fddf359f9b56735640448561a2948b407e8` |
| `PAGE_API_MAPPING.md`（复核后版，原审计版前缀 `47ef2311f1ec`） | `66e6998f0f96ec192227547d9763d7bfe34b096ede5f286c141b7b9524340636` |
| `ROUTES.md` | `e440c055e5960e6e57cc6c1f420979a4b3220af07e33a92b7f4b990de10fb63b` |
| `DESIGN.md` | `d5c471b81ef350f0a5e3c7f8acb05a9a5fa240962c8154fe7b1959200bcdbe2d` |
| `../api/openapi.v1.json` | `0584f4ad2d7512f3430774366b60f4d4cc1d547df347bb59a30b454b65530136` |
| `../api/v1.md` | `93f9a2d5da4c066684f26cd94dfa6b2ef3c9f4d9c0dca2b279a4319930eddf16` |
| `../design/城市空气质量监测数据分析平台_系统总体设计方案_V3.0.md` | `4373fca30fd71061c4e795ebd02b73190e39d451dd7830c0dc5bf01e58379bf6` |
| `../design/总体实施方案.md` | `ef43ad2459ab643f0fb54bb4fb16cfaa745fef8cd1283e40ba22a7c9ec4410d7` |

§2–5 可用于现行请求和解码。此前前端反向核验保留在 `air-quality-frontend:docs/CONTRACT_REVIEW.md`，上表仅用于追溯分仓审计时收到的文件。现行服务端规范为 `air-quality-platform:docs/api/openapi.v1.json`（OpenAPI 3.1.0，`info.version=v1-s08-phase02.1`，SHA-256 `48e935fe288b4be8de78e0bc055a36e4bfe24910c31083443a72c68924c7adcb`），已知实现描述差异以本文件勘误及 BG 台账追踪。§6 均为**已冻结、待实现及真实联调验证**，新增线协议在实现验证后才纳入现行 OpenAPI。

## 2. 现行共同约定

**操作定位。** 所有下列相对路径以 `/api/v1/` 开始，保留末尾 `/`。55 条路径、74 个操作（前端映射记载 60 个 SPA 调用、14 个未调用），另两项 F 模拟路径只在客户端。查找任一操作，先在后端 OpenAPI 按 Method + Path 取 `operationId`，再按该 ID 查原审计技术附录的处理器及这里的差异说明。未调用操作含夹具、站址、许可、处置、账号、备份与恢复，按原权限和执行责任保留。OpenAPI `air-quality-platform:docs/api/openapi.v1.json` · 逐操作核对表 `air-quality-platform:docs/frontend/BACKEND_GAPS.md#附录逐操作技术核对表` · 页面调用方 `air-quality-frontend:docs/PAGE_API_MAPPING.md`

- **类型与缺省。** `int64` 对象键为正整数，UUID 为 UUID 字符串。浏览器须无损解析大于 `2^53-1` 的整数，按端点要求序列化（研究 JSON 中对象 ID 要 JSON 整数）。整数标识、十进制观测字符串、统计数值、opaque 版本、显示标签分别建模。`?` 表示可省略，`|null` 表示可显式为空。部分响应把无权字段整个省去。空集合只说明该获权分支返回零项。缺失字段不能解码为零、无事件或已获权。例：`PATCH me/` 只提交更改的姓名，空字符串合法，`null` 无效。`fields:[]` 有明确定义，见下节。
- **身份与用途。** 同源 Session Cookie。用 `GET auth/csrf/` 取 `csrf_token`，登录轮换后重新取得。写和只读 POST 携当期 `X-CSRFToken`。私有响应遵守 `no-store`。`GET me/capabilities/` 的 `workspaces:string[]` 对活账号含 `user`，有具名动作时另含 `manage`。`actions:string[]` 来自实际分配权限，审阅与裁决、调度配置、源执行、诊断、账号管理各自检查。对象、用途、来源、数据域、保留权和到期在实际处理器复核。切换会话、撤权、离开相应域时清旧投影及私有请求状态。能力清单 `air-quality-platform:backend/accounts/profile.py` · 会话与用途 `air-quality-platform:backend/accounts/research_session.py`
- **错误与重试。** `403/not_authenticated` 走登录，`403/csrf_failed` 刷新 token，`403/use_denied` 或 `permission_denied` 收回相应用途。`400/invalid_request` 改输入，`invalid_cursor` 重新取首批，`query_too_large` 缩小请求。`409/page_changed` 放弃整次合页，`409/input_changed` 重建研究父结果或重新预览，`precondition_conflict` 重读目标版本，`request_conflict` 核对命令身份。`422/incompatible_series` 更换可比条件。`404/not_found` 用于相应的不可发现对象，`410/expired` 用于仍保留的过期票。`429` 只在实际提供时使用 `Retry-After`。`503/database_unavailable` 与带编号的投递不确定分开。按操作区分 JSON、CSV 与 Django 原生 HTML 404/405/500。成功空结果、无权和超限分别展示。API 文档 `air-quality-platform:docs/api/v1.md` · DRF 错误 `air-quality-platform:backend/accounts/api.py`
- **时标、数据域与覆盖。** 小时窗使用含时区瞬时、半开区间 `[start,end)`，D 取源时区报告日。执行、抓取、发布、首次有效写入分别显示。正式观测 GET 通常省略 `scope`，F 需 `synthetic`，部分管理 GET 也接 `formal`，研究父意图显式区分两域。L 同刻城市清单、C 城市七指标、S 站址期、T 单城趋势、D 独立日报、H 获许可的历史站点、F 合成夹具分开选择和署名。AQI 标准由证据确定，未知系列不用标准色、等级推定或跨系列排名。CO 的 mg/m³ 与其余浓度的 µg/m³、O₃ 不同窗分别核。零、删失、缺测、无报、无覆盖、争议保留区别。
- **统计结论。** E 是获权时段内可确定的应有槽，O、V 分别为实到、有效，E_common 为共同应有，P 为共同有效配对。分母未知保留 `null` 和原因。单序列整期结论要求 V/E ≥ 75%，跨对象结论要求可比且 P/E_common ≥ 75%。Pearson 另需 P ≥ 30、两列非零方差。受限子窗只描述其局部。统计由后端计算，含季节、自相关和缺测偏倚提示。两站各 18/24、共同 12/24 不能给整期排名。统计实现 `air-quality-platform:backend/observations/research_statistics.py` · 设计 §8.4–8.5 `air-quality-platform:docs/design/城市空气质量监测数据分析平台_系统总体设计方案_V3.0.md`

## 3. 观测、分析与本人资源 —— 实装与规格勘误

| 使用页面、操作 | 可直接依赖的请求与响应 | 版本、裁剪或已知偏差 |
|---|---|---|
| M01 `GET overview/national/`，M02/M03 城市、趋势、目录和站址 GET | `home=1` 可带 `q,geography,grade=1..6,order=city/aqi_desc/aqi_asc,focus_city,page_size,cursor`，`grade` 和 AQI 排序需明确已核系列及汇总权。按获权城市 ID 与响应链接进 M02。`GET cities/{city}/stations/` 按原始 `at?` 续批，响应 `at` 是展示锚点。 | 换条件回首批。首批省略 `at` 则续批亦省，显式则原值不变。地图请求当前只得 unavailable 空投影。C、T、S 分请求，缺 C/T 时单凭城市 ID 尚不能发现所有仅 S 权的空目录。BG-001–008。全国 `air-quality-platform:backend/observations/s04_national.py` · 站址 `air-quality-platform:backend/observations/s04_station.py` |
| M03/M06 `GET observations/query/` | 单 `product`，`cities` 或 `site_epochs`（逗号分隔，≤5），`indicators` ≤7，`report_kind,window_definition`。`mode=window` 带 `start,end`，`latest_valid` 可带 `as_of` 且仅 `quality=valid`。window 另可用 `all/suspect/missing/censored/invalid/disputed`，默认 valid。最多 720 小时、3600 格。回 `selection,evaluated_at,synthetic,state,results[],page:{page_size,next_cursor:string或null}`。普通行有 `key,revision,qualification_version`。 | 按观测时刻、键双降序。现游标签人、原条件、页长和末键，最近模式另定锚。先前非末行更正无法可靠检出，当前逐批可读，多批同版尚无保证。`state=no_coverage/no_data` 均可能合法。BG-009。查询 `air-quality-platform:backend/observations/detail.py` |
| M02/M06 `GET observations/current/` | `product`、唯一 `city` 或 `site_epoch`、`indicator,report_kind,window_definition,standard_series`，瞬时用 `observed_at`，日报用 `local_report_date`。200 `{count:0,results:[],reason:string,…}` 或 `{count:1,results:[row],reason:null,…}`，两支均有 `attribution,rights_attributions,synthetic`。有效行给整数 `revision_id,revision_no`、十进制字符串 `value`、专用 64 位小写 hex `input_version`、瞬时或报告日。 | 零行可能因 `no_observation_at_time`、`unresolved_dispute` 或当前质量。组合校验有漏判，不能将任何 200/0 都判为合法无报。`revision_id` 仅标当前修订，未给治理逻辑键；显示行 ID 与研究版本隔离。BG-007/010/011。精确读取 `air-quality-platform:backend/observations/queries.py` |
| M04/M05 `POST analysis/statistics/`、`POST analysis/compare/` | 前者 `method=statistics/pearson`，后者 `compare`。父意图为 `{method,selection,method_version?,grain?}`，缺省 `s05.v1/day`。`selection` 含单源产品、同层有序对象、指标、单位和标准、`quality=reliable`、域和半开窗（或 `anchor,span_hours`）。统计 1×1、比较 2–5 对象×1 指标、Pearson 同对象×2 不同浓度。回规范父意图、`input_version,requested_window,source_timezone,attribution`。 | 统计看 `columns,buckets,conclusion` 及获准图层。比较看 `E_common,P,paired_ratio,ranking,partial_summary`，Pearson 看 `pearson:number或null`、P 及原因。有逐点展示权才给 `chart`。比较的 `paired_points` 是按序 A−B 及以 B 为分母的变化率，B=0 时百分比空。不能解码为 Pearson 的 x/y。BG-012–014/041。父意图 `air-quality-platform:backend/observations/research_spec.py` · 统计 `air-quality-platform:backend/observations/research_statistics.py` |
| M04/M05 → M06 `POST analysis/details/` | 原父意图＋`input_version`，`support=observed/effective/paired`，可选 `bucket:{start,end,pos?,local_hour?}` 和 `page_size`（默认 50、≤100）、`cursor`。只有 `method=detail` 首批预读可省版本。回规范父意图/版、`state,results,page`，含行的 `key,revision,pos,entity,indicator,observed_at,value,quality` 等。 | `paired` 禁桶内 `pos`，detail 父意图禁 paired。版本变更 409；行按时刻、列位、键分页，同一对可跨页。现有 `paired` 接受应到未知但有效的槽，Pearson 系数只用 `expected_common AND paired`，不可从这组行直接承诺同样本散点。BG-012/013/016。明细 `air-quality-platform:backend/observations/research_api.py` · 配对谓词 `air-quality-platform:backend/observations/analysis_query.py` |
| M02/03/07 `me/favorites/` 与 `{item_id}/` | 本人同域列表按 `(created_at DESC,id DESC)`，嵌套 `page.next_cursor`。创建同偏好 200、初建 201、已存异偏好 409。本人目标撤权 GET 200 仅 `{id,kind,state:"unavailable"}`，仍可 DELETE 204。跨人/域或记录不存在 404。 | `target_id,product,name,link,epochs,results` 依可见情况。M02/M03 按钮最多查三批，不足以判定第 301 项。M07 首选产品拒绝也要清过期摘要。机器规范已校正收藏列表的排序和可见/裁剪行形状。BG-019–021。收藏 `air-quality-platform:backend/accounts/favorites.py` |
| M04/05/07 `me/saved-queries/`、`{item_id}/`、`{item_id}/run/` | 创建/更新只接受 `statistics/compare/pearson`，PATCH 带 `intent_revision`，陈旧为 `409/input_changed`。run 的 `current/data_changed/baseline_unavailable` 有结果与意图修订，`target_unavailable` 只给 state，`method_changed` 给 `method_version`。 | `detail` 留给明细父意图，不能保存。取单个方案只读本人意图，重跑时再授权。机器规范已校正保存方案的方法枚举。BG-022/041。方案 `air-quality-platform:backend/accounts/research_saved.py` |
| M04–07 `POST me/exports/` 与票据 GET/DELETE/download | `kind=detail/aggregate,groups:[规范父意图+研究input_version],fields?`。实装 1–2 组、同一域，≤12000 格/5 对象/7 指标。`fields` 省略或 `[]` 取全部必备列，附加列逐列授权。签发 201 `{id:UUID,kind,created_at,expires_at}` 或 200 `{state:"no_data"}`。GET 单票只给四字段。 | 本轮将现行 OpenAPI 的 `ExportIntent.groups.maxItems` 从 5 校为 2。M06 当前在用户确认后才预读整组 `method=detail,support=observed`，随即签票。预读可包括可披露的非 valid 已报行。确认前展示实际父范围是 §6.5 的验收条件。下载成功为 `text/csv`，最多 10000 行/4 MiB，拒绝无附件。票据元数据 GET 对本人仍保留的过期票可回 200。下载先核归属、用途、格式和再生版本，通过这些检查才会按到期回 410。清理后 404、输入变更 409、撤权按当前访问拒绝。当前 `s05.csv.v1` 未强制域标识，D 明细可能把地方日写在 `observed_at`。BG-015–018/043。票据及 CSV `air-quality-platform:backend/accounts/research_exports.py` |

## 4. 治理 —— 权限、状态与回执

| 页面和读取入口 | 已核对的实装行为 | 写、恢复和隐匿边界 |
|---|---|---|
| M08 `GET admin/catalog/products/`、`GET admin/collection-jobs/`、`GET admin/collection-jobs/{pk}/` | 产品目录按 `purpose=configure/execute/ledger/quality/operations` 核权，返回顶层 `next_cursor`。Job 列表用 `page.next_cursor`，详情 `targets.job/product/source` 各有对象及控制版本。 | POST Job 控制取相应目标当前版。动作 `pause/resume/resume_source/resume_product`。控制 POST 成功只给 `{job:int,enabled:bool,reason:string}`，写后重读 `targets`。`POST admin/collection-jobs/` 是从登记模板 `create_draft`。`GET/POST …/{pk}/schedule/` 读计划或做 `propose/activate/discard`，读计划缺省 `action_id`，写计划投影附 `action_id:UUID`。调度理由依动作分别为 `schedule_create/schedule_change/schedule_activate/schedule_discard`。草案停用，激活才推进分界。BG-028/033/037。计划 `air-quality-platform:backend/collection/s08_schedule.py` · 控制 `air-quality-platform:backend/collection/control.py` |
| M08 `GET admin/collection-windows/`、`…/{pk}/`、`GET admin/collection-runs/`、`…/{run_id}/` | Window 列表 `id,product,synthetic,target_start,target_end,full_scope,result,links`，部分授权 `result=null`。详情按 `after/run_after` 取获权覆盖和 Run。Run UUID、Window 正整数。Run `status` 为 `pending/running/finished/failed/paused/expired/coalesced`，`dispatch` 表示投递，`result` 表示业务结果。一个 Window 可与多个 Job 有关。 | `POST admin/collection-runs/ {job,reason:manual/review/backfill,target,request_id}` → 202 或投递未证 503，均可给 `{run_id,window_id,dispatch}`。`POST …/{run_id}/` 原 Run 重投 → 202 `{run_id,window_id}`。需 `dispatch=manual`、pending、未发、无所有者、无待重试等并重查授权。服务端裁定。BG-033/036。Run `air-quality-platform:backend/collection/services.py` · HTTP `air-quality-platform:backend/collection/views.py` |
| M08 `POST admin/history-imports/`、`GET admin/history-imports/{attempt_id}/` | 只收 `{profile:"uci501-edges-v1",stage_id:UUID,reason:"historical_verification"}`。202 `{attempt_id}`，投递不确定可返 503 `{code:"dispatch_unconfirmed",attempt_id}`。已知编号可获权读 `id,state,dispatch,created_at,started_at,finished_at,links`，时间可空。 | 若完全丢失首包，当前生命周期会消费暂存。最近尝试列表无法唯一关联原提交，须保留“未核实”，等待本人原请求查证。BG-035。导入 `air-quality-platform:backend/observations/history.py` · 状态读取 `air-quality-platform:backend/observations/history_status.py` |
| M09 `GET admin/quality-issues/`、`…/{event_id}/` | 队列按 `product,scope?,run?,window?,status?,before?`，状态 `open/in_review/resolved`，每批最多 50 条可见行，顶层 `next_before:int或null`。详情含 `id,event_version,status,handler:int或null,affected:object` 和当前可披露的关联。仅有观测键时附 `current_revision:int或null,candidate_revisions:int[],qualification_version,disputed,evidence:object`。其中 `evidence.correct_source?/correct_mapping?` 为签名串，不能推导当前人能执行的全部动作。 | `POST …/{event_id}/actions/` 收 `{request_id:UUID,expected_version:int,action,reason_code,evidence_ref}`。动作 `claim/hold/close/correct_source/correct_mapping`。普通动作证据显式 null，更正签名写入时限 1800 秒。源证据和映射规则当前限受准入 F。首次 200 `{action_id,status,event_version,affected:object[],outcome}`，同命令重放仅前三项。close 不能解除数据争议。当前已获审阅权的人探测不存在的事件与失去对象用途的事件，可能得到不同 404/403；这个差异仍需后端修复。BG-029–032/034/037/041。详情、证据与写回执 `air-quality-platform:backend/observations/quality_governance.py` |
| M09 `GET admin/observation-evidence/` | `product,scope?,after?` 加 `city_key/station_key/batch` 三选一。键分支返回 `revisions,receipts,issues,next_after` 等，batch 分支返回 `receipts,issues,next_after,fetched_at,profile` 等，均有 `attribution,synthetic`。键分支 receipts 没有自己的 `receipt_id`，batch 行可以有 `receipt_id,key_id,revision`。定位/原值各需相应用途。 | 当前事件 `affected` 或修订号不足以可靠构造键分支入口。用合法已知 batch 看批证据，相关键证据待后端提供类型化引用。BG-031/041。证据读取 `air-quality-platform:backend/observations/queries.py` |
| M08/09 `POST admin/command-receipts/lookup/` | 同源只读 POST，须 CSRF。`{family,target,command}` 保留本人原目标及原命令。现有 family 为 `manual_run/job_control/redeliver/schedule/quality/station_change/grant_withdraw`。质量成功回 `{state,action,recorded_at,action_id,version}`。已过期的原签名可为已提交命令查账，写入仍需当期有效证据。 | 查账按原操作、原人及现权复核。合法信封下，异人/越权/不匹配统一隐藏 404，表示“无法核实”。`action_id` 能证该动作已落账。丢失首次响应后，现有查账缺 `affected/outcome`，不得凭现状还原更正前后范围。BG-034/035。本人查账 `air-quality-platform:backend/accounts/s08_receipts.py` |
| M10 `GET admin/operations/health/` 与 `admin/operations/trends/` | 独立 `diagnose_operations` 能力可见组件；窗口需附加审阅/数据权。`include=first_valid_write` 取窗口最近首次有效写入，`known/none/unavailable` 分开。趋势按获权产品和 `[start,end)` 源地方日，最长 30 日，分子分母未知、局部和截断分别投影。 | 保留现有有效覆盖、平均执行时间两图及表格。全局深度/源端健康、小时分母待定义。账号 REST 按 `manage_accounts` 的有效权限与目标管辖核，原生 `/admin/` 另需 active staff。导航投影对账号动作也要求 staff，这三者分别验收。BG-023/024/038–040。诊断 `air-quality-platform:backend/accounts/operations.py` · 账号管辖 `air-quality-platform:backend/accounts/governance.py` |

治理命令的现代信封以非负 `expected_version` 和同一 `request_id` 绑定原操作、主体与参数。在支持该协议的入口，同信封查原回执，换参数/旧版本按冲突处理。登录、方案创建、签票和导入各有自己的恢复行为。超时优先保留原身份或编号再读，使用新 UUID 重写可能产生另一个动作。调度、控制、质量的理由按端点分开，现有调度已逐动作校验，质量/部分采集仅校验理由全集。BG-037 的整改必须保留原已提交命令的可查证性。命令身份 `air-quality-platform:backend/accounts/governance.py` · 调度校验 `air-quality-platform:backend/collection/s08_schedule.py`

## 5. 路由、下钻和返回

| 出发 → 到达 | 前端可使用的依据与失效后的落点 |
|---|---|
| M01 → M02 → M03 | 从正式同刻 L 列表的获权链接和稳定 `city` ID 到 `/app/cities/:city`，以 S 目录的站点和 `site_epoch` 到 `/app/stations/:station`。换产品、站址期或域重新发现。T 是同城最近 24 小时独立趋势，D 从获权地方日入口精确读。 |
| M02/M03 → M06 → M04/M05 | 单城市/站址沿原对象、产品、指标、时窗到 `/app/query/history`，可把合法条件带回 `/app/query`。近时有效和指定历史时段分别读取。分析入口必须有对应研究用途与获权系列。 |
| M04/M05 → M06 → M07 | `/app/query?mode=research` 需当前研究父意图、版本和支撑类型/桶。上下文丢失时回分析重新执行。签票只接受整组，票据/保存方案/收藏以本人身份在 `/app/me` 重读。过期票回申请页，跨会话来源缺省可按 kind 回通用入口。 |
| M08 ↔ M09 ↔ M10 | 用 Run UUID、Window/事件正整键及获权链接进管理深链。按产品+Run/Window 查质量队列，诊断窗口需审阅。过滤条件、回执、证据签名留在当前会话内存。无权收回选中对象、证据与动作，保留仍获准的账本或公共页。 |
| 登录 → `next` / 公开页 | `next` 仅受控站内目标，按目标能力逐次复核。M08 需相应采集/审阅/导入能力之一，Run/Window 详情另需审阅，M09 需审阅，M10 需诊断。无合适工作区回公共说明。19 个命名路由、3 条旧地址及 HTML 深链交给同源宿主专项验收。 |

管理预览、受理、异步执行与最终完成分别显示。F 保留首页图表、地图、材质和互动，在专属适配层使用演练参数。正式精修保留 S08-01–05、S09-01/02 已确认范围。路由全表 `air-quality-frontend:docs/ROUTES.md` · 页面需求 `air-quality-frontend:docs/PAGE_REQUIREMENTS.md`

## 6. 联合冻结的八组目标契约（待实现）

以下均沿用 §2 的认证、CSRF、同源和拒绝规则。路径省略 `/api/v1/`。未标 `?` 的字段必填，只有列出 `null` 的字段接受空值。`Instant` 为带偏移 ISO 8601 字符串，`Date` 为 `YYYY-MM-DD`，`Version` 为规范父意图的 64 位小写十六进制版本，`Href` 为服务端生成的同源 `/api/v1/` 内相对地址。错误为 JSON `{code:string}`。客户端的能力提示、旧签名、F 样例均不能代替每次请求的后端复核。

### 6.1 普通观测跨页一致性（M03/M06，BG-009）

`GET observations/query/` 沿用 §3 的选择器及 `page:{page_size:int,next_cursor:string|null}`。首批省略 `cursor`，后续原样回传，并保持原条件（包括是否显式 `as_of`）及页大小。后端在签名游标内部绑定主体、域、条件、同一评估锚点及整个**获权稳定结果投影**。版本覆盖可见成员、排序、修订、资格、选择范围及字段披露。`evaluated_at`、自然增长的 `age_seconds` 不参与比较。仅隐藏记录的增删改、等效授权续期都不得影响同一投影，真实到期使披露变化则按变化处理。

完整投影可继续时 200。可见非末行更正、插删、部分撤权改变投影时 `409/page_changed`，前端清空该次所有已合页，在同会话和域内从首批重读。全失权走现行 `403/use_denied`。篡改、过期或与原条件不符的游标为 `400/invalid_cursor`。验收必须分别覆盖自然时间推进、纯隐藏更改、可见非末行及撤权，无权时清除旧内容。落地前只承诺单批，退回重新查询。

### 6.2 图点下钻（M04/M05 → M06，BG-012）

`POST analysis/statistics/`、`POST analysis/compare/` 在 `chart.points[]` 增 `drilldown?:{support:"observed"|"effective",bucket:{start:Instant,end:Instant,pos:int}}`。父结果的 `input_version:Version` 必填。同列 `pos` 从 0 计，须与父 `columns[].pos`、点的 `pos` 一致。服务端用该列实际时轴生成半开桶。exact 中有已报逻辑键的单点给 `observed`，桶精确到唯一时刻，不纳入相邻非整点记录（可用 `[at,min(at+1微秒,父窗末))`）。无记录的空槽没有入口。range 的最小/最大值给 `effective`，支撑区间为实际压缩桶 `[桶首,min(桶首+实际桶宽,父窗末))`，仅有缺口、无有效样本则省略。已有统计桶、热图保持各自的 `drilldown/observed_drilldown` 与 `local_hour` 语义。无权展示点或取证时省略相应入口。

前端将**原规范父意图、父 `input_version`、该点原 `support,bucket`** 原样 POST `analysis/details/`，日期字符串不经 JS 毫秒精度重写。续批固定原版本与桶。缺描述只显示图和现有已标明的表格支撑。列号越界或桶越父窗 `400/invalid_request`。有效列号只返回该列支撑，前端按实际点击的点提交原列描述，父版过期 `409/input_changed` 回父页重算，用途撤回 403。验收需用同刻不同列、非整点邻近点、跨 DST 的两个瞬时、压缩尾桶及无报槽验证返回明细边界。

### 6.3 Pearson 配对（M05，BG-013）

`POST analysis/statistics/`，`method="pearson"`。父 `P:int|null` 为完整同口径样本数，`pearson:number|null` 为按全体样本和现行门槛计算的系数。新增 `pearson_pairs?:{points:[{at:Instant,x:string,y:string}],shown:int,truncated:boolean}`，x/y 分别取父意图第 0/1 列有效规范十进制值，时刻唯一，按瞬时升序。点和系数在同一父版同一关系上对齐，条件均为 `expected_common AND paired`。两列都已获逐点展示权、P 已知才返回。P=0 时返回空数组、0、false，P 未知或权限不足时省略整个投影。P=29 或零方差时保持父结论，严禁从显示点另算一个系数。

在现行查询/响应预算内优先完整返回，不固定截断在 1,000。若完整结果超出返回预算，返回能容纳的升序前缀，`shown=points.length`，`truncated=(shown<P)`，页面写明“按时刻升序，显示前 shown / P 对”以及全体样本系数。连父摘要也超预算走现行 `400/query_too_large`。已有 `paired_drilldown` 的 `paired` 谓词包括应到未知但双有效的槽，入口标为“配对明细”，不能标为“相关系数完整样本”。比较的 `paired_points` 继续表示比较差值。验收核相同父版、数值顺序、29/30、零方差、未知应到双有效、截断与未截断、无逐点权。未实现时展示真实系数和覆盖结论，散点交互保留 F 域。

### 6.4 获权站址目录发现、精确查询（M02/M06，BG-005/010）

`GET catalog/objects/` 新增可选 `city=<正 int64>` **目录发现分支**，路径与现有发现共用，原无 `city` 分支及行形状不变。此分支可选 `product,scope,at,page_size,cursor`，F 取 `scope=synthetic`。返回 `{results:[{city:int64,product:int64,synthetic:boolean,station_directory:Href}],page:{page_size:int,next_cursor:string|null}}`。每个获权 S 产品和该城市至多一行，按产品 ID 升序续批。服务端按目录权、源/城市归属及对应时刻裁剪，包含有获权站址但无观测或获权查询空目录的城市，避免从 C/T 读数反推站址权。无可披露目录（含未知城市）统一 200 空页。`station_directory` 指向 `GET cities/{city}/stations/?product=…`，含域和已选时间依据；前端跟随链接及其原 `at` 续页。产品或目录撤权后按当前用途拒绝，不暴露其他读数或计数。

`GET observations/current/` 请求和零/一行响应沿 §3。以产品形态、`INDICATORS`、`REPORT_KIND/WINDOWS`、指标对应 `standard_series` 联合判定。D 必须 `daily/local_daily/local_report_date`，非 D 用带时区 `observed_at`，H 仅 `hourly_label_unspecified`，滚动窗仅允许 C/S 的 O3，AQI/等级只取 `HJ633-2012/HJ633-2026/unknown`，浓度只取 `not_applicable`。非法组合 `400/invalid_request`，合法且获权无报 `200 {count:0,results:[],reason:"no_observation_at_time",…}`，争议或质量原因保留其原码。产品/对象无权仍走用途拒绝。验仅 S 权、有目录无观测、空目录、无目录权、无 C/T、D 地方日、错误系列与现有发现行兼容。退回已知获权链接，无法发现时不拼接深链。

### 6.5 CSV 域、版本与旧票（M06/M07，BG-015–018/043）

`POST me/exports/ {kind:"detail"|"aggregate",groups:[规范父意图+input_version:Version],fields?:string[]}`。1–2 组、同域、不可重复，组总预算和响应沿 §3。`fields` 省略或 `[]` 表示全部受控必备列。签票确认前展示实际父组、来源、时窗、时区、`observed` 明细范围及列，`summary` 和非法选列返回 `400/invalid_request`，第三组 `400/query_too_large`。用户选附加列和正式 D 日报附件按 PD-06 独立延期。客户端若丢失来源按 PD-07 回通用申请入口。

新版内部 `format_version="s05.csv.v2"`。UTF-8 CSV 带标题，沿现行列序在 `group` 后插入必备 `data_scope`，两类每行严格 `formal` 或 `synthetic`，取自签发产品/域，和文件名、界面徽标无关。v2 默认明细 24 列、汇总 25 列，重放沿票里冻结的字段/格式/父版本。`export_manifest` 和每个 `csv_<字段>`（含 `csv_data_scope`）必须经当期该类导出及相应保留授权，用户不得通过删列避权。201 仅本人票元数据，200 `state=no_data` 无票。下载 `GET me/exports/{item_id}/download/` 重验同一投影、字节数、行数和 SHA-256，通过才返回 `text/csv; charset=utf-8` 附件。

切换至 v2 后仍保留的 v1 票不迁移字节：原用户有权且格式不匹配时沿现行 `409/method_changed` 安全失效，重新预读并申请。有效格式的过期票 `410/expired`，已清理或非本人 404、权限不符 403、父输入变更 `409/input_changed`，均无附件。验两域×两类的真实字节（中性来源名）、缺字段权、混域、无报、过期/格式失效与输入变更。旧格式 F 文件无法通过 v2 验收，已下载的旧字节不作新格式证明。正式 D 专项另验源地方日、时区、格式及字段权。

### 6.6 质量动作、理由、证据和对象级隐匿（M08/M09，BG-030–033/037/041）

`GET admin/quality-issues/{event_id}/` 沿用详情字段，必给 `event_version:int`（0 至 2⁶³−1）及 `allowed_actions:[{action:string,reason_codes:string[],evidence_required:boolean}]`。只读审阅人、已办结或无动作时返回 `[]`。`evidence_links?:{key?:Href,batch?:Href}` 仅在至少一项获准时出现。两类链接分别指向带 `product`、域和唯一 `city_key`/`station_key` 或 `batch` 的 `GET admin/observation-evidence/`，服务端验证关联和当期证据权限。链接提供阅读入口，写命令所需签名由现有 `evidence.correct_source?/correct_mapping?:string` 单独给出。

| action | reason_codes（单元素数组） | evidence_required | 个体资格（均须可审阅该事件且有 `accounts.adjudicate_quality`） |
|---|---|---|---|
| `claim` | `claim_review` | false | open |
| `hold` | `retain_isolation` | false | open 或自己经办的 in_review |
| `close` | `review_complete` | false | 同 hold，仅关闭事件，争议资格仍按观测规则 |
| `correct_source` | `verified_correction` | true | 未结案、对应源冲突且有核准来源规则、当前候选集和可签的订正凭据 |
| `correct_mapping` | `registered_mapping` | true | 未结案、已登记映射规则及可签的候选，原/目标范围均获 raw 审阅与保留权 |

`POST …/{event_id}/actions/` 严格接收 `{action,reason_code,expected_version:int,request_id:UUID,evidence_ref:string|null}`。非更正显式 `null`，更正提交该动作当前签名，绑定人、事件、动作、版本、候选及期限（现行 1800 秒）。客户端只呈现已返回的动作和理由。后端先检查读/写入口资格（缺失 403），对具备相应入口资格者，可解析的对象请求在版本、证据和理由业务反馈前复核事件可见性，隐藏/不存在同为 `404/not_found`；锁内再次检查对象、用途、版本、状态、经办人、原/目标证据及资格。可见对象上的非法组合 `400/invalid_request`，证据不足或签名失效 `403/mapping_evidence_mismatch`，并发旧状态 `409/precondition_conflict`，同 UUID 换命令 `409/request_conflict`。旧命令的本人只读查证保持兼容。PD-09 条件未达时只保留获准审阅/隔离和已登记 F 演练。验收用有入口但无对象权、只读、异人经办、失权和双人并发场景核响应和审计行数。

BG-037 同步约束 M08 新命令的动作/理由。Job `pause/operator_pause`、`resume/operator_resume`、`resume_source` 或 `resume_product/operator_recovery`，Run `redeliver/operator_recovery`。调度沿用 `create_draft/schedule_create`、`propose/schedule_change`、`activate/schedule_activate`、`discard/schedule_discard`。已落账的旧组合按原信封查证。对象资格仍读原 Job `targets` 和计划状态。

### 6.7 质量裁决失联恢复（M09，BG-029/034）

`POST admin/command-receipts/lookup/ {family:"quality",target:int64,command:<原写信封>}`。只查本人已落账动作，核原目标、请求号、动作、原因、版本及证据指纹。首次响应、幂等重放仍沿 §4 的不同 DTO。查账 200 返回现有 `{action_id:UUID,action:string,state:string,version:int,recorded_at:Instant}` 加必填判别对象 `impact`：

- `{state:"available",affected:Scope[],outcome?:string}` 只列能够将原动作与**当前可披露**组相联系的非空列表。`outcome` 只在能证实且可披露时返回，取 `claim/hold/close/correct_source/local_mapping_fix/mapping_review_required/mapping_conflict`。不承诺这份列表覆盖全部更正对象。
- `{state:"unavailable"}` 表示已核实动作、无法核实可披露影响，省略 `affected` 和 `outcome`。响应未能核实动作则 `404/not_found`。两种响应在界面上分别显示为“动作已确认，影响待核实”和“原命令尚无法核实”。

`Scope` 沿用 `quality_governance.project_scope` 的获权粒度。必填 `product:int64,synthetic:boolean,geography:string,object_code:string,indicator:string,start:Date,end:Date`，日期为源地方日半开跨度。键级在可证明并可披露时再加唯一 `city_id:int64` 或 `site_epoch_id:int64`、`report_kind:string,window_definition:string,standard_series:string,observed_at:Instant|null,query_types:string[]`（日键时间可空）。无法安全表达范围时整项裁剪，禁止用隐藏名称、个数、是否另有范围、前后差集或全局结果泄露它。回执披露本身须通过当期原事件、本人命令和适用签名范围的访问检查；无法披露回执时统一 404，不降级绕过。签名写入时限已过仍可用原签名查已落账动作，不再写入。

验首包丢失、相同 UUID 同一 `action_id`、过期签名、部分披露、完全无法核实、撤权与异人隐藏。仅失效真实相关的查询组，不误删无关组。旧服务只能确认 `action_id`，保留影响待核实。

### 6.8 历史导入失联恢复（M08，BG-033/035/036）

同一只读 POST 增 `family:"history_import"`，`target:UUID` 为原 stage，`command:{profile:"uci501-edges-v1",stage_id:UUID,reason:"historical_verification"}`，三层字段均必填，UUID 用规范字符串。原用户从登录会话取，不接收自报用户。`target` 与原命令 `stage_id` 一致，沿 `StagedInput.owner`、固定 profile 所属产品、`ImportAttempt.actor/stage/reason` 和 `stage` 的一对一约束，唯一匹配原提交。返回前复核当期本人读取资格及历史状态的来源权。原 stage 已消费、过期或文件已清理时，只依据获准保留的账本作证。

200 **仅** `{attempt_id:UUID}`，再用现有 `GET admin/history-imports/{attempt_id}/` 读状态。合法外信封下异人、撤权、错目标、命令不匹配、未受理或无可证保留记录同为 `404/not_found`。错误外信封 `400/invalid_request`，不可用数据库 503。查账不建 stage/attempt、不提交、不投递 Worker、不重传文件。验同人两份暂存、首包丢失、重复查、清理后仍有回执及撤权，以 attempt 和投递次数不增长为据。未实现时已知编号只读跟踪；完全失联保持未核实。最近列表不用作提交身份证明。

## 7. 阶段出口与验证边界

**Phase 02 基线内容已冻结，待 Git 基线固化，可以进入 Phase 03。** 11 项产品选择见正式 `PRODUCT_DECISIONS.md`。八组目标均已确定技术口径，页面可以依现行分支及带标签的 F 样例开展精修。哪些页面切片需提前实装及 Phase 08/09 的承接由整改计划按 BG 跟踪。正式更正、地图、全国趋势、D/H 和来源运行保留各自授权、证据与独立验收门槛。

**PASS（本轮静态）** 两端真实调用与 F 分支、数据类型、路由、handler、模型及既有断言已交叉核对。前端依据为 `air-quality-frontend:src/api/query.ts`、`air-quality-frontend:src/api/analysis.ts`、`air-quality-frontend:src/api/quality.ts`、`air-quality-frontend:src/api/collection.ts`、`air-quality-frontend:src/types/`、`air-quality-frontend:src/mocks/`、`air-quality-frontend:src/analysis/results.ts` 及 `air-quality-frontend:src/collection/useCollectionCommand.ts`。后端依据见 §2–4 各处理器，并核 `air-quality-platform:backend/observations/discovery.py`、`air-quality-platform:backend/observations/models.py`、`air-quality-platform:tests/test_s04_station.py`、`air-quality-platform:tests/test_s05_statistics.py`、`air-quality-platform:tests/test_s05_private.py`、`air-quality-platform:tests/test_s08_receipts_session.py`。

本轮已在现行 OpenAPI 校正导出 1–2 组、收藏排序及返回形状、保存方案方法枚举。研究方法分支、治理/采集等复杂 DTO 的机器化精化由 BG-033/041 跟踪，现行客户端按 §3–4 区分返回类型。

**NOT RUN（目标实装及正式联调）** 上述静态核对不能替代双会话撤权、PG 并发、Worker/Beat、同源宿主、实际 CSV 字节或正式授权源验收。前端阶段验收及本轮运行检查结果由 `air-quality-frontend:docs/BASELINE_REVIEW.md` 维护。
