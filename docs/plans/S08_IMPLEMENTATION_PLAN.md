# S08 Implementation Plan｜正式前端与双端联调

**最终实施基线 · 2026-11-25**。用户转达 GPT 最终定向 Plan Review 为 **APPROVED / PASS**；R01–R05 全部通过，§6.2 本地城市检索别名方案及当前计划的六个内部实施阶段、已确认接口增强范围均获批准。历史首次 **CONDITIONAL PASS** 与定向复核过程保留在 §11.1。六个实施阶段及正式前端验收在批准时为 **NOT RUN**，本文件现作为 S08 执行基线。

## 1. 交付范围与判定依据

S08 在一个同源 Vue 应用内打通观察复访、研究与明细核对、治理与影响追踪三条业务链。七个用户模块与三个管理模块映射到 §4 的正式入口。交付物包括真实 Session 联调、渲染与交互证据、可复现的前端构建以及按用途限定的数据和资产说明。正式名称沿用项目名称，保留已认可的全国总览视觉，其他页面直接在正式工程中设计、实现、迭代。

| 决定什么 | 依据与用法 |
|---|---|
| 业务范围、数据含义、验收 | [V3.0 §2–4、6–10、12–14](../design/城市空气质量监测数据分析平台_系统总体设计方案_V3.0.md) 的 M01–M10、D01–D13、T01–T12。[总体实施方案 §5/S08、§6–10](../design/总体实施方案.md) 定义依赖、重点 P1、预算和各阶段出口。 |
| 页面如何呈现 | [frontend/DESIGN.md](../../frontend/DESIGN.md) 是唯一视觉与交互规范。**已确认**覆盖 Clear Sky Glass、首页构图、双工作区及导航。**已定待实现**包括真实会话、跨入口选中、浮层与撤权清理。**实测**仅对指定 F 原型、引擎、视口及状态有效。**候选**包括部分尺寸、断点、材质、时长及首页外的构图，可按实测归一化。 |
| 工程约束 | 会话给出的全局及项目指令与本机 `~/.codex/AGENTS.md`、[仓库指令](../../AGENTS.md)、[前端目录指令](../../frontend/AGENTS.md) 已对读。核过本轮计划、设计、契约、验证和原型目录的祖先指令路径。 |
| 接口可依赖的行为 | [S07 计划](S07_IMPLEMENTATION_PLAN.md) 说明验收设计，[封版报告](../verification/S07.md)、[遗留台账](../verification/S07_LEGACY_LEDGER.md) 说明实际裁决。[v1 样例](../api/v1.md)、[OpenAPI](../api/openapi.v1.json)、[V12 操作矩阵](../verification/S07_V12.json) 与本地 resolver、校验、投影、权限、测试交叉核对。 |

**S08 退出条件**要求同一受测版本满足 §8 业务切片、§9 工程/浏览器/三链/性能判据，原型缺陷逐项有正式回归，变动契约完成兼容回归及所需独立审查，逐项签收 M/T 与未覆盖范围。P0 软件能力缺失须补齐或由用户明确决定范围，保留未达项。资产或实源阻塞可交付已验的软件链与列表降级，地图、适用 H 和实源判据分别记账。S09 承担整机离线交付、真实城市离线证据及城市正式在线准入。P2 历史地图回放、复杂个性化、辅助气象等留在阶段外。

## 2. 本地基线与初稿复验

| 核查项 | 2026-11-24 本地事实及复核入口 |
|---|---|
| 分支与工作区 | 初稿核查起点 `main@89f3c8a99e41b144cf0a785071dd717a7efd7049`，相对本地跟踪引用 `origin/main` 为 `+0/-0`。`git status --porcelain=v2 --branch` 仅列已有 `? photo/`，索引及 tracked diff 为空。该目录保留原状，未作为地图、品牌资料或验证输入。 |
| 封版及实际验收版本 | `git show-ref --tags s07-backend-v1` 得到 annotated tag 对象 `d0df9f5b7e93e18a7579a2813dcfda428a611373`，剥离后指向文档提交 `a3fde5b2239fbd55e83e54ad0006e034b26f57a1`。F/I、正式 T09 运行基线为 `5c2073eeefca01fcc004010534b65e8da67bace7`。`1ddb3ed` 为原报告包，`4081c06` 为 H 补验材料。307 个运行输入按 `scripts/s07_handoff.py` 对账，当前源码指纹 `1258e04db92db956deef816b06cef660cbdefd677aef7994b263cbd24d85f6b8`。 |
| 标签至 HEAD | `git diff --stat s07-backend-v1..HEAD` 为 66 个文件，`c948705` 保存 F 原型和样例，`89f3c8a` 登记设计、实测证据与指令。`backend/`、`tests/`、`scripts/`、`docker/`、宿主入口、镜像/Compose 配置、依赖与 `docs/api/` 相对标签无差异。运行提交到封版有文档/证据归档，包括 `docs/api/v1.md` 的冻结说明。现有契约差异按 §6 处置。 |
| 后端与迁移 | Python 3.13.18，`uv lock --check --offline` PASS（67 包）。`uv.lock` SHA-256 `f088f9ecf7fa705a957ab4aa4ef3be8b67a1667a29333b967e8963ce188ee84b`。本地 `.venv` 实装与 lock 一致，包括 Django 5.2.19、DRF 3.18.3、psycopg 3.3.9、Celery 5.7.0、django-celery-beat 2.9.0、HTTPX 0.28.1、Gunicorn 26.2.0、WhiteNoise 6.12.0。自有迁移 accounts/collection/observations/samples 分别为 6/9/17/2 个，叶节点为 `0006_worker_subscription`、`0009_control_versions`、`0017_backup_preparation_dependencies`、`0002_local_grants`。 |
| 契约与运行配置 | `/api/v1/` 49 条路径、66 个显式操作，OpenAPI SHA-256 `187e7d27b976442ded2524cc7248fd2abe66f388d64b47e4fb4ae8ea028f7e26`。V3.0 文档 SHA-256 `4373fca30fd71061c4e795ebd02b73190e39d451dd7830c0dc5bf01e58379bf6`。[URLConf](../../backend/config/urls.py)、[settings](../../backend/config/settings.py)、[Dockerfile](../../Dockerfile) 与 [宿主运维](../operations/S07.md) 显示 Django 提供 `/admin/`、就绪探针及 API，WhiteNoise 使用 `collectstatic`，目前无正式 SPA 构建、HTML 入口和深链接处理。 |

**本轮后端实际验证 · PASS**。在 PostgreSQL 真库、socket-only、bwrap 独立网络 R 中运行下面的定向集合。26 PASS / 0 FAIL / 0 NOT RUN，守卫 PASS、外部路由为空、仅 `lo`、清理成功。收据 `tmp/s06-evidence/s06-956d6af2e940/{pytest.json,V01/result.json}` 记录源指纹、迁移叶、网络、用例及清理。覆盖 OpenAPI/resolver、F 的真实 HTTP/Cookie/CSRF、两用户、输入版本、授权前置的排名/名录、目录和独立坐标授权，以及注入异常。其中的 resolver 测试核路径和方法。§6 的小时窗枚举差异来自逐项核对参数校验器和契约。

```sh
.venv/bin/python -B scripts/verify_s07_host.py r pytest tests/test_s07_contract.py tests/test_s07_contract_user_http.py tests/test_s04_national.py tests/test_s07_catalog.py tests/test_s07_exact.py
```

**历史签收**。S07 `CLOSED / PASS`，三链、V01–V17、C01 Beat 排他、C02 精确读与旧票、同版 600 个唯一回归节点、百万 F 五会话正式性能见 [S07 报告](../verification/S07.md) 与 [T09](../verification/S07_T09.md)。L22 的 CSV 首次失败保留，随后九轮未复现，根因未知，具体残余风险已接受。L30 的 S06 旧包待保管人，L31 的 UCI H 授权限当轮且已到期。L32/T11 城市在线 NO-GO，L33 真实日报/历史回补取决于来源证据，L34 共享介质由保管方处理，L35 地图 BLOCKED，L36 正式浏览器归 S08，L37 整机归 S09。L38 趋势有历史 PG 对账，L39 一般 P1 须按模块取舍，L40 为 P2，L41 仅有旧写者停机后的前向迁移证据。本轮覆盖的后端测试见上方命令。全量 S07、当期 H、真实来源、整机及正式前端记 **NOT RUN**。

### 2.1 后续双端联调的实际入口与数据准备

沿用 S07 的环境代号。A 为完整 Compose 实例，B 为宿主 Python 进程配独立基础服务，R 为隔离的回归运行环境。本轮后端定向复验使用 R。

1. 使用本轮自有独立实例名、临时配置 `0600`、根目录 `0700`，分配空 PG/低权角色、专用 Redis/队列、回环端口和新卷。按手册核对 `airquality.s07_instance`、Redis `airquality:s07:instance`、`btree_gist`、静态目录与 Beat 锁身份。`uv sync --locked --offline` 需已备齐缓存。在核准的实例执行 `python backend/manage.py migrate --noinput`、`collectstatic --noinput`、`python main.py check`。开发可用 `python main.py web --dev`，另起同实例 Worker 处理 F。周期链仅在专用隔离环境登记并启动唯一 Beat。`CNEMC_MODE=off`，默认关闭外采，以网络守卫核验。拒绝错库、错卷、占用端口或身份不符的队列，清理仅按本轮归属执行。[宿主与 Compose](../operations/S07.md) · [交接手册](../operations/S07_handoff.md)
2. 生产形态联调复用 A 的 `compose.yaml` → `compose.offline.yaml`，验收再叠 `scripts/s07_isolation.yaml` 和本轮已审的前端构建输入。使用唯一项目、私有 env，先 `config --services` 核 db/redis/migrate/web/worker/beat、新实例身份和本地镜像，再启动。`scripts/verify_s07.py flows` 与 `scripts/verify_s07_host.py contract` 是创建并回收自有环境的验收运行器。正式 E2E 需在运行器的资源生命周期内驱动浏览器，或显式建同等隔离的长驻联调实例。回环真同源，业务请求仅访问本机，无公网依赖。
3. 普通 A/B 用真实注册各建独立 Session。受控种子以每轮生成的临时口令和最小具名权限建任务管理、只审阅、质量裁决、诊断及受限管理账号，另备被停用/降权会话。仅由隔离造数进程使用特权，不用超级用户作正例。读写前核对象归属、来源用途、测试实例标识。`tests/test_s07_contract_user_http.py::seed_products` 的 C 仅三小时 CO，L/T/S 样本各一条。沿 `tests/s05_support.py`、`scripts/s07_{user_flow,contract_admin,flows}.py` 的领域服务准备同源多城/多标准 L、C 七槽、T 错时/缺口、迁址 S、合成 D、独立应到名录、版本与账本。F 固定时钟/数据标签用于可重放对账。界面默认正式域，主动选 F 后全部下钻保留标识。
4. H 按当时来源、受众和用途重核。S07 UCI 501 的两站、两段各 48 小时、六浓度曾获限时查询/研究等权，A/B/R 应用授权于 2026-11-23 UTC 分别到期。新一轮记录署名、加工、对象/字段/日期/期限，核指纹，走受控暂存与导入，单独验两站 H 的发现、分析和权外导出拒绝。H 缺当前授权则记 `BLOCKED / NOT RUN`，可继续 F 软件测试。公开截图使用可分发 F。受限原件及可恢复的 HTTP/截图/trace 仅在确有对应留存、受众和交付许可时保存。凭据、Cookie、令牌、私有材料不进仓。[H 权利范围](../verification/S07_H/RIGHTS.md)

## 3. 正式前端起点、原型复用与资源

`frontend/` 只有 `AGENTS.md` 和 `DESIGN.md`。可执行工程在 `prototypes/frontend-preview/`。`src/router.ts` 注册 `/`、`/city`、`/analysis`、`/quality` 并把未知路径转到城市。[App.vue](../../prototypes/frontend-preview/src/App.vue) 将「数据查询、我的空间、采集任务、系统运行」接到范围说明弹层。[preview.ts](../../prototypes/frontend-preview/src/shared/preview.ts) 默认模拟 `operator`，角色和两套收藏保存在内存。Axios 已安装，当前四页只读本地 F，正式请求客户端待建。后三页可作实现参考，视觉签收仅覆盖全国首页。正式数据发现、独立站点页、用户/管理导航和私有业务都要以真实 API 验证。

原型业务目录位于 `src/`，其中 `national/` 内含首页组件及 `assets/`。完整路径见[原型工程](../../prototypes/frontend-preview/)。

| 源码或资源 | 可迁入的具体价值 | 正式替换、依赖与验证 |
|---|---|---|
| `src/national/{NationalPage,NationalSide,SkyMetric}.vue`、`sky.css` | 天空与云白表面、五项摘要、非等宽地图/榜/等级、次排及通栏列表、导航收展与数字基线 | 抽取语义 token、版式与响应行为。以路由壳和真实账号代替 F 首页专属壳、候选品牌及演示开关。全站按 DESIGN §2–5 映射 Element Plus 和浮层，在展开/折叠及六档宽度实拍。 |
| `NationalTable.vue`、`NationalDialog.vue`、`AtlasMap.vue`、`mapPoints.ts` | 紧凑行、短详情、键盘选点、相邻点命中区、关闭还焦与视野工具 | 以服务端城市 ID 和合法 `links` 贯穿点/榜/行/详情/URL。服务端查询、游标、明确当前页地图范围取代名称、页码切片与 SVG 静态点。正式 ECharts 地理资产另核。密集、无坐标、被筛掉、撤权都验。 |
| `snapshot.ts`、`sample-cities.json`、`NationalStats.vue`、`national/charts.ts` | 图表信息密度、图例、视觉层级及 F 断言的反例价值 | 原型 50 名录、47 到报、46 点位、TOP5、优良/到报比、趋势和污染物计数均为 F。视图片段保留，正式查询使用 §5–6 的获权投影。替换固定纵轴、硬编码标准图例与本地排序/统计。 |
| `shared/ChartView.vue`、`shared/chartTheme.ts`、`analysis/chartOptions.ts` | ECharts 按需注册、容器 resize、字体就绪重测、释放实例、图例/提示起点 | 重做有尺寸才 init、稳定 series ID 的增量更新与监听清理，替换深 watch 加 `notMerge:true` 全量重置。测隐藏页签、容器变化、反复进出、快速切换、图表与键盘等价明细。 |
| `city/{CityPage,CityLedger,CityTrend}.vue`、`fixture.ts` | 来源时刻、分槽展示、缺测和趋势/明细的构图与测试 | C 七槽、获权 T 24 槽及 S/H 独立新页按服务 links 接入，清除静态状态、演示城市和模拟收藏。测错时/空窗、原身份和复访。 |
| `analysis/AnalysisPage.vue`、`researchMock.ts`、`research-fixtures.json`、`city-series.json`、`scripts/generate-research-fixtures.mjs` | 已应用条件与结果分区、图/支撑证据、样本不足和返回交互 | 正式研究页通过 S05 请求取得配对、统计、版本、方案、分页和导出。F fixture 可用作专门 UI 反例，不进正式 bundle 或作为服务器证据。以 PG 独立计数与 CSV 对账。 |
| `quality/QualityPage.vue`、`qualityMock.ts` | 事件时间线、证据 Drawer、版本冲突和确认交互 | 替换模拟角色、时延、裁决、影响范围与回执。以 S06/S07 可见投影重做三管理入口，验证纯审阅、部分获权、竞态和回读。 |
| `App.vue`、`shared/preview.ts`、`main.ts`、`router.ts`、`style.css`、`public/mark.svg` 与 `src/national/assets/sky-mark.svg` | 跳转内容、面包屑、反馈、中文 locale，字体和图标接入示例 | 正式 Router 统一承载两种工作区，替换内存演示权限、收藏状态与通配重定向。共用一个 session owner、Router、主题、焦点/浮层生命周期。品牌标志与字体逐件核来源。 |
| `src/**/*.spec.ts`、`e2e/fixtures.ts`、`e2e/{national,city-shell,governance}.spec.ts`、`evidence/national/` | 保留操作断言、测试数据意图、尺寸、视觉对照与已知失败 | 按正式可访问名称、稳定对象键和真实会话改写。DOM/axe/截图补充 HTTP、PG 证据。禁止把 F 展示测试当正式后端联调。 |

**版本选择**。本机 Node `24.19.0`、npm `12.0.2`，原型声明 `node >=22.12.0`。推荐正式项目固定本机可复现的 Node/npm 组合，以原型 `package-lock.json` v3 的解析结果作为起点，生成正式独立 manifest/锁并用 `npm ci` 验证。固定 Vue 3.5.45、TS 5.9.3、Vite 7.3.8、plugin-vue 6.0.11、Router 4.6.5、Pinia 3.0.4、Axios 1.21.1、Element Plus 2.14.8、icons-vue 2.3.2、ECharts 6.1.0。按实际使用保留 dayjs 1.11.23。工程测试基线 `vue-tsc` 3.3.14、ESLint 10.15.0、Vitest 4.1.12、Vue Test Utils 2.6.0、Playwright 1.65.1、axe 4.14.0。正式 lock、Node engines/包管理声明与可用浏览器 revision 一起验证。常规升级另记理由、锁差异和受影响浏览器回归。[manifest](../../prototypes/frontend-preview/package.json) · [锁](../../prototypes/frontend-preview/package-lock.json)

**资产及工具**。原型使用本地 Fontsource Inter 5.3.0 与 Noto Sans SC 5.3.1，拉丁数字等宽，中文缺字走系统字体。正式包随字体附 OFL 版权与许可原件，记录字重/子集/版本、保留字形回退。系统字体仅引用已安装字体。Element Plus/icons-vue 的 MIT 和 ECharts 的 Apache-2.0 等逐包核 LICENSE/NOTICE 及图标署名，含构建后实际分发的字体、图标与间接资产。`atlas-relief.webp`、`sample-cities.json`、`neId`、SVG 标注和投影仅有原型的 Natural Earth 1:50m 阴影地形和城市中心点来源说明。正式纳入需取得可追溯版本、适用地域和边界、几何、坐标映射与 CRS、加工记录、展示及打包/离线分发依据。背景可以用本地 CSS 生成。候选名称「澄空观测」与标志另核权属，重大品牌变动交用户。第三方软件许可、观测使用权、地图素材权分别列账。依据见[原型说明](../../prototypes/frontend-preview/evidence/national/评审说明.md)与[首页实测](../design/前端设计研究与首页核查.md)。

核对了本机 `~/.agents/skills/{frontend-delivery,impeccable,web-design-guidelines}` 和 `~/.codex/skills/{taste-skill,ui-ux-pro-max,playwright-cli}` 的 `SKILL.md`。`frontend-delivery` 负责正式切片交付和证据，`design-taste-frontend` 取网格与边缘对齐方法，`ui-ux-pro-max` 用于具体控件、图表和焦点疑问，`impeccable` 用于任务型页面的局部打磨，`web-design-guidelines` 用于定向界面及可访问性检查，Playwright CLI 或仓内锁定的 runner 负责操作真实页面。设计判断统一写回 DESIGN 的相应规则。上述是实装与职责核查，具体采用的检查见本轮记录和 §9。

## 4. 业务需求追踪矩阵

同一 Vue 工程承载用户工作区四入口、城市和站点两类独立详情、公共说明与身份页，以及管理员工作区三入口。管理员登录成功也进入用户区，根据能力切换。各行最后一栏同时给出实施阶段（§8）和独立可复核的验收行为（§9）。P0 软件与实源/地图条件分别签收。

| 模块与需求 | 页面/交互入口 | 现有服务与能力差异 | 待实施与验收证据 |
|---|---|---|---|
| **M01 全国总览** · P0 L 同刻、覆盖/时间、搜城和地域/等级、AQI/首要污染物、排名分布、地图与列表下钻 | 监测总览，快照/来源、KPI、点/榜/图例、列表、短详情→城市 | `overview/national` 的授权快照、名称/地域、`series/levels/links`、高值榜及按标准的汇总。低值、等级过滤、定位选中、正式地理映射见 §6。 | 阶段三，T01/T02/T12。比对同一 L 锚点、同标准、未知/部分名录，验证多入口同 ID。地图和可搜索列表各自留证。交付地域筛选。省际比较与两期同城差值排行递延，需确认可比覆盖和相应汇总权。 |
| **M02 城市详情** · P0 直接 C 七项、等级/首要污染物、观测/采集时刻，独立 T、已存 C 与站点 | 从 L 或获权目录开城市详情，产品分区，历史图及站点入口 | `cities/{city}/`、`…/trend/`、`…/stations/`、`observations/query/` 和 D 精确读。允许同刻 L/C/T 数值各异，来源信息/采集时刻可空。 | 阶段三，T02/T04/T11。逐产品对七槽、错时及 24 槽缺口并验证同城链接。获准月/季统计转 M05。暂缓空间差异摘要。真实城市产品验收另列。 |
| **M03 站点详情** · P0 原码/命名空间、所属城市、站址期、可核坐标、各指标最近有效及历史/时效 | 城市站点目录或 H 目录→选站址期→站点独立页、历史与收藏 | `stations/{station}/`、`observations/query/`，`state=select_epoch`、有条件的 identity/coordinates、逐指标时间、来源状态。 | 阶段三，T03/T10。改名/退役/迁址/同名、逐指标错时、断源旧值、窗内空数据、站址期外、缺坐标。已有异常/迁址证据可展示。完整覆盖日历递延。 |
| **M04 同层比较** · P0 多城或同城多站对齐，P1 共同样本、差值/变幅与解释 | 分析工作台「对象对比」→同条件配对明细→保存 | `analysis/compare`、`analysis/details` 的同产品 C:C、S:S、同一合规数据集的 H:H，单位/标准/站址/时间轴校验、共同样本与不足态。 | 阶段四，T02/T03/T04。对齐时标，跨层/跨城不兼容。各 18/24、共同 12/24 时可看曲线和配对明细，无全天排名，基准为 0 的变化率留空。 |
| **M05 综合统计** · P0 小时趋势、日/月浓度统计、极值/中位数/分布、样本和缺测。**重点 P1 多维统计** | 分析「趋势与分布」「指标关联」，季节/热图/箱线、候选异常→明细 | `analysis/statistics` 的 `statistics/pearson` 与有权的 `columns/buckets/chart/distribution/heatmap/anomalies`，按许可/方法有缺项。 | 阶段四，T04/T09。源地方时、冬季跨年、完整月与站址/标准界线，按有效小时加权，不平均日均。核样本不足、零方差、P<30、区间图与明细，异常候选保留原峰。 |
| **M06 查询与导出** · P0 对象/来源/指标/质量/单位/窗口/标准、稳定游标、版本化明细与合规 CSV。P1 可选字段、授权汇总 | 数据查询基础/高级条件，分析下钻保留父查询，我的空间→近期导出 | `observations/query`、`current`、`analysis/details`、`me/exports`。自由查询、研究明细和整组导出支持的筛选不同，见 §5.2、§6。 | 阶段四，T06/T07/T08/T09。从 `/app/query` 独立完成一条已确认整组范围的合法 CSV 下载，核首屏与整组行数、零/删失/缺测、票据和下载两次授权。另验自由 GET 筛选无法无损导出的提示，见 §9.1。 |
| **M07 账户与个人中心** · P0 注册/登录/退出/改密、资料、收藏城市站点、关注摘要。P1 保存分析意图 | 公共说明、登录/注册、账号菜单、我的空间「我的关注/分析方案/近期导出」 | `auth/*`、`me/`、`me/favorites`、`me/saved-queries`、`me/exports` 真实私有资源与重跑。管理入口需会话能力。 | 阶段二、三、四，T01/T03/T07/T08。双账号、改密与他端旧会话、我的关注复访重取原产品最近获权摘要、时标和链接，站址需选择及撤权后的最小占位，另核方案改版/撤权与票据到期（§9.1）。个性化排序/通知不纳入。 |
| **M08 采集任务** · P0 来源/产品配置、获准周期创建编辑、定时/手动/导入、窗口互斥/部分成功/重试。**重点 P1 可靠补采** | 管理「采集任务」的配置摘要/计划、窗口、各次运行、子项、回执/批次→质量，导入状态 | `admin/collection-{jobs,runs,windows}`、`history-imports`、受控登记命令。投递和认可分别有 PG 账本。配置缺口、执行者发现及职责见 §6。 | 阶段五，T05/T08/T11。F/注入同窗竞争、应到/实到/有效、合并、仅质量、无新、部分授权、可补/不可补及暂停恢复。核 PG 已认可子项唯一，经 Worker 查原运行。 |
| **M09 质量与数据管理** · P0 字段契约/单位/缺失/异常、可信修订与争议、站址和来源。**重点 P1 质量治理** | 管理「数据质量」事件→批次/原修订/候选→受控裁决或标记待核→影响窗口/重查 | `quality-issues`、`observation-evidence`、`stations/{id}/changes`、许可/处置。范围、版本、证据以返回的投影为准，元数据准入在受控主机。 | 阶段五，T02/T04/T06/T08。按窗与指标核 E/O/V 及原因，从异常单元追到批次、回执和事件。普通结案与有据纠正分别验，重读影响组的统计/支撑和票据，对照无关组版本（§9.1）。批量复核延期。 |
| **M10 系统运行** · P0 受控账号和基础字典、服务/源/任务状态、权限与管理审计。P1 运行趋势 | 管理「系统运行」→健康/来源诊断、有限日趋势、受权操作回执，必要时进原生 Admin 或主机 | `operations/{health,trends}`、`accounts/{pk}/actions`、受限 Admin 账号目录、治理审计记录。字典替代项、本人回执与获权对象审计见 §6.2。 | 阶段五，T05/T07/T10/T12。五组件分列，按所选获权窗口/返回页展示积压、最早等待年龄、截断和错误摘要。以 §9.1 的键首次有效写入时刻与源观测、抓取时刻分别对账。最小权限和范围拒绝、谁/何时/何事/结果可核。备份/恢复由主机保管方签认。一般 P1 的受控重置只使用已有服务及适用审计。额外重置功能递延。 |

## 5. 页面使用的冻结接口

下列路径均相对 `/api/v1/`，实际请求保留末尾 `/`。可从 [URL 配置](../../backend/config/urls.py)、各 `*_urls.py`、[66 个操作逐项 HTTP 对照](../verification/S07_V12.json) 进入。API 分组用于定位调用者，完整请求类型和状态分支以端点而定。

### 5.1 观察与研究

| 入口 / 源码 | 实际请求与响应 | 界面约束 |
|---|---|---|
| `catalog/objects/` · `observations/discovery.py::{discover,entry}` | GET `product?,at?,scope?,page_size?,cursor?`。按 `(product,city或site_epoch)` 排序，每对象/产品一个获权观测锚和指标。`name` 可空，返回 `shape/source/product/synthetic/attribution/rights_attributions/link/history_link`，D 给 `local_report_date`。 | 先用获权卡片引导选择，跟随本地白名单中的服务链接。未知与被隐藏对象不作区别。锚点只证明该观测。某小时链接、目录可见、日历预期、获权分析与完整历史覆盖各自取证。 |
| `overview/national/` · `s04_national.py::{_selection,_summary,_roster,read_national}` | GET `product,scope?,at` 或 `as_of`、`standard_series,window_definition,order,q,geography,page_size,cursor` 按需传。先按获权产品/时窗确定 `snapshot_at`，再筛城市。`at` 是源地方整点。默认城市 ID 升序。`aqi_desc` 要求明确已知标准与汇总权，按 AQI 降序、并列用城市键消歧。响应含 `evaluated_at/state/selection/summary/results/page`。每城有 `series/levels/links` 和按权给的名称/地域。 | 同刻 L 比较，`*Level` 只标等级。汇总独立核 `process_local` 与 `display` 的 `overview_summary` 及数据读取依据。`groups[standard].distribution` 分母用本组 `ranked_city_count`。`summary_eligible_city_count` 标获准汇总数。`covered_city_count=null` 表示无法完整提供。`expected` 需对应唯一独立名录、`roster_basis`、`catalog` 和 `collection_ledger`，可为 null。仅在对应数均可披露且分母为正时显示比例。真实 0 保留，`summary.state=unavailable` 仍可有城市行。 |
| `cities/{city}/`、`…/trend/`、`…/stations/`、`stations/{station}/` · `s04_city.py`、`s04_station.py` | C `product` 加 `at` 或 `as_of`，返回 `snapshot_at/slots/links`。T `product` 加 `anchor` 返回连续 24 个小时槽/状态。同城 S/H 目录支持 `at` 和游标。站可先回 `select_epoch`，再按 `product/site_epoch/as_of/indicators` 取 `identity/results/history`，与目录分页参数分开。 | 城市稳定键与来源、站 ID 与站址期分别保存。站收藏绑定站，观测绑定 epoch。温和展示未映射/停用/待核、获取依据和逐指标年龄。C 之外的 L/T/D 分区标源，同刻差异可回查。 |
| `observations/query/` · `detail.py::{validate_series,read_detail}`、`projection.py::public_rows` | GET `product,source?,cities` 或 `site_epochs`，同类 ID 及 `indicators` 用逗号串，另传 `report_kind/window_definition/mode`、可选 `unit,standard_series,quality`。固定 `[start,end)` 最多 30 天、3600 格、5 对象/7 指标。`latest_valid` 取 `valid`，可给 `as_of`。按观测时刻、键倒序。 | 保留 `value/censor_limit` 的十进制字符串或 null，实际质量决定 `0`、`<3`、缺测、无效、争议的展示。用服务单位（AQI `1`、CO `mg/m³`、其他浓度 `µg/m³`），保留 H 原单位依据。每行区分 `observed_at`、贡献批次 `fetched_at/fetched_at_basis`、获权运行状态和评估时刻。区分 `no_data`、有身份依据的 `no_coverage` 和用途拒绝。 |
| `observations/current/` · `queries.py::{product_space,read_current,exact_version}` | GET `product,scope?`，单数 `city` 或 `site_epoch`、单 `indicator`、`report_kind,window_definition,standard_series`。小时用 `observed_at`，D 用 `report_kind=daily,window_definition=local_daily,local_report_date`。精确读无分页。 | 读取 `count/results/reason`，按原因显示记录、空窗或争议。获权行有 `revision_id` 和 64 位十六进制 `input_version`，仅作该用户、同一精确选择的等值检查。D 显示源地方报告日，贡献批次时间从有该投影的明细入口读。 |
| `analysis/{statistics,compare,details}/` · `research_spec.py`、`research_access.py`、`research_api.py`、`research_statistics.py` | POST JSON，无 query string。保留 canonical `selection,method,method_version=s05.v1,grain`。`selection` 指明单产品/源、`scope`、有序 `cities` 或 `site_epochs`、指标、单位/标准映射、`quality=reliable`、报告/窗口及 `[start,end)` 或 `anchor/span_hours`。统计 1 对象×1 指标，Pearson 1×2 浓度，对比 2–5×1。常规 30 天/3600 格，单对象单指标按月/季可到 366 天/8784 格。研究 JSON 响应上限 1 MiB，统计量和计数为 JSON 数字或 null。 | 读取 `source_timezone/requested_window/expected_windows/columns/conclusion/buckets/input_version` 和按权的 `chart/heatmap/distribution/anomalies` 等。`chart` 可因原序列权缺失而省略。配对看 `E_common/P`、限定结论与排除原因。绘图只消费 `chart.points`，`resolution=range` 展示极值区间、`valid_count`、`gaps`。`details` 传同父请求、顶层研究 `input_version` 和服务端支撑描述 `support=observed/effective/paired`、原 `bucket(start/end/local_hour/pos)`，同条件游标，顺序 `at,pos,key`。独立 `method=detail` 首次无游标可取得研究输入版本。 |

两种 GET 的模板如下。尖括号由本实例发现的获权 F 标识替换，生产域按契约省略 `scope`。

```text
GET /api/v1/observations/query/?scope=synthetic&product=<C>&cities=<city>&indicators=CO&mode=window&report_kind=hourly&window_definition=hourly_label_unspecified&start=2024-02-01T00:00:00Z&end=2024-02-02T00:00:00Z
GET /api/v1/observations/current/?scope=synthetic&product=<D>&city=<city>&indicator=AQI&report_kind=daily&window_definition=local_daily&standard_series=<series>&local_report_date=2024-02-01
```

E 为授权范围按产品节奏、有效期和实际应覆盖窗口应有的时槽，O 为收到独立记录的时槽，V 为兼容有效时槽，E共是共同应有时槽，P 是其中共同有效数。取用服务输出 `O/E`、`V/E`、`P/E共`、版本与范围，不以非空点数补分母。单列整期解释须满足适用整期覆盖和 `V/E≥75%`。整期比较须满足 `P/E共≥75%`。Pearson 另须 `P≥30` 且两列方差非零。`E=null`、`E=0`、不足、部分覆盖与充分覆盖均有单独展示。按源地方时分桶，小时、滚动 O₃、独立官方 D 与探索性统计分别标注。标准按有据的系列分组。未知标准可保留源文，停用版本化配色/榜。HJ633-2012 与 HJ633-2026 分别测试 0、一级边界和量程外，按后端等级/可比标志展示。阈值见 V3.0 §8.1–8.5。

### 5.2 身份与私有业务

- 公共页介绍观测产品、来源、合成域和允许用途。身份表单走 `GET auth/csrf/` 获取 JSON `csrf_token` 与 Cookie。`POST auth/register/` 201 后另行登录。`POST auth/login/` 带 `X-CSRFToken`，成功后重取 token，再请求 `/me/` 及批准的能力投影。改密、退出以及所有不安全方法和受保护的研究 POST 均用当前 token。会话 Cookie HttpOnly、SameSite=Lax，默认 8 小时，HTTPS 配置 `COOKIE_SECURE=1`。JSON 请求体受本机 `DATA_UPLOAD_MAX_MEMORY_SIZE` 16 KiB 约束。资料 PATCH 仅 `first_name,last_name`，`POST auth/password/` 要求旧/新密码，成功后重登。[会话服务](../../backend/accounts/views.py) · [资料与改密](../../backend/accounts/profile.py)
- `me/favorites/` 及 `/{item_id}/` 支持列表/新建、读/改偏好/删。提交 `kind,target_id,product`，新建 201、同意图可回 200、偏好冲突 `409/favorite_exists`。`me/saved-queries/` 保存研究父意图及 title，PATCH 携 `intent_revision`。`/{id}/run/` POST 重核现权。分辨 `current/data_changed/baseline_unavailable` 与 `target_unavailable/method_changed`，仅在有 `result` 时呈现新结果。方案与收藏意图保留个人归属，受限摘要和站点改名/退役状态按返回投影显示。[收藏](../../backend/accounts/favorites.py) · [保存与重跑](../../backend/accounts/research_saved.py)
- `me/exports/` 的 POST 使用 `kind=detail|aggregate`、获准 `fields`、`groups=[父查询+研究 input_version]`。最多 2 组、合计 12,000 格/5 对象/7 指标、实际 10,000 行/4 MiB，空集可回 `200/no_data`，201 才有票。本人票据列表/详情/删除与 `/{id}/download/` 分开，元数据含 `id/kind/created_at/expires_at`，不预判能下载。有效期至多 15 分钟且受许可时限约束，准备与下载分别核许可、字段、版本和私有归属。仅成功 CSV 附件才生成下载，长度/行数、署名、单位、公式转义按服务文件。拒绝按 JSON/框架错误处理并释放临时 Blob URL。[票据实现](../../backend/accounts/research_exports.py)
- **明细边界**。研究支撑页可选 `paired/effective` 及桶，导出票据以整个 `groups` 为单位，`method=detail` 有自己的选择条件。普通 GET 页的 `latest_valid`、任意质量筛选或支撑子集不能无损表达为整组票据时，明确给出可导范围。用户另行打开并确认可表示的显式研究条件，拿到对应父查询版本后方可申请。只在同条件已证等价时提供「导出此结果」，不把整组 CSV 标成「当前配对/当前页/当前桶」。S08 的票据范围止于上述已确认的整组。M06 从独立数据查询入口完成的 CSV 闭环见 §9.1。S04 精确行标记、S05 研究输入、`intent_revision` 和管理 `control_version/event_version` 使用不同类型。

### 5.3 管理、审计与主机入口

| 工作台/责任人 | 请求、投影与限制 | 操作及回执 |
|---|---|---|
| 采集计划、运行、窗口 | `admin/collection-jobs/` 列获准 Job，详情 GET 给 `targets.job/source/product` 及各自 `control_version`。`admin/collection-windows/`、`admin/collection-runs/` 列表要求 `product`，可按 `scope/job/object_code/indicator/start/end`，运行另可按 `window`。按源地方日 `[start,end)` 最长 30 天。Job/Window 按 ID 倒序，Run 按 `(created_at,id)` 倒序。 | `POST …/collection-runs/` 用 `{job,target,reason,request_id}`，202 受理，可能合并或已有终态。503 可含 `run_id,window_id,dispatch=unconfirmed`，沿原 ID 查询。Job POST 用 `pause/resume/resume_source/resume_product`，携目标种类对应版本、规范 UUID、允许的理由。503 后先等待并查原 Run/Window。`…/collection-runs/{uuid}/` POST `redeliver` 要求原 Run `pending + unsent + dispatch=manual`、Run/Window 无活动 owner、无待重试、窗口允许本意图，另核 `expected_version`、当前权限、可回溯能力和预算。查 `collection/{views,control,services,recovery}.py`。 |
| 窗口、子项与影响 | `…/collection-windows/{id}/` 使用 `after/run_after` 两种 50 行分页，可按 `run/object_code/indicator`。`…/collection-runs/{uuid}/` 实际复用窗口投影并限原 Run。读 `runs/items/receipts` 等实际投影，核 `batch_id`、键、修订、质量事件和 truncation。批次内证据经 `admin/observation-evidence/` 按 `product + batch` 或受权观测键取，支持 `after`。 | 分列本次 `Run.status/dispatch/request_state/result`（有字段才呈现）与 `WorkWindow.state/result/coverage(E/O/V)/cells`。一次 Run 可以是 `finished + no_new` 同时窗仍 `open + partial`。`new/changed/quality_only` 分别给新增/修订/仅质量回执。`partially_authorized`、`result=null`、子项/回执截断或不可披露时按已投影范围展示。应用链接从此窗到相应 Run/批次/事件，维持产品和时间上下文。[窗口投影](../../backend/collection/queries.py) |
| 历史导入 | `admin/history-imports/` GET 列表与 `/{attempt_id}/` GET 状态。提交用主机已注册的 stage 引用，具有 `import_history` 的操作者看本人，审阅者依 `review_observation` 及 `retain_normalized/review` 看获权状态。 | POST 202 或 `503/dispatch_unconfirmed` 携 `attempt_id`，查原尝试。介质登记/字段核验、按条件重投用主机 `s02_stage_uci/s02_register_uci/s02_redeliver` 等既有流程，网页只接收契约要求的标识。[导入视图](../../backend/observations/views.py) · [状态投影](../../backend/observations/history_status.py) |
| 数据质量与站址 | `admin/quality-issues/?product=…&scope=…` 按 `run/window/status/before` 筛选，ID 倒序，`next_before`，50 条可见事件。详情含级别、规则、处理人、`event_version`、Run/Window/Item/Batch/Receipt 与 `affected`，有证据权才给当前修订、水位、候选和签名引用。站址变动 GET 按 `action/product/identifier/at/target` 的适用组合取得范围、版本及证据。 | 仅审阅可读，裁决另需 `accounts.adjudicate_quality`，站址操作另需 `manage_catalog` 和适用用途。`claim/hold/close/correct_source/correct_mapping` 以规范 UUID、理由、期望版本和动作所需证据执行。`rename/relocate/reuse_code` 通过 `…/stations/{station_id}/changes/` 预览再提交。展示 `action_id/status/event_version` 或目录 `version`、已返回的 `outcome/affected`，重放的精简回执再回读。对争议、可信新版、同值推进、迟到及 `mapping_review_required` 各留轨迹。[质量服务](../../backend/observations/quality_governance.py) · [站址服务](../../backend/observations/catalog_governance.py) |
| 系统运行、账号、处置 | `admin/operations/health/` 的 Web、PG、Redis、Worker（订阅/最近消费）、Beat 分组件给状态/时标/依据。带获权窗口才有 `visible_*` 和 `truncated`。`…/trends/` 按产品及最多 30 个源地方日、1,440 个获权窗口，给逐日 E/O/V、首次新观测窗率、终结尝试响应率、等待/执行/排期与未知分母。`/health/ready/` 只探 Web/PG。 | 来源异常在产品、窗口/错误摘要中定位，采集或响应时间与源发布时间区别显示。原生 `/admin/` 账号表单办理启禁（active staff + `manage_accounts`），`admin/accounts/{pk}/actions/` 另支持授权/撤销与角色组调整，领域服务核本人/下属/权限上限。许可撤回需预先确定 grant ID，处置需 product ID。按 §6.1 的受控交接进入 `admin/grants/{id}/actions/` 或 `admin/disposition-products/{id}/`，展示权失效、留存到期和待办各有对应状态。[健康](../../backend/accounts/operations.py) · [日趋势](../../backend/collection/s07_admin.py) · [受限 Admin](../../backend/accounts/restricted_admin.py) |
| 保管人及测试入口 | `admin/backup-products/{id}/segments/`、`backup-segments/{id}/publish/`、`…/restore-intents/` 与 `backup-authority/{action}/{id}/` 分别服务获准准备、发布、短期意图和保管人复核。旧 `admin/fixture-runs/`、`fixtures/observations/` 由隔离夹具/兼容测试调用。 | 网页仅在业务需要且有明确用途时呈现获权状态/意图。保管方包外签名、暂存介质、清理、匹配空目标和 `s06_restore` 实际执行由受控主机负责。用户端 F 仍走同一套观测/研究 API。[受控操作说明](../operations/S07_handoff.md) |

管理读取与写入的能力分别授予。S07 的 Job 目录/控制使用 `observations.manage_collection`，S08 新建/改期/确认用 §6.1 的独立配置权限，执行用 `observations.run_collection`，`reason=review` 另核审阅。Window/Run 读需 `review_observation`。质量写和站址变更再核具名权限。健康用 `accounts.diagnose_operations`，带窗/趋势还需对应账本审阅权。`collection_operator`、`quality_reviewer` 权限组和 staff/superuser 标记均不能概括上述组合。服务端按请求、目标和当前来源用途作最终裁决。证据见 `accounts/{governance,session_generation}.py`、`collection/s07_admin.py::principal`、`observations/quality_governance.py::user_permission`。

### 5.4 全端错误和恢复

| 收到什么 | 应用行为与复验 |
|---|---|
| `200/no_data`、`no_coverage`、精确读的 `reason`、部分投影 | 区分授权空窗、筛选无匹配、有明确依据的范围外、缺投影及部分成功。显示最近有效的入口需用户主动选择。保留服务给的 null/未知，禁止外推隐藏数量。 |
| `403/not_authenticated`、登录 `403/invalid_credentials`、`403/csrf_failed` | 前者推进会话代次，清掉私有视图与在途输出并引导重登。登录凭据错误就地提示。CSRF 失败重取 token，幂等读取可由用户重查，写入依该动作的查账与重放契约。 |
| `403/use_denied`、`403/permission_denied`、私有 `404/not_found` | 收起该对象/用途和关联明细、图、票据入口与缓存，复核能力，保留合法会话。私有对象统一提示无法访问，受限对象不提供名称、计数或存在性线索。 |
| `400/invalid_request`、`query_too_large`、`invalid_cursor`、`409/page_changed`、`422/incompatible_series` | 按端点提示整点/窗、合法组合或缩窄范围。S04 通用分页默认 50 条，最多 100 条。签名游标有效期 1 小时、≤2048 字符，绑定路由、用户、条件和页长。条件变化、过期或行/权限变化时废弃后页从首页读。实际的 `next_before` 和 `after/run_after` 适配各自协议，停止由返回的末页标记决定。 |
| `409/input_changed` 或 `method_changed`、`410/expired` | 保留安全查询意图，重跑父查询确认新输入/方法，再取下钻或重新申请票据。显示到期时间，按现权申请。区分方案 PATCH 的意图竞争、重跑的 200 状态与研究/下载失败。 |
| `409/precondition_conflict`、`request_conflict`、`window_contract_conflict`、超时/连接中断、503 投递未确认 | 先查原 Run/Attempt、对象现态/目标版本、已知回执或 §6.2 的本人完整命令查账。查询不足以证实时显示待核，交原操作者查账。相同意图、原信封、重放前置条件均成立才用原 UUID。修订命令需重新审阅并使用新意图标识。高影响动作关闭自动重试、取消按钮只影响客户端等待。 |
| 429、数据库不可用 503、405/406/415、未匹配路径与原生 500 | 区分限流、临时故障、内容/方法错误，停止轮询并按需重试。HTTP 状态、`Content-Type`、契约 `code` 一并核对，204 无需解析。HTML/非 JSON 给通用提示，拒绝把正文或堆栈插入页面。CSV 接口仅按成功附件类型处理。私有 `no-store` 与框架提前拒绝的头差异见 `docs/api/v1.md`。 |

## 6. 冻结契约差异与已确认的取舍与增强规格

以下是供最终批准的实施规格，路径相对 `/api/v1/`。S07 的 66 项操作是兼容回归基线。未选择新投影的旧请求继续使用原字段、排序、游标、权限和错误。阶段一为下列每项登记旧请求、新请求、最小权限成功、拒绝及撤权样例，核 resolver、OpenAPI 闭合 schema 与真实 Session。各切片实施时同步更新 `docs/api/{v1.md,openapi.v1.json}` 和增量 V12，保留原标签及其收据。超出本节列举的字段、用途或写动作须另审。

**读请求的共同约束。** 当前有效 Session，私有响应 `no-store`。两个新增 GET 目录省略 `scope` 表示正式域，F 显式传 `scope=synthetic`。每次只取一个域、一个用途，`page_size` 默认 25、上限 50，按稳定获权键续页。签名游标绑定用户、用途、域、条件、页长，最长 1 小时。页尾、`next_cursor` 和条数只取获权项。拒绝未知或重复参数、越界 ID/时间/枚举（400）。缺功能权限 403，获准发现无选项 `200/results=[]`，显式目标的不存在与不可披露同形。读取结束复核 Session 及适用授权。新增 POST 均校验 CSRF，命令外层遵循 §5.2 的 16 KiB 限额。

| 页面与调用 | 输入、输出及限额 | 权限、拒绝与兼容回归 |
|---|---|---|
| 工作区导航 · `GET me/capabilities/` | 无参数。返回 `workspaces`（user、适用时 manage）和 `actions[]`。只取 `run_collection,manage_collection,configure_collection_schedule,review_observation,adjudicate_quality,manage_catalog,import_history,diagnose_operations,manage_grants,manage_accounts,maintain_city_search_alias`。前述标识映射到服务的具名权限，裁决/站址操作还需 `review_observation`，账号/字典入口还需 active staff。空数组只给 user 工作区。 | 用 `accounts/profile.py::current_user` 及当前组/直接具名权限，只给可进入的工作流。选择目标后仍逐对象核权。过期 `403/not_authenticated`。回归普通、staff 无权、各单权及组合、改密和撤权的双会话。原 `GET/PATCH me/` 的三字段 `Profile`、登录/注册响应保持兼容。 |
| 研究工作台从零选数 · `GET catalog/series/` | 普通展示仍走 `catalog/objects/`。新读仅收 `purpose=analysis,method=statistics/compare/pearson,start,end,scope?,product?,city?或site_epoch?,indicator?,page_size?,cursor?`。发现请求采用带时区的半开窗口，最长 30 天，可先不指定产品/对象。实际长窗分析继续执行 S05 的选数上限。一次至多一项产品和一项城市或站址期。每行一个可选组合 `source/product/shape,city或site_epoch,indicator,unit,report_kind,window_definition,standard_series,source_timezone,permitted_from,permitted_until`。区间限本次所选范围与确证身份/授权的交集。以产品、对象、指标、窗、标准、许可片段起点组成稳定续页键。 | 复用 `research_access.py::uses`、`analysis_query.py::compatible` 的方法、平台留存、分析与 LOCAL 派生展示口径。仅有派生许可可选合法组合，原始曲线/明细另核。SQL 先按获准地域、身份、字段、时间限制候选并按键分页。多对象比较、指标关联的组合仍由提交时的分析服务校验。选项表示可请求，普通目录锚点只证明一个观测。缺少覆盖依据时由用户选窗，E/O/V 从实际分析取得。回归单权、两站同数据集 H、越界、撤权、权外对象/新增观测对目录及游标无影响。 |
| 管理工作台选产品/目标 · `GET admin/catalog/products/` | `purpose=execute/configure/ledger/quality/operations,scope?,product?,page_size?,cursor?`。`execute` 必带一项整点 `target` 与 `reason=manual/review/backfill`，每行仅 `product,job,reason,target`。`configure` 给主机已登记的 `template,template_version,product,source,contract_version,scope_label,allowed_cadences,plan_state` 及获权计划引用。其余三种带源地方日 `[start,end)`（≤30 天），仅返回有适用范围的 `product,source,shape,links`。同一目的下按产品及目标稳定键排序。 | 执行分支按 `admission.py` 核操作者和登记执行者的 `run_collection`、目标窗、端点可取性、用途、暂停、限额与计划归属。`review` 再核 `review_observation`，补采再核获准缺口/时效。发现只读取，不预占配额。`configure` 按 §6.1 的配置权限与范围。账本/质量复用 `s07_admin.review_window` / `quality_governance.checks` 的审阅谓词，趋势还需 `diagnose_operations` 与 `permitted_days` 的时段授权。仅诊断可单看原组件健康。回归纯执行可选目标、提交、查本人回执，访问跨窗/他人明细拒绝。原 Job 列表与 `catalog/objects/` 继续按各自用途。 |
| 全国总览 · `GET overview/national/` | 在 L 上附加 `order=aqi_asc`、单值整数 `grade=1..6`。`order` 只给表格排序。`home=1` 选择单次投影，才接受 `rank_preview=aqi_desc/aqi_asc`、单一稳定 `focus_city`、可选 `map_limit=1..400`。沿旧结构返回 `summary/results/page/snapshot_at`，附 `ranking{state,rows}`（最多 5 城）、`focused`（最多 1 行，无匹配为 null）。请求点位时可附 `map{state,items,has_more}`，详见 §7。沿原 `page_size≤100`。首页用独立签名游标。 | 排序与等级按同一快照、明确已核标准、`_rankable`、`overview_summary` 授权。表格显式请求 AQI 升/降序或 grade 而缺汇总权时 403。普通城市序可返回表与不可用的可选榜。等级越界、显式请求分级/AQI 排序却缺已核标准时 400。低值按 AQI 升序取名次，保留真实 0，同值用 dense rank 并列、城市键确定展示顺序，筛选在分页前。首页 `q` 的单一别名扩展限 §6.2。分别对旧请求、低值、仅普通读、部分汇总、权内订正/权外变更和跨页选城做 PG/HTTP 对账。 |
| 本人已提交命令 · `POST admin/command-receipts/lookup/`（只读） | 一次一个 `{family,target,command}`。`command` 复用原端点完整请求体含 UUID，`target` 为原业务入口标识。`family` 限 `manual_run,job_control,redeliver,schedule,quality,station_change,grant_withdraw`。返回 `state,action,recorded_at` 和原契约已获准的 `action_id/version` 或 `run_id,dispatch`。原结果与当前运行/投递状态分栏。禁止以查账创建 Run 或重投。 | §6.2 逐项核本人、原命令、目标、现权。跨人、错目标、错信封、无可披露收据同为 `404/not_found`。无法确认提交时显示「待核」，不自动重放。验纯执行、跨账号同 UUID、已暂停、撤权和原 202/503 响应丢失。原控制端点的精简重放形状照旧。 |
| 从对象查治理经过 · `GET admin/object-audit/` | `kind=job/run/quality_issue/station/grant,id,start,end,page_size?,cursor?`，必指向一个已知对象，最长 30 天，时间倒序、记录键破同序，按目录限额。只返回 `at,action,outcome,actor_relation,actor_ref?,before_version?,after_version?` 和可重鉴权的 `affected_link?`。 | §6.2 对每条记录及其原范围重核。纯执行只能查本人命令。分页只基于可披露记录。验部分授权、无合法条目、跨对象、原 Job 上下文、撤权及并发新增。全局按人/UUID/时间检索归主机。 |
| 系统运行 · `GET admin/operations/health/?window=…&include=first_valid_write` | 仅在指定单窗时接受新枚举 `include`，沿用可选 `object_code,indicator`。在 `window` 中附 `first_valid_write_state=known/none/unavailable`、`latest_first_valid_write_at`（带时区时间或 null），口径见 §9.1。无此参数的健康、趋势和窗口覆盖响应照旧。 | 需 `diagnose_operations`、`review_observation` 与该窗、对象、字段的留存和审阅用途。只从获权、可证明的键/修订/裁决投影。复用 `visible_*` 积压和获权 Run 错误码。用先缺后有效、重复、迟到、空窗、不可证明及权外写入复验。无全库最近时刻查询。 |

**勘误与复用。** `overview/national` 的 `window_definition` 实际只收 `hourly_label_unspecified/hourly_left_label/hourly_right_label`。正式省略 `product` 时查 `cnemc/city-list-hour`，显式 F 必传。规格修正条件必填和该端点枚举，测试 `at/as_of` 互斥、3 种小时窗/3 种拒绝窗、缺产品/缺用途和合法空窗。`discovery.py::entry` 的 L `link` 保留实际小时窗与有据的系列，做锚点往返。`QualityIssue` 的示意 `version` 更正为实际 `event_version`，核详情、动作、精简回执与两人竞争。复用 `tests/test_s04_national.py`、`test_s07_catalog.py` 及 V12 对照。

首页次排固定为所选城市获准 T 近期 AQI（标明城、产品、锚点和观测时间）、同城同刻 L `levels`、本次 L 快照可披露的覆盖摘要。缺 T 或等级响应时给合法空态及覆盖说明。全国 6/12 小时到报和首要污染物分布聚合在 S08 范围外，逐城获准的源报首要污染物仍可显示。CSV 采用 §5.2 现有整组票据与 `s05.csv.v1`。冻结回归包含 V04/V07/V10/V12 的行数、版本、字段与双时点用途拒绝。

### 6.1 周期任务、恢复与主机交接（M08–M10）

**配置契约。** 主机登记来源条款、适配器、产品及可用模板。模板明确固定的来源/产品、城市或站群、执行者、profile、选择范围、标准、名册、获准频率及补采策略。模板 ID 指向主机明确登记的可用配置，版本只覆盖该模板的许可范围与操作参数。模板登记和改版沿受控主机流程。Vue 只传模板引用、允许的频率和未来起始投递时刻。模板摘要和变更均需新增独立权限 `observations.configure_collection_schedule` 及目标范围的 `retain_normalized/review` 账本用途。复用 `register_job` 的注册、准入及预算校验，保留旧主机入口的 `manage_collection` 检查。执行者仍须 active、持 `run_collection`，并满足 `acquire_online/process_local/retain_normalized`、实际端点和来源限额。现有 `cnemc_jobs.configure` 拒绝给 `validation_grant_id` 安排周期，模板登记与激活保留该拒绝。F 仅在隔离夹具环境开放。配置者不因配置权限获得执行、来源解暂停或全账本审阅权。[注册与调度](../../backend/collection/services.py) · [准入](../../backend/collection/admission.py) · [来源时钟与重叠校验](../../backend/collection/cnemc_jobs.py)

| 调用与目标 | 允许的命令、返回与拒绝 |
|---|---|
| `POST admin/collection-jobs/` 首次创建 | `action=create_draft,template,expected_version`（模板版）、`cadence_seconds,first_due_at,reason_code,request_id`。频率必须在模板枚举内，F 采用 1–24 小时内获准的整小时频率，CNEMC 保留已审投递分钟与频率限制。`first_due_at` 为带时区的未来投递时刻，转换为目标整点 B 后也须在未来。只产生禁用草稿，201 返回 `action_id,job,state=draft,version,template_version,first_due_at`。同范围已有活跃计划应走后继流程。 |
| `POST admin/collection-jobs/{id}/schedule/` 准备后继 | `action=propose` 以当前链尾为 `id`，传原 Job `expected_version,template_version,cadence_seconds,first_due_at,reason_code,request_id`。201 回 `action_id`、草稿/前驱 ID 与两者**提交后**版本，以及预览。`GET` 同路径只读当前对象，给 `state,version,template_version,predecessor?,pending_successor?,effective_from,effective_until` 与切换预览（B、旧末槽、新首槽、计划间隔、可披露冲突状态）。不能从已有 Job 变更来源/名册/执行者。 |
| 同路径 `POST` 确认启用或放弃 | `id` 为草稿，`action=activate/discard,expected_version,template_version,expected_predecessor_version`（首份为 null）、`request_id,reason_code`。200 回 `action_id,state,job,version`、适用时回前驱新版本/生效边界。放弃保留审计并释放待启用位置。`create_draft/propose/activate/discard` 分别仅接受固定理由 `schedule_create/schedule_change/schedule_activate/schedule_discard`。重复命令先核现权，再按操作者、原 UUID 和完整命令回原 `action_id` 及结果，换意图 `409/request_conflict`。旧版本、过期 B、非链尾或分界已有账本 `409/precondition_conflict`。输入不合规 400，无配置权/用途 403。 |
| 已启用计划的人工控制 | 原 `POST admin/collection-jobs/{id}/` 的 `pause/resume/resume_source/resume_product` 继续按 `manage_collection`、目标版本和现行准入办理。Vue 对高影响动作总带理由/UUID/版本。待启用草稿的原 `resume` 返回状态冲突，只能按上行原子启用。旧 Job 到界后 `resume` 仅恢复其所辖目标的执行资格。回归既有 S07 Job 控制、Run 重投与受控主机注册。 |

**切换与恢复的实现条件。** 现有 `scheduled/reconcile_due` 用可变 `next_due_at` 选槽，`scan_gaps` 同时用它决定是否扫描和频率相位。`admission` 核 `enabled`、Job/来源/产品暂停及执行者。`ensure_schedule` 当前按同产品、城市、同频率判重，并另核站群模式重叠。`register` 会先造 Window/Run 再判时效，未来分界已有失败 Run 也要计入冲突。`s03_beat_slot` 只保证单 Job 的时钟唯一。[Job、窗口与认可子项约束](../../backend/collection/models.py) · [恢复扫描](../../backend/collection/recovery.py) · [已有配置冻结](../../backend/collection/migrations/0007_cnemc_window_contracts.py) · [控制版本触发器](../../backend/collection/migrations/0009_control_versions.py)

1. **唯一归属。** 在现有 Job 账本记录前驱、不可变 `schedule_anchor_at`、计划状态和 `[effective_from,effective_until)`。以 `target_end − 1 小时` 作归属键，T 用同一规则取得 24 槽的**锚小时**。未来分界 B 取新首个 `first_due_at` 对应的 UTC 目标整点。`< B` 归旧、`≥ B` 归新。投递分钟与频率分别按原/新相位计算，预览列出切换前后实际计划时槽。沿用 WorkWindow 的产品/业务范围/目标窗/profile 唯一键及每窗 `item_key` 认可唯一约束。在源锁内将时钟重叠校验扩展到不同频率的同业务范围。保留站群模式约束。旧 Job 只在受控接入且相位和业务范围可证时迁入这套分界。
2. **草稿与事务。** 草稿没有生效段，原计划照常登记。每个前驱最多一个未放弃后继，根模板同一范围也只允许一条生效链。修改未启用草稿先按原版放弃，再提新草稿。B 已到须重选未来 B。连续变更从已确认链尾发起，分界严格递增。配置写与手动受理在 Source→Product→相关 Job（稳定 ID 序）的锁内锁操作者/执行者，重核 Session、用途和状态。调度和后台扫描沿同一锁序核登记执行者、用途与限额。激活需前驱可运行、来源/产品均未暂停。丢弃草稿只核操作者现有配置权、目标审阅用途和版本，不执行采集。事务内复核模板/草稿/前驱版及 `[B,+∞)` 同业务范围有无已登记 Window 或 Run，有则 409 保留原账，由操作者另选 B。冲突检查先核整个配置目标的授权，响应只含可披露的冲突状态。成功一次写前后段、控制版本和回执。把草稿引用及归属字段纳入配置版本触发器，游标进度不升配置版。并发变更只接受一份。事务提交前，旧计划继续生效。
3. **到界与恢复。** `scheduled` 在锁内截断 due 和 skipped slots，到界是正常退出。旧段可结束实时游标，保留原 `enabled/pause_reason`。手动、补采、Beat 登记新 Run 都先判目标归属。迟到 Worker、重投、租约恢复、同 UUID 原命令沿原 `run.job/window`。`reconcile_due` 的缺口候选和 `scan_gaps` 改以固定相位，在旧段与当前许可、运行日历、lookback 的交集内，从 `backfill_cursor` 有界续扫，包括获准而未建 Run 的缺口。仍检查缺口资格、单次认可、执行者/原操作人适用权限、取数时效、来源/产品/人工暂停与配额。人工暂停的对象仍是指定 Job，覆盖它的已登记 Run 和允许补采的历史范围。后继只能在 B 到达后受理其首槽，在锁内按控制账本复核分界时的暂停。若前驱在切换时仍人工暂停，后继落为暂停、记录原因，需 `manage_collection` 按新 Job 的版本显式恢复。迟到扫描按生效时的控制事件判定。来源/产品暂停约束两段，其解除仍保留各 Job 的人工暂停。`allow_paused=True` 仅用于既有登记/启用的预检，运行和恢复走常规准入。已过最新端点可取时限的旧窗记录不可补，外部请求数为零。
4. **验收。** 独占 PG/Redis/单 Beat/Worker，以 F 及 I 预列 B 前后时槽。查未启用、放弃/重提、B 上已有窗口/未投递或失败 Run、两人并发、同 UUID 同/异意图、连续两次改频、跨 B 重启/延迟投递。对账每槽单归属、同窗同子项最多认可一次、原 Run/历史缺口仍归旧段。分别在激活前、激活后 B 前及跨 B 暂停 Job、来源或产品，撤执行权或用途，验证切换和恢复受相应控制，受拒端点调用数为零。A10 策略用隔离输入和故障注入复验有界历史扫描，证据标 I。延用 `test_s06_collection.py`、`test_collection_postgres.py`、`test_cnemc_jobs.py`、`test_cnemc_recovery_catalog.py` 与 V03/V08/V12。

**许可撤回。** 主机向指定操作者交接获准 `grant_id/product`、受众、用途、对象/字段/时间范围与核对依据。交接资料用于人工核对目标，服务端仍以该动作的具名管理权裁决。Vue 用现有 `GET admin/grants/{id}/actions/` 核 `grant/product/version/state`，匹配交接后以 `manage_grants`、`withdraw,reason_code,expected_version,request_id` 确认，回 `action_id/version/state`。不一致则停在待核。处置预览限已交接产品，全局审计、条款原件、保管人签认、实际恢复与受控清理在主机。复验仅持该权限者撤回 F 展示、错目标、旧版、本人查账、兄弟数据可见和留存处置（§9.1 T08）。[撤回与处置](../../backend/observations/disposition_control.py)

### 6.2 低风险字典、本人回执和对象审计（M08–M10）

**字典默认只读。** `City.name` 进入来源重复登记、采集入库的城市名核对、全国检索和收藏。`City.geography` 参与授权，正式代码与站址期参与身份、时间和观测归属。`Station.name/SiteEpoch.name` 也进入目录及备份。有证据的站码、迁址、别称已有站址治理服务。上述原表字段、来源产品契约、许可和官方观测均排除在 Admin 写白名单外。[注册](../../backend/collection/registration.py) · [采集写入](../../backend/collection/writer.py) · [目录治理](../../backend/observations/catalog_governance.py) · [受限 Admin](../../backend/accounts/restricted_admin.py)

**提请最终批准的最小替代**是一个实际供全国页 `q` 使用的本地城市检索别名。以已登记 L `product + city` 唯一绑定，只维护一个 `search_alias`（空值清除）。Admin 按获准目录逐对象打开。仅 active staff + 新具名权限 `accounts.maintain_city_search_alias`，并核该产品/对象适用 `catalog` 的审阅与留存范围。该词只在同一产品 `home=1&q=…`、当前时刻获准展示 `catalog` 的城市内匹配，界面仍显示正式名。表、获权汇总和独立名录的 `q` 谓词一致。原 v1 普通 `q`、身份解析、上游登记、S05 查询及输入版本沿原口径。此替代项列入定向复核。M10 字典维护以获批字段在真实检索中的实际读写为验收依据。

- Admin 限单对象操作 `set/clear`，只在受限 Admin 的 `GET/POST /admin/observations/city-search-alias/{product}/{city}/` 读写该词。GET 回绑定对象、现值、版本，POST 经 CSRF 校验后回执或给出字段错误、403/404、409 版本/意图冲突。唯一可写业务字段为 `search_alias`。固定绑定、枚举理由 `search_correction`、规范 UUID 与 `expected_version`（首写 0）。去首尾空白、Unicode NFC，1–32 个字符，仅文字/数字/内部空格或连字符，拒绝控制符、URL、富文本和多值。相同别名不用于唯一解析。事务锁产品/对象，首写防并发，先识别原信封后校验版本。清除词时保留单调递增版本，重建同一词也不得接受旧的首写版本。回执记操作者、目标、动作/理由、前后版本、时刻和结果，参数只留服务端指纹。独立别名记录随目录留存/处置清除，包含因 Job 引用保留 City 的分支。授权未覆盖的材料不进入备份或再分发。[目录处置](../../backend/observations/disposition.py)
- `StaffLogin/RestrictedSite` 对 active staff 的准入分账号与字典两支。`AccountDirectory.has_module_permission/has_view_permission` 和控制 URL 显式要求 `manage_accounts`，字典站内列表/写 URL 重核字典权与对象范围。禁通用 add/change/delete、批量操作、未核权历史和其他模型入口。将两个新增具名权限纳入 `accounts/governance.py` 受限委派白名单，不自动授予旧角色组。验仅字典员、仅账号管理员、两者组合、深链越权、两人写同版、撤权、别名清除、权外同名不改变命中/页界，复验原账号治理及正式城市登记。

**本人回执。** 读取先由 Session 确定 actor，按命令族选原账本。手动触发查 `Run(actor,request_id)` 并核原 Job、reason、目标小时。仅有执行权者按原 Job、时槽、执行权限与当前允许披露其提交状态的来源用途取回执，无须账本审阅权。收据只证实本人提交/投递，查询不取窗口全体运行、计数、观测或其他操作者身份。重投还核原 Run，许可撤回核交接 grant 与现行管理权。其他命令查 `GovernanceAction(actor,request_id)` 及对应控制/裁决记录。服务端重建原 `target_kind/target,action,reason_code,before_version,parameter,origin`，配置创建按原模板、提案按原前驱、启用/放弃按原草稿定位，首次响应丢失也无需猜测新 Job ID。参数包含原模板/执行目标/时槽或证据信封的规范指纹。摘要不可由客户端自报。按原动作现时核应用权和合法范围（来源/产品控制还核原 `origin=job`，映射类核涉及两边范围），再返回本人最小结果。查账验证**原命令**的前置版本，控制对象的**当前版**另读。权限与用途仍适用时，可在动作完成后查回原结果。欠缺合法依据、旧无 UUID 快捷动作或记录无法匹配时保持待核。导入仍凭既有 `attempt_id` 与本人/现权投影，账号/字典用受控 Admin，保管动作由主机查账。双账号同 UUID、同人错目标/理由/版本/参数/入口、失权、撤令后原版回执及网络超时分别验。[命令与重放](../../backend/accounts/governance.py) · [采集控制](../../backend/collection/control.py) · [手动受理](../../backend/collection/services.py)

**对象审计。** 每条均从指定对象回溯现有 `ControlEvent/GovernanceAction/QualityDecision/CatalogChange`。Job 先核 `manage_collection` 与 `review_job`（配置动作可由具配置权者按其模板范围审阅）。Run 需 `review_observation` 和对该 Run/Window 及操作影响范围的完整账本审阅。质量事件需 `review_observation`，按 `quality_governance.checks` 复核事件、对象、字段及纠正所涉双方。站址先核 `review_observation`、`manage_catalog` 和各生效时期的目录用途。grant 只受理已交接对象，以现行 `manage_grants` 核回执状态。来源/产品级控制仅在原 `origin` 与其完整作用范围均可审阅时返回。不可证的记录不参与分页或计数。操作者投影默认 `self/other/system`，仅另有账号与该操作者可见依据时显示受控标识。排除原请求 UUID、签名证据、原始值、条款、跨人全局检索。测试同一对象两人操作、相邻但未授权对象、部分授权与撤权后的响应和页尾。主机按保管权限核查完整审计。

## 7. 正式工程组织与关键实现选择

| 范围 | 推荐实现与验证点 |
|---|---|
| 项目结构与路由 | 单一 `frontend/package.json`、`src/main.ts`、`App.vue`、Router/Pinia。按实际页面形成 `pages/` 或小型业务目录，`layouts/` 管用户/管理壳，`api/` 管分域请求、`shared/` 管实际共用的控件、格式化和图表宿主，`styles/` 管全局及命名变体。测试靠近业务或放 `e2e/`。首批路径拟为 `/app/` 总览、`cities/:id`、`stations/:id`、`analysis`、`query`、`me`，`manage/{collection,quality,operations}` 以及 `login/register/about/sources`。历史 `/:pathMatch → /city` 替换为可达的 404。站址期、产品、时窗和安全返回路径随实际资源传递。详情可分享、可返回，菜单按能力显示。 |
| 开发同源 | Vite 仅监听已核闲置回环端口，`server.proxy` 将 `/api/`、`/admin/`、`/health/` 转到经检查的同一隔离 Django 实例，`strictPort` 防悄悄换端口。凭据只走同源 Cookie。保留浏览器 Host/Origin 令 Django CSRF origin 与请求宿主一致，在实际 POST 上验证。若代理需要重写 Host，仅对专用开发 origin 做精确配置并回归拒绝外域。URL、JS 和仓库不得带私有配置。 |
| 生产构建与深链 | 推荐直接复用 Django/Gunicorn + WhiteNoise。Vue Router history base `/app/` 与 Vite 生产资源 base `/static/app/` 独立配置。单次 `npm ci && npm run build` 生成带 hash 的 JS/CSS/字体、路由 chunk 和壳 `index.html`。在受控构建阶段将静态资源归入 `collectstatic` 的 `app/` 命名空间，HTML 放在专用只读壳路径，主机入口与镜像均消费同一组产物。Docker 可用锁定 Node 构建层。现有 `.dockerignore` 默认排除全部路径，构建阶段只放行经审的前端源、入口、必要配置、manifest、锁及可分发资源。用实际镜像验证所需文件齐备，`photo/`、本机 `node_modules`、账号私件和验收材料均在上下文之外。断网重建需事先准备依赖/镜像并记录摘要。根 `/` 指向首页，SPA 的导航 GET/HEAD 仅对 `/app/` 的前端路径返回壳，缺文件的 `/static/` 及 `/api/`、`/admin/`、`/health/` 的错误继续按原入口处理。壳重验证，hash 静态资源可缓存。私有响应禁止缓存。以正式 WSGI 验直接请求、刷新、旧 chunk 失效、非 GET、Admin 静态和 API 404 的状态/媒体类型。`tests/s07_contract.py::routes` 与 `test_s07_contract.py` 显式分类新的非 v1 HTML 边界，保留 v1 全量差集约束。 |
| 会话和 API Client | 同源 Axios 实例、相对 `/api/v1/`、超时、取消、JSON 写与 CSRF 头、CSV/204 专门分支。验证服务 `link` 的 origin、路径白名单与所属域后路由到本地页面。按 endpoint 声明 DTO（包括 null、部分投影和空数组）及轻量响应适配，保留精确字符串、UUID、带偏移时间和稳定键。Django `BigAutoField`/请求正整数可超 JS 安全范围。推荐从 JSON 解析处检验整数，无法无损表示的 ID 拦截并报不支持，不对近似数发请求。若需支持全 int64 域，使用可验证的无损解码及数值序列化，或另审字符串 ID 契约。两侧边界值、同名/近似 ID 与 URL 回环必须实测。 |
| 状态、竞态与隐私 | 路由只存获准分享的非敏感条件（工作区/页签、产品/城市、站址期、时间窗、指标/排序），先复核再显示。规避 URL 中的 Cookie、票据、签名证据、候选标识、个人资料和观测值。页面分草稿条件/已应用条件，分析显式「应用」。Pinia 只管当前 session/capabilities、数据域及需跨区协调的选中对象。分页与已应用父查询在页面内存绑定身份、域、产品、过滤、输入版本。hover/焦点、弹层、地图视野为独立临时状态。不要全局持久化 store。浏览器新开深链重新取得适用投影。 |
| 请求与清理 | 读取按 canonical query、递增请求代次、当前会话/数据域归属接收结果，改窗/换页/离开中止 `AbortController`，迟到的成功和错误一并丢弃。账号切换、注销/改密、真实会话失效推进 session generation，清 Pinia、图例/图表实例旧点、表格/详情、受限 URL 与下载 Blob。用途/能力收窄立即收起已知受限范围。回焦、恢复标签/bfcache、重新进入工作区及高影响动作前重新验权。在同源多标签传播失效信号即可，消息仅带失效标识。无轮询写行为，用例验证撤权后旧请求无法重新填屏。 |
| 可视语言与共享控件 | 由 DESIGN 取天空渐层、云白内容面、深蓝读数、品牌/焦点/状态色、字阶和等宽数字。建立基础色→语义 token→Element Plus `--el-*` 与 ECharts 主题映射。功能玻璃（导航/短控件/浮层）和稳定内容面（长表/图）分级。建立实际会复用的壳、来源时间/质量标识、筛选与已应用摘要、空错/部分态、表格/游标、证据 Drawer 和高影响动作 Dialog，配全局 overlay host、具名 Popper class。实体主题覆盖 KPI、卡片、Tip、菜单、Dialog、Drawer 及遮罩。照明层/边缘、阴影、字号和密度按实际屏幕调节。 |
| 层级与图表交互 | 在锁定 Element Plus 组件上测试公开 `append-to`/`teleported` 等挂载选项，模态内 Select、Date Picker 的展开面板处于本模态可访问树，处理 z-index、overflow、滚动锁定、焦点环、Tab 陷阱及逐层 Esc/还焦。ECharts 在有尺寸且可见容器初始化一次，按稳定 series/对象键增量更新，保留仍有效的 zoom、图例/选择。`ResizeObserver`、事件与实例在卸载/权限变化时释放。缺测留断线，范围桶画极值/断档，服务不披露点时给可用摘要。图表均配数据表/键盘等价入口，等级配文字/形状。动作反馈使用 CSS/Vue Transition 和既有 ECharts，支持连点中断，减少动效时即时反馈。 |

### 全国总览中选择与查询的约定

1. **一次读取同一 L 快照。** `home=1` 在同一 `ReadContext`、产品锁和结束核权下给出当前 `summary/results/page` 及获准的 `ranking/focused/map`。本次读取在同一数据库快照中完成，或由覆盖全部写入路径的锁保证一致，含名录、别名与质量修订。首次解析 `snapshot_at` 后按该观测时刻取后页。表、榜、分布、选中城市及点位共享地域、名称、小时窗、标准和等级条件。等级只取获准的已核系列、单位 `1`、`comparable=true`、后端 grade 1–6 的 AQI。污染物 `levels` 在选城卡标为污染物等级。等级筛选后 `expected=null`、`completeness=unknown`（缺报者的等级未知），按筛后本标准 `ranked_city_count` 标明分布分母。只获部分汇总权时只排可披露子集。只有普通读权时按 `order=city` 给列表、`summary/ranking.state=unavailable` 与说明。用户显式设置无权 grade/表排序而遭拒后，可主动清筛回普通列表。沿用正式首页构图和信息密度。[排名与名录](../../backend/observations/s04_national.py) · [读取授权](../../backend/observations/selection.py)
2. **分页与选择。** `order` 控制表格，`rank_preview` 控制五城榜，`focus_city` 只取当前条件下的一条获准行。第 2 页换榜向或点本页外城市，用**进入当前页**的游标重取同页，同时替换 L 区域。保留表行、边界和分母。已应用的表条件（含页长、排序、数据域及锚点）变化时另开第一页。点/榜/行/短详情/URL 共享来源命名空间及稳定城市 ID。悬停、焦点、视野各自独立。刷新/返回重核选城，筛除、失权与不存在都给 `focused=null` 并清旧详情。
3. **首页专用游标。** 原游标只复核上一页末城或末行修订。新签名绑定当前会话主体与代次、域、L 产品、锚点、窗、标准、`q/geography/grade`、**表**排序、页长和末键。摘要只以当前获权关系中决定整份有序表、可披露投影及对应汇总的稳定值计算。`rank_preview/focus_city/map_limit`、T、动态年龄、评估时钟、全产品修订号及权限表全局版本均排除。续页先重核现权。改变获权非末行使页界或同版投影变化时 `409/page_changed`，整组从第一页重读。仅榜向/选城改变仍可用原游标。固定同一评估时刻，对只改权外城市/名录/字段的 A/B 请求核表、榜、可披露点位、分母、页界、游标摘要及 HTTP 状态均相同。五会话测 SQL 与字节成本，超 §9.3 预算需优化/复核。
4. **T 与地图。** 仅按已选城获准 T 链接独立请求。以 `(会话代次,域,城市,产品,anchor,请求代次)` 接收响应，换城立即清旧图，中止在途请求。A→B 的延迟 A 成功或错误都须丢弃。T 不可用显示同城合法空态。地图优先取同快照、同条件的**有界获准点**。每个 `map.items` 项限 `city`、可披露的 `aqi/grade`。可离线分发的材料按已核来源键、有效期和 CRS 关联城市 ID。受限坐标仅随同一投影附加有独立许可且在快照时有效的 `lon/lat/crs`。达到上限且有更多可披露点时明示范围。仅能取表页时标「本页已定位 x/y」，两数须可披露。缺合法几何、点位或校准依据，交付可搜索列表并记地图 `BLOCKED`。`City` 无坐标，站址期坐标沿用 `s04_station.py::_coordinates` 的证据、时间和字段权。控制点、跨期迁移及撤权需分别验证。

## 8. 内部实施顺序、变更范围与检查点

下表以单开发者连续交付为默认。可独立进行的工作包括资产/许可核查与接口设计、正式工程准备与不共享写路径的契约测试设计、观察链通过后的研究与治理读侧验证。管理写入需在权限、隔离、查账和兼容门槛齐备后进行。每个业务增量验收包含该增量实际桌面/窄屏、键盘和状态截图。

| 阶段 | 前置、任务与预计路径 | 通过检查点的证据 |
|---|---|---|
| **一　确定接口与联调输入** | Plan Review 通过并经确认后，在 `docs/api/`、对应 `backend/{accounts,observations,collection}` 和 `tests/` 为 §6 选定项登记旧行为、拟议 schema/权限/限额、样例与拒绝形状。先做用户能力、发现和全国查询的最小补丁。按 §6 登记管理读写的字段、权限、限额、回执及反例，管理实现留第五阶段。准备隔离 A/B、F 种子、角色、独立 SQL 对账和 H/地图许可清单。 | 从空 F 实例可注册、查询获权对象、看到真实能力、选定 L 快照，无管理/展示/原始行权限的反例成立。与 tag 对照旧请求、字段、排序、同标准分母、S05 票据，形成增量 V12。独立 L2 复核权限及冻结契约，关键发现处理后开放对应入口。 |
| **二　建立单工程与身份、视觉基础** | 建 `frontend/` manifest/lock、Vite、TS、ESLint、Vitest、Playwright、单应用、两工作区、公共说明、注册/登录/改密/会话。按 §7 交付 Client、受限路由、首批主题/表/图表宿主、嵌套控件、空态与实体降级。实现宿主/镜像同源静态接入，涉及实际需要的 `backend/config/`、`Dockerfile` 与运维/构建配置。可与阶段一已定的读契约并行。 | 新目录 `npm ci` → typecheck/lint/test/build。真实浏览器在 Vite 代理和生产壳登录、刷新合法深链、看自己的信息、无权不能进管理、管理员默认用户区。浮层键盘、展开/关闭、三种材质/动效条件实拍。静态缺件、API 错误、过期会话保持正确状态。 |
| **三　打通监测与复访** | 前置一、二。`frontend` 的总览、地图/列表、城市、站点、收藏/关注与数据域切换接入 L/C/T/S/D/H 适用路径。复用 §3 的原型视觉片段，替换数据端、状态、分页和 URL。真实资产齐备才接区划/点位投影，先验列表和缺授权。 | 观察链（§9）有真实会话、SQL/HTTP、同刻排名、C 七槽、T 24 槽、原站址及历史空窗、收藏复访。首页五类已知问题各有实际操作及前后截图。更换账号/标准/数据域时旧选择不泄露。核第 2 页换榜/选城时原表页稳定，迟到 T 响应丢弃，关注列表重鉴权。地图按材料另签。 |
| **四　研究、查询与我的空间** | 前置一、二和稳定对象下钻。完成「趋势与分布/对象对比/指标关联」、M06 独立查询和插入的同父请求支撑页、M07 资料/方案/票据。按需接 `analysis/*`、`me/*`，使用获权 H 和 F 两套证据。保持返回上下文与方法/输入版本。 | 研究者从零选数、看足样本与不足样本、下钻、翻页、保存并重跑、核 CSV 或明确拒绝。从 `/app/query` 独立完成 §9.1 的整组 CSV。表格/图点/共同样本和导出按各自父查询对 PG。版本变更、跨用户、空组、过期/撤权/超额可重现，精确读标记误用得到拒绝。 |
| **五　管理治理与受控配置** | 前置一、二和可用读链，按 §6.1–6.2 已复核的契约实施。先交付管理发现/账本、健康/趋势、质量及导入的只读下钻，再接经确认的模板化周期创建/后继变更、暂停/恢复、裁决、站址/许可动作和审计回读。落实受限 Admin 单项字典及其实际检索入口，按主机交接范围验证许可撤回。`backend/collection`、治理服务与迁移仅在经审变更确需时修改。 | 最小权限操作者可从任务追到子项→批次→事件→获准处置→影响及反馈。部分投影、无新/仅质量、冲突超时与恢复各获真实回执，用 PG 对账唯一认可、旧版本拒绝、审计和撤权。在 F/I 独占 Worker/Redis/单 Beat 核草稿/并发/切换边界 B、原 Run 与历史缺口恢复，及 M09 影响组重算、M10 可见积压与首次有效写入（§9.1）。L2 针对高影响配置、审计、重放和权利收窄。涉及后端统计或数据版本亦复验相应 S07 用例。 |
| **六　全站联调与版次交接** | 将已完成增量置于同一受测 build，轮换新会话/新数据重走三链，运行跨引擎/无障碍/性能矩阵，复核导航、一致性、合法资源及失败路径。更新实际建成的工程命令、接口差异、运维接入、截图与验收索引。 | 按 §1、§9–10 逐项给出 PASS/FAIL/NOT RUN/外部条件，未解释的数据错误、权限泄露、竞态覆盖、严重交互阻断或测试失败先修复复验。独立终审记录范围/SHA/发现/处置，最终提交同一版可重建证据及外部未放行清单。 |

## 9. 验证设计与证据位置

实施时按单项判定。执行并符合预期记 `PASS`，执行且不符记 `FAIL`，尚未执行或未能执行记 `NOT RUN`。外部阻塞另加 `BLOCKED`，登记依赖、责任人和解除条件。每次运行生成 `tmp/s08-evidence/<run>/`，只在有权留存/转交时将脱敏索引、获准截图、公开结果放进 `docs/verification/S08.md` 和 `docs/verification/S08/`。每条记录含提交与工作区差异、锁/构建摘要、实例守卫、URL 与安全条件摘要、账号能力组合/数据类别、操作、应得/实得、命令及退出码、时间、证据路径。会话和高权限操作不保存可使用的 token。F、获权 H、注入故障 I、获准真实来源分别标明，涉及网页展示或图像分发再核许可。

### 9.1 工程检查与三条真实业务链

| 验收项 | 执行环境和操作 | 预期与留证（相对当轮证据目录） |
|---|---|---|
| 安装、类型、规则、组件和生产 | 干净工作副本，锁定 Node/npm、Python/uv。`cd frontend` 后依次 `npm ci`、`npm run typecheck`、`npm run lint`、`npm test`、`npm run build`。锁定后 `npm run test:browser`。Vitest/组件测试覆盖 DTO 空/缺字段、数字/单位/时间、路由查询往返、选中与取消、票据/冲突状态。用渲染后的控件测焦点。 | 退出码 0，安装不改锁。记录版本/包体、初始路由实际加载字节、剩余告警与处理理由。后端改动跑 Ruff、适用 mypy/pytest、迁移与 schema 差集，静态构建经 A/B 的真实入口读取。`engineering/`、`contracts/`。 |
| **观察链** · A 或 B，浏览器真实 Session，PG，F | 新注册→CSRF 登录→默认正式域的授权结果/无数据/拒绝→主动 F→统一 L 锚点排行/筛选→城市 C/T→同城站点与站址期→收藏→退出/再登录→我的关注复访。用账号 B 直接访问 A 的私有对象。以改密/停用使旧会话失效。 | 并发修订非末行城市或撤其权，游标末行保持原值，首页各区域须同版或整组失效，权外变更不产生可观察差异。对照 L 独立名录/未知标准和 C/T 不同值，七槽、24 槽及 S 错时值各归原产品。历史空窗不回填，撤展示权保留收藏意图但不显示受保护读数。B 私有读取 404，过期 Session 403。桌面/窄屏/键盘相同下钻，记录脱敏操作/HTTP、PG 合成键及截图 `observation/`。T01/T02/T03/T07/T12。 |
| **M06 独立数据查询→CSV** · 同源浏览器、PG、获准导出的 F | 直接打开 `/app/query`，在本页选择「整组明细」，同一源/产品、城市或站址期、指标/单位/标准/报告窗、显式 `[start,end)` 和 `quality=reliable,scope=synthetic`。`POST analysis/details/` 用 `method=detail,support=observed,page_size=2`，取得规范父查询和顶层 `input_version`。预览整组范围、字段与来源，确认后 `POST me/exports/` 提交 `kind=detail,groups=[父查询+该版本]`。201 后本人 `GET me/exports/{id}/download/?scope=synthetic`。 | 造 3 条可披露观测（真零、删失、缺测），首屏 2 条，翻完为 3 条。按 PG 核 CSV 整组三行、身份/版本/单位、删失界限和缺值。GET `latest_valid`、任意质量筛选、当前桶或配对子集不等价时须另开整组并确认，可取消。仅展示权或缺选列导出许可 403 无附件，跨人 404，旧版 409，过期 410，空集无票。留脱敏确认界面、请求、下载校验及 PG 依据 `exports/query-entry/`。 |
| **M07 我的关注** · 观察链原账号与第二账号、F | 收藏 C 城市和站点后退出。在隔离数据中增加该城较新获准 C 小时观测、较晚的滚动 O₃、站点不同指标的错时值，重登直接访问我的关注列表及对象详情，再撤一项展示权。 | `me/favorites/` 与单项都按原收藏身份/偏好产品重新投影。C 用获准小时锚，较晚滚动 O₃ 不推进 C 小时快照。L 以同刻 AQI 锚附可读等级。站点按当前获准站址期逐指标显示最近值/时标，仅历史站址时 `select_epoch`。首选产品无法披露时只留 `id/kind/state=unavailable`，清旧名称、数值及链接。核本人复访与第二账号 404，截图、HTTP 与 PG 键入 `observation/favorites/`。 |
| **研究链** · 同源浏览器 + PG，F 正反例和当期获准 H 分跑 | 获权产品/对象/单位/窗→统计或对比→选服务支撑桶→同意图明细翻到末页→保存→重跑→获准导出或合理拒绝。两站各 18/24、共同 12/24。另设充分覆盖、未知/零 E、零方差、P<30、真实零、删失/争议，单指标一年与季界。 | 独立 PG 或手算 E/O/V/E共/P、加权均值、月份/配对、版本和 CSV。检查支持 raw 图的权与仅获派生展示的情形。旧票受影响时拒绝，权外追加不扰可见结果。票据失效、归属、公式注入、9,999/10,000/10,001 行和 4 MiB 边界按 S05/S07 复验。有权 H 的同数据集双站分析须单列，本轮预设 H 不具平台导出权。`research/{F,H}/`、`exports/`。T03/T04/T06/T07/T08/T09。 |
| **管理员治理链** · A/B 独占 PG、Redis、Worker、唯一 Beat，F 与 I | 具名用户从用户区切换，获权 Job→WorkWindow/缺口→各次 Run/子项/批次→质量事件/观测证据→合法裁决/站址操作→回执/范围→重查用户侧数据或健康。定时/手工/重试竞争同一 F 窗。只恢复可补的历史缺口。模拟投递未确认/超时/重复请求、权限中途撤回及 Worker 停复。 | 从独立 PG 审计对 `request_id/action_id`、窗口/认可子项一次、仅质量、`finished+no_new/open+partial`、全/部分授权行与服务心跳。T06 `v1=10→v3=10→迟到v2=20` 后仍一键/值10/水位v3。另设可信异值使旧汇总/票据失效。治理闭环另按下列 M09/M10 用例核受影响范围、积压、错误及写入证据。来源故障按对应产品显示。普通/只读管理员写请求拒绝，受控审计只含必要标识。`governance/`、`health/`、`replay/`。T05/T06/T07/T08/T12。 |
| **M09 质量覆盖、定位与重算** · 治理链 F、两种角色、PG | 在获权 Window 打开 `coverage.E/O/V/by_indicator` 与缺口原因，从异常单元定位 Run/子项/Batch/Receipt、规则、候选和事件。取影响组与获权无关兄弟组的 S05 父查询、版本和票据。先测普通 `close`，对另一含争议且有可信更正依据的同键事件执行 `correct_source`，另测跨键 `correct_mapping`。取 `action_id/event_version/affected/outcome`。 | 缺值或争议已收到可 `O=1,V=0`，普通结案不解除争议。以确有可信更正的样本对 PG 复核目标键/指标覆盖 V 从 0 到 1、受影响统计和支撑明细重算。映射跨键时依各自认可回执核原键和目标键的窗口与统计，保留原回执的窗口归属。旧输入/旧票据按失效契约拒绝，获权兄弟组的版本与结果不变。撤权的影响范围不回显。留裁决前后覆盖、事件定位、父版本和真实 HTTP/SQL `governance/quality/`。 |
| **M10 获权积压、等待、错误与首次有效写入** · 治理链 F/I、PG | `health/?window=` 的 `visible_pending/visible_quota_wait/visible_sent_not_started/visible_unconfirmed_dispatch/visible_retry/visible_running` 与 `oldest_visible_pending_age_seconds/next_visible_quota_check_at/truncated` 只描述该窗获权 Run 页。核最早等待为评估时刻减最早可见 pending 的 `created_at`，无 pending 为 null。仅在同页有相应获权 Run 时列出其最早等待开始时刻。错误摘要来自获权 Run 的 `error_code/result/request_state` 和组件 `reason_code`。用部分授权、截断页和权外故障验范围标签和无侧漏。 | 单窗另取 §6 的可选时标。对迟到旧观测首次有效、先缺后可信订正有效、重复/同值水位、只有候选、权外新写分别对账。源 `observed_at`、`Batch.fetched_at` 与首次获准有效写入的服务端时刻分列。无证据时刻为 null，既有 `first_observation_window_rate` 可计首条缺值，`coverage.latest_valid_at` 仍为源观测时标。留各状态、授权页、三时刻与独立 PG 对照 `health/window/`。 |
| **T08 展示撤权、留存处置与恢复** · 任务专属 F 库、受控副本、隔离空恢复库 | 先仅撤展示，用 §6.1 的交接目标及仅持 `manage_grants` 的账号执行，核受影响图、明细、票据和在途响应被撤下，同库仍获许可的兄弟对象可查。再令留存到期或撤回，按权利运行处置，并在独立现行许可/撤回记录下演练恢复判断。仅联动界面时沿用 S07 已签收的持久层证据，涉及依赖或处置改动时按受影响范围实际复跑。 | 对比处置前后的规范化/修订/派生物及受控副本、审计必要字段。对已处置段或不能排除无权部分的旧备份核拒绝或受控清洁恢复。保留展示授权、用途拒绝与物理处置的独立断言。`tests/test_s06_disposition.py`、受守卫的 `scripts/verify_s07_host.py restore`、`disposition/`。 |
| 会话、安全与竞态 · 上述三链共享 | 浏览器独立 Cookie jars，测试预登录/登录轮换 CSRF、缺 token、跨用户收藏/方案/下载、无管理权限、跨标签用户切换、已知撤权、前进/返回缓存、刷新、转 F/H、网络乱序（延迟旧的真实响应）、框架 HTML/数据库不可用、错误到期与限流。 | 记录屏幕和 DOM 的最终状态、受限数据撤下及迟到响应丢弃，允许仍有权的数据继续访问。高影响动作断在服务已提交/客户端未确认后，通过原回执和版本恢复。重复操作不产生第二份完成事实。受限范围拒绝采用同形状态，不侧漏计数。`session/`、`race/`。 |

**M10 时刻的计算依据。** 在所选 Window 中，从获权认可子项及回执定位城市/站址期逻辑键。对每个键找首次由入库或有据裁决成为有效当前观测的服务端时间。判据为当时已认可、非候选、未争议/退役、`quality=valid` 且有数值（含零）。用可证明该转变的 `CityRevision/StationRevision.created_at`。仅由资格裁决首次生效时用相应 `QualityDecision.action.created_at`，不采用候选最初写入时刻。每键取首次，再对获权键取最近一次。历史资格以修订、认可回执和裁决当时证据复原，不把今天的 `is_current` 倒套到过去。已证明范围内从无有效写入为 `none`，证明链或披露依据不足为 `unavailable`，两者时间均为 null。页内或部分授权须注明可披露范围，不根据隐藏修订推算。先缺测再首次有效读后者时刻。同值水位、重复抓取、普通结案均不推进。直接对照 `observations/revisions.py::decide`、`collection/queries.py::tally`、`collection/s07_admin.py`、`accounts/operations.py::window_state` 及 `tests/test_s07_operations.py`。

受影响的后端回归可从 `scripts/verify_s07_host.py r pytest <selectors>` 的新建 R 环境开始。身份与投影选 `tests/test_s07_{contract,contract_user_http,catalog,exact}.py` 和 `tests/test_s04_{national,selection,favorites}.py`，统计/私有版本选 `tests/test_s05_{statistics,selection,private,cost}.py`。治理选 `tests/test_s07_operations.py`、`tests/test_s06_{collection,quality,catalog,accounts,disposition}.py`、`tests/collection/test_collection_postgres.py`。涉及跨进程投递/恢复再跑 `scripts/verify_s07_host.py contract` 或 A 的 `flows`，前后版 Session/游标/保存意图/票据兼容用 `scripts/s07_contract_upgrade.py` 及其实际受审基线。先记录旧行为及回归覆盖，新增端点追加差集。如部署需迁移，用自有旧版 F 实例停旧写者、前向迁移、二迁无待办，核历史行与会话。完整 S07 全量复跑由变更风险决定。

### 9.2 真实浏览器、视觉与可用性矩阵

使用项目锁定 Playwright 及本机实际浏览器，分辨桌面鼠标/键盘、触摸模拟和物理触屏。记录引擎/version、OS、字体就绪/降级、viewport、真实缩放/DPR、输入方式、路由、数据/方法版与展开态。正式 E2E 将原型阻断全部 `/api/` 的守卫替换为**同源真实 API** 白名单，预期拒绝列为显式断言。控制台异常、未说明的失败与越界网络记录为失败。每个非平凡页面改动打开实际页面，对照设计、保留代表性的初版/修订截图和操作，再在最终版复核。

| 场景 | 操作、判据与证据 |
|---|---|
| 桌面、平板、窄屏及缩放 | 1920×1080、1440×900、1280×800 的双工作区、收展侧栏。768×1024 抽屉/条件摘要。390×844 和 320 CSS px 单列、长名/单位/空错及局部二维表。检查全页无意外横溢、长表关键列和详情可达、软键盘/安全边距。实际文字 200%、1280 宽浏览器真实 400%（约等效 320 CSS px）另记。`visual/layout/` 的首屏、全页、局部状态与实际滚动值。 |
| 控件、浮层、焦点 | 分别展开 Select、Date Picker、Tooltip、Popover、Dialog、Drawer。在模态内再开 Select 和日期器，检实际挂载节点、层叠、可访问树、边界翻转、滚动/软键盘、主题和背景不可交互。Tab/Shift+Tab、方向键/Enter 选项，Esc 先收面板再收模态，逐层还焦。取消与危险操作区分默认动作。长菜单/窄屏和亮/暗合成背景各拍展开态。`visual/overlays/`。 |
| 首页选中与图表 | 实际点位命中（含稠密区）、键盘或列表选择 A→悬停 B→切指标→榜单选 C→表格选 D→复位视野→返回。点/行/短详情/独立详情和 URL 一致。hover、焦点、视野复位不改选择。跨页保留、排序、刷新同快照、筛选排除、返回和撤权分别断言实际可见状态，地图缺失时从列表走完。图切指标、图例/缩放后仍可读断点、图转表/支撑明细。`visual/selection/`。 |
| 五项原型专项回归 | ① 上述跨入口同 ID。② 390/320 的 AQI 六档区间有可见操作与键盘/触摸等价说明。③ `prefers-reduced-transparency`、显式实体主题、无 `backdrop-filter` 各测 KPI/内容卡/侧栏/地图工具/菜单/对话/遮罩。④ 测紧凑表格实际点击盒及邻距，≥24×24 CSS px 或给出适用间距/等价入口证据，触屏主动作优先 44×44。⑤ 缓字体/首载与稳定后全页、滚入/离开、旋转/缩放/重排后，检查两环心文字的真实像素与读屏文本。留 DOMRect、命中与前后截图 `visual/regressions/`。 |
| 语义、状态、材质与动效 | 首载/局部加载、空窗、陈旧或时效未知、缺测、部分授权、拒绝、失败、版本变动、加载中切换与高影响确认。合成背景最亮/暗及滚动叠加面上取样，普通文字 ≥4.5:1、大字 ≥3:1、适用的非文本 ≥3:1。键盘焦点可见且未被盖住。触发/取消/反向动效时可继续输入。减少动效时取消位移/插值，读数与选中即时更新。`visual/states/` 记录样本、交互与定向修订。 |
| 引擎与辅助技术 | Chromium、Firefox、WebKit 各跑正式构建核心路由、嵌套浮层、图表/字体/材质、Session/下载、返回。Safari 的系统整合/字体另在实际 Safari 核。触摸模拟与物理设备、axe 与真实读屏分别登记。至少按实际可用设备对 VoiceOver 或 NVDA 的标题/表格/菜单/模态/图表替代操作走链。环境缺失写明版本/设备/待补动作与风险，判 NOT RUN。`visual/accessibility/`。 |

### 9.3 性能与容量

1. 沿用 V3.0 §10.2 与 S07 T09 的四个形状。在百万 F 小时键、5 个独立真实已登录 Session、正式 Gunicorn 1 worker/gthread/8 threads 上测单对象 24h 和 100 行一页各热 HTTP P95 ≤1 s，5 对象×30 天单指标及单对象一年按月汇总各 ≤2 s。分别记录冷/热、并发、机器/内存、版本、首/续页、样本数、错误率、独立内容核验、SQL 计划。后端口径或 SQL 路径变化时按 [T09 重建手册](../verification/S07_T09.md) 复验受影响负载，高风险变化做完整四形状。设计容量 20 城+60 站约 70.08 万对象小时行/年，300+1000 约 1138.8 万/年，仅作预算情景。
2. 浏览器分段计时请求发出→完整 HTTP、解析/适配→首个可见图表和可交互、筛选/开关/滚动→下一次绘制。固定数据/设备/版本，各测桌面和窄屏冷启动、缓存后复访，记录样本数、P50/P95、长任务、布局移动、内存及 chart 实例/监听器回收。以首个正确实现建立浏览器基线，后续同条件对比，对反复卡住输入、错图、增长不收敛及明显绘制回退定位并复测。没有为浏览器虚设后端 SLA。`performance/{api,browser}/` 留精简计时、浏览器轨迹位置与资源报告。
3. 展示默认 ≤1,000 点，服务已下采样时消费共享轴的极值/缺口区间，统计、支撑明细与 CSV 仍按同父查询的完整数据。核大表仅渲染分页、有界地图点、字体子集/中文 locale/按需图形、图表销毁/resize、玻璃滚动和低端设备的绘制成本。根据 trace 决定分块、延迟加载或局部降低模糊。对 10,001 行/超字节/超 cell 的请求要求缩窗，不做静默截断。

## 10. 风险、决策与交接

| 风险 / 责任 | 当前处置、解除条件与对出口的影响 |
|---|---|
| 冻结 v1 与管理权限 · GPT、实现者 | 以 §6 列明的接口核权限和兼容边界。重点复核周期切换、旧 Run 恢复、本人回执的现权检查，以及 §6.2 本地检索别名的 P0 替代。原契约与受影响的 S07 回归是对应切片的出口。 |
| 首页构图与统计依据 · 实现者 | 按 §6–7 的 T 城市趋势、L 等级与 L 快照覆盖实施。缺项给合法空态。用同刻订正、表页换榜/选城及无汇总权验证，视觉细节按 DESIGN 的浏览器实测归一化。 |
| 地图与 H · 来源/资产保管人 | 合法几何及城市键、按期位置/CRS 与相应用途为正式地图解除条件。材料不足时提供同筛选列表与下钻并记录地图 BLOCKED。当期 H 的查询/研究须有对本实例、用途和受众生效的授权，缺件记 `NOT RUN / BLOCKED`。平台导出和证据转交另核。两项并行准备、分别签收。 |
| 城市在线、日报、整机 · 来源负责人、S09 | `T11 NO-GO`。受限试运行需要实际用途许可、三地各至少两个发布周期、获准城市 Celery→PG→查询/重放/断源闭环，冻结阈值后另行批准。自然 168 小时和正式复审是后续放行依据。北京 A03 是一次历史运行。真实 D、共享介质处置、整机断网冷启恢复和真实城市离线仅交接条件与当前状态。 |
| 浏览器与运行可靠性 · 实现/验收者 | 跨页选择、部分授权、首次下载/投递失联、L22 CSV 偶发、前端大包与字体、滚动玻璃渲染分别有 §9 的反例及测量。L22 重现时留首个脱敏诊断，按当前版本和压力定因。引擎/设备/辅助技术受限时登记缺口及复验环境，阻塞条件不计入 PASS。 |

**最终复核结论与实施授权 · 2026-11-25。** 用户转达 GPT 已完成对修订版的定向复核并给出 **APPROVED / PASS**：R01–R05 全部通过；批准 §6.2 中严格限定字段、权限与业务用途的「本地城市检索别名」；批准当前计划的全部六个内部实施阶段及已确认接口增强范围。该批准将本版确定为 S08 正式执行基线，无须重新开展计划审查。地图及当期 H 仍由相应保管人提供有效证据，T11 维持 **NO-GO**；这些未放行项不得被解释为已批准采集或在线运行。

### 10.1 后续工程沉淀与封版

每个通过验证的切片更新实际使用说明、命令、契约变动与截图索引。视觉 token/控件固化时对照 `frontend/DESIGN.md` 与实测截图，仅记录已经核实的规则。`frontend/AGENTS.md` 用于稳定工程约束。保留许可/署名清单及可转交资产的版本/摘要。记录前后端同一受测 SHA、锁、环境、配置、数据类型/许可期限、PG 对账和浏览器结果，说明如何从安全的新实例复现。

后续在用户批准的授权范围内，于契约切片、观察、研究、治理和全站验收选择本地 Git 检查点。主实现者先核任务起始差异、完整索引与工作区，只暂存可归因且通过相应检查的精确路径或清楚的 hunks，复核提交补丁及其父提交并记录真实 SHA。审阅者只读。高风险契约做 L2，复杂阶段收口做独立 L3，记录发现、处置和定向复验。开发跨会话确需进度记录时复用本计划，进度只保存执行状态与证据定位。最终记录 S08 前后端组合的契约版本、兼容差异和判定，保留原 S07 标签。新的封版标记/对外交付按届时授权执行。

## 11. 初稿核查证据与计划自审（历史记录）

| 已执行检查 | 结果及范围 |
|---|---|
| 原型安装、静态、单测与生产构建 | `/tmp/s08-npm-lock.ba1IpeSa` 从两份 npm manifest/锁启动干净环境，`npm ci --offline --no-audit --no-fund` **PASS**，安装 304 包。npm 12 报 `esbuild@0.28.2` 的 postinstall 被本地脚本策略拦截，同一安装中实际 Vite build **PASS**。正式工程需核可选平台二进制、目标 OS/架构和受控脚本策略。`/tmp/s08-preview-audit.lA9KlD6g` 以实际源码和已装依赖副本复验，`npm run typecheck` **PASS**，Vitest **18 PASS**（5 文件），Vite **PASS 有告警**，共享图表块 614.89 kB/gzip 208.81 kB，99 个 WOFF2 共 4,539,552 B。`npm run lint` **FAIL**，`src/city/CityPage.vue:46:257 no-irregular-whitespace`。原型声明的 Node 22 最低版本还须满足 ESLint 的 `^22.13.0`，故选已实测的 Node 24 组合。 |
| 原型浏览器与截图 | `docs/verification/frontend-design/homepage-probe.json` 的 43 个输入文件指纹 `fbc714a1cf4e52873efd81382687df85845fbad14a526819d5a476661cb03616` 与当前源码相符。归档 F 首页专项 **13 PASS / 1 FAIL**，实际桌面/菜单/390 全页及环图近景见[设计证据](../verification/frontend-design/)。早期原型说明的 17 个 Chromium 用例有其历史范围。当前锁定 Playwright + Linux Chrome 158.0.8127.36，`npm run test:browser` 双 worker **25 PASS / 1 FAIL**，1440×900 用例的本地字体请求 `net::ERR_NETWORK_CHANGED`。`npm run test:browser -- --workers=1` **26 PASS**，首错仍保留。运行日志 `browser.log` 与 `browser-serialized.log` 位于上述原型临时副本。 |
| 真实选择、命中与降级专项 | 当前 F 专项复现三处 **FAIL**。地图选成都，经榜单/表格打开石家庄后详情标题更新，URL 与地图仍为成都。390/320 六档图例区间隐藏。Chrome 媒体模拟确认减少透明度偏好已命中，侧栏和地图工具栏转为实底，KPI 的两端透明度仍为 0.863/0.89，排名及污染物卡为 0.95/0.94，底色透明。紧凑详情按钮约 41.02×22.59 CSS px，间距例外待验。归档 390 全页环心缺字而近景可见，本轮等待字体后全页与滚入均可见，首载/时序纳入正式复验。同一临时目录留有 `targeted-probe.log`、`material-probe.json`、`material-reduced.png` 与窄屏全页/近景 F 截图。 |
| 工具使用与证据资格 | 四个原生只读子任务分别核对原型/设计、用户读契约、管理治理、阶段与验收。第五个独立上下文 `/root/plan_check` 只读审阅 `main@89f3c8a` 上的计划草案。主实现者逐条对照源码并修订，见下表。采用 `frontend-delivery` 的工程/证据要求，浏览器用锁定的 Playwright runner。`playwright-cli` Skill 已安装，CLI 不在当前 PATH。其余设计 Skills 在本轮核对安装、职责和使用位置。Impeccable launcher 当前有执行位，实际引擎启动留待需要时验证。正式 Vue 页面、三条正式浏览器链、Firefox/WebKit/Safari、物理设备/读屏/真实缩放、当期 H、正式地图及 T11 本轮 **NOT RUN**。 |

**计划自审收敛项**

| 审查问题 | 送审稿中的处理 |
|---|---|
| M01–M10 与三个重点 P1 是否可追溯 | §4 每行有行为、入口、服务、差距、实施阶段与 T 用例。补入 M02 采集时刻、M03 站址/收藏身份、M08 周期编辑、M09 字段治理/重算、M10 最近新观测/受限字典/审计。V3 的完整验收仍逐项对应输入类别。 |
| 发现、导航与统计是否可能越权 | §5–6 分清普通读、研究派生、管理审阅三套用途，把管理发现从用户 display 目录拆出，`focus_city` 复核实属同一 L 条件。明确无完全名录/可比字段时的可披露分母、GET/POST scope、小时枚举、L 锚点链接及错误恢复。 |
| 支撑明细与 CSV 是否确实同条件 | §5.1–5.2 与 §9 明确 S04/S05 两种 `input_version`，父查询的 `support/bucket` 和整个导出组各有适用范围。GET 自由筛选无法原样表达时先列可导范围并重新确认。最终取舍见 §5.2。 |
| 计划是否能真实执行及复核 | §7–9 指定隔离入口、真实临时账号、F 种子缺项、H 有效期、独立 SQL 对账、浏览器操作与证据。补入 SPA 原生边界的 resolver 回归、模态内两种选择器、三类材质回退、实际缩放及首次与串行 E2E 结果。 |
| 冻结边界、成本与判定 | §6 仅列有对应业务缺口的增量接口与受控主机选项，§8 先读后写。首页获准视觉直接用于正式开发，候选参数按浏览器结果调整。S08 软件联调、地图、整机及城市在线各依其判据签收。 |

**只读计划审阅的五项修订**

| 发现 | 已写入送审稿的处理及开发验收 |
|---|---|
| 同一时刻的修订可使分次读取互相矛盾 | §6–7、§9.1 选单次获权首页投影及首页专用游标。订正非末行或撤权，分别验屏内同版、续页重开和权外稳定。 |
| 改频后的 Job 归属与旧 Run 恢复不明确 | §6.1、阶段五明确目标锚小时 B 的半开边界、旧配置继续恢复、并发改期/重启后的 PG 唯一性及来源暂停约束。 |
| 许可管理者缺少可核对的操作目标 | §5.3、§6.1、T08 采用有范围的主机交接和现行版本预览，验只有 `manage_grants`、未知目标、事后用户查询和隔离持久处置。 |
| M10「最近新观测」缺少真实判据 | 初稿以选定窗口的首次修订创建时刻为依据。R05 已将首次有效写入细化至键、质量与资格证据，现行定义及反例见 §9.1。 |
| 通用明细与精确读容易交叉传参 | §5.1 拆成两个端点，补完整请求模板、字段、时间依据、精确读版本和失败语义。 |

**初稿送审准备核查 PASS（上轮）。** 已复核 10 个模块行、T01–T12 的阶段归属、6 个内部阶段、53 处本地文档链接和 16 张表格的列数，并将基线摘要与后端运行收据、原型归档和本次浏览器记录逐项对照。结束时索引为空，未跟踪路径为本计划和起点已有的 `photo/`。

### 11.1 CONDITIONAL PASS 后的定向自审

本轮从已送审计划开始，起点 `main@89f3c8a99e41b144cf0a785071dd717a7efd7049`，本地 `origin/main +0/-0`，索引与 tracked diff 为空。未跟踪路径为该计划和已有的 `photo/`。上列 S07 及原型运行记录属于前一轮。三项原生只读核查分别核对周期与恢复、字典与回执、验收判据，主实现者对照实际服务整合并复核首页与目录行为。

| 发现 | 计划自审处置及复核位置 |
|---|---|
| R01 冻结接口范围 | §6 将每个新增调用限定到具体页面、动作、字段、用途、限额与兼容反例。普通发现、次排业务、整组 CSV、主机操作沿已有入口。 |
| R02 周期切换与恢复 | §6.1 依据 `scheduled/reconcile_due/scan_gaps/admission/register` 与 PG 约束，将计划生效段和人工暂停分开，明确草稿、过界冲突、连续变更、原 Run 和历史缺口的恢复与对账。 |
| R03 字典与审计 | §6.2 排除 `City.name` 等高影响原字段，给出一项有正式使用方的字段白名单。受控 Admin 模块权限、本人完整命令与现权、对象审计范围及撤权反例均可测试。 |
| R04 首页一致性 | §6–7 区分表排序、榜向和选城。检查等级授权、页界、获权游标摘要、权外不干扰、独立 T 竞态及有界地图/页内说明。 |
| R05 业务验收 | §4、§8、§9.1 增补 M06 查询页独立 CSV、M07 复访摘要、M09 覆盖/定位/重算、M10 获权积压/最早等待/错误和首次有效写入，均指定数据、操作、预期与证据目录。 |

**本轮文档检查 PASS。** 已对照修订前副本检查增量，核实六个阶段名称及顺序、M01–M10 追踪行、T01–T12 引用、R01–R05 处置、63 处本地链接和 18 张表的列数。对照本地 S07 OpenAPI 的 66 个操作检查了新增路径与现有调用的分界。Git 索引与 tracked diff 均为空，仓库内本轮写入仅本计划。新增契约、PG/Worker/Session 与浏览器用例为实施期 **NOT RUN**。下一关为 GPT 定向复核与最终批准。
