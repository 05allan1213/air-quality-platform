# S01 实施计划｜可运行骨架与第一条纵向链路

**评审稿 · 2026-11-21 · 等待 GPT Plan Review。** 本轮交付计划。以下路径、接口、命令、样本和验收结果，除 A 节标明的实测外，均为后续实施规格。GPT 审查通过并收到另行启动指令后，才开始开发。

## A. 工程基线

| 2026-11-21 本地核查 | 事实与依据 |
|---|---|
| Git | 当前唯一工作树位于 `main`。`HEAD`、跟踪分支 `origin/main`、只读 `git ls-remote origin refs/heads/main` 均为 `f8340418a6fe0753ff93bb774e101e454c787501`。`origin` 为 `https://github.com/05allan1213/air-quality-platform.git`。左右差 `0/0`，没有待推送提交。 |
| 文件与用户修改 | 任务开始时 `git ls-files` 只有 `.gitignore`、`AGENTS.md` 和 `docs/design/` 下两份设计文档。`git status --porcelain=v2 --branch --untracked-files=all` 无变更项，暂存区为空。检查忽略项及隐藏文件，只见 `.serena/` 工具配置，保留原状。没有现成计划目录、正式源码、迁移、测试、依赖清单、Compose、项目 PG/Redis 配置或阶段验收产物。沿用本仓库。 |
| 设计依据 | 核对根 `AGENTS.md`、`docs/design/城市空气质量监测数据分析平台_系统总体设计方案_V3.0.md`（V3.0）§5、§6.1/6.3、§8.1、§9、§12～14 及 `docs/design/总体实施方案.md` §2～5、§7～9。V3.0 的 SHA-256 为 `4373fca30fd71061c4e795ebd02b73190e39d451dd7830c0dc5bf01e58379bf6`，与实施方案声明相符。S01 退出条件以总体实施方案 §5 为准。 |
| 宿主工具 | Ubuntu 24.04.5 LTS / WSL2 x86_64，zsh，Git 2.43.0。`python3.13 -VV` 为 3.13.18，`uv --version` 为 0.13.6，离线列举可见该解释器。Docker CLI/Engine 均为 29.9.3（读服务端成功），`docker compose version` 为 v5.7.0。 |
| 可见但未作项目验证 | `docker compose ls -a` 无项目，镜像缓存有 `postgres:16-alpine`、`redis:7-alpine`。宿主 `redis-cli` 7.0.15、Ruff 0.17.3 可用，`psql`、`pg_isready`、`pytest` 不在 PATH。Python 3.13 的包元数据中未见 Django、DRF、Celery、django-celery-beat、psycopg、redis、pytest 或 pytest-django。缓存镜像与全局工具仅作候选，锁定环境和数据库连接均待实测。 |
| 研究材料 | 按实施方案原链接逐一验证，仓库外的 13 个相对链接目标均可读，涵盖 `Phase2/verification/` 三轮材料、前端使用说明和 `design-review/` 两份记录。首轮 `tests/test_guards.py` 可定位会话/CSRF 断言，`lab/ingest.py` 可定位样本写入函数，第二轮 `integration/r2db/tasks.py` 可定位处理与回读函数。本轮只查可访问性和相关符号，未运行原型、迁入代码或复制样本。新检出只有正式仓库也应可实施。 |

**启动判断为 READY WITH CONDITIONS。** 设计、独立仓库、自制 F 的路线和本地基础工具已就绪。无已证实的技术阻塞。首次锁包、基础镜像获取、构建及真实服务联调是实施中的检查点。上述只读核查为 **PASS**，S01 应用、Ruff/pytest、迁移、真实任务、HTTP 和重启验收均为 **NOT RUN**。真实来源许可与 T11 城市在线准入独立，当前在线业务为 **NO-GO**。

## B. 目标、依赖与退出条件

唯一交付链为 **受控管理员提交固定 F → Redis → 独立 Worker 校验并写 PG → 普通登录用户按产品和观测时刻从 HTTP 查回**。空环境迁移后观测数为 0，处理后为 1。输入、PG 和 API 可逐字段对账，保卷重建后仍可查，拒绝路径无未授权副作用。同一版本须能在第二个全新测试项目重复验收。

| 追踪 | S01 承担的部分 | 后续归属 |
|---|---|---|
| M07、M10 / D01、D10、D12 | USER 注册、Session、受控管理操作、启动状态和模块化单体 | S04 私有资料/收藏，S06 运行管理，S07 后端总验 |
| M08、M09、M06 / 受 D02、D03、D06、D07、D13 约束 | 一次手工 F 任务、来源/用途门禁、基本校验、精确回读与留痕 | S02 正式产品/地理字典、城市与站址身份、可信版本裁决和 H 导入。S03 外采、窗口/子项账本、可靠投递与恢复 |
| T07、T08、T10 | 只交付会话与角色、用途拒绝、F 持久化的部分证据。真实异步为 T05 的基础 | 完整 T05/T06、撤权处置/备份恢复、离线冷启、T01～T12 系统验收留在相应阶段。T11 需要另行实源准入 |

S04/S05 的通用查询、统计、收藏和导出，S08 的 Vue、地图和浏览器交付均在后续。S01 只建有实际调用者的模块，自制 F 只进入测试/本地演示空间。H、香港样本、CNEMC 回执和任何真实产品均不作为种子。

## C. 技术方案与接口契约

### 运行拓扑

在仓库根放 `pyproject.toml`、`uv.lock`、`.python-version`、`Dockerfile`、`.dockerignore`、`compose.yaml`、`.env.example`。`backend/manage.py` 与 `backend/config/` 采用 Django 官方工程结构。`accounts/` 管会话和受控身份入口，`samples/` 管 F 的策略、规范化、存储、任务和回读，二者均在 `backend/`。HTTP 负责校验请求并调用服务，Celery 入口只传运行号，领域服务负责两端共用的用途规则及事务。测试放在 `tests/`。

锁 Python `>=3.13,<3.14`、Django `>=5.2,<5.3`。运行依赖为 DRF、`psycopg[binary]`、`celery[redis]`、`django-celery-beat` 及 Django。开发组锁 Ruff、pytest、pytest-django、mypy（只检查纯规则和输入/输出类型）。实施时解析兼容补丁版本，把 Python、uv、PG、Redis 镜像固定到核实的 tag 与 digest，并用同一锁文件构建 Web、Worker、Beat 和迁移进程。S01 本地镜像包含验证工具。

| 服务 | 启动、连接与证据 |
|---|---|
| `db`、`redis` | PG 使用项目专属命名卷，Redis 只作 broker。两者均只接内部网络且不发布宿主端口。分别用 `pg_isready`、`redis-cli ping` 探活。 |
| `migrate`、`web` | PG 达 `service_healthy` 后单独执行 `migrate --noinput`。Web 等 `service_completed_successfully` 后启动，容器内监听服务端口，仅发布至宿主 `127.0.0.1`。Web 接前后两张网，后网设 `internal: true`。`GET /health/ready/` 只返回 Web/PG 可读状态。PG 故障返回 503，队列不可用时获准的既有观测仍可读。 |
| `worker`、`beat` | 两个独立服务等迁移成功和 Redis 健康。Worker 用 Redis URL、JSON 序列化、专用 F 队列，`task_always_eager=False`，关闭结果后端。唯一 Beat 用 `django_celery_beat.schedulers:DatabaseScheduler`。Worker 和 Beat 仅连内部网络。禁用内嵌 Beat，启用的业务周期任务数须为 0，区分框架维护任务。 |

Web 使用单进程本地服务，关闭自动重载，验收运行 `DEBUG=False`。在 `.env.example` 列出 `DJANGO_SECRET_KEY`、`POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD`、`WEB_PORT` 及 Host/Cookie 模式，仅给非敏感示意。数据库和 broker 使用服务名。缺少必需秘密时启动失败。`.dockerignore` 排除真实 `.env*`、`.git/`、`.serena/`、`.venv/` 和临时证据。

### F 的输入、权限和数据库

项目自写不可变夹具 `co-hour-v1`，随文件注明作者/来源、自制版本和本地测试用途。产品 `F`，来源 `local.fixture.v1`，对象 `F:CITY:001`，性质 `synthetic=true`，限本地演示。输入指标 CO、浓度 `1250 µg/m³`，原时刻 `2001-02-03T08:00:00+08:00`（夹具指定 `Asia/Shanghai`），采用前一小时的半开区间 `[2001-02-02T23:00:00Z, 2001-02-03T00:00:00Z)`，以右端标时。设 `USE_TZ=True`，用 `Decimal` 得到 **`1.250 mg/m³`、`2001-02-03T00:00:00Z`**。API 用三位定点字符串 `"1.250"`。验收以手算常量独立比对。保留原值/单位、原时间文本、窗口语义、质量状态与夹具版本。源获取时刻记 Worker 读取夹具的时间，任务执行起止另记，源发布时间未知。CO 浓度的 AQI 标准记为不适用。

| 最小 PG 模型 | 规则及选择理由 |
|---|---|
| `FixtureGrant` | 数据迁移只登记自制 F 在合成地域、本地认证受众的 `process_local`、`retain`、`display` 三项独立许可，以及各自的依据、生效/到期条件和启用状态，每用途一行且唯一。每个入口按产品、来源、操作、受众、地域、期限和启用状态匹配。未匹配或停用即拒绝。S01 没有在线获取、导出、再分发、备份授权。变更仅在隔离测试库注入，不开放许可 CRUD。S02 再建跨产品正式许可目录。 |
| `FixtureRun` | UUID、服务端操作者、固定原因码、夹具键、请求/执行时间、执行状态（`pending/succeeded/rejected/failed`）、错误码、独立的投递状态和关联观测。表示一次手工请求。独立请求可链接同一 F 观测。仅允许经授权的服务创建，运行事实留在 PG。 |
| `FixtureObservation` | 单独的 F 表，DB 约束产品为 F、合成为真，夹具键唯一。保存上列溯源和规范化字段，查询同时限定 F 产品、对象与观测时刻。唯一键只对冻结的夹具定义成立。比较稳定的规范化内容，排除获取/执行时刻。同键同内容复用原行，同键异值拒写并在运行中记原因。S02 的正式城市/站点表另建，不能沿用夹具键作为官值身份。 |

写入先验证来源、对象、带偏移时间、窗口、单位和有限非负数。真零有效。缺测、删失、非法值给不同原因码，此单样本通路只允许有效浓度入观测表。无权或无效的输入均不能变成有效行。安装迁移只建表、约束、许可与框架元数据，观测必须由 Worker 首次创建。

### 身份、触发与回读

| 客户端入口（拟定，同源 JSON） | 合同 |
|---|---|
| `GET /api/v1/auth/csrf/`，`POST /api/v1/auth/{register,login,logout}/` | 用 Django 原生 Auth、密码校验与哈希、PG Session。GET 用 `get_token` 发 CSRF cookie 和可用于 `X-CSRFToken` 的令牌。三个 POST 走有 `CsrfViewMiddleware`/`csrf_protect` 的 Django 视图，含匿名登录和注册。登录后重新取得轮换的 token。Session cookie 为 HttpOnly、SameSite=Lax、显式期限，HTTPS 下启用 Secure。注册字段白名单，角色/授权字段返回 400。注册返回 201，账号为活动普通用户，无 staff/superuser 标志或任务权限。登录凭据无效或账号停用均返回 `403/invalid_credentials`，成功返回 200，退出返回 204 并清除服务器会话。 |
| `POST /api/v1/admin/fixture-runs/` | 必须为有效会话、有 `samples.run_fixture` 权限的活动账号，且当前允许 F 本地处理和留存。请求只接受固定夹具键及枚举理由，不接受操作者、URL、文件、观测值或任务名。管理员通过可信本地 `manage.py createsuperuser` 建立，日常任务管理员可由维护者在本地给活动的非 superuser 授予该项权限。该权限区分本阶段的 ADMIN 和 USER。`is_staff` 本身不授予任务权，superuser 仍须过用途检查。S01 管理入口为此 API，默认 Django Admin 站点不挂路由，避免开放周期任务或观测的通用编辑入口。 |
| `GET /api/v1/fixtures/observations/?product=F&observed_at=…` | 登录后检查当前 `display` 许可，再按固定来源、对象、指标和精确 UTC 时刻读取。只返回 0 或 1 项，带筛选、实际条数、产品/来源/对象/指标、源时区、质量、合成标记、本地演示用途、夹具版本、观测/获取时刻、单位/窗口和值、观测年龄，新鲜度阈值未知。合法空时刻返回 `200` 空集及 `no_observation_at_time` 原因，禁止最近值回填。请求真实或未知产品一律拒绝，不透露计数。 |

业务 DRF 视图仅用 `SessionAuthentication`，默认要求登录。匿名读取 `403/not_authenticated`、有效 USER 越权 `403/permission_denied`、来源用途不符 `403/use_denied`、CSRF 失败 `403/csrf_failed`，均给稳定 JSON。Django 用 `CSRF_FAILURE_VIEW`，DRF 在会话 CSRF 检查处标识同一错误码，再由异常处理器输出。参数错误 400，依赖不可用 503。注册/登录和管理触发分别限速，超限 429 且无写入/投递。S01 单 Web 进程采用 Django 本地内存计数和短时间窗，入口分别按直连来源或已认证账号计数，忽略客户端自报的代理地址。多进程部署再考虑共享限速。

**从请求到提交。** Web 通过权限检查后在 PG 创建 `pending` 运行。在最外层事务提交后才以 `task_id=run_id` 投递，仅携带运行 UUID。发送返回后应答 `202 + run_id`。设置有限的 broker 连接、socket 和发布预算，关闭本次发布的自动重试。V08 本地拒连目标为 5 秒内返回 `503/dispatch_unconfirmed + run_id`，验收 HTTP 客户端超时设为 10 秒。单独更新投递字段，保留 Worker 已提交的结果。人工按编号对账。Worker 在短事务中先锁运行。若已终结，记录本次重复消费结束。否则锁定相关许可，重新读取账号及命名权限，核对 `process_local`、`retain` 后读取固定夹具。通过规范化校验才插入或复用唯一 F 行，并原子写入运行成功状态及关联。校验/拒绝可落原因，数据库写失败先回滚，只有 PG 可用时才记录失败。日志只留运行号、任务名、阶段、原因码和时间。操作者及理由从受限的 PG 运行记录核查。

选择固定 F 文件和独立 PG 授权行有两个原因。固定文件便于独立复算，也限定了 Worker 的输入来源。三行许可可以在任务排队时分别撤回，这是纯代码白名单难以用真实进程验证的行为。每次请求留一条运行记录，足以区分投递与写入。事务发件箱和通用恢复机制交由 S03 的业务窗口统一设计。

## D. 实施任务与检查点

文件均为预计位置。P01 → P02 后，身份与 F 规则可分开推进。P05/P06 的服务可分别开发，路由合并与完整 HTTP 验收待两者具备后进行。单人可按编号完成。

| 任务与依赖 | 文件/模块与工作 | 测试、完成判据及证据 |
|---|---|---|
| P01 锁定可运行工程 · 已获实施授权后 | 根 `pyproject.toml`、`.python-version`、`uv.lock`、`backend/manage.py`、`config/`。建官方 Django 骨架，配 PG 驱动、DRF、Celery 入口和分组工具，固定导入路径与 Django settings。 | 生成锁文件后用 Python 3.13 执行 `uv lock --check`、`uv sync --locked` 和框架导入。用进程内临时生成的密钥/测试配置跑 `manage.py check`（不指定数据库检查）。真实连通性留到 P02。保留版本和退出码。 |
| P02 空环境启动 · P01 | `Dockerfile`、`.dockerignore`、`compose.yaml`、`.env.example`、`config/settings.py`、健康入口。构建同一应用镜像，编排五个常驻服务和一次迁移，应用 auth、contenttypes、sessions、django-celery-beat 的框架迁移，配置专属网络/卷。 | 在新项目执行 `docker compose config -q`，构建启动，PG/Redis 健康，迁移退出 0，Web ready、Worker 可见、Beat 恰一份。留服务清单和迁移结果。 |
| P03 身份与权限入口 · P02 | `accounts/`、`config/urls.py` 与 `tests/`。注册、登录、重取 CSRF、退出、限速和拒绝响应。 | 真实 HTTP 验证 V03 的账号入口，PG 核对注册权限及退出后会话记录消失。受保护读端的旧 cookie 重放随 P06 验，管理触发随 P05 验。 |
| P04 F 规则与存储 · P02 | `samples/{models,policy,normalization}.py`、自制夹具、`samples/migrations/`、纯函数与 PG 测试。创建三张最小表、许可迁移、自定义权限和唯一约束。 | 全新及再次迁移均正确，验证 Auth 生成的 `samples.run_fixture` 权限和受控授予。核换算、零/异常分类及 PG 对重复键/错误标签的拒绝。真实消息重投随 P05 验。 |
| P05 受控投递与写入 · P03+P04 | `samples/{views,services,tasks}.py`、路由和任务测试。实现提交后发布、执行时复核、事务写入/运行关联及错误处理。 | 停 Worker 投递，确认排队且未写观测，恢复后按同一 UUID 对到 Worker 和 PG。权限拒绝、重投、broker 失败结果见 V04～V08。 |
| P06 登录精确回读 · P03+P04 | `samples/{queries,serializers,views}.py`、路由、查询测试。授权先于数据和计数，形成精确时刻、合法空集和错误合同。P05/P06 共用文件由同一实施者整合。 | 独立测试库可用受控 F 行验证过滤。阶段验收必须读 P05 实际写入的行，输入/PG/HTTP 对账见 V06/V09。 |
| P07 重建验收及交付 · P05+P06 | `tests/`、`scripts/verify_s01.py`、`README.md`、`docs/verification/S01.md`。用标准库 HTTP cookie 客户端与 Compose CLI 串起真实流程，重建复验并索引证据。 | 按 E 节顺序在两个新项目各验一遍，填写逐项实得、基线、证据位置和缺口。必要集成项失败或缺证，S01 保持未通过。 |

## E. 验收矩阵（本轮全为 NOT RUN）

实施时，运行器创建唯一 Compose 项目、专属卷、临时秘密和可用回环端口，先确认没有重名资源。`pytest-django` 使用同项目 PG 的独立测试库，仅给隔离测试角色相应建库权限。涉及提交或回滚的测试启用 `django_db(transaction=True)`，每例建立已提交的 F 许可与测试账号，避免依赖上例留下的数据。跨进程验收使用 Web/Worker 共同的本次验收库和已提交的初始化数据，走真正 HTTP 与 `docker compose exec -T db psql`。测试账号及许可变更仅限该一次性项目。所有业务输入标 **F**，停服务、撤权和非法输入另标**注入故障**。轮询设截止时间，超时记 FAIL。

每轮按以下顺序隔离场景。V01 新卷启动，V02/V07 事务反例在独立 pytest 库运行。V03 验会话。观测为空时先跑 V08 的三项排队撤权，以及 V09 的入队前用途拒绝。每项结束后恢复授权并确认队列清空。撤销 `retain` 的全部测试都在零观测时结束。随后连续跑 **V05 → V06 → V10**，保留同一条记录和卷。再跑 V07 重投、V08 的 Redis 故障和 V09 的读取用例，恢复展示权。V04 的限速放到最后。各场验收用同一确定配置，按需重启本项目 Web 清空内存计数，避免先触发限额。每场记起始/结束计数。P07 以两个不同项目名各执行同一套断言，第二轮也从空卷开始。

| 编号 / 层级 | 前置与输入 | 预期结果与保留证据 |
|---|---|---|
| V01 部署 · 真 Compose/PG | 空项目和新卷，锁文件、示例配置补齐秘密后构建并启动 | 迁移一次成功，重跑无待迁移。保存 `compose ps -a`、镜像 ID、`showmigrations`、ready HTTP 状态、Worker 注册任务和唯一 Beat 的进程/调度器记录。F 观测为 0。 |
| V02 规则 · 单元+真 PG | 有效 F、真零、缺测、`<3`、非有限/负数、错时区/单位/对象。并在临时 PG 事务试插违反 F 标签/唯一性的行 | 手算换算和 UTC 一致。各异常原因可区分，非法输入无有效观测，DB 约束真实拒绝。保存输入编号、期望/实得、断言与计数。 |
| V03 会话 · 真 HTTP+PG | 匿名精确 GET。合法请求体分别测试注册、登录、退出缺失/错误 token。另以有效 token 提交权限字段或不可信 Origin。登录轮换后用同一新 cookie 分别配旧/新 token 退出。 | 匿名 GET 为 `403/not_authenticated`。缺/错 token、跨源和旧 token 逐项为 `403/csrf_failed`，对每次缺/错 token 或跨源的注册独立核对 PG 账号数无增量。失败的登录无有效会话，失败的退出仍可读。合法来源携带权限字段为 400，PG 无新增账号。合法注册仅 USER，登录后新 token 退出为 204，重放原会话 cookie 读为 403。核 Cookie 属性，仅留脱敏断言。 |
| V04 触发边界 · 真 HTTP/PG/队列 | 固定同一合法请求体。有活动会话的 USER 和仅 staff 账号分别带有效 CSRF，获权管理员分别缺/错 CSRF。独立测注册/登录和管理触发限速。 | 前两者 `403/permission_denied`，后者 `403/csrf_failed`。获权且带有效 token 可获 202，达到各自限额后为 429。被拒触发前后比较运行数、观测数及停 Worker 时的队列数，三者均无增量。被拒注册/登录核相应账号或会话，无新增。 |
| V05 消费因果 · 真 Redis/Worker/PG/HTTP | 从 F 观测数和队列长度均为 0 起步，记下已有拒绝运行，再停本项目 Worker。管理员 POST 固定 F，读 PG 与专用 Redis 队列长度，再启动 Worker | HTTP 202 带 UUID，停机时 `pending`、队列净增 1、观测 0。恢复后有同 UUID 的 Worker 收件、PG 成功运行以及关联的唯一行。保留停/启顺序、队列差、脱敏日志和 SQL 结果。同步调用、eager 或 Mock 均不能通过此项。 |
| V06 三方对账与空窗 · 真 HTTP/PG | V05 完成，另一个 USER 以 F、目标时刻查询，并查询相邻空时刻和缺少时区的时刻 | 比对独立预期、SQL 与 JSON 的产品/来源/对象、合成、CO、定点值、单位、源时区、UTC 时刻、窗口、夹具版本和质量。逐行核 API 与 PG 的获取时刻相同。期望 `1.250 mg/m³`、`2001-02-03T00:00:00Z`、1 行。相邻时刻 `200` 空集，非法时刻 400。留对账表、脱敏响应。 |
| V07 重投与事务 · 真 PG/Worker | 记录消费日志位置，同一 UUID 经 Redis 再投，等本次新增的收件和处理结束日志后才核计数。另触发一次同夹具。独立真 PG 测试在空观测表首次插入后、运行成功更新前注入异常并断言命中。另向同一领域服务给出同键异值。 | 重投运行/观测增量均 0，新请求运行 +1 并指向原行。独立连接确认回滚后无观测、无成功关联。异值拒绝，原行及其值未变。保存关联、计数和原因。 |
| V08 队列及权限变化 · 真服务+注入 | 分三次停 Worker，分别在排队后撤销非 superuser 操作者的任务权、`process_local`、`retain`，每次只改变一项条件。每次从空观测表起并恢复 Worker。另在有 F 后停 Redis，合法提交并查询既有 F。 | 三次各有收件，原因为角色/处理/留存拒绝，观测增量均 0。Redis 停机时在预算内实收 `503/dispatch_unconfirmed + run_id`，PG 有已提交运行及独立投递未确认标记，已存 F 仍可查。记录耗时、HTTP、PG 与 Worker 证据，恢复 Redis 并确认 Worker 重连。 |
| V09 用途与外采 · 真 HTTP/PG/Compose | USER 和管理员各查 C/H 及未知产品。有 F 后只关 `display`，两个角色再读 F。提交端用空观测场，停 Worker，独立关闭 `process_local`、`retain` 各测试一次合法管理员触发。另在有权时分别传 URL、非固定键。核路由、网络和 Beat 启用项。 | 无权读取均 `403/use_denied`，无值/计数，允许留存的原行仍在。非法参数各为 400，两组缺用途各为 `403/use_denied`，逐项核 PG 运行与静止队列均无增量。取证仅用本地配置、数据库和任务清单，外采业务计划为零。 |
| V10 持久化 · 真服务重建 | V05/V06 后记录观测 PK、运行号、卷名。仅对自有项目 `docker compose down` 保留卷，再以同项目 `up -d`，用新登录会话读 | 核对容器新 ID、同一 PG 卷、相同 PK/运行关联和不变的观测字段。保存重建前后 SQL 与 HTTP，观测年龄可自然变化。随后短暂停本项目 PG，ready 须为 503，恢复后再次确认可读。 |
| V11 工程回归 · 锁定工具+独立 PG | 在同一锁定环境执行 `uv lock --check`、`ruff check .`、`ruff format --check .`、mypy 纯规则、`manage.py check`、`makemigrations --check --dry-run`、`pytest`。记录两轮验收脚本的结果 | 保留命令、版本、退出码、收集/通过/失败数、两次项目与卷标识。Mock 只可用于纯规则的时钟等局部输入。任何真实联调项缺失单列 NOT RUN。 |

## F. 工程交付

后续 `README.md` 给出从新检出生成本地秘密、锁定构建、迁移、受控建号、取 CSRF、触发和回读的顺序。记录健康检查、队列中断后按 UUID 对账、保卷重启及运行 `python3.13 scripts/verify_s01.py` 的命令。仅对确认为运行器所有的临时资源做清理。`docs/verification/S01.md` 作为一份阶段报告兼证据索引，列出代码基线、环境、F/注入性质、V01～V11 的期望/实得、证据路径和未覆盖项。Cookie jar、密码、完整环境和未脱敏日志不进报告。报告内保留足以复核的最小 SQL/HTTP 摘录及手算值，详细脱敏输出可放在已忽略的 `tmp/s01-evidence/<run-id>/`。

## G. 风险与待审事项

| 事项 | 实施处置与审查重点 |
|---|---|
| 可复现启动 | 锁包、镜像 digest、容器内 PG 驱动及 Compose 健康依赖均待 P01/P02 实机确认。宿主缺 `psql`/pytest 可分别使用 PG 和应用容器内工具。首轮依赖获取失败时记录具体缺件。 |
| 身份和数据许可 | 评审匿名登录 CSRF、轮换令牌和普通用户持有效 CSRF 的反例能否分别证明两道防线。检查后台权限与 F 用途的独立性，以及 Worker 排队期间发生撤权的测试。 |
| S02/S03 衔接 | 评审 F 独表与不可变键是否足以隔离实源，PG 请求记录是否恰好能说明投递与提交。崩溃留下的 pending/投递未确认按 UUID 人工对账，通用重试、事务发件箱和窗口恢复留到 S03。 |
| 后续放行 | 目前没有必须由用户重定架构的冲突。补丁版本、端口、内部文件拆分可由实施者决定。GPT 独立审查重点是上述边界、三方对账、保卷重建与第二次干净运行能否支撑 S01 退出。下一步为 GPT Plan Review。 |

**本轮技术自审记录**　对照阶段目标、阶段外事项及 P01～P07/V01～V11 作了独立只读检查。已按发现修订权限生成与验收顺序、CSRF 各拒绝分支、broker 有界失败、测试项目内的场景隔离、首次写入的回滚观察点，以及角色/处理/留存三项排队撤权测试。定向复核又补齐逐次注册失败无新增、重投完成后再计数，以及停 Worker 分别验证两次入队前用途拒绝的断言。实际运行证据由实施阶段提供。
