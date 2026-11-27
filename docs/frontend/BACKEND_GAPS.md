# 前后端需求与接口契约差异审计

2026-11-27 · 后端 `main@0c0a064769a69a8474ce3dda20ba82c05916fd9d` · 静态整改基线

> **当前处置见下方「[Phase 02 联合冻结处置](#phase-02-联合冻结处置)」**。第 1 节至操作附表保存前次审计，原分类、缺口及建议均按当时版本理解。
>
> 本目录 `DESIGN.md`、`PAGE_REQUIREMENTS.md`、`PAGE_API_MAPPING.md`、`ROUTES.md`、`CONTRACT_REVIEW.md` 是前次反向核验的输入快照，内链按原前端文件目录解释。现行设计在 `air-quality-frontend:DESIGN.md`，需求、映射、路由及历史反核的维护位置为 `air-quality-frontend:docs/`。阶段验收及前端清单只在前端仓维护。本库维护 `BACKEND_GAPS.md`、`BACKEND_REMEDIATION_PLAN.md`，并维护两份共同遵循文档的唯一正本。[前端整改](../../../air-quality-frontend/docs/FRONTEND_GAPS.md) · [阶段验收](../../../air-quality-frontend/docs/BASELINE_REVIEW.md) · [契约正本](CONTRACT_BASELINE.md) · [产品决策正本](PRODUCT_DECISIONS.md)

## 1. 审计总览

**55 条 REST 路径、74 个正式操作全部匹配 Django 路由、HTTP 方法和 OpenAPI operationId。主要差距发生在已存在的接口上，涉及查询翻页的一致性、从事件取得证据、失联后的命令核实，以及调用方对字段的解释。** 用户观察、管理治理、研究分析三条路径均有实际后端服务。当前证据支持逐项整改，真实集成验收仍开放。

| 覆盖项 | 数量及依据 |
|---|---|
| 页面和能力 | 16 个页面，含 19 条命名路由中的 3 条管理详情深链。按第 2 节各行编号的操作合并为 **57 个业务能力组**，包含静态页面及其零 API 情形。另核 3 条旧路径重定向。 |
| 接口 | **60** 个基线记载的 SPA 调用，**14** 个契约内无 SPA 调用的操作，另核 **2** 条 F-only 模拟路径。按 Method + Path 去重，非路径个数。 |
| 数据层 | 4 个项目应用，46 个当前 ORM 模型，38 个项目迁移文件。核到 Django Auth、Session、PostgreSQL 约束与原生 SQL、Celery Worker/Beat 的责任边界。 |
| 差异 | 共 **43 项**。A 后端能力缺失 **7**，B 接口契约不一致 **13**，C 前端接口映射问题 **6**，D 后端已有、前端未使用 **4**，E 需求或契约待确认 **10**，F 无法验证 **3**。 |
| 优先级 | P0 **0**，P1 **23**，P2 **20**。每项只有一个主分类，跨页面按同一原因合并。D 项的 P2 表示后续接入顺序，不要求增加功能。 |

页面需求、API 映射及路由的 SHA-256 前 12 位依次为 `87f806a21226`、`47ef2311f1ec`、`e440c055e596`。映射正文明确说明仓库沿用 `PAGE_API_MAPPING.md` 路径。前端冻结报告提供已知问题、历史注入及未决范围的出处。[页面需求](PAGE_REQUIREMENTS.md) · [API 映射](PAGE_API_MAPPING.md) · [路由](ROUTES.md) · [冻结报告](../../../air-quality-frontend/docs/BASELINE_REVIEW.md)

**缺件**　当前工作树缺前端正式 `DESIGN.md`、Vue 源码和产物。冻结报告记载 DESIGN 哈希以 `d5c471b81ef3` 开头，`HEAD:frontend/DESIGN.md` 的历史版本为 `e292c755861f`。本审计使用已提供的业务与接口条款，不从旧设计补设需求。文中“当前前端调用”引用冻结映射，后端事实来自本仓实现。前端侧 B01–B17 为原审计编号，BG 编号为本次登记。

| 验证状态 | 实际证据 |
|---|---|
| **PASS** 路由与规格 | 逐项对照 74 组 Method、Path、operationId。运行 `pytest -q -p no:cacheprovider tests/test_s07_contract.py`，**2 passed**，含 OpenAPI 3.1、本地引用、resolver 差集、Django 路径转换器与操作唯一性。 |
| **PASS** 迁移声明 | 使用 `MigrationLoader(None)` 与 `MigrationAutodetector` 比较迁移终态与当前模型，差集为空，连接对象未打开。手查项目迁移的约束、索引和触发器。此检查的对象是代码声明。 |
| **PASS** 无库 HTTP 边界 | 同源 Django 测试客户端启用 CSRF、禁止数据库连接。匿名 CSRF 为 200/64 字符且 no-store，匿名能力为 `403/not_authenticated`，缺 CSRF 的登录为 `403/csrf_failed`，非法 Run URL 和未知 `/app` 路径为 HTML 404，GET 注册为 HTML 405。使用独立静态设置，没有取用业务凭据。 |
| **PASS** 导出规则核对 | 禁止数据库连接后直接调用 `research_exports.normalize`，`kind=summary` 产生 `invalid_request`。`columns(kind, [])` 给明细 23 个、汇总 24 个默认字段，两者均缺显式数据域标识。见 BG-015、017。 |
| **FAIL** 需求/契约一致性 | 第 7 节 A/B/C 有明确反例，如 `kind=summary`、F 更正规则的产品限制、目录续页 `at` 的绑定。方法/路径核对通过不能覆盖这些问题。 |
| **NOT RUN** 集成及授权数据 | 当前浏览器、双会话、活 PG Schema、迁移执行、PG/Redis/Worker/Beat 并发、受控文件、实际下载、合法 H/D、正式地图及在线源均未运行。所列业务测试为已检查的测试定义。既有 S07/S08 收据及 F 不计作本轮实测。 |

## 2. 页面、操作与后端能力

每页将同一用户意图下的读写操作合并计数，括号内为能力组数。三个管理详情深链计入所属页面。共享账号设置归 M07，研究支撑归数据查询。权限表示服务端仍须执行的检查。没有异步说明的操作由当前 HTTP 请求完成。

| 页面与路径 | 核对的业务能力组 | 实际后端和闭环 | 差异编号 |
|---|---|---|---|
| PUB-01 平台说明 `/app/about`（2） | ① 说明三类用途与 F/正式域 ② 公开导航、进入工作区 | 静态内容无需读表。真实受保护入口由会话、能力和对象授权接管。 | 025 |
| PUB-02 使用边界 `/app/sources`（2） | ① 产品、署名、数据/地图/软件许可说明 ② 按组定位、聚焦和返回 | 静态导航由页面完成。正式观测的实际来源与授权随数据响应，登记见 Source/Product/UseGrant。 | 004、027 |
| AUTH-01 登录 `/app/login`（3） | ① 校验凭据、建立会话 ② 轮换 CSRF、按资料及能力确认结果 ③ 安全回跳、拒绝及过期恢复 | Django 锁定账号、认证并绑定会话代际。客户端持新 Cookie 核能力。合法 next 的选择属于前端。 | 023、026 |
| AUTH-02 注册 `/app/register`（2） | ① 创建普通账户、处理弱密码/重名/限流 ② 成功转独立登录、处理结果未知 | UserCreationForm 与唯一约束持久化普通账号。成功 201 不会建立登录态，前端可倒计时、回登录核实。 | 026 |
| M01 总览 `/`、`/app`（5） | ① 同刻 L 城市表 ② 搜索/筛选/游标及下钻 ③ 覆盖、分布、并列排名与焦点 ④ 地图/列表选城 ⑤ 全国趋势与独立 F 演示 | 同一可见快照、目录许可和摘要权支持基本列表。前端可接现有检索。正式地图与全国时序分别受阻。F 的三项 KPI、缩放和联动属于演示端。 | 001–004、027 |
| M02 城市 `/app/cities/:city`（5） | ① C 七指标及小时历史 ② T 最近 24 槽及报表 ③ D 地方日报 ④ 同城 S 目录与翻页 ⑤ 关注、进入研究或查询 | C/T 两套独立数据和锚点。D 可沿精确链接。S 需产品+城市及目录权。关注持久化到本人。条件与返回由客户端管理。 | 005–008、021、027 |
| M03 站点 `/app/stations/:station`（4） | ① 稳定站选址期、身份及坐标 ② 六污染物最近值、质量和逐项时标 ③ 指标趋势、缺槽与历史 ④ 关注和获权比较/查询 | 站址期及名称按有效期，合法坐标另核证据/CRS。最近 valid 与显式历史窗独立，站点关注使用稳定站 ID。 | 008、009、021、027 |
| M04/M05 分析 `/app/analysis`（6） | ① 发现获权系列及方法 ② 同城站/多城对比与排名 ③ 小时/日/月/季趋势、分布、箱线和候选 ④ Pearson 与真实配对 ⑤ 按父版本下钻 ⑥ 保存/更新方案及明细/汇总导出 | 有界 SQL 建时间槽关系并计算，组合重新鉴权。可读 H 双站限同集、同城、身份已核且实际获权。无分母时保留未知。图点、散点和汇总映射需对齐。 | 012–017、022、027、041、042 |
| M06 数据查询 `/app/query`（4） | ① 多条件窗口/最近有效 ② 精确瞬时/地方日报 ③ 普通记录证据及研究支撑 ④ 整组预览/申请导出 | 有界且半开查询、许可裁剪、版本和三类支撑。整组导出绑定研究父版本，最近/精确与任意筛选子集不能直接套用。 | 009–013、015–018、041、043 |
| M06 观测历史 `/app/query/history`（2） | ① 单对象多指标分页、按实测时刻合并 ② UTC+8 日期/小时筛选、恢复并跳回查询 | 服务按观测时刻/键倒序给行。日期、小时、跨页分组和地址恢复为前端，后端不得拿最近值补历史。 | 008、009 |
| M07 我的空间 `/app/me`（5） | ① 本人资源概览和当批筛排 ② 关注/偏好/迁址复访/移除 ③ 方案编辑/删除/重跑 ④ 票据查询/下载/移除 ⑤ 共用资料修改、改密及退出 | 私有资源按 owner+域读取。方案乐观版本检查。下载再次鉴权，改密注销。列表可无总数、资源可仅有受限占位。 | 015–023、026、043 |
| M08 采集 `/app/manage/collection`（7） | ① Job/模板发现与独建 ② 首挂/草案/提议/激活/放弃和分界 ③ 暂停/恢复 ④ 手动窗口与重投 ⑤ Window/Run 账本和两条详情深链 ⑥ 暂存标识导入及查状态 ⑦ 原命令回执和对象审计 | 管理、配置、执行、审阅、导入分别核权。事务登记业务窗和尝试，Celery 投递与完成分开。已有 Job/模板可走基本链。独建/首挂及失联导入尚有缺口。 | 026、028、033、035–037 |
| M09 质量 `/app/manage/quality`（4） | ① 队列、事件深链、修订/证据 ② 认领/隔离/结案 ③ 有据更正、回执/影响复查 ④ 站址、许可撤回及处置交接 | 审阅和裁决独立，提交有版本锁及回执，更正引起读模型和票据变化。正式证据规则、事件级动作及失联核对见明细。 | 026、029–032、034、037、040–042 |
| M10 运行 `/app/manage/operations`（4） | ① 五组件和队列就绪 ② 受权窗口、首写与关联时间线 ③ 源地方日趋势和同版图表 ④ 账号/别名及备份恢复交接 | 诊断权限可独立读组件，窗口/趋势另核审阅和用途。组件样本来自 Worker/Beat，首写查业务账本。主机执行由保管人承接。 | 024、026、036、038–040、042 |
| SYS-403 `/app/forbidden`（1） | ① 拒绝后按最新能力安全恢复 | 复用 capabilities 和公开退路，对象拒绝由业务 API 裁剪，前端清会话缓存及迟到响应。 | 023、032 |
| SYS-404 `/app/:pathMatch(.*)*`（1） | ① 未匹配地址、深链刷新与安全恢复 | Vue 捕获和宿主首次分流是不同环节。裸 Django 当前返回 HTML 404，能力读取失败时可回公开说明。 | 023、025 |

键盘/触屏、布局、减弱动效、主题、加载反馈、日期输入和页内导航由前端基线验收。B02 的 F 选城、B03/B06 的视觉差异、B04 的回跳与侧栏、B05 的非法 M08 ID 地址恢复均已有前端登记。前端基线记录了这些行为，本仓只能核其服务端依赖。B05 的前端错误路由与 BG-025 的宿主责任分别验收。

## 3. 接口契约差异

附录逐项列出 74 个正式操作的请求、成功 DTO、已登记错误状态及后端处理。路径统一相对 `/api/v1/`，`?` 表示可选。具名 DTO、完整属性类型及安全方案可按同一 operationId 查 [OpenAPI](../api/openapi.v1.json) 和 [v1 契约](../api/v1.md)。表中区分规格声明与处理器实际返回，二者有差异时以第 7 节登记为准。

| 直接影响调用的约定 | 当前边界 |
|---|---|
| 查询地址与选择器 | 观测 GET 用逗号分隔对象/指标、带偏移的时刻。研究 POST 使用数组和逐指标单位/标准映射。普通读正式域通常省略 scope，研究父意图为 formal/synthetic。地方日、报告窗、站址期均属逻辑键。 |
| 数值和版本 | 普通原始读数为十进制字符串或 null，统计量和计数为 JSON 数字。观测 key、revision_id、qualification_version、研究 input_version、方案 intent_revision、治理 expected_version 的作用域各异。 |
| 续页 | 普通资源 `page.next_cursor`，管理目录顶层 `next_cursor`，质量队列 `next_before`，证据/窗口按 `after/run_after`。游标须沿原身份和条件继续，个别列表对两页间版本变化的保障不同（BG-006、009）。 |
| 下载与异步 | CSV 成功为附件，其他研究操作和回执为 JSON。研究及下载同步完成，采集/历史导入返回受理后仍须查同一 Run/Attempt。 |
| 路由外的两条 F 操作 | `POST admin/f-ui/jobs/` 发送 `{product,name,description}`。`POST admin/f-ui/jobs/{id}/schedule/` 发送首挂草案。二者来自前端 adapter，正式 resolver 无此路由。已有模板和主机能力、正式需求差距见 BG-028。 |

## 4. 数据库及数据能力

仓库采用 Django ORM 及有界原生 PostgreSQL 查询。数据访问分布在域服务、`queries`、选择与投影模块，无须存在名为 DAO 的单独层。下列 `accounts/`、`observations/`、`collection/`、`samples/` 和 `config/` 代码路径均相对 `backend/`。

| 数据与支持的页面 | 模型、迁移及索引证据 | 可提供的能力与限制 |
|---|---|---|
| 身份、私有资源 M02–M07 | `accounts/models.py`、迁移 `0001`–`0007`。Django User、Session、AccountState、Favorite、SavedQuery/Target、ExportTicket。按 owner+目标唯一和按 owner/域/创建时间/ID 的分页索引。 | 事务保存、重名拒绝、删除方案级联清目标、目标删除后可置空。票据有期限、失效位、哈希、行/字节约束，下载时重算。列表无需捏造总数。 |
| 产品、时态与权利 M01–M09 | `observations/models.py` 和迁移 `0001_product_rights`、`0002_geography`、`0010_online_endpoint_grants`、`0015_grant_registration`、`0018_citysearchalias`。Source→Product→UseGrant，City/Station 各归来源，SiteEpoch→Station/City。半开区间及 GiST 排除约束约束代码和站址重叠。 | 许可带用途、受众、地理、对象、指标、数据期/有效期和字段。站坐标附证据与 CRS。城市模型没有全国地图所需点位证据。对象、单元和目录权限不可互换。 |
| 观测与版本 M01–M06、M09 | `observations/migrations/0003_observation_ledger.py`、`0004_correction_links.py`、`0011_quality_governance.py`。CityObservation/StationObservation 区分键。时刻与地方日互斥，复合唯一含产品、实体、指标、报告/窗口、标准及锚。各键修订有版本唯一及当前行约束。 | 独立保留 Decimal 值、删失限、质量、watermark、争议、资格版本、收据与批次。`revisions.py` 处理同内容推进水位和迟到版本。`selection.py` 先授权和选时再投影。精确读仅取当前可用修订。 |
| 研究统计和分母 M04–M06 | `analysis_query.build_relation`、`research_coverage.evidence_calendar/site_comparable`、`research_statistics`。用同一授权关系进行 PostgreSQL 分组、配对、分位、相关和降采样。 | E=应有、O=实到、V=有效，E_common 和 P 描述共同应有与有效配对。独立日历目前只识别指定 UCI H 和登记 F。一般正式 C/S 可返回未知分母。实际到报数不能补 E。阈值和量纲影响结论。增加真实日历需有独立证据。 |
| 采集、异步与审计 M08–M10 | `collection/models.py`、迁移 `0001_initial`、`0002_ledger_guards`、`0009_control_versions`、`0010_schedule_plans`。WorkWindow 业务键唯一。Run 的手动请求、Beat 时槽、重试后继受唯一约束。RunItem 与 Run 同窗复合外键，收据血缘触发器。`accounts.GovernanceAction` 按 actor/request_id 唯一。 | Job→Run→Window，多个 Job 可共享一窗。状态/送达/业务结果分开。到期、重试和租约字段支持恢复扫描。Run 有 Job 外键，但 Window 没有单一所属 Job。 |
| 质量、撤权、保管 M09/M10 | `observations/models.py` 中 QualityIssue/QualityDecision/CandidateClearance/DispositionJob，`quality_product_status` 复合索引。迁移 `0011`–`0017` 建质量、处置、备份及依赖。`accounts.ServicePulse` 保存组件最近证据。 | `quality_governance.py`、`research_disposition.py`、`disposition_control.py` 及 `backup*.py` 分离纠正、输出失效、受控存储和恢复复权。Beat 定期清私有票据。ServicePulse 只能支撑其登记的证据，不能回推出全局消费速率。 |

迁移声明、业务唯一约束及分页索引可从仓库复核。运行库实际部署的 DDL、执行计划和代表性负载下的性能属于 BG-026，缺少这些证据不能据此要求新增索引或分区。

## 5. 关键业务闭环

| 验收路径 | 已追到的业务结果 | 需要优先处理的断点 |
|---|---|---|
| 用户观察 | 注册写用户 → 独立登录、轮换 Session/CSRF → 同刻 L 列表 → C、T、同城 S 分别读取 → 可披露站址期历史 → 唯一收藏记录 → 登出/重登重读本人的目标。可在 `tests/test_s04_{accounts,national,city,station,favorites}.py`、`test_s08_national_home.py`、`test_s07_exact.py` 查边界用例。 | 正式页仅发首批，S-only 城市发现和无锚续页受限，跨页历史可能混版。地图、时间序列和 D 来源单独签收。 |
| 管理治理 | 登记模板或既有 Job → `s08_schedule.perform` 事务检查计划分界 → `collection.services` 登记同一业务窗 → 提交后投递 → Worker 写 RunItem/Receipt → 质量事件及版本化裁决 → M10 复查窗口/首写。历史导入走 StagedInput→ImportAttempt→Worker，主机暂存不经浏览器上传。`tests/collection/test_collection_postgres.py`、`test_s08_schedule.py`、`test_s06_quality.py`、`test_s08_receipts_session.py`、`test_s06_history_generation.py` 提供用例。 | 首包丢失的导入关联及质量影响难以恢复。正式有据更正受规则限制，认领后动作和证据链接需补。无权线上采集应继续关闭。 |
| 研究分析 | `catalog/series` 发现 → 父意图建有界授权关系 → SQL 统计/配对 → 以父版重读支撑 → 保存本人方案及修订 → 重跑重新核权 → 申请时算 CSV、核列/用途/版本 → 只保存票据 → 下载再核与重算 → 过期、撤权或版本变化拒绝。`tests/test_s05_{statistics,selection,private,races,rights,revisions}.py` 涵盖主要断言。 | 需对齐图点/散点下钻、汇总枚举及导出预览，明确 F 附件性质。受限 H 的样本、许可和实际字节留给同版联调。 |

## 6. 异常处理、权限及边界

| 情形 | 代码与实际语义 | 前端处理／复验边界 |
|---|---|---|
| 401 / 403 | 同源 Cookie 使用 SessionAuthentication。匿名保护读实际是 `403/not_authenticated`。原生登录凭据错为 `403/invalid_credentials`，CSRF 拒绝为 `403/csrf_failed`。动作/来源拒绝用 permission_denied/use_denied。见 `accounts/api.py`、`views.py`、`profile.py` 和 `config/settings.py`。 | 区分重登、刷新 CSRF、留在页面显示用途拒绝。实际接口未承诺统一返回 401。过期与注销均须清私有结果。 |
| 400 / 空结果 | `accounts/parsers.py` 有界 JSON，选择器拒多余键、重复查询参数或非法窗的规则依操作而异。普通 GET `no_data/no_coverage`、精确读 0 行附 reason、票据 200/no_data、汇总遮蔽均有成功分支。 | 请求错误不能显示为“无观测”。精确读校验差异见 BG-010。窗口缺报、删失、争议与零分别表达。 |
| 404 / 405 | 本人私有记录不可见与不存在可均为 not_found，收藏目标失权另有占位。管理事件资源级隐匿见 BG-032。路径转换器未匹配的 404、原生方法拒绝的 405 可为 HTML。 | 对象拒绝与整个 SPA 的未知路由分开，不对 HTML 强取 JSON code。 |
| 409 / 并发 | `page_changed`、`input_changed`、`request_conflict`、`precondition_conflict` 分别对应游标、输入版本、命令身份、目标版本。部分损坏/条件不符的游标为 400。事务/锁约束同窗和当前修订。 | 改条件重新查首批或重做预览。不盲重放写入。核原回执。普通明细末行检查和受限回执见 BG-009、034–035。 |
| 410 / 422 | 存在的到期票可 410/expired，清理后可 404。研究组合如不相容同城/标准可 422/incompatible_series。普通参数和超预算多走 400。 | 票据回申请页。研究收窄或更换可比组合。日期合法不等于有权或可比。 |
| 429 | `accounts/limits.py` 单 Web 进程固定一分钟窗口，注册/登录按 IP，改密等按用户。票据另有本人申请配额。若响应无 Retry-After 则不编造剩余秒数。 | 写入先核原状态，读取按界面反馈重试。接口限流、上游配额等待及 Worker 排队属于不同状态。 |
| 500 / 503 | `config/errors.BoundaryErrors` 将数据库连接异常转 503/database_unavailable。未捕获异常可由 Django 产生 HTML 500，OpenAPI 登记 UnexpectedHtml。投递不确定时 503 可带原 Run/Window/Attempt 标识。Redis/Worker 的局部故障也可体现在健康投影中。 | 对非 JSON 显示服务错误。任务区分受理、投递、启动、完成、有新数据。失败时保留已知编号，首写用 known/none/unavailable，HTTP 成功不证明覆盖。 |

所有管理动作均应按具体端点的具名权限、对象、相关来源用途再核。前端隐藏按钮仅控制交互。`accounts/research_session.py` 管会话、依赖锁及终点复验。目录可见不授予导出、裁决或备份权，诊断权本身只允许组件诊断。

## 7. 优先级整改清单

这份登记以同一业务缺口为单位。A、B 进入后端或契约整改队列，C 由前端核映射及真实请求，D 供选用，E 需双方决策，F 需补齐证据。P0 按三条基础链路是否整体受阻评定，当前没有足够证据定为 P0 的项目。

**推荐执行顺序**

1. 先处理质量事件的对象隐匿、证据链接、动作资格和回执恢复（BG-029–032、034）。它们决定管理员能否在授权范围内解释一次更正。
2. 处理历史合页的一致性、图点和散点支撑、F 附件标识及失联导入（BG-009、012–013、017、035）。同步校正前端检索/续页、CSV 枚举与确认内容（BG-001、006、015–016）。
3. 补仅 S 权的目录发现、正式地图和全国时序、独立 Job/首挂路径（BG-002–003、005、028），分别落实数据准入和接口范围。并行收敛 P2 的校验、文档和类型问题。
4. 取得当期前端构建、同源宿主和获准样本，按第 5 节重验三链及 BG-025–027。验证记录中分别记载前后端版本、数据性质和实测范围。

### P1

| ID · 类型 | 页面/业务操作 | 前端要求 | 后端现状、差异与代码证据 | 影响 | 建议及验收标准 |
|---|---|---|---|---|---|
| BG-001 · C | M01 同刻城市检索、翻页、选城 | S09-01 需要在同一 L 快照检索、下钻并使用获准排名。 | 映射仅发 home/page_size/map_limit，不取下一页。`observations/s04_national.py` 的 `_selection/_matching_catalog/_rank_preview/_home_digest` 已提供 q、geography、grade、已知标准下的并列排名、focus_city 和会话游标。 | 首批以外的获权城市无法从当前列表正常选择。 | 前端沿原条件翻页，换筛选回首批。以两页、并列、无汇总权、未知标准和撤权复核选城。 |
| BG-002 · A | M01 正式地图 | 正式地图独立验收，位置、CRS、分发权和同刻城市 ID 均须有依据。 | `s04_national._read_national` 的 map 分支固定 unavailable 和空 items，OpenAPI `NationalResult` 限空数组。`observations.models.City` 无城市位置依据，`SiteEpoch` 坐标只代表站址。 | 正式地图目前无法交付，同刻列表仍可用。 | 取得获准底图和城市位置后定义可撤回的投影。按同一 ID 验证地图、榜单和列表，失权或无依据时回退。 |
| BG-003 · A | M01 全国时序 | M01 要求正式全国趋势分项验收。 | `s04_national._read_national/_roster` 计算单一授权快照，`s04_city.read_city_trend` 仅算单城 T 的 24 槽。逐时读 L 可作核对，当前没有绑定整段快照、许可及各时分母的全国时序结果。 | 无法用正式接口直接复现 F 的逐小时全国优良占比。 | 先约定时区、标准、分母和开放范围，再提供获准逐时结果或等效规范读取。覆盖中间缺时、分母未知及部分授权。 |
| BG-005 · A | M02 仅有 S 权的城市深链 | 纯城市 URL 应能发现可披露的同城站址目录。 | `s04_station.read_station_directory` 已支持给定 product+city 的目录。`discovery.candidates/entry` 从可见观测出发，S 行给 site_epoch 和站点链接，未给同城目录入口。C/L 导航或逐站读可恢复部分关系，无观测时都无法发现。 | 有目录权、无 C 链接的用户可能进不了站点页。 | 提供按城市发现可披露 S 目录的入口或等效链接。分别验证仅 S 权、有站无观测、合法空目录和撤权。 |
| BG-006 · C | M02 站址目录续页 | 映射要求续页固定使用返回的 at。 | `s04_station.read_station_directory` 把首批请求的 requested_at 编入条件指纹，响应锚点另存于游标。首批省略 at 后在续批加上响应 at，会返回 400/invalid_cursor。 | 默认最近目录可能只能看首批。 | 改映射，首批省略时续批也省略，首批指定时保持原值。两种请求均连续取两页，切条件重新读。 |
| BG-009 · B | M06 窗口、历史和多指标合页的一致性 | 映射要求在原条件和版本上继续，变化时重读。 | `detail.read_detail` 的续页主要复查末 key 仍可见，`s04_http.encode_cursor` 签用户及条件。与 M01 home 和研究父版本不同，先前非末行订正或部分撤权不一定触发 409。 | 多批拼接可展示不同时刻的投影，特别影响按同一观测时间合页。 | 明确版本保证并让客户端识别可见集合变动。两页间修改已展示的非末行，续读须保持一版或要求重来，隐藏行变化不泄露。 |
| BG-012 · B | M05 图点到 M06 支撑 | 映射规定图点带同父版下钻，页面要复现选择的桶与支撑。 | `research_statistics.chart` 的 exact 点和 range 压缩点均无独立 bucket/support。统计桶、热图、候选点已有 drilldown，`research_api.drilldown` 可接受父版本和半开桶。 | 图点下钻可能退化为整组或落入错误的列、末桶。 | 为获准图点提供描述，或冻结可验证的点到桶规则。验精确点、空槽、压缩末桶、列位置和版本变化。 |
| BG-013 · B | M05 Pearson 散点 | 页面要系数、共同样本、散点和限制。映射列出 paired_points。 | `research_statistics.paired_summary` 的 paired_points 在双对象 compare 分支表示差与变幅。Pearson 分支给系数、状态和 paired_drilldown，压缩图的极值并非逐对样本。 | 直接按映射读取无法画出可靠的 Pearson 散点。 | 优先复用获权 `analysis/details/` 的 paired 支撑并按同刻和列配对，或明确有界散点 DTO。验缺数、P<30、常数列、超过压缩阈值及明细失权。 |
| BG-015 · C | M04–M07 汇总导出枚举 | 映射写 kind=detail 或 summary。 | `accounts/research_exports.normalize`、`models.ExportTicket`、OpenAPI `ExportIntent` 接受 detail/aggregate。summary 在进入业务查询前返回 400。 | 按映射实现的汇总申请会失败，明细仍可申请。当前 Vue 发出的值未实测。 | 将映射对齐 aggregate 并核 real 客户端。获权整组应能申请和下载汇总 CSV，无数据不发票。 |
| BG-016 · C | M06 导出确认与列选择 | 确认页须反映实际范围、来源、质量和列。B11 记录确认文案与请求不符。 | `research_exports.columns/normalize/data_rows` 校验一个域、父意图和版本，必选列始终包含，fields=[] 使用默认列。观察明细含获准的 observed 行，reliable 是父选择口径。 | 用户可能按“全部来源／全部质量”确认不同内容，手选列与必备列混淆。 | 按实际规范父组呈现确认内容，对照 CSV 表头及行，分别验明细与有效样本汇总。字段发现和手选范围由双方确认。 |
| BG-017 · A | M06/M07 F 导出标识 | 共用验收要求 F 标识随派生数据和下载保留。 | `observations.models.Product` 明存 synthetic/family。`research_exports.COMMON_FIELDS/OPTIONAL/data_rows/download_export` 不输出数据域，文件名固定 air-quality.csv。来源 namespace 和署名没有强制 F 字样的约束。 | 合法 F 附件离开页面后不能保证辨认数据性质。 | 在兼容票据和列授权的前提下提供可随附件保留的明确标识。用中性来源名、同指标的 F/正式样例比较明细与汇总字节，验旧版本票据处置。 |
| BG-023 · E | AUTH-01、SYS 与管理专属账户 | 基线含仅 manage、空工作区、按目标具名能力回跳，B04 有注入响应复现。 | `accounts/profile.capabilities` 对有效会话固定先发 user，再按动作加 manage。`tests/test_s08_capabilities.py` 固化此行为，不能产生仅 manage 或空工作区。 | 当前后端无法提供这两种联调角色。 | 约定角色策略和无 next 的默认落点，前端按目标动作核权。分别验普通、仅管理或约定替代、已撤权和过期会话。 |
| BG-024 · E | M10 账号治理资格 | 账号目录/治理入口依具名能力和操作范围开放。 | `accounts/profile.CAPABILITY_REQUIREMENTS` 将 manage_accounts 导航限 staff。`accounts/governance.account_action` 按具名权限、目标层级等授权，REST 的规则与原生 Admin 的 staff 门槛不同。 | 有具名权的非 staff 可能没有可发现动作，双方对能力含义不一致。 | 明确 staff 是管理站点门槛还是该 REST 动作的前提，用 staff/非 staff、授权/未授权和不可管理目标核对投影与写入结果。 |
| BG-025 · F | SYS-404 与同源宿主直达 | 页面要求根入口、深链刷新、未匹配应用路径及 API/管理路径正确分流。 | `config/urls.py` 及独立后端镜像仅注册 API/Admin/就绪探针。无库解析 /、/app、/app/analysis 失败。`docs/operations/S08.md` 记录拆出的后端交付，当前没有同版前端 dist 或交付宿主配置。 | 无法签收最终同源站点的首次直达、刷新和 HTTP 状态。 | 集成方提供实装宿主及构建，验 GET/HEAD、三个旧重定向、未知 /app 子路由、错误 API 和 Admin 的独立响应。 |
| BG-026 · F | 跨端三链、数据库及进程实证 | 验收需同版真实会话、PG 事务、Worker/Beat、附件和撤权竞争。 | 本轮取得无库静态证据，历史 `docs/verification/S07_V12.json`、`S08_V12.json` 和本仓测试绑定各自版本。当前缺同版 SPA 源与冻结 DESIGN，未运行带数据的链路。 | 只能签静态整改基线，不能签 S09 或并发/性能/离线实测。 | 后续用隔离环境按第 5 节三链核 PG 账本、Cookie、CSV 字节、重投与撤权，保留版本、输入种类和验证结果。 |
| BG-027 · F | M01–M07 正式样本与外部权利 | D 须官方来源和地方日，同城 H 双站需同集且获权，在线 L/C/T/S 和地理素材各自准入。 | `collection/adapters/cnemc.py` 登记小时产品，`observations/import_uci.py`、`uci_catalog.py` 为 H。`queries.read_current` 能读 D，但现有源配置、测试或过去验证不证明当前有权的正式日报、双站 H、持续在线及地图分发权。 | 有实现也无法用现有材料签真实发布、统计分母、D 来源和 T11。 | 核许可用途、有效期、实体/标准/地理依据及获准数据，分别签 D、H、地图及城市在线。无授权维持关闭。 |
| BG-028 · A | M08 独立建 Job 和首次挂计划 | 基线保留独立建 Job、在原 Job 首挂首个计划，基本链允许用已有 Job 或登记模板。 | `collection/services.register_job` 是主机路径。`s08_schedule.fresh_draft/locate` 从登记模板复制 Job，变更依赖现有模板关系。`collection.models.Job` 未登记 F 中的自由名称／说明。两条 f-ui 路径只在 adapter。 | 正式 SPA 无法完成这两个起始操作，已有 Job/模板路径可继续。 | 共同定正式 DTO、最小权限及准入。验创建后停用、在同一 Job 首挂、重复/冲突均无第二个 Job 且不自动启源。 |
| BG-029 · A | M09 正式来源的更正及站址证据 | 有据更正和站址治理须可按正式证据执行，缺证时保持隔离。 | `quality_governance.correction_evidence/mapping_candidate` 仅接受有登记规则的 F，映射更正还限 CO 等条件。`catalog_governance.registered` 同样只支持登记的 F 站址证据。 | 非 F 的事件即使具备全局动作权也不会得到相应更正或站址签名。 | 先为真正获准的源登记可审阅证据规则。验签发、版本推进、局部失效、无关组不变及失权拒绝。不得开放任意编辑。 |
| BG-030 · A | M09 逐事件动作资格 | 认领后须能辨认本人可执行的 hold、close 或有据更正。 | `quality_governance.event_result` 有 handler 整数，`accounts/profile.profile` 只发用户名等资料。`act` 区分 open、in_review、处理者、resolved 和证据，没有本人关系或逐事件 allowed_actions。基线只在 open 启用动作。 | 认领后页面无法可靠展示可用操作，hold/close 写处理本身已存在。 | 按当前事件投影已获准动作或等价关系。两名审阅者验认领前后、仅审阅、证据过期与 resolved，POST 仍独立核权。 |
| BG-031 · B | M09 事件到观测证据 | 详情要求定位当前/候选修订、逐字段对照与按需证据。 | `quality_governance.event_result/project_refs` 返回受影响对象、修订 ID、可选 batch 等，未发映射列的 city_key/station_key/证据读取链接。`queries.read_evidence` 需真正观测键或 batch。批次内获权收据可部分恢复关联。 | 有键无批次的事件无法可靠打开对应观测修订。 | 按许可给类型化观测键或链接。验 city/station 有键无 batch、仅结构事件、撤权和无原值许可。 |
| BG-032 · B | M09 事件存在性隐匿 | 页面要求不可访问与不存在的事件不暴露对象存在性。 | `quality_governance.act/checks` 与 `accounts/management_api.rejection` 对缺对象及已有对象但无使用权返回不同资源级状态。会话验证另有独立分支。 | 仅统一页面文案不足以满足端到端隐匿。 | 服务统一资源级拒绝的可见形态，并保留未登录反馈。用隔离授权矩阵验证读写两端及响应体。 |
| BG-034 · B | M09 不确定写入后恢复更正影响 | 同一原命令应可核实回执和影响，区分受影响组和无关组。 | `quality_governance.act/result` 首次写给 outcome/affected，重复命令仅有 action_id/status/event_version。`accounts/s08_receipts.quality` 的最小凭据同样不能还原跨键影响。 | 首次响应丢失时可证已办，不能可靠恢复旧组和新组。 | 提供获权影响查询或回执重建。模拟首包丢失、更正跨键和随后撤权，验同一回执、精确范围和当前裁剪。 |
| BG-035 · B | M08 历史导入响应丢失 | 用原尝试或原请求确认写入状态，避免重复导入。 | `observations/history.submit` 认领 ready stage 并建 UUID 尝试，`history_status.project` 的列表/详情无 stage 或客户端请求关联。已有 attempt_id 可追踪，丢失首包时多条同期尝试不能唯一辨别。 | 按时间或重复提交都不能可靠确认原尝试。 | 增加本人获权的原暂存关联或原命令查找。两个并发 stage、一次丢包核只产生原尝试，隔离其他用户。 |

### P2

| ID · 类型 | 页面/业务操作 | 前端要求 | 后端现状、差异与代码证据 | 影响 | 建议及验收标准 |
|---|---|---|---|---|---|
| BG-004 · E | PUB-02、M01 产品与素材说明 | L 卡须准确说明内容和许可。映射将正式浓度、IAQI、应到／缺报名录、首污构成列待确认。 | 基线 B07 记载前端卡片写“公开”“城市与行政区划”。`observations/normalization.py`、`s04_national._project_page/_roster` 区分 L 等级、已获权分母与 C 浓度。`models.UseGrant` 分用途授权。 | 可能把公开说明、产品使用权和 F 的演示指标混读。 | 产品与权利方分别确认数据、几何、软件和各新增指标的来源、窗口、字段许可。无确证的分母显示未知。 |
| BG-007 · D | M02 地方日报入口 | M02 需区分已获准 D 报告和暂未开放，当前只显示占位。 | `discovery.entry` 可发按 local_report_date 的精确链接，`queries.read_current` 读当前 D 修订。M06 已使用同一接口。 | 已有获权 D 时可通过 M06 阅读，M02 尚无入口。 | 有准入样本时沿获权链接接入并核地方日、署名、无报和撤权。来源签收见 BG-027。 |
| BG-008 · E | M02/M03 目录增强、时效及空间 | 站型、全量站名搜索、最近摘要与总数目前在 F。数字年龄、完整覆盖日历、空间差异需核口径。 | `s04_station._catalog_page/_coordinates` 做获权站址分页与坐标裁剪，无 q/站型/全量总数。`projection.public_rows` 有 age_seconds，正式 freshness 保守为 unknown。`source_status.collection_status` 给可证的单元运行时效。 | 不能从当前页站数还原全目录，也不能从年龄自动判断断源。 | 先利用已披露年龄。若需要正式全量筛选、陈旧阈值、跨期差异或日历，分别定义站型来源、时区、分母和许可，用缺依据样本验收。 |
| BG-010 · B | M06 精确键校验 | 精确读取需区分非法选择与合法指定时刻无报。 | OpenAPI `readExactObservation` 限 standard_series 的枚举。`queries.read_current` 检查产品指标和全局报告窗，未完整检查标准字面值、指标与标准、产品与窗的组合。`detail.validate_series` 在普通读取另有校验。 | 有权对象上的部分非法组合可落入 200/count=0。 | 统一精确选择器校验。用无效标准、C+日报、D+错误小时窗和获权 D 的合法空日分别断言 400 与 200/0。 |
| BG-011 · C | M06 精确行身份 | 映射把 revision_id 同时记作 key 和 revision。 | `queries.read_current/project_revision` 发修订 PK，`projection.public_rows` 在普通查询分开发逻辑 key 与 revision。同一逻辑观测可有多版。 | 用修订 ID 作为跨版业务键或证据接口 key 会定位错误。 | 标明仅可用作本次渲染行标识，治理链接用真正逻辑键或服务链接。用同观测两次修订验收。 |
| BG-014 · D | M04/M05 排行、部分覆盖、箱线和月季 | 分析按服务端获权结论展示，部分投影缺专门呈现。 | `research_statistics.paired_summary/statistics_buckets/anomalies/summarize` 已计算 ranking、partial_summary、共同窗口、四分位/须、月季桶和合规候选。`tests/test_s05_statistics.py` 有共同覆盖门槛用例。 | 页面可复用现有计算，跨站址期拼接仍需另定范围。 | 决定呈现时以 18/24 各列、共同 12/24、零 IQR 及月季缺段复核。保留不足原因和权限裁剪。 |
| BG-018 · E | M07 票据跨会话回访 | 过期回申请页，来源缺失时提供保守入口。 | `accounts/research_exports.ticket_metadata` 仅有 id/kind/created_at/expires_at，过期清理后可 404。 | 异会话重开不一定知道申请页，列表也不能代表实时下载资格。 | 约定可披露来源、失效历史保留期。用另一个会话验证签发、撤权、到期及清理前后复访。 |
| BG-019 · B | M07 收藏顺序与 schema | 前端沿本人当批游标消费稳定顺序、链接及获权摘要。 | OpenAPI `listFavorites` 描述 ID 升序，`accounts/favorites.list_items` 按 created_at、id 双降序。`Favorite` 开放额外属性但未准确描述实际 link/results/epochs。 | 生成的客户端或排序测试可能与真实结果不符。 | 修契约描述及可裁剪字段。两收藏同创建时间跨页验证次序，撤权占位仍通过类型检查。 |
| BG-020 · C | M07 收藏复访的 404 与撤权占位 | 映射称跨人／对象不可见统一 404，同时允许失权占位。 | `accounts/favorites.get_item/read_summaries` 对他人记录隐藏，本人记录的目标撤权可 200 unavailable，允许删除。 | 把本人撤权目标算作收藏不存在，会残留旧摘要或丢失移除入口。 | 区分记录与目标。用两账号、一次撤权验证 404、200 裁剪、204 移除。 |
| BG-021 · E | M02/M03/M07 关注状态反查 | 基线最多三批各 100 条核目标，总数与反查待定。 | `accounts/favorites.list_items` 允许 scope/page_size/cursor，无 kind+target 查询。`accounts.models.Favorite` 已有本人稳定目标唯一约束。 | 第 301 项之后的目标无法靠三批证明未关注。 | 约定继续分页、显示未知或获权反查。验目标在第四批及撤权时不误报或泄露。 |
| BG-022 · B | M04/M05/M07 保存方案的方法枚举 | 页面保存三类分析，需可验证的请求类型。 | OpenAPI `SaveQuery/PatchQuery` 包含 detail，`accounts/research_saved.research_spec` 明确拒绝保存该方法，研究支撑本身接受 detail。 | 按机器契约构造的部分保存请求会 400。 | 限定保存/编辑方法的 schema。三种分析通过，detail 仅在相应明细操作获准。 |
| BG-033 · B | M08 控制、计划、投递与窗口响应 | 映射列 Job 目标新版、计划 action_id、重投 dispatch、Window 行 state 和详情事件关联。 | `collection/control.job_result` 给 job/enabled/reason，`views.recover` 给原 run_id/window_id，重投另要求 manual 等状态。`s08_schedule.project` 的读响应无 action_id。`s07_admin.list_activity` 窗口列表和 `queries.read_window` 详情各有不同字段。 | 以写回包推断共享目标状态或把缺字段当空事件，可能误报执行结果。 | 明确读写 DTO 和可选字段。控制后读 targets，重投读原 Run/回执，Window 经队列 product+window 查事件。验暂停、源恢复、深链直接打开和投递不确定。 |
| BG-036 · E | M08/M10 Job、Run、Window 深链 | 基线要求获权关联，名称、创建人及可披露的关系待核。 | `collection.models.Run` 有单一 job 和 window，业务窗可由多 Job 共用。`s07_admin.list_activity/queries.read_run` 可沿已知 Job 筛和从 Run 找窗，详情没有通用 job/name/creator。 | 不能从 Window 倒推出唯一 Job 或执行人。 | 确认是否给 Run 的获权 Job 链接及 Window 的多对多可见关系。验两 Job 同窗、部分授权和直达刷新。 |
| BG-037 · B | M08/M09 审计理由 | 映射含 claim/claim_review、correct_source/verified_correction 等有业务意义的配对。 | `quality_governance.ACTIONS/REASONS` 和 `accounts/governance.command` 分别校验成员。`collection/control.parse` 亦涉及分离校验。动作与理由各合法时，不保证组合有意义。 | 审计可能记录与实际操作相反的理由。 | 定每种动作允许的理由集合及通用理由。验有效配对、错配拒绝且不产生审计事实。 |
| BG-038 · E | M10 新监测范围 | 源整体健康、全局队列深度/消费速率、源时标和小时质量趋势要求界定范围。 | `accounts/operations.broker_probe/pulse` 有 PING、订阅和最近消费。`collection/s07_admin.bucket` 按源地方日统计，source_publication_delay 未知。`operations_first_write.FirstWrite` 给首写证据，不保证关联观测时刻及独立证明期限。 | 窗口成功、HTTP 响应率和全局来源成功率不可互换。 | 逐指标约定对象、时间尺度、分子分母、保留用途、证据 TTL。无观测证据时验 unknown。 |
| BG-039 · D | M10 已有窗口及趋势投影 | 映射仅用两图与部分健康字段。 | `accounts/operations.window_evidence`、`collection/s07_admin.bucket/trends` 已有可见抓取时间、等待分档、终态分布、调度延迟等。`tests/test_s07_operations.py` 有 SQL 对照。 | 这些字段可按现行权限用于进一步诊断。 | 产品选用时验证部分可见窗、零分母、空日和同版图表，维持请求响应率的原义。 |
| BG-040 · D | M08–M10 无 SPA 调用的 14 个治理/夹具操作 | 映射将这些操作列作外围交接和 F 运维。 | 夹具 2、站址 2、许可/处置 4、受控账号 2、备份/恢复意图 4 均已注册。源代码和执行责任见第 9 节及附表。 | 已有 REST、原生管理站点及主机操作可分别承接，是否加入口属产品决定。 | 需要交接时分别验证具名权限、动作版本、主机签认和回执。页面成功仅承诺其实际完成的阶段。 |
| BG-041 · B | M04–M06、M09 机器契约字段 | 前端按方法、状态和用途读取明确字段，需能辨识缺省与 null。 | OpenAPI `ResearchResult` 未完整逐项描述 E_common、P、pearson、ranking、partial_summary 等。`QualityIssue`、证据与动作回执的开放对象也未准确描述 event_version/evidence/affected 及各分支。真实投影见 `research_statistics.summarize`、`quality_governance.act`、`queries.read_evidence`。 | 开放对象可通过 schema 验证，生成的类型仍不足以安全使用真实字段。 | 按方法和授权定义成功分支、实际字段名、类型、缺省/null，修订样例。以裁剪、首次/重放及月季/配对响应验证。 |
| BG-042 · E | M04/M05、M09/M10 历史增强与外部交接 | 需求将两期同城/省际比较、完整覆盖日历、批量复核、运维重置等列为待定，原生管理与主机保管另有职责。 | `research_spec.parse_spec`、`quality_governance.act`、`backup_urls.py` 分别约束单一父窗、单事件版本及备份意图。现有目录与治理均按目标授权，未登记任意批量/主机重置的 SPA 合同。 | 缺少决定范围、比较基准和后果的业务定义。 | 产品与保管角色逐项决定是否纳入、选数和事务粒度。F 回放、进阶排序/通知、气象变量等可选范围保持原优先级。 |
| BG-043 · E | M06/M07 D 日报 CSV | 精确读需保留地方日报日，映射将 D 的导出范围列待核。 | `research_exports.data_rows` 对 D 明细把 local_report_date 写入 observed_at，report_kind 为可选列。当前 M06 精确单键未直接开放导出。 | 附件读者可能把源地方日期解释为观测瞬间。 | 共同确定独立地方日列或明示带类型的时间列，用非 UTC 源时区验实际表头和行。 |

## 8. 待双方确认和待补证据

| 负责方 | 决策或补证事项 | 关联登记 |
|---|---|---|
| 产品、数据权利方 | 确认 L 卡与各素材依据、全国扩展指标的口径，定义站型/时效、额外日历和空间比较所需的披露范围。正式 D/H、地理资产和在线源各自取得当期凭证。 | BG-004、008、027、042 |
| 产品、前后端 | 确认是否有仅管理/空工作区，非 staff 的治理授权边界，收藏反查、票据跨会话来源和日报导出列。确认普通历史跨页一致性与图点支撑形态。 | BG-009、012–013、018、021、023–024、043 |
| 治理、运维、保管人 | 确认 Job 和业务窗的可披露关联、来源整体状态及全局队列指标定义、原生 Admin 和主机恢复入口。正式更正适配需有签发证据的责任方。 | BG-028–029、036、038、040、042 |
| 集成交付方 | 提供与需求同版的前端、DESIGN、同源宿主及隔离 PG/Redis/Worker/Beat 环境。分别补浏览器会话、实际账本、裁剪、CSV 和授权来源证据。 | BG-025–027 |

## 9. 后端已具备、当前 SPA 未使用的能力

- **观察与分析**　全国列表已经支持 q、筛选、焦点、已知标准排名和签名游标。D 有获权精确链接。对比已有排名、部分覆盖结论和成对差值，分布已有四分位、箱须、月份与合规异常候选。见 BG-001、007、014。
- **诊断**　获权窗口和逐日桶已有抓取时标、等待分档、终态分布与延迟。诊断区当前只取部分字段。见 BG-039。
- **契约内 14 个未调用操作**　夹具提交/读取 2 个，站址预览/执行 2 个，处置预览/准备及授权读取/撤回 4 个，受控账号读取/操作 2 个，备份准备/发布、恢复意图及权威材料 4 个。附录最后一组逐个列出请求及实际责任。原生 Django Admin 的账号目录与城市别名、后续保管签认及实际恢复另有执行者。见 BG-040。

## 附录　逐操作技术核对表

每组先列可复查的 OpenAPI 请求与成功 DTO，再列实际处理器、权限和业务结果。`int`/`uuid` 等为字段或路径类型，路径转换器已通过 resolver 核对。`object` 或开放 DTO 只能说明规格允许该形态，准确的分支字段以实现列为据。未获权字段可缺省或为 null，页面不能把缺省计为零。

### 身份和账号

| Method 和相对 Path · operationId | Query / JSON 请求 | 成功 HTTP 与响应 DTO | 已登记异常 HTTP |
|---|---|---|---|
| `GET auth/csrf/` · `issueCsrf` | — | `200 {csrf_token:str}` | 405, 500, 503 |
| `POST auth/login/` · `loginSession` | `Credentials` | `200 {username:str}` | 400, 403, 405, 429, 500, 503 |
| `POST auth/register/` · `registerAccount` | `Credentials` | `201 {username:str, role:USER}` | 400, 403, 405, 429, 500, 503 |
| `POST auth/logout/` · `logoutSession` | — | `204 空体` | 403, 405, 500, 503 |
| `POST auth/password/` · `changePassword` | `PasswordChange` | `204 空体` | 400, 403, 405, 406, 415, 429, 500, 503 |
| `GET me/capabilities/` · `readCapabilities` | — | `200 {workspaces:user/manage[], actions:enum[]}` | 400, 403, 405, 406, 500, 503 |
| `GET me/` · `readProfile` | — | `200 Profile` | 400, 403, 405, 406, 500, 503 |
| `PATCH me/` · `editProfile` | `ProfilePatch` | `200 Profile` | 400, 403, 405, 406, 415, 500, 503 |

| 操作 | 实现、字段语义、请求限制及调用链证据 | 关联 BG |
|---|---|---|
| `issueCsrf` | 匿名即可，无参，返回 Django masked `csrf_token:string` 及 CSRF Cookie，私有 no-store。`accounts/views.py:csrf`。 | — |
| `loginSession` | 精确 JSON `{username,password}` 两个非空字符串，解析界限 1024，用户名锁界限150。400 invalid_request、403 invalid_credentials/csrf_failed、429 rate_limited。锁用户，authenticate、Django login（轮换 CSRF）、记录 session_generation，200 username。`accounts/views.py:sign_in`、`session_generation.py:bind_generation`。 | — |
| `registerAccount` | 同 CSRF/凭据结构。UserCreationForm 校验名字与配置的四类密码规则，事务保存默认普通用户，201 `{username,role:"USER"}`，无自动登录。重复含并发 IntegrityError/弱密码通用400，429。`accounts/views.py:register`、`config/settings.py:AUTH_PASSWORD_VALIDATORS`。 | — |
| `logoutSession` | 空正文、有 CSRF，调用 Django logout 清 Session，204。匿名已退出也可安全结束。`accounts/views.py:sign_out`。 | — |
| `changePassword` | 仅 `{old_password,new_password}` 两非空字符串≤1024。锁 active user/验原密码与新策略、保存密码哈希后 logout。204，旧密码错403 invalid_credentials、弱/相同400、频控429。`accounts/profile.py:change_password`。 | — |
| `readCapabilities` | 拒绝 query。锁账户和所属群组，检查会话数据库行、代际和密码哈希，枚举直接/组具名权限（账号及别名动作需 staff）。`workspaces:string[],actions:string[]`，登录用户固定先放 user，有动作再放 manage。过期/无登录403 not_authenticated，并非401。`accounts/profile.py:capabilities,CAPABILITY_REQUIREMENTS` 与 `research_session.py:SessionCheck`。 | 023–024 |
| `readProfile` | 无参。复查 active、代际、密码 hash，200 `{username,first_name,last_name}` 全为字符串。`accounts/profile.py:profile,current_user`。 | — |
| `editProfile` | 接受姓/名的非空键子集，可空字符串，每项≤150，禁 query/未知键，事务行锁 `save(update_fields=...)`，200 同 GET。400 invalid_request，CSRF 403。`accounts/profile.py:profile`。 | — |

### 观察、目录和普通查询

| Method 和相对 Path · operationId | Query / JSON 请求 | 成功 HTTP 与响应 DTO | 已登记异常 HTTP |
|---|---|---|---|
| `GET overview/national/` · `readNational` | `product?:int, scope?:synthetic, at?:date-time, as_of?:date-time, standard_series?:unknown/HJ633-2012/HJ633-2026, window_definition?:hourly_label_unspecified/hourly_left_label/hourly_right_label, order?:city/aqi_desc/aqi_asc, grade?:int, home?:1, rank_preview?:aqi_desc/aqi_asc, focus_city?:int, map_limit?:int, q?:str, geography?:str, page_size?:int, cursor?:str` | `200 NationalResult` | 400, 403, 405, 406, 409, 500, 503 |
| `GET catalog/objects/` · `discoverObjects` | `scope?:synthetic, product?:int, at?:date-time, page_size?:int, cursor?:str` | `200 DiscoveryPage` | 400, 403, 405, 406, 409, 500, 503 |
| `GET cities/{city}/` · `readCity` | `product:int, scope?:synthetic, at?:date-time, as_of?:date-time, standard_series?:unknown/HJ633-2012/HJ633-2026` | `200 CityResult` | 400, 403, 405, 406, 500, 503 |
| `GET cities/{city}/trend/` · `readCityTrend` | `product:int, scope?:synthetic, anchor?:date-time, standard_series?:unknown/HJ633-2012/HJ633-2026` | `200 CityTrendResult` | 400, 403, 405, 406, 500, 503 |
| `GET cities/{city}/stations/` · `readStationDirectory` | `product:int, scope?:synthetic, at?:date-time, page_size?:int, cursor?:str` | `200 StationDirectory` | 400, 403, 405, 406, 409, 500, 503 |
| `GET stations/{station}/` · `readStation` | `product:int, scope?:synthetic, site_epoch?:int, as_of?:date-time, indicators?:str, page_size?:int, cursor?:str` | `200 StationResult` | 400, 403, 405, 406, 409, 500, 503 |
| `GET observations/query/` · `queryObservations` | `product:int, scope?:synthetic, source?:str, cities?:str, site_epochs?:str, indicators:str, mode:window/latest_valid, report_kind:hourly/rolling_8h/rolling_24h/daily, window_definition:hourly_label_unspecified/hourly_left_label/hourly_right_label/rolling_8h_right_label/rolling_24h_right_label/local_daily, unit?:str, standard_series?:unknown/HJ633-2012/HJ633-2026/not_applicable, quality?:enum, start?:date-time, end?:date-time, as_of?:date-time, page_size?:int, cursor?:str` | `200 ObservationPage` | 400, 403, 405, 406, 409, 500, 503 |
| `GET observations/current/` · `readExactObservation` | `product:int, scope?:synthetic, city?:int, site_epoch?:int, indicator:str, report_kind:hourly/rolling_8h/rolling_24h/daily, window_definition:hourly_label_unspecified/hourly_left_label/hourly_right_label/rolling_8h_right_label/rolling_24h_right_label/local_daily, standard_series:unknown/HJ633-2012/HJ633-2026/not_applicable, observed_at?:date-time, local_report_date?:date` | `200 ExactResult` | 400, 403, 405, 406, 500, 503 |

| 操作 | 实现、字段语义、请求限制及调用链证据 | 关联 BG |
|---|---|---|
| `readNational` | 可省 product 则指定正式默认 L。at/as_of 互斥。standard_series/window_definition、q/geography、order=city/aqi_asc/aqi_desc、grade=1..6。home=1 时 rank_preview,focus_city,map_limit 1..400。page_size≤100/cursor NationalResult 返回 selection.home=1、snapshot_at 可空、state=ok/no_data、summary 不可用或按 series 分组、ranking、focused 可空、results[].city(int)/name 可空/links/series/levels、page、map（若请求）。AQI decimal 字符串、levels 为等级。`s04_national.py:read_national/_read_national` RR 快照及释放前独立复权。仅已知标准排名。收齐合法名册和披露许可才给 expected。 | 001–004 |
| `discoverObjects` | 可选 product,at,scope,page_size≤100,cursor，默认 page_size=50。拒绝未来 at DiscoveryPage: product(int)/shape/source/synthetic，city 或 site_epoch，name 或 null，link/history_link 字符串、attribution 及观测时刻或地方日。`discovery.py:candidates/entry/discover` 从当前有效获权记录按稳定 product/object 分页。无当前获准记录时为空，不是全目录。页尾投影变化 409。 | 005、007 |
| `readCity` | product 必需，at/as_of 互斥，standard_series?。at 须源时区整点。无分页。window 保持本地，不是合法额外参数 CityResult 返回 七个 `slots[].{indicator,state=ok/unavailable,results[]}`，`snapshot_at` 单刻、city_name 可空、history/trend/stations 为获权链接。 `s04_city.py:read_city/_anchor/hourly_rows/city_navigation`，C 独立数据，浓度（CO mg/m³ 及其他 µg/m³）不会被 AQI 标准筛掉。`tests/test_s04_city.py`。 | — |
| `readCityTrend` | product 必需，anchor 源时区整点、standard_series?，无分页 CityTrendResult：独立 T。anchor_basis=explicit/latest_visible/unknown，start/end，若有锚则 24 个 `points[].{observed_at,state,results[]}`，缺点空数组。无锚/无数据可 0 槽。`s04_city.py:read_city_trend`，`tests/test_s04_city.py`。 | — |
| `readStationDirectory` | product 必需且 S/H，at?,page_size≤100,cursor。额外 q,station_type 会 400。按 station_id/可披露起时/epoch 排序 StationDirectory：`at,state,results[].{station,site_epoch,city,station_name,city_name,site_name,codes,period_start/end,status,coordinates,link},page`。筛有效 [) 期，目录独立字段许可，坐标另核来源证据/CRS/权限。空合法目录 200/no_data。 `s04_station.py:_catalog_page/_coordinates/read_station_directory`。 | 005–006、008 |
| `readStation` | product，as_of?,site_epoch?。不带期为 select_epoch+分页，可选具体期/indicators，不可在详情混用 page_size/cursor StationResult 返回 目录 results/page。详情 identity，逐指标 `value` 字符串、unit,observed_at,quality,age_seconds:int,freshness,source_status/history[]`。`s04_station.py:read_station/station_rows/_history_links`，`selection.py:latest` 按站期+指标+窗口+系列。无数据/目录期与明细分支。读数按最新 valid，显式窗缺失/删失另由明细 quality 检索，F 附加场景不入正式 HTTP。 | 008 |
| `queryObservations` | product，cities 或 site_epochs 逗号串≤5，indicators≤7，mode=window + start/end 或 latest_valid + as_of?、report_kind/window_definition。可限 source/unit/standard_series，窗口质量 all/valid/suspect/missing/censored/invalid/disputed，latest 固定 valid，正式省 scope。≤720h/3600格，page_size≤100 `detail.read_detail` → `selection.ReadContext` → `projection.public_rows`，按观测时刻/key 倒序，可持续合页。`selection,state,results,page.next_cursor`。key/revision/qualification_version、零/删失/争议/缺测、可披露来源/抓取/时区、产品署名。`no_data/no_coverage` 或拒绝。签名游标被篡改 400 / 已不可见锚点 409。普通行没有研究 input_version，也没有 D 的 local_report_date。日期小时筛选/按时刻跨游标合页属前端。`tests/test_s04_selection.py` | 009 |
| `readExactObservation` | product、单 city 或 site_epoch、indicator、report_kind/window_definition、standard_series、observed_at 或 local_report_date，正式省 scope，无游标 200 `count=0/1,reason,results,attribution,rights_attributions,synthetic`。当前有效行有 revision_id/revision_no、**精确读专用** input_version，争议/异常或指定时刻无观测返回 reason，不回填。`queries.read_current/exact_version`, `tests/test_s07_exact.py`。见 BG-010。 | 007、010–011 |

### 研究与支撑明细

| Method 和相对 Path · operationId | Query / JSON 请求 | 成功 HTTP 与响应 DTO | 已登记异常 HTTP |
|---|---|---|---|
| `GET catalog/series/` · `discoverResearchSeries` | `purpose:analysis, method:statistics/compare/pearson, start:date-time, end:date-time, scope?:synthetic, product?:int64, city?:int64, site_epoch?:int64, indicator?:enum, page_size?:int, cursor?:str` | `200 {results:object[], page:object}` | 400, 403, 405, 406, 409, 500, 503 |
| `POST analysis/statistics/` · `readStatistics` | `StatisticsIntent` | `200 ResearchResult` | 400, 403, 404, 405, 406, 409, 410, 415, 422, 500, 503 |
| `POST analysis/compare/` · `compareObservations` | `CompareIntent` | `200 ResearchResult` | 400, 403, 404, 405, 406, 409, 410, 415, 422, 500, 503 |
| `POST analysis/details/` · `readResearchDetails` | `DetailIntent` | `200 ResearchDetail` | 400, 403, 404, 405, 406, 409, 410, 415, 422, 500, 503 |

| 操作 | 实现、字段语义、请求限制及调用链证据 | 关联 BG |
|---|---|---|
| `discoverResearchSeries` | 必须 purpose=analysis、method=statistics/compare/pearson、带时区 start/end ≤30天。可传 product、city 或 site_epoch、indicator、scope、page_size 1..50、cursor 200 results 中 product int、source、shape=C/S/H、city 或 site_epoch int、indicator/unit/window_definition/report_kind/standard_series/source_timezone、permitted_from/until。只列数据库确有获权当前系列，签名游标与结果快照绑定，变化 409。`s08_series.selection`, `s08_series_selection._authorized_ranges/_sql_page/read_series`, `tests/test_s08_series.py` | — |
| `readStatistics` | 父意图 `{method,selection,method_version?,grain?}`。C/S/H，产品 int、来源、对象 ID 数组、指标、单位及标准映射、半开时窗、quality=reliable。statistics 1×1，pearson 同对象两不同浓度，s05.v1、hour/day/month/season。≤3600格/720h，单对象单指标月季 ≤8784 格/366天 PostgreSQL 算 E/O/V、分母状态、覆盖、桶、结论、极值、箱须、直方分布、月/季与热图、异常候选。Pearson P<30/常数列有理由，缺许可时裁剪 chart。配对下钻可取真实配对。`research_statistics.summarize/statistics_buckets/anomalies/paired_summary/chart`, `tests/test_s05_statistics.py` | 012–014、041 |
| `compareObservations` | 同一父意图。compare、2–5 同层对象、单指标。H 同一已登记获准数据集的不同同城站，S 同城且位置依据，禁未知标准比较 200 绑定共同父版本，columns、E_common、P、paired_ratio、expected_common_windows、conclusion、适用时 ranking/partial_summary/mean_difference。逐点 A−B 百分比以 B 为分母，零返回 null。授权窗及共同覆盖 75% 门槛。`research_coverage.site_comparable`, `research_statistics.paired_summary`, `tests/test_s05_statistics.py:120` | 014、041 |
| `readResearchDetails` | 父意图、input_version、support=observed/effective/paired、可选 bucket {start,end,pos?,local_hour?}、page_size≤100、cursor。detail 首批可省版本作为版本预读。继续须版本。size/版本/桶/用途构成游标绑定 `research_api.drilldown`, `analysis_query.detail_rows`: 200 results、page.next_cursor、父选择和版本、时区、署名。每行 key/revision/pos/entity/qualification_version、observed_at/value/unit/quality、出处抓取依据。按时刻/列/key 升序，超限 400，输入变化 409。D 仅整源地方日且 observed_at=null。`tests/test_s05_selection.py`, `test_s05_cost.py` | 012–013 |

### 本人资源

| Method 和相对 Path · operationId | Query / JSON 请求 | 成功 HTTP 与响应 DTO | 已登记异常 HTTP |
|---|---|---|---|
| `GET me/favorites/` · `listFavorites` | `scope?:synthetic, page_size?:int, cursor?:str` | `200 FavoritePage` | 400, 403, 405, 406, 409, 500, 503 |
| `POST me/favorites/` · `createFavorite` | `scope?:synthetic + CreateFavorite` | `200 Favorite / 201 Favorite` | 400, 403, 404, 405, 406, 409, 415, 500, 503 |
| `GET me/favorites/{item_id}/` · `readFavorite` | `scope?:synthetic` | `200 Favorite` | 400, 403, 404, 405, 406, 409, 500, 503 |
| `PATCH me/favorites/{item_id}/` · `patchFavorite` | `scope?:synthetic + {product:int}` | `200 Favorite` | 400, 403, 404, 405, 406, 409, 415, 500, 503 |
| `DELETE me/favorites/{item_id}/` · `deleteFavorite` | `scope?:synthetic` | `204 空体` | 400, 403, 404, 405, 406, 409, 500, 503 |
| `GET me/saved-queries/` · `listSavedQueries` | `scope?:synthetic, page_size?:int, cursor?:str` | `200 SavedQueryPage` | 400, 403, 404, 405, 406, 409, 410, 500, 503 |
| `POST me/saved-queries/` · `createSavedQuery` | `scope?:synthetic + SaveQuery` | `201 SavedQuery` | 400, 403, 404, 405, 406, 409, 410, 415, 422, 429, 500, 503 |
| `GET me/saved-queries/{item_id}/` · `readSavedQuery` | `scope?:synthetic` | `200 SavedQuery` | 400, 403, 404, 405, 406, 409, 410, 500, 503 |
| `PATCH me/saved-queries/{item_id}/` · `patchSavedQuery` | `scope?:synthetic + PatchQuery` | `200 SavedQuery` | 400, 403, 404, 405, 406, 409, 410, 415, 422, 500, 503 |
| `DELETE me/saved-queries/{item_id}/` · `deleteSavedQuery` | `scope?:synthetic` | `204 空体` | 400, 403, 404, 405, 406, 409, 410, 500, 503 |
| `POST me/saved-queries/{item_id}/run/` · `runSavedQuery` | `scope?:synthetic + {}` | `200 {state:target_unavailable/method_changed/baseline_unavailable/current/data_changed, method_version?:str, intent_revision?:int, result?:ResearchResult}` | 400, 403, 404, 405, 406, 409, 410, 415, 422, 500, 503 |
| `GET me/exports/` · `listExportTickets` | `scope?:synthetic, page_size?:int, cursor?:str` | `200 ExportTicketPage` | 400, 403, 404, 405, 406, 409, 410, 500, 503 |
| `POST me/exports/` · `createExportTicket` | `scope?:synthetic + ExportIntent` | `201 ExportTicket / 200 {state:no_data}` | 400, 403, 404, 405, 406, 409, 410, 415, 422, 429, 500, 503 |
| `GET me/exports/{item_id}/` · `readExportTicket` | `scope?:synthetic` | `200 ExportTicket` | 400, 403, 404, 405, 406, 409, 410, 500, 503 |
| `GET me/exports/{item_id}/download/` · `downloadExport` | `scope?:synthetic` | `200 text/csv` | 400, 403, 404, 405, 406, 409, 410, 422, 500, 503 |
| `DELETE me/exports/{item_id}/` · `deleteExportTicket` | `scope?:synthetic` | `204 空体` | 400, 403, 404, 405, 406, 409, 410, 500, 503 |

| 操作 | 实现、字段语义、请求限制及调用链证据 | 关联 BG |
|---|---|---|
| `listFavorites` | `scope?,page_size?`(1..100，默认50),`cursor?`。按 owner/domain、created_at DESC,id DESC，token绑定人、域、页长。锚移除409 page_changed，非法 cursor 400。`results,page.{page_size,next_cursor}`，撤权行可仅 id,kind,state=unavailable，非授权目录不填。`accounts/favorites.py:list_items,read_summaries,project_batch`。 | 019、021 |
| `createFavorite` | `{kind:city或station,target_id,product}`，正int64或其十进制字符串。按城市/稳定站点与产品核可见，数据库唯一约束，201/同偏好200/异偏好409 favorite_exists。`accounts/favorites.py:create_item`。 | — |
| `readFavorite` | int64 item_id，scope?。本人、同域，不存在/他人404。本人的已撤权目标200 unavailable，不应误为404。单项读取当前来源/站址期与观测，获权才有 target_id/product/name/link/epochs/results。`accounts/favorites.py:get_item,peek,station_summaries,city_summaries`。 | 020 |
| `patchFavorite` | `{product:正int64或数字串}`，事务锁+当前形态/权利，200同条目，400/403/404/409。偏好更改不改变创建排序。`accounts/favorites.py:change_preference`。 | — |
| `deleteFavorite` | scope?、空正文。锁当前本人及数据域行，支持撤权后清本人占位，204、跨人/重复404。`accounts/favorites.py:delete_item`。 | — |
| `listSavedQueries` | 同 owner/scope、page_size/cursor，created_at DESC,UUID DESC。一致 keyset 页，非全局按 updated/title。返回 `results,page`。`id:UUID,title,created_at,updated_at,intent_revision:int,selection,method,method_version,grain`，目标外键置空后 selection 含 null。当前模型是本人意图，GET 不读观测。`accounts/research_saved.py:projection,saved_queries`、`research_session.py:paged`。 | — |
| `createSavedQuery` | 需 title≤100非空、selection、method（compare/statistics/pearson），可带 method_version/grain。授权检查、锁依赖和会话、构造研究关系，事务存方案和有序目标及获权基线，201/400/403/409/422。无网络重放键。`accounts/research_saved.py:payload,research_spec,save`。 | 022 |
| `readSavedQuery` | UUID,scope?。本人同域404边界。SessionCheck + 个人意图投影200，不提供新的分析结果或 input_version，观测重跑另行授权。`accounts/research_saved.py:saved_query`。 | — |
| `patchSavedQuery` | 要求正整数 intent_revision + title或父意图字段。校验合并后 spec/锁前后依赖和当前行，修订+1、重新保存基线，200，过期409 input_changed，无用途403。`accounts/research_saved.py:saved_query,save`。 | 022 |
| `deleteSavedQuery` | scope?，空正文。 owner/domain + 锁，删方案并由 CASCADE 清目标，204/404。`accounts/research_saved.py:saved_query`、`accounts/models.py:SavedQueryTarget`。 | — |
| `runSavedQuery` | `{}`，owner/domain，重验会话/用途、重建有界 relation、summarize。200 current/data_changed/baseline_unavailable 含 intent_revision + result.input_version。200 target_unavailable（只 state）、method_changed（state+method_version）均无 result。权限403、版本409、不可比422。`accounts/research_saved.py:run_saved_query`。 | — |
| `listExportTickets` | 可选 scope/page_size≤100/cursor，归属+域过滤、created_at desc/id desc。无服务端状态/日期搜索、总数 `results[{id(UUID字符串),kind(detail/aggregate),created_at,expires_at}],page`，日期和状态筛选是已取得批次的本地行为。`research_session.paged/owned`, `research_exports.ticket_metadata` | — |
| `createExportTicket` | kind=detail/aggregate，groups 长1–2，各组带64小写hex input_version。fields 可空或选登记列，必备列永远保留、附加列逐列授权。groups 总≤12000格、5对象、7指标。未传 scope 查询参数时按父意图的数据域。 `research_exports.normalize/columns/prepare/materialize` 实算 CSV，校验用途和版本，200 no_data 或 201 最小票据。≤10000行/4MiB，活票12、每分钟5，429。最长15分钟。申请未提供 request_id，同参数重复申请可产生另一 UUID，响应不确定先核本人票据。`tests/test_s05_private.py:168`, `tests/test_s05_races.py`, `tests/test_s05_cost.py` | 015–018 |
| `readExportTicket` | UUID，scope?。仅 owner/scope 200 同四个元数据字段，跨人/域 404。不会判定当前可下载，也不返回状态、签发来源或父版本。`research_exports.export_ticket/ticket_metadata` | 018 |
| `downloadExport` | UUID，scope?，Accept */*，鉴权、锁用户/会话/依赖/票据，下载重新计算 `materialize`，核格式、权限、输入版、签名、行数、字节及哈希 200 `text/csv; charset=utf-8`、attachment air-quality.csv。失权/失效403、无对象404、版本变更409、保留的过期票410，清理后404。拒绝无附件。`research_exports.download_export`, `tests/test_s05_private.py:406`, `tests/test_s05_races.py` | 017–018 |
| `deleteExportTicket` | UUID，scope?，CSRF，按该 owner/scope 锁行 204 实删并由 ORM 清关联，随后404。`research_exports.export_ticket`, `accounts/migrations/0003_export_ticket.py` | — |

### 采集任务

| Method 和相对 Path · operationId | Query / JSON 请求 | 成功 HTTP 与响应 DTO | 已登记异常 HTTP |
|---|---|---|---|
| `GET admin/catalog/products/` · `listManagementProducts` | `purpose:configure/execute/ledger/quality/operations, scope?:formal/synthetic, product?:int, page_size?:int, cursor?:str, target?:date-time, reason?:manual/review/backfill, start?:date, end?:date` | `200 ManagementCatalogPage` | 400, 403, 405, 406, 429, 500, 503 |
| `GET admin/collection-jobs/` · `listCollectionJobs` | `product?:int, scope?:formal/synthetic, page_size?:int, cursor?:str` | `200 CollectionJobPage` | 400, 403, 405, 406, 409, 429, 500, 503 |
| `GET admin/collection-jobs/{pk}/` · `readCollectionJob` | — | `200 JobControl` | 403, 405, 406, 429, 500, 503 |
| `POST admin/collection-jobs/{pk}/` · `changeCollectionJob` | `SubmittedJobControlCommand/object` | `200 JobAction` | 400, 403, 405, 406, 409, 415, 429, 500, 503 |
| `POST admin/collection-jobs/` · `createCollectionScheduleDraft` | `S08CreateScheduleDraft` | `201 S08ScheduleReceipt` | 400, 403, 405, 406, 409, 415, 429, 500, 503 |
| `GET admin/collection-jobs/{pk}/schedule/` · `readCollectionSchedule` | — | `200 S08SchedulePlan` | 400, 403, 405, 406, 429, 500, 503 |
| `POST admin/collection-jobs/{pk}/schedule/` · `changeCollectionSchedule` | `S08ChangeSchedule` | `200 S08ScheduleReceipt / 201 S08ScheduleReceipt` | 400, 403, 405, 406, 409, 415, 429, 500, 503 |
| `GET admin/collection-windows/` · `listCollectionWindows` | `product:int, scope?:formal/synthetic, job?:int, object_code?:str, indicator?:str, start?:date, end?:date, page_size?:int, cursor?:str` | `200 WindowPage` | 400, 403, 405, 406, 409, 429, 500, 503 |
| `GET admin/collection-windows/{pk}/` · `readCollectionWindow` | `object_code?:str, indicator?:str, after?:int, run?:uuid, run_after?:int` | `200 CollectionWindow` | 400, 403, 405, 406, 500, 503 |
| `GET admin/collection-runs/` · `listCollectionRuns` | `product:int, scope?:formal/synthetic, job?:int, object_code?:str, indicator?:str, start?:date, end?:date, window?:int, page_size?:int, cursor?:str` | `200 CollectionRunPage` | 400, 403, 405, 406, 409, 429, 500, 503 |
| `GET admin/collection-runs/{run_id}/` · `readCollectionRun` | `object_code?:str, indicator?:str, after?:int` | `200 CollectionWindow` | 400, 403, 405, 406, 429, 500, 503 |
| `POST admin/collection-runs/` · `submitCollectionRun` | `SubmittedRunCommand` | `202 AsyncRun` | 400, 403, 405, 406, 409, 415, 429, 500, 503 |
| `POST admin/collection-runs/{run_id}/` · `redeliverCollectionRun` | `SubmittedRedeliveryCommand` | `202 RetryRun` | 400, 403, 405, 406, 409, 415, 429, 500, 503 |
| `POST admin/command-receipts/lookup/` · `lookupSelfCommandReceipt` | `SelfReceiptRequest` | `200 SelfReceipt` | 400, 403, 404, 405, 406, 415, 429, 500, 503 |
| `GET admin/history-imports/` · `listHistoryImports` | `page_size?:int, cursor?:str` | `200 HistoryImportPage` | 400, 403, 405, 406, 429, 500, 503 |
| `POST admin/history-imports/` · `submitHistoryImport` | `{profile:uci501-edges-v1, stage_id:uuid, reason:historical_verification}` | `202 {attempt_id:uuid}` | 400, 403, 405, 406, 415, 429, 500, 503 |
| `GET admin/history-imports/{attempt_id}/` · `readHistoryImport` | — | `200 HistoryAttempt` | 400, 403, 405, 406, 429, 500, 503 |
| `GET admin/object-audit/` · `readObjectAudit` | `kind:job/run/quality_issue/station/grant, id:str, start:date-time, end:date-time, page_size?:int, cursor?:str` | `200 S08ObjectAuditPage` | 400, 403, 405, 406, 429, 500, 503 |

| 操作 | 实现、字段语义、请求限制及调用链证据 | 关联 BG |
|---|---|---|
| `listManagementProducts` | s08_management_catalog._options/_check_product，purpose ledger/quality/operations 需 start/end 源地方日半开 ≤30天，execute 用 UTC 整点 target+reason=manual/review/backfill，configure 走 s08_schedule.template_options。scope/product/cursor，page_size≤50，顶层 next_cursor。用途分别核审阅/执行/配置/诊断，枚举不预约配额。 | — |
| `listCollectionJobs` | s07_admin.list_jobs，scope/product/page_size/cursor→results[{id,product,synthetic,profile,state,links}],page.next_cursor。manager+对应产品 review/retain，按主键逆序。 | — |
| `readCollectionJob` | control.job_diagnostic → job:int、targets.{job,product,source} 各对象 int id/版、状态及原因，resume_after 仅 source。 | — |
| `changeCollectionJob` | control.parse→services.change_job，action/reason_code/request_id UUID/expected_version≥0。锁定源、job、执行身份及授权，乐观版本冲突返回 409，有回执后入 GovernanceAction/ControlEvent。回包差异见 BG-033。 | 033、037 |
| `createCollectionScheduleDraft` | schedule_response/perform create_draft 固定 schedule_create+template:int+cadence_seconds:int+first_due_at:datetime+信封，登记周期/分钟、执行及用途授权，201 返回计划+action_id，停用。 | 028 |
| `readCollectionSchedule` | schedule_response/locate/project，零 query，按明确分配的 configure 能力准入，返回版本/前后继/生效分界/preview{boundary,previous_last_due_at,first_due_at,gap_seconds,conflict}。无 action_id。 | 033 |
| `changeCollectionSchedule` | schedule_response/perform，propose 附周期及首期，activate/discard 附前任版本（可 null），固定 reason/模板及目标版本。提议 201，激活/丢弃 200，审阅接管范围，查冲突及未来账本，锁事务，激活后唤醒扫描器。 | 028 |
| `listCollectionWindows` | s07_admin.list_activity，product 必需，scope,job,object_code,indicator,start,end,cursor,page_size。按窗ID递减，返回 id/product/synthetic/target_start/target_end/full_scope/result/links。无 state，有限授权 result=null，无总数。 | 033、036 |
| `readCollectionWindow` | queries.read_window，after/run_after 为有界 offset，另支持 object_code/indicator/run。返回 state(含部分授权/处置/不适用)、coverage/cells/runs/items/receipts、截断及续页字段。不能直接读 quality_issues，依列表或队列。 | 033、036 |
| `listCollectionRuns` | s07_admin.list_activity 与 Window 共用筛选，另可 window。运行要求整窗可审阅，按 created_at/pk 逆序，结果 id UUID/window:int/status/dispatch/result/created_at/links。 | — |
| `readCollectionRun` | queries.read_run 从 Run 锁到窗口，支持 after/object_code/indicator，计数/运行阶段/重试/批次与事件，manage_collection 且可见运行时增 target.control_version、dispatch、owner_present。Run.status、dispatch、result 分别表示执行、配送和业务。 | 033、036 |
| `submitCollectionRun` | services.submit/register，只收 job:int,reason,target整点,request_id UUID，run_collection 并核执行者及源操作，review 另需 review_observation。PG 唯一窗、Run/请求与事务提交后派发 Celery，202 run_id/window_id/dispatch 或 503 仍附原编号。 | — |
| `redeliverCollectionRun` | services.redeliver，版本化 redeliver/operator_recovery，重用原 Run，以投递条件和预算重新授权后入回执及投递，202 两 ID，见 BG-033。 | 033 |
| `lookupSelfCommandReceipt` | accounts.s08_receipts.lookup 按 family,target（redeliver UUID，其余 int）,command 原信封，查本人并当前复权。404 统一未核实，不新建任务。 | 034–035 |
| `listHistoryImports` | observations.history_status.read，import_history 仅本人、review_observation 可获权审阅，全固定 H 选区仍需 retain/review，按 created_at/UUID 逆序，page_size/cursor→results/page。 | — |
| `submitHistoryImport` | observations.history.submit、publish、tasks.process_history→consume，stage_id UUID+uci501-edges-v1+historical_verification。锁定自己的未过期 ready，创建 ImportAttempt，独立派发，202 attempt_id 或503 dispatch_unconfirmed+attempt_id。staging 从主机 s02_stage_uci，无浏览器上传，见 BG-035。 | 035 |
| `readHistoryImport` | history_status.project，id,state,dispatch,created_at,started_at,finished_at,links。ImportAttempt 仅在结算时填写开始/结束，不能用 pending 推断尚未执行。 | — |
| `readObjectAudit` | accounts.s08_object_audit.read/selection 和 scope.authorize/load_target，kind,id,start/end UTC 瞬间≤30日，可 cursor、page_size≤50（默认25）。回 at/action/outcome/actor_relation(self/other/system)/前后版，没有人员名。权限、对象、会话及源许可在返回前复查。 | — |

### 质量事件

| Method 和相对 Path · operationId | Query / JSON 请求 | 成功 HTTP 与响应 DTO | 已登记异常 HTTP |
|---|---|---|---|
| `GET admin/quality-issues/` · `listQualityIssues` | `product:int, scope?:formal/synthetic, run?:uuid, window?:int, status?:open/in_review/resolved, before?:int` | `200 {results:QualityIssue[], next_before:int/null}` | 400, 403, 404, 405, 406, 409, 500, 503 |
| `GET admin/quality-issues/{event_id}/` · `readQualityIssue` | `mapping_target?:str` | `200 QualityIssue` | 400, 403, 404, 405, 406, 409, 500, 503 |
| `GET admin/observation-evidence/` · `readObservationEvidence` | `product:int, scope?:synthetic, city_key?:int, station_key?:int, batch?:int, after?:int` | `200 {attribution:str, synthetic:bool, results?:object[], next_after?:int/null}` | 400, 403, 405, 406, 500, 503 |
| `POST admin/quality-issues/{event_id}/actions/` · `adjudicateQualityIssue` | `SubmittedQualityCommand` | `200 {action_id:uuid, status?:str, version?:int}` | 400, 403, 404, 405, 406, 409, 415, 500, 503 |

| 操作 | 实现、字段语义、请求限制及调用链证据 | 关联 BG |
|---|---|---|
| `listQualityIssues` | quality_urls.issues→quality_governance.list_issues，product:int、scope、run UUID/window int/status open,in_review,resolved/before 正整数，按可见 ID 逆序每50，results/next_before，无日期或总数。 | — |
| `readQualityIssue` | quality_governance.act/event_result/project_refs。审阅+retain，keyed 才有 current_revision/candidate_revisions(IDs)、disputed、evidence 字典，所有事件有 handler:int或null、event_version、affected、关联 batch/receipt/run/window。可提 mapping_target，GET 无写入。未返回 links 或观测键。OpenAPI QualityIssue 仅 id 必填，列 version/evidence_ref，实际是 event_version/evidence。 | 029–032、041 |
| `readObservationEvidence` | observations.views.evidence→queries.read_evidence，要 product/scope+city_key 或 station_key 或 batch 三选一、after 正整数，修订/收据递增投影，归属、保留、原值与定位权限分开。batch 行有 receipt_id/key_id/revision，按 key 的 receipts 无自身 receipt_id，最多100。避免把两分支当相同 DTO。 | 031、041 |
| `adjudicateQualityIssue` | quality_governance.act，action/reason/expected_version/request_id/evidence_ref。更正需要有效 30min 签名，版本/候选指纹对齐，非更正证据为 null。锁源产品、键、事件、账户，核审和裁决权。写 QualityDecision/CandidateClearance、资格版本、Change、原始回执，映射冲突保持 in_review，普通 close 不改 key.disputed。重复及对象差异见 BG-029–034。 | 029–032、034、037、041 |

### 系统运行

| Method 和相对 Path · operationId | Query / JSON 请求 | 成功 HTTP 与响应 DTO | 已登记异常 HTTP |
|---|---|---|---|
| `GET admin/operations/health/` · `readOperationsHealth` | `window?:int, object_code?:str, indicator?:str, include?:first_valid_write` | `200 Health` | 400, 403, 404, 405, 406, 409, 500, 503 |
| `GET admin/operations/trends/` · `readOperationsTrend` | `product:int, scope?:formal/synthetic, job?:int, object_code?:str, indicator?:str, start:date, end:date` | `200 AdminTrends` | 400, 403, 405, 406, 429, 500, 503 |

| 操作 | 实现、字段语义、请求限制及调用链证据 | 关联 BG |
|---|---|---|
| `readOperationsHealth` | 无参。或 window数串≤18位、object_code?,indicator?,include=first_valid_write?。diagnose_operations。有窗口须 review 且按当前许可裁剪。组件 proof 有 state、observed_at、evidence_kind、valid_until、reason_code。worker.queue_readiness 与 last_consumption 分开。window可null。获权窗口覆盖/可见分档与截断。首写三态 known/none/unavailable，单独 page_truncated，实际返回的是写入时间。Redis 探测失败时返回组件 unavailable，PG 中断整体503。`accounts/operations.py:health,pulse,window_state,window_evidence`、`operations_first_write.py:FirstWrite`、`collection/queries.py:read_window`。 | 038–039 |
| `readOperationsTrend` | 必需 product,start,end。scope默认formal，可 job/object_code/indicator。≤30源地方日半开窗，全空获权日也出桶。diagnose_operations+review_observation+时间范围 review/retain_normalized。每桶用 window.target_end - 1µs，返回 `product:int,synthetic:bool,start,end,evaluated_at,bucket_timezone,window_anchor,buckets[]`。部分授权窗不贡献全窗尝试时长。coverage 有 E/O/V、unknown_E_windows、valid分子/分母/ratio，execution含时长和样本。预算超限400 query_too_large、无权403、频控429、503。`collection/s07_urls.py`→`views.py:trends,workbench`→`s07_admin.py:trends,bucket,terminal_attempts`。 | 038–039 |

### 契约内未调用

| Method 和相对 Path · operationId | Query / JSON 请求 | 成功 HTTP 与响应 DTO | 已登记异常 HTTP |
|---|---|---|---|
| `POST admin/fixture-runs/` · `submitFixtureRun` | `{fixture_key:co-hour-v1, reason:local_verification}` | `202 {run_id:uuid}` | 400, 403, 405, 406, 415, 429, 500, 503 |
| `GET fixtures/observations/` · `readLegacyFixture` | `product:F, observed_at:date-time` | `200 {filters:object, count:int, results:object[], reason:str/null}` | 400, 403, 405, 406, 500, 503 |
| `GET admin/stations/{station_id}/changes/` · `prepareStationChange` | `product:int, action:rename/relocate/reuse_code, identifier:int, at:date-time, target:str` | `200 {evidence_ref:str, version?:int}` | 400, 403, 404, 405, 406, 409, 500, 503 |
| `POST admin/stations/{station_id}/changes/` · `applyStationChange` | `SubmittedStationCommand` | `200 {action_id:uuid, version?:int}` | 400, 403, 404, 405, 406, 409, 415, 500, 503 |
| `GET admin/disposition-products/{product_id}/` · `previewProductDisposition` | — | `200 {product:int, version:int, jobs:object[], basis:str}` | 400, 403, 404, 405, 406, 409, 500, 503 |
| `POST admin/disposition-products/{product_id}/` · `prepareProductDisposition` | `{action:prepare_disposal, reason_code:due_reconciliation, request_id:uuid, expected_version:int, basis:str}` | `200 {operation_id:uuid, expires_at:date-time}` | 400, 403, 404, 405, 406, 409, 415, 500, 503 |
| `GET admin/grants/{grant_id}/actions/` · `readGrantControl` | — | `200 {grant:int, product:int, version:int, state:registered/withdrawn}` | 400, 403, 404, 405, 406, 409, 500, 503 |
| `POST admin/grants/{grant_id}/actions/` · `withdrawGrant` | `SubmittedWithdrawalCommand` | `200 {action_id:uuid, version:int, state:withdrawn}` | 400, 403, 404, 405, 406, 409, 415, 500, 503 |
| `GET admin/accounts/{pk}/actions/` · `readManagedAccount` | — | `200 AccountState` | 400, 403, 404, 405, 406, 500, 503 |
| `POST admin/accounts/{pk}/actions/` · `changeManagedAccount` | `账号状态、权限或角色命令（三选一）` | `200 AccountAction` | 400, 403, 404, 405, 406, 409, 415, 500, 503 |
| `POST admin/backup-products/{product_id}/segments/` · `prepareBackupSegment` | `{scope:object, raw?:bool}` | `201 {operation_id:uuid, state:str}` | 400, 403, 404, 405, 406, 409, 415, 500, 503 |
| `POST admin/backup-segments/{segment_id}/publish/` · `publishBackupSegment` | `{}` | `200 {operation_id:uuid, state:str}` | 400, 403, 404, 405, 406, 409, 415, 500, 503 |
| `POST admin/backup-segments/{segment_id}/restore-intents/` · `createRestoreIntent` | `{target:uuid}` | `201 {operation_id:uuid, expires_at:date-time}` | 400, 403, 404, 405, 406, 409, 415, 500, 503 |
| `GET admin/backup-authority/{action}/{operation_id}/` · `readAuthorityMaterial` | — | `200 {action:publish/restore, operation:uuid, current:object, manifest:object, target:uuid/null, expiry:date-time, observed_at:date-time}` | 400, 403, 404, 405, 406, 409, 500, 503 |

| 操作 | 实现、字段语义、请求限制及调用链证据 | 关联 BG |
|---|---|---|
| `submitFixtureRun` | `samples/views.fixture_runs` → `services.submit/consume`。执行人及 F 处理/保留用途通过后创建 FixtureRun，再派发本地夹具任务。202 仅确认受理，投递不确定可带 run_id 返回 503。 | 040 |
| `readLegacyFixture` | `samples/queries.query_observation` 独立核 display 和精确时刻，只查指定 F、CO、有效行，给 filters/count/results/reason。 | 040 |
| `prepareStationChange` | catalog_governance.propose/registered/station_change，product,action(rename/relocate/reuse_code),identifier,at,target→version/affected/evidence_ref。受准入 F 限制。 | 040 |
| `applyStationChange` | 相同处理程序要求版本、request_id、相应 reason_code、evidence_ref。锁源/产品/站点，拒跨时间窗重绑，更新时态 identity/epoch 与影响记录。审阅+manage_catalog+来源范围。 | 040 |
| `previewProductDisposition` | disposition_control.preview，manage_grants，输出 product/version/jobs/basis，basis 绑定当前到期对象。 | 040 |
| `prepareProductDisposition` | 同服务 prepare_disposal/due_reconciliation，信封+basis，回 operation_id/expires_at。s06_dispose/execute 主机后续核验执行。Web 写成功只证明受理。 | 040 |
| `readGrantControl` | disposition_control.withdraw→grant/product/version/state registered或withdrawn，需 manage_grants。 | 040 |
| `withdrawGrant` | withdraw+terms_ended/retention_withdrawn/rights_review、信封，核 product.rights_version，revoke_grant 触发授权及处置，回 action_id/version/state，重复核原身份。 | 040 |
| `readManagedAccount` | `accounts/management_api.accounts` → `governance.account_action/subordinate`。重新校验本人和目标层级，给 account、active、version，跨权限拒绝。 | 040 |
| `changeManagedAccount` | 同一服务。enable/disable/grant/revoke/grant_role/revoke_role 发送 reason_code、request_id、expected_version，按动作附 permission 或 role。锁人和组，治理回执与修改同事务，禁用推进代际。 | 040 |
| `prepareBackupSegment` | `observations/backup_urls.py` → `backup.prepare`，scope 含 geography、object_code、indicator、start/end。产品及保管权控制准备范围，返回待处理 operation_id/state。 | 040 |
| `publishBackupSegment` | `observations/backup_urls.py` → `backup.publish`，检查段状态、当前权利及保管材料，回传 operation_id/state。 | 040 |
| `createRestoreIntent` | `observations/backup_restore.prepare_restore` 对当前目标、权限和段重新核查，创建限期意图。后续由保管人执行实际恢复。 | 040 |
| `readAuthorityMaterial` | `observations/backup_authority.verify_current` 复核 publish/restore、身份、当期权利及过期条件，给签认所需 current/manifest/target/expiry 等字段。 | 040 |

## 后端独立交付证据

2026-11-27 的后端独立证据已整理到 [`docs/verification/backend-independent-v1.0/`](../verification/backend-independent-v1.0/)。索引保存综合回归 690 PASS、0 FAIL、38 skipped，专用成本/恢复/进程/采集/升级/Beat 收据，百万 F 性能摘要、CSV 边界摘要及跨版兼容票据。运行环境为隔离 PostgreSQL、Redis、Worker、单 Beat、F 夹具、自制双域权限样本和注入故障；正式来源和私有数据未进入仓库。完整日志、数据库、凭据和私有载荷不纳入版本库。

最终接口交接沿用 `v1-phase08`。旧普通观测游标返回 `400/invalid_cursor` 时，调用方保留查询条件并从第一页重读。仍获权的旧 CSV 票据返回 `409/method_changed` 时，调用方按当前父组、字段权和格式重新申请。八组目标契约及前端消费要求见 [`docs/api/v1.md`](../api/v1.md) 与共享冻结 §6.1–6.8。浏览器宿主、同源会话、权限交接和用户观察/管理治理/研究分析三条页面链仍待正式联调。

## Phase 02 联合冻结处置

2026-11-27。上方原审计附件（90,315 字节）的历史指纹为 `662f1326006ef2e49fe41854fa84952ee9d8d4414a1605c8dec51be5c8919676`。本版补充入口并校正移动后的导航，原发现和编号继续保留。下表记录产品确认及两库直接核对后的处置。「验收标准」保留冻结时的要求，最后一栏记录后端独立整改结果。真实联调和授权样本验证留至 Phase 09。

每个编号按主要交付点计一次。Phase 02 文档订正 3 项，Phase 03 公共层 2 项，Phase 04 页面消费 13 项，Phase 08 后端集中整改 13 项，Phase 09 集成与交接 3 项，独立专项 9 项，共 43 项。其他阶段可承担同项的配合和复验。Phase 05 回归前端，06 双向完整性审计，07 前端阶段性封版，10 最终验收。

“契约/前置”栏中的 §6 指[共享接口基线](CONTRACT_BASELINE.md)第 6 节的冻结目标。标明“按切片”的编号，只有在精修期间交付相应**真实能力**时才前移所列后端任务，其他编号均无需提前实现后端业务。Phase 03 可直接启动。B 号沿用前端原清单，BG-006/009/011 是前端表中直接使用 BG 的条目，B03/05/06 的纯前端问题仍独立保留。[前端清单](../../../air-quality-frontend/docs/FRONTEND_GAPS.md) · [整改计划](BACKEND_REMEDIATION_PLAN.md) · [正式决策](PRODUCT_DECISIONS.md)

| 编号 | 前端关联 | 最终处置与责任 | 源码证据与工作内容 | 主交付点、依赖及复验 | 契约/前置 | 验收标准 | 本轮后端状态、代码/验证与剩余门槛 |
|---|---|---|---|---|---|---|---|
| BG-001 | B01/B02 | 前端缺口，接现行同刻列表 | `s04_national._selection/_read_national` 已解析检索、标准、排序、聚焦和游标。前端报告定位 `src/api/national.ts` 固定首批以及 F 用名称选城。接稳定城市 ID、原条件续批和获权链接。 | **04** · 03 稳定 ID；04 续页；09 同源 | 现行 | 真 L 同刻两批能检索、续批和下钻，变筛选清游标，未知标准保留城市序，已核标准下并列名次正确。 | 现行后端；`s04_national` 同刻列表，`test_s08_national_home`；基本首页与浏览器下钻待前端验。 |
| BG-002 | B01/B02 | 双方及素材权利方，正式地图缺投影和许可 | `s04_national._read_national` 在请求地图时只给 `unavailable/[]/false`。前端正式页相同，F 地图保留。需城市位置、稳定 ID、坐标系、版本及可分发底图。 | **专项** · 依 027；08 或独立实现；09 地图 | PD-03/04 | 有许可的同刻城市落位可追溯，部分可见及撤权正确裁剪。缺素材时正式列表仍作为 S09-01 基本路径。 | 外部专项；正式点位、底图权利与 CRS 证据待齐；本轮无正式地图取证。 |
| BG-003 | B01 | 双方及数据权利方，正式全国趋势缺时序 | `s04_national._read_national` 是 L 单快照，`s04_city.read_city_trend` 是单城 T。PD-04 选用同产品同系列每时获权、有效、可比城市，逐槽披露实际分母和统计范围。 | **专项** · 依 004/027；08 或独立实现；09 趋势 | PD-04 | 检查缺槽、未知标准、部分授权、分母变化，F 与正式趋势可辨，S09-01 列表可单独通过。 | 外部专项；逐小时多时刻 L 发布权与可比分母证据待齐；全国趋势待验。 |
| BG-004 | B07 | 前端文案错误，说明公开、展示按权 | 前端报告定位 `src/public/sourceCatalog.ts` 混用“公开”和行政目录。`s04_national._roster` 只对获权名录得出可证的期望量，L 的 `*Level` 是等级。明确公开说明的范围与数据用途。 | **04** · 04 修改 PUB-02；09 核当期数据 | PD-03 | 游客可读说明。仅展示获权的同批 AQI 与等级，未知分母可辨，浓度、IAQI 和分发另有依据。 | 前端消费；L 等级、C 浓度和公开说明沿现行授权边界；页面与署名待验。 |
| BG-005 | B08 | 后端补仅 S 权的合法发现，前端沿返回链接读取 | `discovery.candidates/entry` 从已有观测发现站址，`s04_station.read_station_directory` 已可按产品、城市读。按获权目录范围发现同城 S，不从已有观测倒推。 | **08** · 04 现有链接；08 服务；09 空目录/撤权 | §6 目录；按切片 2.1 | 仅 S 权且无近时观测仍可到合法目录，空目录与受限可区分，撤权后入口和旧结果收回。 | 后端提前交付；`discovery.station_directories`，`test_s07_catalog` 实验仅 S 目录权、无观测、空目录与撤权。页面跟链待验。 |
| BG-006 | BG-006 | 前端请求错误，后端接口已支持 | `s04_station.read_station_directory` 把 `requested_at` 放入条件，响应 `at` 是锚点。前端报告对 `useCityDetail.ts` 复现首批省略、续批带响应时刻。分开保存请求条件和展示锚点。 | **04** · 04 原请求时刻；05/09 回归 | 现行目录游标 | 省略和显式 `at` 两条链均能续批。改变原条件配旧 token 返回 `400/invalid_cursor`，主界面改条件回首批。 | 前端消费；`s04_station.read_station_directory`，`test_s04_station`；原请求 at 在页面跨页待验。 |
| BG-007 | B08/B17 | 前端可接现行精确读，权利方提供正式 D | `discovery.entry` 可投影 `local_report_date` 精确链接，`queries.read_current` 处理日报。M06 已接，M02 卡可直接转已有查询页。 | **专项** · 依 010/027；04 有权时接入；09 地方日报 | PD-05，§6 精确读 | 有权显示地方日与署名，合法无报保留空分支，失权收回，小时均值始终单列。 | 外部专项；`queries.read_current` 与 `test_s07_exact` 验 F 地方日报合法空日；正式 D 用途、样本及页面待验。 |
| BG-008 | B08/B09 | 前端接已有时效，双方延期增强目录及研究 | `s04_station._catalog_page` 分页裁剪，`projection.public_rows` 可给 `age_seconds`。前端报告区分 F 全夹具与正式当前批。全量站型过滤、时效阈值、覆盖日历另定依据。 | **04** · 04 接已披露字段；增强依 027/042 独立验 | PD-05 | 当前批与全量标明，缺年龄或阈值显示未知，有时标能解释年龄。新增能力以获权站型、日历、时区和分母独立验收。 | 前端消费；`s04_station`、`projection` 提供可见目录与时间。站型与覆盖日历依 PD-05。 |
| BG-009 | B09/BG-009 | 双方缺口，后端稳页、前端整次丢页 | `detail.read_detail` 签条件、页长和末键，`latest_valid` 固定锚点，但非末行更正可漏检。前端 M03 多指标历史和 M06 不能据 `evaluated_at` 推断同版。按签名游标核可见结果，变化返 `409/page_changed`。 | **08** · 04 仅承诺已读批；08 服务；09 并发 | §6 分页；按切片 2.1 | 两页间修订非末行、撤销部分字段令旧页失效并清屏重读。仅隐藏行变化不泄漏，跨会话/域游标拒绝，研究与精确版本各自独立。 | 后端提前交付；`detail.read_detail`，`test_s04_selection`、`test_s04_noninterference` 验更正锁交错、插入、隐藏行和撤权。清页待联调。 |
| BG-010 | BG-011 | 后端非法选择器误空，前端保留错误与空态 | `queries.read_current` 校验产品指标与全局窗/报告种类，遗漏部分产品/标准组合。客户端已有限制，直接深链仍能触发。区分非法输入、合法未报和无权。 | **08** · 04 本地提示；08 服务；依 027 验 D | §6 精确读；按切片 2.1 | 获权对象 C+日报、D+小时、错误标准拒绝 `400/invalid_request`，合法 D 空日返回 `200/count=0`，无权请求保留原授权边界。 | 后端提前交付；`normalization.valid_selector`、`queries.read_current`，`test_s07_exact`；正式报告专项依 BG-027。 |
| BG-011 | BG-011 | 前端模型与说明，未证实正式页面误链 | `queries.project_revision` 发 `revision_id`，普通 `projection.public_rows` 分逻辑键和修订。前端报告证实精确证据面板显示逻辑键未披露。分离 UI 行标识、逻辑键、修订号和精确读版本。 | **04** · 04 分离标识；证据链接依 031/041 | 现行精确读 | 同一逻辑观测两版显示不同修订，未披露键时没有治理跳转，精确读版本不进入研究父版本参数。 | 前端消费；`queries.read_current`、`test_s07_exact` 区分投影版本与修订 PK；页面绑定待验。 |
| BG-012 | B10 | 双方缺口，图点缺可核的支撑范围 | `research_statistics.chart` 的 exact 是格槽，range 是共有时间轴的压缩段。`research_api.drilldown` 已接受半开桶，统计/热图另有描述。按时间猜附近统计桶无法覆盖压缩末段。 | **08** · 04 只用明确下钻；08 新投影；09 验父版 | §6 图点；按切片 2.2 | exact 单槽、range 尾段截在父窗 end、不同列及缺槽分别核明细，父版变化重读，缺目标/权限时关闭该入口。 | 后端提前交付；`research_statistics.chart`、`test_s05_statistics` 验精确微秒、列号、DST 及压缩尾桶。图点点击待联调。 |
| BG-013 | B10 | 双方缺口，后端提供同父版同口径真实配对 | `research_statistics.paired_summary` 的 compare `paired_points` 只有差值/变幅。`analysis_query.detail_rows` 已可读逐列 `paired` 行，`paired` 与 `expected_common` 条件不同，跨页可能拆对。服务投影稳定槽、固定两列的真实配对，完整 P 与截断数量分别披露。 | **08** · 04 系数/P 和 F 散点；08 服务；09 统计核对 | §6 Pearson；按切片 2.2 | 用同父版真实 x/y 对照共同分母，未知日历下区分实际配对及入算集合。跨页无错配，P<30/零方差原因明确，截断不改变服务系数，撤权清除。 | 后端提前交付；`research_statistics.paired_summary`、`research_api`，`test_s05_statistics` 核同版 P、x/y、预算与裁剪。绘图待联调。 |
| BG-014 | B10 | 已有部分能力，前端按权接入，其余增强单验 | `research_statistics.paired_summary/statistics_buckets/anomalies` 已计算有条件排行、部分子窗、须与候选。前端报告证实已有均值、四分位、月季与热图。优先补未呈现且有投影者。 | **04** · 依 041；04 用实装投影；09 核统计 | 现行研究 | 各站 18/24、共同 12/24 时无全天排名，部分窗单标。零 IQR、缺月、无明细权不虚构异常点或补齐缺段。 | 前端消费；`research_statistics`、`test_s05_statistics` 核共同覆盖、方差及异常分支。页面呈现待验。 |
| BG-015 | B11 | 仅文档枚举错误，映射已订正 | `research_exports.normalize` 和 `ExportIntent.kind` 接受 `detail/aggregate`。前端报告有实际注入请求 `aggregate`，新版映射已订正旧 `summary`。 | **02** · 02 已修，05/09 看实际请求 | 现行导出枚举 | 断言正式请求体和 `summary` 拒绝，`200/no_data` 无票，获权有数据分别签两种票。 | 规格订正已落实；`research_exports.normalize`、`test_s05_private` 核两种 kind 及无票分支。 |
| BG-016 | B11 | 前端确认窗缺口，受控默认列，附加选择延期 | 前端报告定位 `src/query/detailExport.ts` 单组、`quality=reliable`、先取 `support=observed` 版本，再 `fields:[]`。`research_exports.columns/data_rows` 仍含必备列及获权的 observed 行。按将要签票的规范组确认。 | **04** · 04 展示规范父组、必备列；08/09 核新版 | PD-06，§6 CSV | 核申请前展示的产品、来源、时区、窗口、质量及列与请求和 CSV 一致，含非 valid 行时标明。签票/下载各自复权，测试空数据。 | 前端确认窗待验；`research_exports`、`test_s05_private` 核父组、版本、实际 CSV；附加列按 PD-06 延期。 |
| BG-017 | B11 | 后端文件协议缺口，前端分开验两域附件 | `research_exports.COMMON_FIELDS/materialize` 未强制域标识，固定文件名和署名均不足以辨域。F 本地 CSV 有署名提示。新版 `data_scope=formal/synthetic` 为必备列。旧票安全失效或到期后重新申请。 | **08** · 依 016；08 新格式；09 核字节 | §6 CSV；按切片 2.3 | 中性来源名的正式及 F、detail 及 aggregate 四种文件脱离页面仍可识别。旧格式到期或安全失效后重申，新版附件核字节与摘要，撤权禁下载。 | 后端提前交付；`research_exports`、`research_access`、`test_s05_private`/`test_s05_races` 验两域两类的字节、权限、行数、摘要及旧格式处置。页面待联调。 |
| BG-018 | B12 | 前端沿现行入口，持久来源反查延期 | `research_exports.ticket_metadata` 仅 `id/kind/created_at/expires_at`。前端会话内来源丢失后可按 kind 回申请入口。下载独立裁决，需持久来源时再定披露和期限。 | **04** · 04 来源未知走通用申请入口；09 两会话 | PD-07 | 两会话分别读票、复访、下载，核过期、清理后 404、撤权。元数据成功不提示保证可下载。 | 前端消费；`test_s05_private` 验所有权与失权下载；无来源回通用页待前端验，长期来源反查延期。 |
| BG-019 | B12 | 仅规格错误，后端修收藏顺序及裁剪描述 | `favorites.list_items` 按 `-created_at,-pk`，`project_batch` 裁剪 `link/results/epochs`。前端依服务游标，按名可重排当批。修 OpenAPI 升序描述和裁剪联合类型。 | **02** · 现行顺序与裁剪规格已订正，09 对照同时刻双页 | 现行收藏 | 同创建时间跨两页按 ID 降序，锚失效冲突，撤权最小形状可解码，前端不推全量顺序或总数。 | 规格订正已落实；收藏分页顺序、可裁剪投影见 OpenAPI 与 `test_s04_favorites`；页面待联调。 |
| BG-020 | B12 | 前端拒绝后清理缺口，映射已澄清 | `favorites.get_item/read_summaries` 区分他人记录和本人记录的目标。前端报告证实 `200/unavailable` 已裁剪，首选产品动作遭拒后未统一刷新。 | **04** · 04 两入口清理；09 双会话撤权 | 现行收藏 | 他人/异域 404，本人目标失权 200 仅 id/kind/state，保留删除 204；从首选产品和普通复访两入口拒绝后均清旧摘要。 | 前端消费；`research_saved`、`test_s05_private` 核本人重跑与版本分支；页面清理待验。 |
| BG-021 | B12 | 前端保留待核对；任意目标直查为延期增强 | `favorites.list_items` 无按目标直查，前端只核三批并提供“添加或核对”。保留未知，若要任意数量即时确定，按本人、域、稳定目标并核权。 | **04** · 04 未知态；09 跨人/域；增量另验 | PD-07 | 第 301 项不误报未收藏，重复同偏好请求复用既有记录，异偏好返回冲突，跨人/域不可发现。 | 前端消费；`favorites` 与 `test_s04_favorites` 核本人分页；超出已读范围显示待核对，精准反查延期。 |
| BG-022 | B12 | 仅规格错误，后端收窄保存方法 | `research_saved.research_spec` 拒绝保存 `detail`，前端保存已限制为三种分析。应收窄 `SaveQuery/PatchQuery`，保留研究明细父意图使用 `detail`。 | **02** · 保存方法规格已订正，09 校验实际方法 | 现行保存三方法 | 新建/编辑的 statistics、compare、pearson 按既有权限和修订处理，detail 保存拒绝，研究明细仍可请求。 | 规格订正已落实；`research_saved`、`test_s05_private`、OpenAPI 约束三种可保存方法；前端待联调。 |
| BG-023 | B04 | 现行角色模型成立，前端按目标具名权回跳 | `profile.capabilities` 对有效会话始于 `[user]`，有管理动作再加 manage。仅 manage/空工作区来自前端注入。按具名权限分流。仅管理或空工作区作为注入边界。 | **03** · 03 守卫和登录；09 会话，注入另验 | PD-01 | 覆盖无 next、获权/未获权 M08–10 深链、会话失效与公共兜底。注入角色和真实可产生账户分列。 | 前端公共层；`accounts/profile.py`、`test_s08_capabilities`，按具名动作投影；回跳和撤权清理待验。 |
| BG-024 | B04/B16 | 现行权限分工成立，前端守卫与治理方分别验权 | `profile.CAPABILITY_REQUIREMENTS` 对导航 `manage_accounts` 加 staff，`governance.account_action` 对 REST 检查具名权限和目标范围。诊断为独立权限。原生 Admin 与 REST 仍各自逐目标裁决。 | **03** · 03 导航；09 HTML 与 REST 交接 | PD-02/11 | 非 staff 具名权、仅 staff、可/不可管理目标及仅诊断角色分测 HTML/REST/SPA，不能从拥有一项权限推导另一项。 | 前端公共层；`restricted_admin`、`test_s06_accounts` 与 `test_s08_capabilities`；原生 Admin 和页面守卫待同版验。 |
| BG-025 | B17（B05 另案） | 集成取证，宿主交付方及前端负责 | `config/urls.py` 无前端 SPA 回退。前端报告核 Vite GET/HEAD 回退和 Vue 守卫。裸 Django 和开发服务器只证明各自行为。 | **09** · 08 同版产物；09 实际首达和刷新 | 现行路由 | 浏览器首达/刷新根、受保护深链、三旧址及未知 `/app`，记录实际 HTTP 状态、页面与回跳。API、Admin、探针各自按现有协议。 | 正式联调；同版 SPA 和 Django 宿主、首达/刷新/回跳待集成方取证。 |
| BG-026 | B17 | 集成取证，双方与隔离服务负责人 | 本库是 Django/Celery 实现，前端报告的 62 项是浏览器与注入收据。真实会话、PG 提交、异步投递和 CSV 字节需两端同版验证。 | **09** · 依 025、相关 08 交付；09 三链 | 集成环境 | 在本地 PostgreSQL、Redis、Worker、单 Beat 与同源浏览器跑观察/治理/研究三链。留两会话撤权、原回执、同窗并发唯一、真实下载字节及各自版本。 | 正式联调；后端隔离运行结果见整改计划，完整三条页面链待同版 SPA。 |
| BG-027 | B07/B08/B09/B17 | 数据/素材权利方及集成方提供正式证据 | `queries.read_current`、`import_uci.py`、`collection/adapters/cnemc.py` 分别证实读法、H 导入及适配。正式 D、同集同城 H 双站、位置/底图、在线运行分别需要用途、有效期与当期样本。 | **专项** · 分别支撑 002/003/007/029/043；09 按授权验 | 当期许可 | 分项登记授权对象、许可操作、时区/标准/位置及验证批次。D、H、L/C/S/T 在线、地图各留收据，缺哪项即该项待验。 | 外部专项；正式 D/H、地图与在线准入分别等待权利和正式样本，本轮仅验证隔离数据。 |
| BG-028 | B13 | 后端缺独建/首挂，双方保留两项独立交付 | `s08_schedule.fresh_draft/locate` 依登记模板复制停用草案，主机注册与 f-ui 两条模拟路径均不是正式 SPA 独立创建/原 Job 首挂。S09-02 可从已有获权 Job 或登记模板开始。 | **专项** · 04 已有 Job/登记模板；08 或后续独立验 | PD-08 | 基本链用获权 Job/模板验。扩展单独验停用创建、首挂保持原 Job、重复/并发不新建第二项，激活逐项核准。 | 延期专项；既有 Job 与模板路径保留，独立建 Job、原 Job 首挂需各自的写契约与专项验收。 |
| BG-029 | B14 | 后端与正式来源签发责任方，有证 A，否则 B | `quality_governance.correction_evidence/mapping_candidate` 和 `catalog_governance.registered` 限登记的 F 规则。正式来源应按版本证据签发，绑定当前候选及授权范围。 | **专项** · 依 027/030/031；08 已准规则；09 分源验 | PD-09，§6 资格和回执 | 正式有据样本能签发、核验、推进版本，只影响相关组。过期、撤权、无据或错对象拒绝；F 演练单独留证。 缺合法证据或签发责任时验获权审阅、隔离和 F 演练。 | 正式来源专项；`quality_governance.correction_evidence` 与 `test_s06_quality` 在 F 域验证。正式签证须逐源确认责任、候选与核准依据。 |
| BG-030 | B14 | 双方缺口，后端逐事件资格、前端消费 | `quality_governance.act` 已处理 claim/hold/close 等；`event_result` 只有 handler ID，前端资料无法可靠推本人，当前 UI 偏向 open。按事件版本返回 allowed_actions 及证据要求。 | **08** · 依 032/037；04 受限态；08 投影；09 双人 | §6 资格；按切片 2.5 | 两名审阅者测 open、本人/他人 in_review、resolved、只读、过期证据。资格与 POST 同规则裁决，页间失权或版本竞争仍能拒绝。 | 后端提前交付；`quality_governance`、`test_s06_quality` 验逐事件权限、状态、理由、两人竞争。前端消费待验。 |
| BG-031 | B14 | 双方缺口，后端提供获权键或批次链接 | `quality_governance.project_refs/event_result` 有修订引用与部分 `run/window/batch`，没有可通用作 `queries.read_evidence` 入口的观测键。按获权城市键、站址期键及批次定位提供 evidence_links。 | **08** · 依 029/032/041；08 投影；09 撤权 | §6 证据；按切片 2.5 | 城市/站点有键无批次、仅结构事件、无原值许可和随后撤权均核详情及跳转。修订 ID 不用作观测键。 | 后端提前交付；`quality_governance.evidence_links`、`queries.read_evidence` 与 `test_s06_quality` 验获权键/批次入口，正式证据依 PD-09。 |
| BG-032 | B04/B14 | 后端资源级隐匿缺口，前端拒绝后清理 | `quality_governance.act/checks` 与 `management_api.rejection` 区分对象缺失 404 与部分现存对象拒绝 403。前端合并反馈覆盖不到 HTTP 差别。有入口权时，不可见与不存在统一 `404/not_found`。 | **08** · 03/04 清理；08 读写隐匿；09 双人 | §6 隐匿；按切片 2.5 | 对缺对象和隐匿对象比较读、写及关联的响应状态/体，旧证据清空。区分未认证、CSRF、全局权限，获权版本冲突仍可恢复。 | 后端提前交付；`quality_urls`、`quality_governance` 和 `test_s06_quality` 对照隐藏与缺失对象的读写状态/体；浏览器清理待验。 |
| BG-033 | B13 | 前端先对齐已实现读回与资格，后端补规格 | `control.job_result`、`s08_schedule.project`、`views.recover` 分别投影不同结果；`services.redeliver` 要求含 pending、unsent、manual 等条件。前端报告证实已经读回。 | **04** · 04 复用原 Job/Run；08 余项；09 对账 | 现行读写分支 | 暂停、源恢复读回共享目标的状态/版本；计划 GET 允许没有写回执，重投返回原 Run/Window，不能仅凭 pending 重投。缺关联与已知空集合分别呈现。 | 前端消费；`s07_admin`、`s08_receipts`、OpenAPI 的 Window/Run/计划/回执 DTO 及 `test_s07_contract`。页面读回待验。 |
| BG-034 | B14 | 双方缺口，后端按原回执投影当前可见影响 | `quality_governance.act/result` 首次写有 `outcome/affected`，重放仅 action_id/status/event_version；`s08_receipts.quality` 查本人原回执也无前后影响组。按本人原回执和当期权利恢复可核对的 impact。 | **08** · 依 029/032；04 影响待核；08 实现；09 丢首包 | §6 裁决查证；按切片 2.6 | 首包丢失后原信封定位同一动作，跨键新旧受影响组失效、无关组保持，随后撤权不返敏感详情。查无回执仍标未证实。 | 后端提前交付；`s08_receipts.quality`、`quality_governance`、`test_s08_receipts_session` 核原签名、本人动作、当前可披露影响；页面失联恢复待验。 |
| BG-035 | B13 | 双方缺口，后端补原导入只读查账 | `history.submit` 在事务内认领本人 ready 暂存，`history_status.project` 没有 stage 关联。已知 attempt 可追踪，同期多条按时间无法定位丢失的首包。按原用户、stage_id 及原命令唯一查证已有 attempt。 | **08** · 04 无号待核；08 查账；09 故障注入 | §6 导入查证；按切片 2.7 | 本人两份同期 stage、丢首包、并发及另一账号下只确认原 attempt，过期/撤权无泄漏。重复只读查证不创建、调度或重投业务尝试。 | 后端提前交付；`history_status.lookup_submission`、`test_s06_history_generation` 验两份 stage、清理、异人/撤权及无重复投递；实际页面待联调。 |
| BG-036 | B13 | 前端沿获权链接或原账本；额外关联投影后端另排 | `collection.models.Run` 有单一 Job、Window；`queries.read_run` 能关联 Window，多个 Job 可共享窗口。前端会话来路不足以推出创建人。 | **04** · 依 033；04 返回；08 或以后验增强 | PD-08 | 如开启，Run 只链接获权 Job，Window 上只列可见关联。独立深链、共同窗、部分可见及撤权均复测，人员字段逐项授权。 | 前端消费；`collection/s07_admin.py`、`test_s06_collection` 核 Job/Run/Window；多 Job 归属增强专项另验。 |
| BG-037 | B13/B14 | 后端动作与理由未逐对校验，前端消费资格 | `quality_governance.act` 与 `collection/control.parse` 分别校验动作集合和理由集合，`s08_schedule.parse` 已逐对检查。前端和 F 预配对不能替代正式写端。 | **08** · 依 030；08 锁内复核；09 拒绝无副作用 | §6 资格；按切片 2.5/2.7 | 例如 claim 配 claim_review、correct_source 配 verified_correction，合法单值错配须受控拒绝。兼测陈旧版本，确认状态/版本未推进、无成功动作事实。 | 后端提前交付；`collection/control.py`、`services.py`、`quality_governance` 与 `test_s06_collection`、`test_s08_receipts_session` 验新理由配对、旧信封查账。 |
| BG-038 | B15 | 限定现行窗口/源地方日，新增全局指标独立延期 | `operations.broker_probe/pulse` 测 PING/订阅/最近消费，`s07_admin.bucket` 按源地方日，发布延迟为 null。全局队列、源整体健康及小时趋势须各定对象、时间窗、分母、TTL。 | **专项** · 04 现有窗口；新增指标先定义授权、对象及分母 | PD-10 | 探针过期、未知发时、仅部分授权各自呈现。新增指标逐项对照时间、样本和权利；抓取时刻不能充作源发布时间。 | 延期专项；现行 `operations`、`test_s08_operations` 验时标及空缺证据。全局、速率和逐小时增强依 PD-10。 |
| BG-039 | B15 | 后端已有诊断，前端按权使用 | `operations.window_evidence`、`s07_admin.trends/bucket` 已给局部抓取、等待分档、终态和调度信息。保留现有两图，优先展示有用且已披露的字段。 | **04** · 04 现行读模型；09 对照 PG；全局另见 038 | PD-10 | 空日、零/未知分母、部分授权、截断分别显示，在同一获权窗口中与后端证据一致。 | 前端消费；`operations_first_write`、`test_s07_operations`、`test_s08_operations` 验部分权限、首写及截断；图表待联调。 |
| BG-040 | B16 | 未调本身不构成缺陷，治理/运维及前端验交接 | 本文第 9 节及操作附表列出 14 项。`config/urls.py` 及 include 注册夹具、站址、许可、账号和备份操作。实际保管、恢复和登记经原生/主机路径。 | **09** · 依 024/029/042；09 分层/隔离验证 | PD-11 | 按操作区分意图受理、原生治理与主机执行。F-only 不进入正式域，失权入口收回，备份/恢复验收仅用隔离临时数据。 | 正式交接；`restricted_admin`、`test_s06_accounts` 覆盖后端入口。SPA / Admin / 主机签认需治理与集成方独立取证。 |
| BG-041 | B10/B14/BG-011 | 后端现行规格与双方 DTO 有误，导出组数本轮订正 | `research_statistics.summarize/paired_summary`、`quality_governance.event_result/act` 明确分支。`ResearchResult/QualityIssue` 现有描述过宽或字段异名。原规格写 5，现已按 `research_exports.normalize` 实限 2 订正，现有 SPA 固定一组。 | **08** · 02 修 1–2 组；08 其余分支；09 HTTP | §6/现行解码 | 校验三研究方法、裁剪/空结果、质量首次/重放/回执的 required/null/缺省、ID 与日期解码。以 1、2、3 组请求核对组数上限及跨组约束与实际 HTTP 一致。 | 后端提前交付；`docs/api/openapi.v1.json`、`test_s07_contract`、`test_s07_contract_user_http` 核 resolver、74 个操作及类型分支；客户端解码待同版联调。 |
| BG-042 | B16 | 双方保留跨期、空间、批量及主机扩展的原目标 | `research_spec.parse_spec` 约束单父窗，`quality_governance.act` 针对单事件版本，备份/恢复有主机职责。保留两期同城/省际、空间差异、完整日历、批量复核、额外重置的原需求。 | **专项** · 依 008/036/038/040，各专项择期独立验收 | PD-05/10/11 | 若推进，对照窗口径、授权边界、分母、批量逐项结果/冲突、主机执行凭据分别有可复核用例，现有单对象链可独立验收。 | 延期专项；PD-05/10/11 的跨期、空间、批量与主机扩展各需定义及专项验收。 |
| BG-043 | B11 | 正式 D 导出延期，后端修地方日语义，前端按许可开放 | `analysis_query.detail_rows` 对 D 返回 `observed_at=null`、地方日报日；`research_exports.data_rows` 把后者填入 CSV 的 `observed_at`，`report_kind` 可选。独立专项用地方日列及类型/时区，通过前采用获权精确读。 | **专项** · 依 007/016/017/027；格式及真实样本独立验 | PD-06，§6 CSV | 非 UTC 跨日样本逐字节核表头、地方日、空瞬时时刻与署名。无导出权拒绝，已有精确阅读路径单独成立。 | 延期专项；本轮只交付通用 v2 数据域列。正式 D 日报附件的地方日列、字段权与可留存样本仍需 PD-06 专项。 |
