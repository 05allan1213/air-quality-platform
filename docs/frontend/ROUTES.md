# Phase 02 路由基线

路由名、路径和守卫取自 [src/router.ts](../src/router.ts)。下表按 URL 层级阅读。实际实现为平级路由记录，由 App 的同一 RouterView 挂载。页面职责见 [页面需求](PAGE_REQUIREMENTS.md)，权限动作与数据依赖见 [API 映射](PAGE_API_MAPPING.md)。

## 路由树

| 路径 | 路由名 | 页面编号与视图 | 入口权限 |
|---|---|---|---|
| `/`，别名 `/app`（亦接受 `/app/`） | national | M01 监测总览 | 用户区 |
| `/app/about` | about | PUB-01 平台说明 | 公开 |
| `/app/sources` | sources | PUB-02 数据来源与使用边界 | 公开 |
| `/app/login` | login | AUTH-01 登录 | 公开 |
| `/app/register` | register | AUTH-02 注册 | 公开 |
| `/app/cities/:city` | city | M02 城市详情 | 用户区＋对象投影 |
| `/app/stations/:station` | station | M03 站点详情 | 用户区＋对象投影 |
| `/app/analysis` | analysis | M04/M05 分析工作台，三个模式 | 用户区＋研究授权 |
| `/app/query` | query | M06 数据查询，可切换研究明细 | 用户区＋查询授权 |
| `/app/query/history` | history | M06 观测历史，独立路由和页面 | 用户区＋单对象授权 |
| `/app/me` | personal | M07 我的空间 | 用户区＋本人资源 |
| `/app/manage/collection` | collection | M08 采集任务，四个账本 | 管理区＋任一采集能力 |
| ↳ `/app/manage/collection/windows/:windowId` | collection-window | M08 业务窗口详情及底层窗口账本 | 另需审阅 |
| ↳ `/app/manage/collection/runs/:runId` | collection-run | M08 运行详情及底层运行账本 | 另需审阅 |
| `/app/manage/quality` | quality | M09 数据质量队列及桌面预览 | 管理区＋审阅 |
| ↳ `/app/manage/quality/issues/:eventId` | quality-issue | M09 质量详情，窄屏独立显示 | 管理区＋审阅＋对象投影 |
| `/app/manage/operations` | operations | M10 系统运行 | 管理区＋诊断 |
| `/app/forbidden` | forbidden | SYS-403 暂无访问权限 | 公开 |
| `/app/:pathMatch(.*)*` | not-found | SYS-404 页面不存在 | 公开 |

实际有 19 个命名路由。3 个管理详情路由归入所属页面，M04/M05 共用分析页面，得到 16 个页面入口。PUB、AUTH 编号供本文档交叉定位。账号设置是 App 共享面板，能力检查失败是 App 根视图，均无独立 URL。采集 Job、历史导入、首次写入证据、城市短详情、T 报表、导出预览等由所在页面管理。

`/city`、`/analysis`、`/quality` 为旧路径重定向到 `/`，随后走当前总览规则。`/app/manage` 等未注册的 `/app` 子路径落入 SYS-404。Vite 的 HTML 回退覆盖 `/app`、其下路径及三条旧路径的 GET/HEAD HTML 请求，根按入口文件处理。应用外未知路径由宿主负责，API、探针和 Django Admin 不交给 SPA 状态页。S09 核验 Django 深链及 HTTP 状态。 [Vite 配置](../vite.config.ts) · [旧路径](../src/shared/legacyOverviewPaths.ts)

## 导航关系

| 入口 | 去向和恢复方式 |
|---|---|
| 公共顶栏 / 窄屏抽屉 | 平台说明 ↔ 数据来源 → 登录 / 注册。来源页另有本页目录，与公共导航各自控制。平台说明进入平台在 F 跳 `/app`，真实进入登录并携 `next=/app`。来源页可按安全 `from` 返回具体工作区，由目标守卫再次核验。 |
| 用户侧栏 / 移动导航抽屉 | 监测总览、分析工作台、数据查询、我的空间。城市详情、站点详情及观测历史通过上下文入口进入。真实 M01 当前为精简页面，仅有其实际可见的来源、分析、账号和获权管理入口，完整侧栏仍属设计要求。 |
| M01 → M02 → M03 | F 榜单、地图、城市表经短详情、同源获权链接和稳定 ID 进入城市。城市从 S 目录进入站点详情，站点回同城。正式 M01 可直接跟随列表城市链接。面包屑和 history.state 可恢复安全来路，刷新后按当前授权重新读取。 |
| M02/M03 → M04/M05、M06、M07 | 研究入口以授权条件预填。M02 小时历史、M03 单站历史进 `/app/query/history`，可以原条件打开 `/app/query`。观测工作台也可由侧栏打开。关注/取消由详情发起，M07 复访先核本人收藏与目标投影。 |
| M04/M05 ↔ M06 / M07 | 图点、时间桶、表格或共同样本进 `/app/query?mode=research`。父组和版本按当前研究上下文匹配，返回相关模式和焦点。分析方案在 M07 编辑或复跑后进入分析，申请导出后可在 M07 复访。票据可按仍可辨的来源回到合适的申请页。 |
| 用户区 ↔ 管理区 | 优先进入可访问的采集工作台，仅获诊断时可直达系统运行。F 无能力时用户区入口可引导演练登录。真实精简总览只显示获权切换，其他共享侧栏当前仍可能显示「切换工作区」并弹原型范围提示。管理导航按能力显示采集、质量和运行。返回用户区的地址为 `/app`，仅管理会话仍由守卫裁决。见审计 B04。 |
| M08 ↔ M09 ↔ M10 | Window/Run、事件、运行诊断按可见关联跳转。M08 关闭抽屉回原关联详情或账本。M09 窄屏详情可回队列。跨工作台通过会话内存记来源、筛选、批次、详情区段和焦点，回去经当前权限重读。M10 恢复局部诊断和已展开证据时重新请求。 |
| 系统状态恢复 | 403/404 进入时将焦点落在标题。主操作复查能力，有 `user` 才回总览，其余回平台说明。「返回上一页」只使用无 query/hash 的 about、sources、national 来源，需查权限的入口会再核验，无安全历史则回平台说明。顶部提供公开说明、来源及登录。 |

源码参见 [共享侧栏](../src/national/NationalSide.vue) · [公共顶栏](../src/public/PublicHeader.vue) · [移动抽屉](../src/shared/SkyNavigationDrawer.vue) · [系统状态](../src/system/SystemStatePage.vue) · [管理导航能力](../src/shared/managementNavigation.ts)。

## Path 和 Query 参数

URL 只记录下表的视图与页面输入。参数解析、默认值及当前会话授权分别执行。游标、回执信封、票号、方案或收藏 ID、管理筛选及未分享的研究条件由服务响应或页面内存控制。F 场景键只供本地验收，形式不构成真实 API 参数。

| 页面 | 实际接收或生成的参数、默认与边界 |
|---|---|
| PUB-01 | 无业务 Query。 |
| PUB-02 | `from` 经安全回跳校验。Hash 支持 `overview,synthetic,cities,stations,reports,limits` 和大写产品项 `F,L,C,T,S,H,D`，目标标题可聚焦。 |
| AUTH-01 / AUTH-02 | `next` 为安全站内返回地址并可在身份页之间传递。登录识别 `reason=expired\|registered`。 |
| M01 | F 用 `scenario=normal\|empty\|missing\|denied\|error`、`metric=AQI\|PM25\|PM10\|O3\|NO2\|SO2\|CO`、`city`（合成城市名称）、`rank=low`、`hours=6`（否则 12）、`q,province,grade`（1–6）、`order=asc\|desc,page`。真实 M01 当前不消费这组筛选。当前按名称选择与 DESIGN 稳定城市键要求有差异，见 B02。 |
| M02 | `:city` 正 int64。C 用 `product,at,as_of,standard_series`，T 用 `trend_product,anchor,trend_standard`，S 用 `station_product,station_at`。`at/anchor/station_at=latest` 选择最近，实际请求省略对应锚点。`window` 为本地 C 槽筛选。F 可带 `city_scenario,station_q,station_type`。 |
| M03 | `:station` 正 int64，`product,site_epoch,as_of`。站址期可在获权目录中切换。缺产品仅 F 有默认产品，正式缺少有效条件提示不可访问。F 可带 `city_scenario`。 |
| M04/M05 | `view=statistics\|compare\|correlation`，缺省或无效为 statistics。`grain=hour\|day\|month\|season` 可作初值，默认 day。F 按 `product,city/cities,site_epoch/site_epochs,indicator/indicators,start,end,analysis_scenario` 预填并写回应用条件。真实跨页预填用当前 SPA 内存，回跳状态由 `analysisBack` 传递。 |
| M06 数据查询 | `mode=window\|latest_valid\|exact`，默认 window，有精确时标或报告日时可推断 exact。接收 `product,city/cities,site_epoch/site_epochs,indicator/indicators,start,end,as_of,observed_at,local_report_date,source,unit,standard_series,window_definition,quality,cleared=1`。`report_kind` 由窗口推导。`mode=research` 进入独立子视图，必须匹配当前父结果及 `history.state.researchEntry`，复制链接或丢失内存无法恢复完整上下文。F 的 `query_scenario` 优先于 `city_scenario`。 |
| M06 观测历史 | `product` 加单个 `cities` 或 `site_epochs`，以及链路携带的 `mode,indicators,report_kind,window_definition,source,unit,standard_series,quality,start,end,as_of`。站址期多指标视图支持本地 `observation_date,observation_hour,observation_time`，UTC+8 日期后选该日小时或全部时刻。F 可带 `city_scenario`。 |
| M07 我的空间 | `tab=overview\|favorites\|saved-queries\|exports`，缺省/无效回概览。Tab 切换写历史，批次游标和临时筛选内存恢复。普通列表每批 6/4/10，方案可选 4/8/12，票据可选 10/20/50。F 可用 `personal_scenario` 演练。 |
| M08 采集任务 | 仅根路由保留合法单个 `view=jobs\|windows\|runs\|imports`。默认 jobs，视图受限时选首个获权账本。`:windowId` 为正 int64，`:runId` 为规范 UUID，详情不携筛选 Query。 |
| M09 数据质量 | `:eventId` 正 int64。事件队列产品、运行、窗口和状态，已翻批次与滚动位置在当前会话内存。详情只有事件 ID 入地址。 |
| M10 系统运行 | 正式模式无 Query。F 独有单键 `operations_scenario=abnormal\|unknown\|expired\|partial\|none\|empty\|error`。诊断条件、选中窗口和抽屉留在当前会话内存。 |
| SYS-403 / SYS-404 | 无业务 Query。404 保留原 `/app` 未匹配地址，捕获参数不用于业务查询或回跳。 |

源码参见 [城市链接](../src/api/city.ts) · [M02](../src/city/useCityDetail.ts) · [M03](../src/station/useStationDetail.ts) · [分析条件](../src/analysis/conditions.ts) · [M06 条件](../src/query/filters.ts) · [历史](../src/city/RelatedObservations.vue) · [M07](../src/personal/PersonalPage.vue)。

## 访问检查与异常落点

1. F 的用户区允许游客使用一次性合成夹具。真实用户区要求 `GET /api/v1/me/capabilities/` 的 `workspaces` 含 `user`，同一路径只改 Query 时守卫不重跑。管理路由在两种模式都检查能力，会取消被后续导航取代的检查。管理入场要求 `manage`。M08 至少有 `manage_collection,configure_collection_schedule,run_collection,review_observation,import_history` 之一，M09 要 `review_observation`，M10 要 `diagnose_operations`。M08 Window/Run、M10 窗口/趋势/关联另核审阅。裁决、计划、手动运行及具体对象独立授权。
2. 能力返回未登录时用 replace 到登录。真实受保护请求的 `403/not_authenticated` 触发清理和重新登录，`reason=expired` 表示过期。会话有效但能力不足时 replace 到 SYS-403。网络或能力读取失败终止目标导航，在 App 根视图显示重新检查及返回平台说明。对象或用途拒绝由各页面就地处理。管理页另在窗口重新激活时复查能力并在失权后清理。
3. 安全回跳以真实命名路由白名单为准。用户区拒绝外站、协议相对、转义异常、反斜杠、控制字符、Hash、嵌套跳转键、数组参数及外链值。管理只接受采集根、运行根、质量根及合法质量详情形式，守卫未登录时采集深链统一回采集根。登录无可用目标时有用户区能力才回总览，否则回说明。当前登录对采集有专门能力判定，质量与运行的 `next` 仍受默认 user 分支影响，见 B04。
4. M08 缺审阅直接进 SYS-403。有审阅且对象读 403/404 时收回对象并回对应账本。非法详情 ID 当前尝试用不带 `pathMatch` 的命名 404 跳转，可能生成 `/app` 并在刷新后回总览，见 B05。M09 无效 ID 和不可访问事件在详情区反馈，避免暴露对象存在性。普通城市/站点无效对象由所属页反馈。
5. 历史前进/后退按浏览器位置恢复。普通不同路径回页首。M08 同账本及桌面 M09 队列与详情切换避免重置滚动。窄屏 M09 路径切换回页首。返回时以现有授权和可用控件恢复选择和焦点。

源码参见 [路由守卫](../src/router.ts) · [安全回跳](../src/shared/workspaceReturn.ts) · [身份表单](../src/auth/AuthFormCard.vue) · [管理重查](../src/shared/useManagementRecheck.ts) · [冲突登记](BASELINE_REVIEW.md#冲突差异与待确认项)。
