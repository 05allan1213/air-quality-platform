# Phase 02 API 映射

本文为本项目的 API_MAPPING，沿用仓库既有 `PAGE_API_MAPPING.md` 路径，供 DESIGN、契约及开发指引继续引用。页面需要什么见 [页面需求](PAGE_REQUIREMENTS.md)，输入在 URL 或内存如何传递见 [路由](ROUTES.md)。

## 读表规则

- **当前调用**表示 Vue 调用链可达，启用 `VITE_API_MODE=real` 时使用同源 HTTP。按仓库 OpenAPI 的方法和路径列操作，保留已知勘误。**这些操作均待当前后端核验**。历史快照、隔离 Django 收据、F 与浏览器响应注入的覆盖各有限定，见 [接口契约](API_CONTRACT.md)、[OpenAPI](api/openapi.v1.json)、[样例](API_EXAMPLES.md) 和 [历史证据](api/VALIDATION.md)。
- **F** 使用本地 fixtures 和 Axios adapter，或页面本地派生数据。F 请求中额外的 `scope=synthetic`、场景与 `demo` 字段只说明演练能力。正式 GET 通常省略 scope，M04–06 的 POST 父意图以 `scope=formal` 表达数据域。管理通用读取沿端点获权链接，F 按目录与 adapter 约定带 scope，能力及健康总览请求无业务 scope。
- 以下 Path 均相对 `/api/v1/`，花括号为实际对象标识。请求列列出现有发送参数和触发条件，省略的可选值不会自动取得。完整字段语义和历史证据以操作 ID 查阅分册及快照。页面消费列包含展示、筛选、合法性校验和获权导航所用字段。
- [HTTP 客户端](../src/api/client.ts) 同源 Cookie、JSON 约定、15 秒超时，精确读取超安全整数的数字 ID。保护读和私有内容遵守服务端 `no-store`。写和只读 POST 使用 X-CSRFToken。浏览器仅跟随校验通过的相对路径和同域产品链接，不将原始 cursor 转页码。移除无效或迟到响应。真实会话失效与用途/对象拒绝分开。REST JSON 的 `code`、状态和可用时 `Retry-After` 用于反馈。400/422 复核输入，400 无效游标/409 版本变化按端点重读，429/503 按安全读取或原回执规则处理。
- 页内列表的本地搜索、分组和排序只作用于已取得且可披露的内容。普通列表用 `page.next_cursor`，管理目录用顶层 `next_cursor`，质量队列用 `next_before`，治理证据/窗口使用各自的 `after/run_after`。继续时保留原条件和版本，拒绝重复、失效和越权游标。

## 公共、身份、账号和恢复

PUB-01 和 PUB-02 读取 [本地展示数据与说明](../src/public/sourceCatalog.ts)，无页面业务 API。来源、站址、署名及使用许可的正式依据来自实际数据投影和治理记录。SYS-403/SYS-404 和能力失败视图复用能力读取，无错误页专用接口。

| 当前调用及 operationId | 页面触发、请求 | 消费响应及控制 |
|---|---|---|
| `GET auth/csrf/` · `issueCsrf` | 注册/登录/登出/资料/改密等身份写前，登录成功后核会话，CSRF 拒绝后刷新。无参数。研究和治理 POST 也按封装取 token | `csrf_token`。校验 64 位字母数字形式。身份和私有写不自动重放。研究只读 POST 可在明确 CSRF 拒绝后重试一次 |
| `POST auth/login/` · `loginSession` | AUTH-01 提交 `{username,password}` | 200 `username`，再核 CSRF、能力、资料。凭据失败/频控。网络/5xx 先核会话是否已建立 |
| `POST auth/register/` · `registerAccount` | AUTH-02 提交 `{username,password}` | 201 `role=USER` 后转独立登录。正式快照报通用 400，F 可有 `field=username`。不确定提交引导尝试登录 |
| `POST auth/logout/` · `logoutSession` | 共享账号面板确认退出，无正文 | 204，清理账号和各私有工作区，按路由回退 |
| `POST auth/password/` · `changePassword` | 设置面板 `{old_password,new_password}`，再次输入新密码仅作本地校验 | 204，清理并提示重登。字段、CSRF、频控或结果不确定有可读反馈 |
| `GET me/capabilities/` · `readCapabilities` | 无参数。登录、路由守卫、状态页恢复、账号可用权限、管理聚焦/可见时复核 | `workspaces,actions`。未登录/无对应能力/读取失败各有落点。对象继续由业务 API 裁决 |
| `GET me/` · `readProfile` | 无参数，登录确认、账号入口和资料面板、M07 概览、受保护页会话复核 | `username,first_name,last_name`，身份变化清会话缓存。部分页面复核只用 username |
| `PATCH me/` · `editProfile` | 保存变化的姓名，`{first_name,last_name}` 两字段、每项最多 150 字符、可空 | 200 同资料字段。更新同会话资料。校验失败和提交不确定分别提示 |

F 身份 adapter 以及游客合成工作区是本地状态。核验登录 CSRF 轮换、正式注册错误、浏览器 Cookie、能力裁剪、登出及改密清除、仅管理账号回跳。 [身份客户端](../src/api/auth.ts) · [F 身份](../src/mocks/auth.ts) · [账户分册](api/ACCOUNT.md) · [B04、B17](BASELINE_REVIEW.md#冲突差异与待确认项)

## M01–M03 与 M06 观测

| 当前调用及 operationId | 页面触发、当前请求 | 真正消费的响应和边界 |
|---|---|---|
| `GET overview/national/` · `readNational` | M01 进页/重读。真实 `home=1,page_size=100,map_limit=100`。F 另带 `product=1,scope=synthetic,standard_series=HJ633-2012`。F 的页面筛选、排序、指标、榜单切换和页码不发真实请求 | 真实显示 `synthetic,state,snapshot_at,results[].{city,name,links,series}` 中 AQI `standard_series,value,quality` 及 `page.next_cursor` 存在提示。校验 `selection.home,summary,ranking,map,page`。可选组缺省的兼容性待核。F 验快照身份和时间，补本地地图、KPI、趋势、浓度/IAQI、缺报名称和首污构成。正式页面尚未消费服务汇总、榜单或获权地图，也无游标操作 |
| `GET catalog/objects/` · `discoverObjects` | M02 缺 C/T 链接时发现。M06 建 C/S/H/D 候选。M04/M05 补对象名。M07 正式首选产品候选。通常 `page_size=100,cursor`。M06 根据种类/获权链接筛选，按目录游标读取 | `results[].{product,shape,source,synthetic,city,site_epoch,name,link,history_link}` 与 `page.next_cursor`。D 候选需获权精确读，C/T 用相应产品链接，S/H 按站址期。纯 M02 深链的 S 目录仍依赖可披露同城链接 |
| `GET cities/{city}/` · `readCity` | M02 读 C 首载/应用/重试，`product,at` 或 `as_of`，可选 `standard_series`。最近时可省略对应锚点。页面 `window` 本地过滤槽 | `city,city_name,product,source,synthetic,state,snapshot_at,standard_series,slots[].{indicator,state,results},links`。读数用数值、单位、AQI 等级、时间/时区、质量和来源。校验七槽。F 的天际线选择/城市原码和示例等级使用演示值 |
| `GET cities/{city}/trend/` · `readCityTrend` | M02 读 T。`product,anchor?,standard_series?`，最近不传显式 anchor | `state,city,product,source,synthetic,standard_series,anchor,anchor_basis,start,end,points[].{observed_at,state,results}`，有锚点要求 24 槽，取 AQI、单位、质量、点时区和发布依据。图和报表用同一响应，缺槽断线 |
| `GET cities/{city}/stations/` · `readStationDirectory` | M02 读 S 时用 `product,at?,page_size=3,cursor?`，F 加 `q,station_type`。当前会将首批响应的 `at` 带入下一批。若首批省略 `at`，这会改变游标绑定条件。协议应维持原请求 `at` 的值或缺省，响应用于展示锚点（BG-006）。M04 正式站点对比补同城关系时取 `product,at=起点,page_size=100,cursor?` | `product,city,synthetic,at,state,results[].{station,site_epoch,city,station_name,site_name,city_name,codes,period_start,period_end,status,link},page.next_cursor` 等获权身份字段。正式名称等可裁剪，F `demo.total,matched,summaries` 附类型/AQI/首污/最近时刻。宣称全量正式搜索、总数或摘要需后端证据 |
| `GET stations/{station}/` · `readStation` | M03 首载/切期。详情 `product,site_epoch?,as_of?`。获权站址期目录分支 `product,as_of?,page_size=50,cursor?`，有显式站址期时目录省略 `as_of` | `state=select_epoch` 的 `results,page`，或 `product,source,station,site_epoch,as_of,identity,results,history,source_status,synthetic,state`。身份许可字段、坐标与 CRS 按披露。六项最近读数的 `observed_at,value,unit,quality,freshness`，数字 `age_seconds` 当前未直接显示。合法链接决定趋势/历史入口 |
| `GET observations/query/` · `queryObservations` | M06 选窗口/最近有效后应用。 `product,mode,cities 或 site_epochs,indicators,report_kind,window_definition,standard_series?,unit?,source?,quality,start/end 或 as_of,page_size=50,cursor?`。GET 最近有效固定 valid，窗口 ≤720h、≤3,600 候选格，最多 5 对象/7 指标。M02 小时历史和 M06 观测历史沿获权对象/窗口条件，单批 25。站址多指标合并每批 100，UTC+8 日期/小时在本地应用。M03 趋势申请同一获权产品/站址期和单指标半开窗，按 100 合并续页 | `selection.product,synthetic,state,results,page.next_cursor`。读数 `key,revision,revision_no,qualification_version`、产品来源、城市/站址期、指标、值/单位、`quality,censor_limit,censor_direction,standard_series,observed_at,source_timezone,fetched_at` 及依据、`report_kind,window_definition,attribution,rights_attributions` 依视图及披露。普通工作台含证据区。窄屏焦点可恢复。越限缩小窗，收到游标失效后按原条件读首批。普通读没有研究 `input_version`，当前游标不足以证明跨批为同一可见结果版本（BG-009）。自由 GET 不承担 D 日报 |
| `GET observations/current/` · `readExactObservation` | M06 精确读应用。必需 `product,city 或 site_epoch,indicator,report_kind,window_definition,standard_series`，二选一 `observed_at/local_report_date`，无单位/质量/来源筛选和游标 | `synthetic,count,reason,results,attribution,rights_attributions`，0/1 行。`revision_id` 标识当前修订，前端临时复用为当前 UI 行选择键并保留来源署名。逻辑观测 `key` 未披露，正式证据面板不给它填修订值。精确结果的 `input_version` 属该精确选择，不可用于研究父组（BG-011）。M02 D 卡仍未接此调用。获权 D 的地方日、回溯版本与许可待核 |

正式 L 候选需要同一服务端快照的 `summary.groups.{ranked_city_count,distribution}`、`covered_city_count,expected,summary_eligible_city_count` 和获权 `ranking/focused/map`。契约支持的 `at/as_of,product,standard_series,window_definition,order,q,geography,grade,cursor` 等范围需按实际快照核验，当前真实分支只发送上表参数。F 数据使用 `national`、`city`、`query` adapters，按模式模拟缺值、移址、空、拒绝和游标冲突。正式模式未证实存在 F 隐含地名、摘要和盘点范围。 [观测分册](api/OBSERVATION.md) · [全国客户端](../src/api/national.ts) · [城市](../src/api/city.ts) · [站点](../src/api/station.ts) · [查询](../src/api/query.ts) · [数据类型](../src/types/city.ts)

## M04/M05 分析与 M06 研究支撑

下表的父意图是 `{method,method_version,grain,selection}`。`selection` 为 `{scope,product,source,cities 或 site_epochs,indicators,units,standard_series,start,end,quality,report_kind,window_definition}`。常用 `method_version=s05.v1`，粒度 hour/day/month/season，各方法限制依服务校验。图、支撑、导出绑定研究结果的 `input_version`，不借精确读修订或上一查询版本。

| 当前调用及 operationId | 页面触发、请求 | 消费响应及控制 |
|---|---|---|
| `GET catalog/series/` · `discoverResearchSeries` | 打开分析、变更日期/粒度、重跑前用 `purpose=analysis,method,start,end,page_size=50,cursor?`。长窗按 720h 切片取许可并按全窗合并 | `results[].{product,source,shape,city,site_epoch,indicator,unit,report_kind,window_definition,standard_series,source_timezone,permitted_from,permitted_until},page.next_cursor`。站点关系用获权 S 目录，F 用本地站点。产品窗、时区及可比较性约束重新应用 |
| `POST analysis/statistics/` · `readStatistics` | M05 点击应用。父意图 `method=statistics 或 pearson,selection.quality=reliable`。统计一对象一指标，Pearson 一对象两种不同浓度 | `selection,method,method_version,grain,input_version,state,source_timezone,requested_window,attribution,columns,buckets,chart,distribution,heatmap,pearson,paired_drilldown,conclusion` 按方法及权利消费。列 E/O/V 和有效覆盖、桶的时间/值/极值/四分位。前端优先用获权图点支撑，现可尝试匹配统计桶，正式 exact/range 点未承诺带支撑。F 可有 Pearson `paired_points`，正式版需按实际配对投影核验，不能借 compare 的差值点（BG-012/013） |
| `POST analysis/compare/` · `compareObservations` | M04 点击应用。父意图 `method=compare,selection.quality=reliable`，兼容的 2–5 对象与单指标 | 共同使用父输入/版本、`columns,chart,buckets,conclusion`，加 `E_common,P,paired_ratio,mean_difference,paired_points,paired_drilldown`。按披露呈现差值、共同样本及限制，基数 0 不显示变化率。类型声明的排名和局部结论子窗未有专用展示 |
| `POST analysis/details/` · `readResearchDetails` | M06 研究支撑由图、表、配对入口打开。父意图带 `input_version`，`support=observed 或 effective 或 paired,bucket?={start,end,pos?,local_hour?},page_size=20,cursor?`。普通 M06 申请整组时用一次 `method=detail,grain=hour,support=observed,page_size=1` 和同源父选择取得当前组版本 | 校验 `selection,method,input_version`。支撑用 `source_timezone,results[].{key,pos,observed_at,indicator,value,unit,quality,attribution},page.next_cursor`。输入或方法变化要求跑新父组。普通明细版本预读只取规范父意图和版本，确认窗当前展示口径差异见 B11 |

常规分析输入上限 720h/3,600 候选格，月季统计可用 8,784h/候选格。服务预算、规模限制、实际 H 样本，以及按合规样本的 `O/E,V/E,P/E共` 和 75%/P≥30 等由真实数据验证。F `analysis` adapter 能演练热图、配对、空列、分母低/未知及版本变化。真实投影缺箱线/异常候选、月季分段、排名或部分覆盖子窗的支持边界待核。 [客户端](../src/api/analysis.ts) · [条件](../src/analysis/conditions.ts) · [结果](../src/analysis/AnalysisResults.vue) · [F 分析](../src/mocks/analysis.ts) · [观测分册](api/OBSERVATION.md)

## M07 本人资源及 M02–M06 复用

除身份读取外，以下本人操作以当前用户和用途授权。F 私有资源通过 adapter 和当前 SPA 内存模拟。列表沿服务端游标。已取得的单批可本地过滤和重排。

| 当前调用及 operationId | 页面触发、请求 | 消费响应及控制 |
|---|---|---|
| `GET me/favorites/` · `listFavorites` | M07 概览 4、关注 Tab 6，`page_size,cursor?`。M02/M03 核按钮状态最多 3 批、每批 100，以 kind 和稳定对象对照 | `results,page.next_cursor`。收藏 `id,kind,state`，可选 `target_id,product,name,site_epoch,link,epochs,results`。读数含指标、值、单位、等级/状态、时间时区及首污。仅获完整首批认总数。受限可只提供前三项 |
| `POST me/favorites/` · `createFavorite` | M02/M03 可关注对象，`{kind,target_id,product}` | 200/201 本人条目，重复意图、409 偏好冲突分开 |
| `GET me/favorites/{item_id}/` · `readFavorite` | M07 复访、首选产品前核当前条目。M02/M03 核按钮使用本人列表分页 | 记录不存在、跨人或跨域时按不可发现的 404 处理。本人同域记录的目标撤权可返回 200 `state=unavailable`，仅保留 `id,kind,state` 并允许移除。获权 `link,epochs[].{site_epoch,site_name,city_name,period_start,period_end,link}` 决定站址选择（BG-020） |
| `PATCH me/favorites/{item_id}/` · `patchFavorite` | M07 切换首选产品，`{product}`。候选从获权对象目录/站点链接取得 | 更新投影或冲突，撤权后清旧摘要 |
| `DELETE me/favorites/{item_id}/` · `deleteFavorite` | M02/M03/M07 取消，空正文 | 204 后会话与列表核对 |
| `GET me/saved-queries/` · `listSavedQueries` | M07 概览 4、分析方案 Tab 4/8/12，`page_size,cursor?` | `results,page.next_cursor` 和方案 `id,title,created_at,updated_at,intent_revision,selection,method,method_version,grain,state?`。概览沿服务顺序，Tab 可本地按更新时间/创建时间/名称，F 可有对象名 |
| `POST me/saved-queries/` · `createSavedQuery` | M04/M05 已应用结果保存，`{title,selection,method,method_version,grain}` | 201 新方案及 `intent_revision` |
| `GET me/saved-queries/{item_id}/` · `readSavedQuery` | M07 编辑复跑后核修订，M07/分析的冲突恢复 | 单方案及当前获权父意图、版本，404 隐藏私有存在性 |
| `PATCH me/saved-queries/{item_id}/` · `patchSavedQuery` | M07 改名 `{title,intent_revision}`。分析编辑 `{selection,method,method_version,grain,intent_revision}` | 200 新修订，冲突重读并让用户决定 |
| `DELETE me/saved-queries/{item_id}/` · `deleteSavedQuery` | M07 删除，空正文 | 204 后重读当前分类 |
| `POST me/saved-queries/{item_id}/run/` · `runSavedQuery` | M07 复跑或开始编辑，`{}` | `state,intent_revision,result`，仅 current、data_changed、baseline_unavailable 且有效 result 可进入当前分析。target_unavailable/method_changed 单独反馈 |
| `GET me/exports/` · `listExportTickets` | M07 概览 3、近期导出 10/20/50，`page_size,cursor?` | `results,page.next_cursor`。正式票据只消费 `id,kind,created_at,expires_at`。最近来源视图使用 F 投影或当前会话签发记录 |
| `POST me/exports/` · `createExportTicket` | M04/M05 或合规的 M06 整组预览确认，`{kind:detail 或 aggregate,groups:[父意图+input_version],fields:[]}`（BG-015）。当前用服务默认列。M06 的普通查询限可解析单源、时间窗、全部质量筛选且无 as_of，签票预读发整组 reliable 父意图并使用 observed 支撑。observed 明细可能含获准的非 valid 行。当前确认窗沿用查询摘要的偏差见 B11/BG-016 | 201 票据、200 no_data 无票。版本/用途/预算有分支。M06 成功引导到 M07，分析可在本页下载或到 M07。任意桶/配对子集、最近有效与精确读和整组存在语义差异 |
| `GET me/exports/{item_id}/` · `readExportTicket` | M07 查看及下载前重读 | 当前元数据 `id,kind,created_at,expires_at`，不裁决下载资格。客户端结合期限及下载错误显示状态，过期回相应申请页。标题、来源视图和输入版本不属于现行票据投影（BG-018） |
| `GET me/exports/{item_id}/download/` · `downloadExport` | 确认有效且当前还获权的票据。请求 Blob，Accept */* | 200 `text/csv` 与 `Content-Disposition: attachment; filename="air-quality.csv"`，现客户端接收 ≤4 MiB，释放临时 URL。403/404/409/410、503/429 只反馈，不能产生附件或拼页 |
| `DELETE me/exports/{item_id}/` · `deleteExportTicket` | M07 移除，空正文 | 204 后重读。重申请另经整组确认 |

核验两个用户和数据域之间的移除、过期/撤权、分页顺序、跨窗口结果、正文媒体和 CSV 实际字节。历史收藏文档与实际顺序有勘误，前端沿游标，未知计数和目标核对保留。 [收藏客户端](../src/api/favorites.ts) · [方案和票据客户端](../src/api/analysis.ts) · [账户分册](api/ACCOUNT.md) · [G10、G06、M07-G16](INTEGRATION_GAPS.md)

## M08 采集任务

管理目录按能力缩小候选。账本的日期条件是有界半开源地方日窗，次页保留同一产品与范围。窗口、各次运行和批次的结果分别读。计划与人工动作先明确作用范围、版本和原因，命令不确定时保留原信封。有审阅权才查询 Run/Window 账本与深链。

| 当前调用及 operationId | 触发、请求 | 消费响应及控制 |
|---|---|---|
| `GET admin/catalog/products/` · `listManagementProducts` | `purpose,page_size=25,cursor?`。M08 ledger 用 `start,end`。configure 可按 `product` 找模板。execute 用 `target`（UTC 整点）、`reason=manual/review/backfill,product?`。M09 quality、M10 operations 和 ledger 均按诊断日期窗 `start,end` | 顶层 `results,next_cursor`。ledger/quality/operations 取 `product,source,shape,links.{windows,runs,quality_issues,trends}` 中适用项。configure 消费 `template,template_version,product,source,contract_version,scope_label,allowed_cadences,delivery_minute,plan_state,job`。execute 消费可用 `product,job,reason,target`。目录许可是候选，动作继续核权 |
| `GET admin/collection-jobs/` · `listCollectionJobs` | M08 Job 账本，M10 获准 Job 选项。 `product?,scope?,page_size=8,cursor?` | `results,page.next_cursor`，行 `id,product,synthetic,profile,state,links`。状态与名称文本筛在当前批次。正式未稳定承诺最新运行和周期摘要 |
| `GET admin/collection-jobs/{pk}/` · `readCollectionJob` | 打开 Job 或控制后重读，无 query | `job,targets.{job,source,product}` 下 `id,control_version,state,reason,resume_after`，按可见对象操作 |
| `POST admin/collection-jobs/{pk}/` · `changeCollectionJob` | 按当前能力暂停/恢复。 `{action:pause/resume/resume_product/resume_source,reason_code:operator_pause/operator_resume/operator_recovery,request_id,expected_version}` | 控制回执及目标新状态/版本。409 要重新读及确认。未知查原请求 |
| `POST admin/collection-jobs/` · `createCollectionScheduleDraft` | 按登记模板提交停用草案。 `{action:create_draft,reason_code:schedule_create,template,cadence_seconds,first_due_at,request_id,expected_version}` | 201，Job/草案、版本及回执定位。模板时间与分钟受限 |
| `GET admin/collection-jobs/{pk}/schedule/` · `readCollectionSchedule` | 打开详情/配置和版本重读，无 query | `job,state,version,template,template_version,current_template_version,cadence_seconds,first_due_at,predecessor,predecessor_version,pending_successor,effective_from,effective_until,action_id,preview`。预览带分界、旧末次到期、新首期、间隔及冲突 |
| `POST admin/collection-jobs/{pk}/schedule/` · `changeCollectionSchedule` | `{action,reason_code,request_id,expected_version,template_version}`。propose 加 `cadence_seconds,first_due_at`，activate/discard 用 `expected_predecessor_version` | 停用草案、提议、预览、激活或放弃按回执/当前版本前进。409、权限、版本或待核单列 |
| `GET admin/collection-windows/` · `listCollectionWindows` | M08 Window 账本、M10 关联窗口。沿服务链接保留获权 `product/scope/start/end` 等查询，`page_size=8,cursor`。M10 可附 `job,object_code,indicator` | `results,page.next_cursor`。行 `id/window_id,product,synthetic,target_start,target_end,full_scope,state,result,links`，当前批文本筛选 |
| `GET admin/collection-windows/{pk}/` · `readCollectionWindow` | M08 打开/续页用 `after,run_after`。M09 展开关联无参。M10 用可选 `object_code,indicator` | 窗口投影 `window_id,state,result,product,synthetic,target_start/end,closed_at,profile,roster_basis,coverage,cells/items/receipts,runs,quality_issues,next_after,next_run_after` 等。覆盖 O/E/V、缺项、最近有效和可见截断依投影。后端省略关联与分母时不推导 |
| `GET admin/collection-runs/` · `listCollectionRuns` | M08 Run 账本，沿服务链接保留获权范围，`page_size=8,cursor` | `results,page.next_cursor`。`id,window,product,synthetic,created_at,status,dispatch,result,links`，运行状态与投递及结果分别表示 |
| `GET admin/collection-runs/{run_id}/` · `readCollectionRun` | M08 详情、追踪/续页用可选 `after`，M09 和 M10 证据或关联详情按权读。当前 Run 请求无对象码/指标参数 | 使用窗口投影及 `runs` 的 `id,reason,status,result,dispatch,error_code,request_state,created_at/started_at/reserved_at/finished_at/fetched_at`、重试时点、`batch_id`，可见 `target.control_version,dispatch,owner_present` 决定能否重投 |
| `POST admin/collection-runs/` · `submitCollectionRun` | M08 确认获准 Job/窗口。 `{job,request_id,reason,target}` | 202 `run_id,window_id,dispatch`，按授权进入账本。503 可附 unconfirmed 的原 Run/Window，明确编号则追踪。未确认用原命令查回执。有限自动追踪，终态停止 |
| `POST admin/collection-runs/{run_id}/` · `redeliverCollectionRun` | 获权、版本在手、待确认又无执行者，`{action:redeliver,reason_code:operator_recovery,request_id,expected_version}` | 202 原 Run/Window 投递状态，保留原业务窗，避免新任务替代旧回执 |
| `POST admin/command-receipts/lookup/` · `lookupSelfCommandReceipt` | M08/M09 对未知命令核验本人原回执，`{family:manual_run/job_control/redeliver/schedule/quality,target,command}` | 回执的 `state,action,recorded_at` 和相应 `job/action_id/version`、`run_id/window_id/dispatch/status` 或质量结果。404 表示未核实，留原信封 |
| `GET admin/history-imports/` · `listHistoryImports` | M08 历史导入账本，`page_size=8,cursor?` | `results,page.next_cursor`，尝试 ID、状态和受理时刻，只显示本人/获权审阅 |
| `POST admin/history-imports/` · `submitHistoryImport` | 已登记暂存标识提交 `{stage_id,profile:uci501-edges-v1,reason:historical_verification}` | 202 `attempt_id` 后读详情。503 dispatch_unconfirmed 带原有效 ID 时直接追踪，没有 ID 时刷新账本核对。浏览器无文件上传 |
| `GET admin/history-imports/{attempt_id}/` · `readHistoryImport` | 打开/刷新尝试详情，无 query | `id,state,dispatch,created_at,started_at,finished_at,links`，时间线显示实际阶段。空、部分或拒绝仍按许可 |
| `GET admin/object-audit/` · `readObjectAudit` | M08 Job/Run 抽屉选择有界日期后读，`kind,id,start,end,cursor?`，未显式传 page_size | `results,page`，行 `at,action,outcome,actor_relation,before_version,after_version`，限单个获权对象，不做按人全局审计 |

**F 专用调用**　`POST admin/f-ui/jobs/` 专用于独立创建 Job，正文 `{product,name,description}`，读取创建后的 Job。`POST admin/f-ui/jobs/{id}/schedule/` 专用于给独立 Job 挂首个计划，发送创建草案同型正文，读草案及版本。两者来自本地 adapter，在真实分支不能调用，不能作为需向后端照搬的合同。F 的创建人、所属 Job、名称、批次及各类状态只在其投影范围有效。 [客户端](../src/api/collection.ts) · [类型](../src/types/collection.ts) · [F 采集](../src/mocks/collection.ts) · [治理分册](api/GOVERNANCE.md) · [B05、B13](BASELINE_REVIEW.md#冲突差异与待确认项)

## M09 数据质量

| 当前调用及 operationId | 触发、请求 | 消费响应及控制 |
|---|---|---|
| `GET admin/quality-issues/` · `listQualityIssues` | M09 产品目录允许后读队列，`product,run?,window?,status?,before?`。日期只约束目录。无直接事件日期范围参数。M10 关联证据可沿获权窗口/Run 查询首批并按对象/指标裁剪 | `results,next_before`，事件 ID、状态、类型、时间和可见对象/窗口/运行。before 递减，追加去重，无总数承诺 |
| `GET admin/quality-issues/{event_id}/` · `readQualityIssue` | 打开详情、刷新或裁决后读，无 query | `id,status,event_version,code,created_at,resolved_at,disputed,affected,run,window,batch,receipt,current_revision,candidate_revisions,city_key,station_key,evidence,links`。影响范围的产品、对象、指标和起止时刻，修订版本与字段对照按披露。403/404 收回失权内容 |
| `GET admin/observation-evidence/` · `readObservationEvidence` | 按需展开批次/收据/观测键。 `product` 加 `city_key/station_key/batch` 三选一，`after?`。F scope 适用 | `synthetic,attribution,next_after`。按键的 `revisions,receipts,issues,disputed` 或按批次的 `receipts,fetched_at,profile`，回执 ID 匹配定位/修订，after 递增 |
| `POST admin/quality-issues/{event_id}/actions/` · `adjudicateQualityIssue` | M09 明确确认。 `{action,reason_code,request_id,expected_version,evidence_ref}`。认领 `claim/claim_review` 且证据 null。更正 `correct_source/verified_correction` 或 `correct_mapping/registered_mapping` 配服务签发证据 | `action_id,status,event_version,outcome,affected`。版本冲突清草稿重预览，失权收回操作，结果不明查原质量信封。当前仅 open 且版本有效时按动作能力和证据提供。hold/close 仍缺当前处理人及可用动作投影 |

复用上表的管理产品目录、Window/Run 读取和本人回执。F 拟合带证据的修订和状态。历史记录的更正证据适配有限，历史快照不能证明正式源已能签发有效证据。审阅、事务、资格、证据过期及更正后版本/票据影响待核。 [质量客户端](../src/api/quality.ts) · [裁决模型](../src/quality/qualityModel.ts) · [F 质量](../src/mocks/quality.ts) · [治理分册](api/GOVERNANCE.md) · [B14](BASELINE_REVIEW.md#冲突差异与待确认项)

## M10 系统运行

| 当前调用及 operationId | 触发、请求 | 消费响应及控制 |
|---|---|---|
| `GET admin/operations/health/` · `readOperationsHealth` | 进入/刷新无参的基础设施探测。选窗口后 `window`（正 int64 且 ≤18 位）、`object_code?,indicator?`。证据展开才传 `include=first_valid_write` | `components.{web,pg,redis,worker,beat}.{state,observed_at,evidence_kind,valid_until,reason_code},worker.queue_readiness`。窗口 `window_id,state,result,truncated,visible_retry,coverage.{latest_valid_at,valid_ratio,E,V,not_received,not_valid}`。首写 `first_valid_write_state,latest_first_valid_write_at,first_valid_write_page_truncated`。来源提示还用获权 Window/Run 的错误和覆盖，不能当来源整体探针 |
| `GET admin/operations/trends/` · `readOperationsTrend` | M10 应用条件。沿获权目录趋势链接的 `product/scope/start/end`，加可选 `job,object_code,indicator`。源地方日半开窗 ≤30 日，预算过大缩小范围 | 核返回产品/域、日期、`window_anchor=target_end_exclusive`、唯一日桶。图与表消费 `bucket_timezone,evaluated_at,buckets[].{date,window_count,partially_authorized_windows}`、桶内 `coverage.unknown_E_windows,coverage.valid.{numerator,denominator,ratio}` 和 `execution.{mean_seconds,known_count,sample_count}` |

诊断能力单独允许组件/队列探测。窗口、趋势及关联还需审阅，Job 选项需 manage_collection。复用管理目录、Window 列表/详情，适用且有运行上下文时读 Run，质量事件按获权链接或详单的 `quality_issues` 读取。写入、Run、事件时间线使用实际事件时标。抓取时刻、可见等待分档、结果等部分字段有历史类型/契约但未消费。队列全局深度/速率、逐小时趋势、写入关联观测时刻和证明期限等需核正式投影。F 健康/诊断有独立场景。 [诊断客户端](../src/api/operations.ts) · [诊断流程](../src/operations/useOperations.ts) · [F 运行](../src/mocks/operations.ts) · [B15](BASELINE_REVIEW.md#冲突差异与待确认项)

## 契约收录、SPA 尚未调用

以下 14 个显式操作保留原有合同与历史证据。此表按已存在的 OpenAPI 路径列出，动作可用范围、当前资格和对接方式待后端核验。无实际 Vue 消费字段。完整请求从分册和冻结快照取证。

| 功能边界 | 已收录操作 | 尚待的前端或外部协作 |
|---|---|---|
| 夹具运维 | `POST admin/fixture-runs/` · `submitFixtureRun`。`GET fixtures/observations/` · `readLegacyFixture` | 后端夹具流程，当前 F 使用本地 adapter。与页面的两条 F-only 操作各自独立 |
| M09 站址变更 | `GET admin/stations/{station_id}/changes/` · `prepareStationChange`。`POST admin/stations/{station_id}/changes/` · `applyStationChange` | 重读身份/目标版本、用有据提案和原因提交。仅当前登记源产品可用，仍需确认正式工具入口 |
| M09 许可撤回及处置 | `GET admin/disposition-products/{product_id}/` · `previewProductDisposition`。`POST admin/disposition-products/{product_id}/` · `prepareProductDisposition`。`GET admin/grants/{grant_id}/actions/` · `readGrantControl`。`POST admin/grants/{grant_id}/actions/` · `withdrawGrant` | 预览/写入分开，获权范围和版本核对，后续持久化处置及保管角色另行交接 |
| M10 受控账号操作 | `GET admin/accounts/{pk}/actions/` · `readManagedAccount`。`POST admin/accounts/{pk}/actions/` · `changeManagedAccount` | 按版本和许可做现有动作。账号目录和低风险城市检索别名由原生 Django Admin HTML 管理，目前没有 SPA 链接。没有通用账号搜索 REST |
| M10 受控备份和恢复意图 | `POST admin/backup-products/{product_id}/segments/` · `prepareBackupSegment`。`POST admin/backup-segments/{segment_id}/publish/` · `publishBackupSegment`。`POST admin/backup-segments/{segment_id}/restore-intents/` · `createRestoreIntent`。`GET admin/backup-authority/{action}/{operation_id}/` · `readAuthorityMaterial` | 网页只可能跟随获准回执和意图。主机保管签认、清理、实际恢复另设授权。就绪/存活探针和原生管理入口由宿主提供，SPA 不直接轮询 |

[治理分册](api/GOVERNANCE.md) · [接口快照](api/openapi.v1.json) · [B14–B16](BASELINE_REVIEW.md#冲突差异与待确认项)

## 待后端核验或界定的能力

| 页面 | 需要确认的数据/行为 | 接口合作边界 |
|---|---|---|
| 全站身份与授权 | 当前版本真实 Cookie、CSRF 轮换与读写、频控、字段错误、具名能力、用途/对象拒绝、撤权与竞态、Django 深链回退 | 用隔离用户实测现有操作，保护数据不持久缓存。管理专属登录落点先确定产品行为 |
| M01 | 同一授权快照的分组、分母、排序/并列、名录、焦点、翻页及兼容裁剪。正式地图须具备位置和 CRS 以及几何和分发准入。逐小时全国优良占比、正式浓度视图、缺报名录与首污构成缺现成同口径投影 | 先核 `readNational` 支持范围和 `*Level` 语义。新增投影的范围、来源、时区和许可待确认。C 单城/T 单城不能填全国汇总 |
| M02/M03/M06 | D 来源、正式报告日及样本，C/T/S 同刻/不同发布依据，只有 S 权限的发现路径，站址期/源状态/抓取和时效裁剪，站点目录全量名称搜索、类型/最近摘要和可披露总数 | C/T/S/H/D 按来源验证，现有目录/API 先核与 F 的差异。空间差异、完整覆盖日历若推进需明确产品方案和契约 |
| M04/M05/M06 | 当期获权 H 同集双站，覆盖、配对、极值、中位、箱线/四分位、月季分段、异常候选、对比排行和部分窗，图点下钻版本、游标和裁剪 | 逐响应确认可用投影和方法。定义未有的分析输出前先核产品授权和可比性 |
| M04–M07 | CSV 规范父意图、许可、列登记、范围、大小、版本、申请/下载两次校验和字节。收藏按目标反查、总数、票据来源、标题及复跑时的现状与版本 | 先校现有路径、票据媒体类型、CSV 行/列、撤权和过期。导出列选择、目标反查及票据签发来源是否开放待确认 |
| M08 | 登记模板和历史暂存、跨分界、原回执、独立 Job 与首挂计划、Job/Run/Window 直接关联、字段/裁剪、Worker/Beat/PG 同窗唯一 | 先验证现行模板/账本。独建/首挂无正式 DTO。卡在目标关联时依获准响应，不能用 F 字段 |
| M09 | 认领及逐事件 hold/close 资格、证据签发及正式修订规则、站址/许可处置、可披露判例、受影响组输入版本和票据 | 复验当前更正后果。自由说明、独立候选修订字段、事件总数或批量动作需产品及权限明确定义 |
| M10 | 组件状态时效、最近抓取/源时标、窗口部分可见和首写、获权趋势、队列分档与全量深度/速率、来源整体状态与小时粒度、行政和保管交接 | 有界读取沿现有接口，核已存在但尚未消费的投影。新增诊断能力或账户与备份入口待确认作用范围 |

操作覆盖合计 60 个契约内的当前调用和 14 个无 SPA 调用的显式操作，另有 2 个 F-only 路径。映射供后续逐项验收，历史后端证据的版本和范围见 [证据索引](api/VALIDATION.md)，冻结与联调阻塞见 [审计报告](BASELINE_REVIEW.md)。
