# S07 Implementation Plan｜后端独立验收、启动入口与接口基线

2026-11-23，GPT 独立 Plan Review 结论为 **APPROVED WITH CONDITIONS**。本稿落实审查条件，等待 GPT 复核及用户后续实施授权。**P01–P10、V01–V17 均为 PROPOSED，S07 运行结果均为 NOT RUN。** 代码基线 `main@8de867853c470393617bc7ee14bb692a97fe8c5c`。历史验证和现存缺口见[本地预检](../verification/S07_PRECHECK.md)及[遗留台账](../verification/S07_LEGACY_LEDGER.md)。

## Review Decisions / Conditions

GPT 已批准三项技术决策。① S06 V19 由 S07 P05/V17 交付，作为一般 P1，交付范围限于现有 PostgreSQL 账本的有界跨窗查询与投影。② P04/P06 在冻结前将精确读的旧 `input_version` 收敛为当前授权范围内的不透明标记，安全优先于旧对象格式兼容。③ P02 采用 Linux/WSL2 单机离线运行，Gunicorn 单 worker、多线程，Celery Worker、唯一 Beat 各自独立。正式线程数由 V13 实测选定并随受测配置交付。原生 Windows 和多主机集群在本版支持范围之外。

| 审查条件 | 计划与台账 | 本稿修订及最终验收判据 |
|---|---|---|
| C01 Beat 排他 | P02 §2，V03，L19/L20，退出条件 2 | 四个入口共用实际 Beat 的双锁。每个受支持组合均以 Redis 实际消息和 PG 业务窗对账。失锁、暂停、竞争或锁文件身份不明时拒发或拒接管，无法证明的混用在入口拒启。 |
| C02 精确查询版本 | P04/P06 §3.2，V10/V12，L21/L23，退出条件 4 | 迁移旧字段消费者并移除原位置的授权元数据。权外变更、权内变化、撤权、可信修订四组 Session 实测与 S05 下钻、票据兼容回归同时达标才能冻结。 |
| C03 T09 契约及配置 | P08 §5，V13/V16，L26，退出条件 3 | 按实际 `chart.points` 和小时明细对账。交付包按手册重建受测 WSGI Compose，并在同配置、百万键、五并发下复验四项热 P95。 |
| C04 CSV 首错 | P03，V04，L22，退出条件 1 | 复现则定因、修复并同等压力复验。未复现则带历史首错、规模、环境及残余风险提交 GPT 独立裁决。正式负载出现未解释 HTTP 失败或数据错误直接阻断封版。 |

## 1. 交付判定与依赖

V3.0 §6–9、§10.2、§12.4、§13 定义正确行为。总体实施方案 §5/S07、§7–10 定义后端交接。实施使用两个新建环境。**A** 为按本轮标签构建的 Compose，负责规定负载、网络隔离及冷启。**B** 为本机 Python 3.13 + 独占基础服务，负责宿主运行与一致性。S06 原有 socket-only PG/bwrap 环境 **R** 继续负责高风险处置与一次性恢复。所有运行按新指纹建账。F 是项目自制且标明合成，I 是标明注入位置的故障，H 是本次有据的历史站点输入。R 在本文仅作环境代号，实源证据均写全称。

| 顺序 / 责任 | 依据、拟改位置与具体工作 | 前置 / 保护边界 | 可测退出及证据 |
|---|---|---|---|
| P01 基线、隔离、遗留逐项接单 | 对照两个设计和台账逐行复核，固定实现/锁/迁移/镜像/资源版本。拟增 `scripts/verify_s07.py`，借用 `s01_runtime`、`s06_guard`、`s06_evidence_index` 的选项白名单和清理策略。建 A/B/R，登记本轮合法 H 或明确缺项。 | 待 GPT 条件落实复核及用户实施授权。只对任务专属资源分配标记。历史源、共享卷和宿主 Docker socket 均不挂入应用。 | V01。注入错误 DB/卷/外连/项目名的守卫反例在首次写前退出。留 `environment`、范围清单、台账初始状态。 |
| P02 统一宿主入口与 Compose 对照 | 根 `main.py`、`backend/config/{settings,celery,scheduler,pulses,wsgi}.py`、`.env.example`、`compose*.yaml` 离线配置、`Dockerfile`、`pyproject.toml`/`uv.lock`、运维说明和启动测试。按 §2 将同一排他检查接到 main.py、原始/正式 Compose 及项目直接 Beat 命令。 | P01。基础 Compose、一次 A03 覆盖和原运行器保持可复现，新增入口默认离线。Worker 涉及处置，先验归属。 | V02–03。验全套与单组件、错库、信号及部分失败。用实际发布者和窗口事实核同实例排他，无法证明安全的组合拒启。收据 `startup`、`singleton`。 |
| P03 CSV 偶发与历史返修回归 | `tests/test_s05_cost.py`、`tests/s05_http.py`、`scripts/s06_{sandbox,observed}.py` 增最小脱敏诊断。证实问题后才动 `accounts/{research_exports,research_session}.py` 或对应领域代码。S03 F01/F02、S05 C01–C03 与 S06 L3 在影响面内回归。 | P01，可与 P02 的隔离准备分开。用独立可销毁集群，保留首次失败。 | V04。复现须定位、修复并同等压力复验。未复现依 C04 提交 GPT 独立裁决。正式负载未解释的 HTTP 失败或数据错误直接阻断。`csv`、原因与修复对照。 |
| P04 用户发现、产品字段与研究下钻 | 从零知识用户的 Session 重走 L→C→T/S、纯 H 历史入口、统计→全部明细/CSV，与 V3.0 §3、§7、§9 对账。依 §3 补最小投影、可发现入口和分页，按 §3.2 迁移精确读版本及旧格式断言。预计 `observations/{queries,s04_*,detail,projection,analysis_query,research_*}.py`、`accounts/{profile,favorites}.py`、`config/urls.py` 和对应测试。 | P01，先建失败验收样例。各产品、原字段、派生字段、目录、地理和使用权分别核准。 | V05–07、V09–10。合法用户无数据库主键清单也能完成适用链路。隐藏组与不存在组公开输出等价，旧精确读版本通过 C02 的权内外对照。收据 `user`、`research`。 |
| P05 管理发现、运行诊断与重点 P1 | `collection/{views,control,queries,services}.py`、`accounts/{management_api,operations,governance}.py`、`observations/{quality_urls,quality_governance,disposition_control}.py`。补按准许范围发现任务/窗口并追到运行、质量、处置结果的入口。交付 S06 V19 的一般 P1，按授权产品、对象和有限时间桶从现有 PG 账本汇总质量覆盖、任务时延与成功率（V17）。三项重点 P1 各自验收。 | P01。保留紧急暂停、幂等 UUID、预期版本、具名权限和当前用途过滤。涉及 P04 共用投影时在同一实现基线集成。 | V05、V08–11、V17。空/部分授权、拒绝冲突、有限追溯、重新计算均有 HTTP+PG 对照，`admin`、`quality`。 |
| P06 v1 接口候选基线 | `config/urls.py` 各 URL 模块、`accounts/api.py`、`config/errors.py`、各 view/parser，计划交付 `docs/api/openapi.v1.json`、同目录 F 请求样例与 `tests/test_s07_contract*.py`。实施 §3 的 resolver 差集、结构和 Session 验证。 | P02–05 的接口形状稳定。一般扩展优先新增路径/可选字段。C02 按 §3.2 原位替换旧版本对象，交迁移清单，锁定工具。 | V12。每个 v1 显式方法均可定位实现、规格、正负实测及角色。错误、头、媒体类型和领域状态相符，精确读安全样例及 S05 版本/旧票回归入 `contract`。此时为候选。 |
| P07 三链独立集成 | `scripts/verify_s07.py` 和 `tests/` 的独立场景复用 S03–S06 验证器、PG SQL oracle、现有 HTTP 工具。按 §4 用管理员写入/治理、用户观察、研究复算把 M01–M10 贯通。发现缺陷回 P03–05。 | P02–06。锁定授权、版本与任务所有权。事实从第二 PG 连接和真实客户端读回。 | V05–12、V17。T05–T08 全部后端断言通过，逐项给输入/实际值/关联号。仅 F 的结论标 F。`flows`。 |
| P08 Compose 正式性能及稳定性 | 拟新增任务专用生成器和 HTTP driver，引用 `tests/s05_volume.py`、`tests/test_s05_cost.py` 的 oracle/计划采样，调整只凭测量命中的 SQL/索引/投影。按 §5 在拟交付的正式 WSGI Compose 上测量，用同版配置重建复测。 | P02、P06、P07 主要读端通过，独立 A 性能资源独占，测量期间不跑其他验收。 | V13。百万行、5 个已登录并发、四种热 HTTP P95 均过线。R3 小时分母、扁平图点和分页明细同源对账。留冷态/错误/资源/年窗计划及最终配置指纹，`performance`。 |
| P09 恢复、断网冷启及两入口一致性 | `scripts/s06_{collection_runtime,restore_verify,restore_target}.py`、S02/S04/S05 H 输入验证与拟增 S07 场景。跨 Web/Worker/PG/Redis/Beat 分服务停复，按 §4 复核合法分段、独立冷启和共享服务隔离。 | P02、P06、P07。恢复使用 R 的空目标和包外保管人。H 需新授权。可与 P08 在不同资源错峰。 | V03、V08、V11、V14。验证实际隔离恢复、持久性、失权/重授与合法 H。缺 H 时单列 T10 后端阻塞。`offline`、`restore`。 |
| P10 工程回归、封版与交接 | 维护受影响的 README、运维、契约和 `docs/verification/S07.md` 与脱敏证据索引。复用旧验证器和 S06 专用 33 节点，用独立干净交付目录重走关键路径。复核台账→证据→决策，按 §6 交包。 | P01–09。封版前完成高风险定向审查与独立验收，同版复验，授权私料留在受控域。 | V15–16。可复跑、交付包独立核验、阻断清单为空、全部必需出口成立后方可申请冻结，`regression`、`handoff`。 |

`environment` 等收据名均相对未来 `tmp/s07-evidence/<run>/`。每个 V 单独建目录与条目，不覆盖首轮失败。计划把缺口修复和接口固化分开，以免生成规格后才发现前端无法取得必要身份。失权、CSV 并发及单 Beat 是定向审查点。代码定位见台账，无新缺陷的旧功能只补相关组合证据。

## 2. `python main.py` 启动设计

**目标是配置一次、从仓库根执行 `python main.py` 就能前台运行离线后端。** 平台为项目实际使用的 Linux/WSL2 与 Python 3.13。PG、Redis 各自作为已配置服务运行。初次配置和迁移是运维动作，日常启动不隐式装包、拉镜像或升级数据库。

| 决策 | 拟实施的行为、失败处理和验收 |
|---|---|
| 入口与运行模式 | 根脚本 `argparse`，无参数等价 `all`，另有 `web`、`worker`、`beat`、只读 `check`（用只读 PG 会话核状态，仅做有界 Broker 探活），`--dev` 与显式 `--env-file`。使用当前 `sys.executable`，固定 `backend` 导入路径与项目 cwd，子进程使用 argv 数组。`all` 同时管理三个组件，各单组件以前台模式只管理自己。无 shell 拼接、无 Worker 内嵌 Beat。 |
| 正式与开发 HTTP | 默认以虚拟环境 Gunicorn 运行 `config.wsgi:application`，绑定 loopback。采用 **1 个 WSGI worker、gthread 多线程**，先以可服务五并发的线程数验证，在 T09 固定最终值。`accounts/limits.py` 用进程内 LocMem，多 Web 进程会改变限速语义。`--dev` 才用 Django `runserver --noreload`。在正式交付的 Compose 覆盖文件使用同一 WSGI 命令，回归现有 base 开发命令和 CNEMC overlays。Admin 静态文件走同源本地路径，公共 UI 归 S08。用 5 Session 和限速反例核正式配置。`Dockerfile` 的依赖层仍按 `uv.lock` 构建，对 Compose 单独提供本地正式覆盖文件，沿用 base 的回环发布、PG/Redis 内网以及既有 `compose.cnemc.once.yaml` 的 Beat=0。 |
| 依赖与密钥 | `uv sync --locked` 在准备期运行。新增运行依赖限 Gunicorn 和用于 Admin 静态文件的 WhiteNoise，只解锁必要包，先核 Python 3.13 与离线轮子。`python main.py` 检查解释器、Django/Celery/驱动。宿主专用 `0600`、当前属主、无符号链接的配置（或已显式导入的环境）只读加载固定键的 `KEY=value`，禁止可执行语法。明确环境覆盖顺序，DB/Redis/模式/标记冲突报错。拒绝静默采用仓库共享 `.env` 与终端遗留 `DJANGO_SETTINGS_MODULE`、代理/在线模式。应用进程只使用本实例的低权限凭据。 |
| 基础设施与归属 | 参数化 `CELERY_BROKER_URL`、`S02_STAGE_DIR`，保留容器原默认。宿主优先任务自有 Unix socket，或专用本机服务的 loopback 非共享端口。需要容器提供基础设施时另给**仅 db/redis** 的显式覆盖、独立项目/卷/凭据、仅绑 `127.0.0.1`，由操作者预置。Python 入口只起本机 Web/Worker/Beat。不能把 `db`、`redis` 容器 DNS 或 Redis DB 编号当隔离。初始配置由部署者在自有 PG 预置实例标记、独占 DB 与角色，并把 PG 身份、Broker socket/受保护 endpoint、私有根目录结合在同一配置。只接受已登记者。无法验证所有权、授权处置义务或同一 Redis 被别的实例共用则停止于任何写入之前。隔离测试再核 Docker 标签、空库/角色/端口、PG 标记、网络与卷代次。 |
| 启动顺序 | 首先检查配置/所有权/端口/依赖与到期义务涉及的介质，再做 Django `check`、只读迁移计划或 `migrate --check`。有未应用项则退出并给出独立迁移说明。只在已确认的新环境由唯一一次性迁移进程执行 `migrate --noinput`。应用角色实际 `rolsuper=false`，本机验证客户端和服务用同一实例标记。已配置实例校验成功后，Worker 调原 `s02_dispose_due` → `s03_reconcile_due`，两者成功后才消费 `s01.fixture,s02.history,s03.collection`，`--concurrency=1`，节点名与 `AQ_WORKER_NODE` 对应。任一步失败整套退出并保留原账本/待办。Web 可单独在 PG 就绪时启动，缺 Redis/Worker/Beat 单独呈现。`all` 优先确认 Worker 启动成功，再放行 Beat。 |
| 唯一 Beat | 当前 `settings.py` 指向 `config.scheduler:ObservedScheduler`，其 `tick()` 在 `super().tick()` 后才记脉冲。P02 在 `config/celery.py` 的项目 `app.Beat` 启动入口校验实际 scheduler，在加载/首次调度前拒绝其他 `-S/--scheduler`。`main.py all/beat`、基础 `compose.yaml` 的原 `celery -A config beat -l INFO`、正式 Compose 的 Beat 和直接 `celery -A config beat` 均进入同一受保护的 `ObservedScheduler`。实际调度实例在初始化 `DatabaseScheduler` 前取锁，Celery 横幅的 `lazy=True` 探测不持锁、不建排程。见本节双锁规则。一次 A03 的 `scale: 0` 仍关闭 Beat。 |
| 就绪与退出 | HTTP `/health/ready/` 验 Web+PG。Worker 要见新的 worker_ready、匹配节点及三队列订阅，再在验收环境投一个获准 F 任务核 UUID/消费/PG 回执。日常启动禁止用真实采集探活。Beat 看持锁 PID、新的 scheduler tick/TTL，隔离场景看一次到期 F 调度。启动限时，子进程异常退出即记录组件、返回码，先停 Beat，Worker warm TERM 完成本次事务，Web 正常退，限时才强停自己启动的进程组，返回非零。SIGINT/SIGTERM 传递并 wait，不自动无限重启。端口冲突/部分失败不借用已有服务。私有 owner lock 和 PID/启动时刻用于识别残留，父进程被强杀后要求核对实际子进程，未知归属只拒启。禁止按名称杀进程。 |
| 文件、授权与离线 | H 暂存用属主 `0700` 的专用 tmpfs/受控易失目录，按许可对齐重启清理与卷代次。无合适介质则拒 H 暂存。备份、包外现行依据分别挂受控私有路径，保管人私钥不在应用环境。沿用原卷身份、签认、待办与 PG 对账。既有义务缺原卷不得报告已处置。新实例可关闭备份功能，明确 unavailable。宿主仅支持 `CNEMC_MODE=off`，只接受离线配置与获准的内部任务。保留 `s03.collection` 的 F 消费，拒绝真实产品调度、limited/periodic 及真实出口代理。生产适配器和来源用途门保留。验收使用可验证无外部路由的命名空间，只给本地 PG/Redis/F origin，可观测上游请求计数 0。正式 CNEMC 仍限原专用网络及单独准入。 |

**宿主配置的落点。** 建议查找顺序为 `--env-file`、显式 `AQ_HOST_CONFIG`、当前用户的 `${XDG_CONFIG_HOME:-$HOME/.config}/air-quality-platform/backend.env`。允许白名单内的非身份参数作显式覆盖，PG/Broker/实例标记来源不一致时报告键名并退出。宿主配置仅需应用身份和 Django 密钥，PG bootstrap 口令仅给基础设施准备者。受控文件打开检查属主、路径及权限后再传给已知组件。日志按组件输出到前台标准流，脱敏数据库连接串、Cookie、任务参数、原包及私有路径。私有暂存校验一并修改 `observations/disposition.py:private_dir`，将当前只适用于 `/run/s02-history` 字面路径的挂载检查扩展到宿主配置的受控介质，验证容量、属主、挂载类型和卷代次。

**进程映射。** 所有子进程使用同一虚拟环境，在 `backend/` 下运行。正式 Web 的 argv 对应 `python -m gunicorn config.wsgi:application --workers 1 --worker-class gthread --threads <已验证线程数> --bind 127.0.0.1:<port>`（8 可作候选值），容器内绑定容器接口且仅向宿主回环发布。Worker 依次执行 `python manage.py s02_dispose_due`、`python manage.py s03_reconcile_due` 和 `python -m celery -A config worker -n <node> -Q s01.fixture,s02.history,s03.collection --concurrency=1`。Beat 为 `python -m celery -A config beat`。正式 Admin 静态文件建议在准备期通过 `django.contrib.staticfiles` 与 `collectstatic` 归集，用小型 WSGI 静态中间件 WhiteNoise 仅服务专用 `STATIC_ROOT`，锁入 runtime 组。V02 从同源读取一项实际被 Admin HTML 引用的 CSS，核实备份和暂存目录从任何静态路径均不可读。

**C01 双锁与接管规则（P02/V03）。**

1. 每个受支持的实例只登记一个 PG 业务库、Broker 和本机锁文件。准备阶段在持久、本地 Linux 文件系统的受保护目录预建 `beat.lock`，Beat 身份无权改名、删除或重建目录项。基础及正式 Compose 把登记的锁文件 bind mount 给 Beat，宿主及直接命令打开同一底层文件。校验实例标记、挂载来源、属主、文件类型、打开 FD 的 `fstat` 与登记的设备号/inode。V03 还须由宿主持锁、容器非阻塞抢锁失败，反向再测一次。仅路径同名、容器私有层、不同内核或无法确认锁语义的文件系统一律拒绝同实例混用。
2. **实际 Beat 进程**依次非阻塞取独占 `flock` 和同库固定调度键的 `pg_try_advisory_lock`。PG 锁使用独立、无连接池的 psycopg 会话，连续持有至最后一次发送结束，禁止 ORM 换连或在原进程重抢。每个 tick 及实际发布入口用原连接核 `pg_backend_pid()` 和 `pg_locks` 中本会话的调度键，并核锁 FD 与登记文件的身份。任一取锁失败、身份变化、会话超时或丢失均停止调度，竞争者在写排程或发消息前以非零退出。守卫错误须传播至进程退出，不能被 Celery 的发送错误处理吸收成下一轮重试。正常关闭先停止发送，再关 PG 会话，最后关锁 FD。进程异常退出由内核回收 FD。
3. PG 会话断开会释放数据库锁。暂停的旧进程仍持有本机 `flock`，新进程即使连上 PG 也须拒启。PG 验锁与 Redis 写入之间存在在途区间，已交给 Broker 的消息按原号对账。身份不明、旧进程停顿在发送中或文件被替换时禁止自动接管。先停止旧发布者并确认退出，核清已发及结果不明的在途消息、原文件和实例，再由新进程重新取得两锁。失联后恢复运行的旧进程必须在下一次发布前退出。V03 在发送前后设停顿点实证。任一跨模式关键时序或同机锁同一性无法证明时，两个入口须拒绝混用同一实例，改用独占实例或确认旧 Beat 已退出后的人工切换。每个入口自身的双进程排他失败，直接阻断 P02。

**取舍。** `manage.py runserver` 便于开发，现有 `config/wsgi.py` 支持正式 WSGI。Gunicorn 与当前 Linux/WSL2 目标一致。跨平台 WSGI 如 Waitress 仅在新增原生 Windows 支持要求时重新评估。独立 PG/Redis 保留事务、窗口与恢复语义。默认进程组随前台生命周期退出，服务托管可由平台现成的进程管理器处理。规划核对了 Django 5.2 *How to use Django with Gunicorn*、Celery *Periodic Tasks*（运行时锁定 5.7.0）、PostgreSQL 16 §13.3.5 *Advisory Locks* 及 WhiteNoise 的 Django 静态文件说明。V02/V03 以实际锁定环境的 HTTP、队列与进程行为判定。

## 3. API 盘点、候选补齐与冻结方法

### 3.1 当前入口清单

基线从 `backend/config/urls.py` 的实际挂载向下展开，含由 `research_api` 动态构造的三个 action。下表路径以 `/api/v1/` 为前缀，保留尾斜杠。`{n}` 表示该位置的正整型主键，`{uuid}` 为 UUID，`{action}` 为字符串白名单。44 路径、59 个显式方法，另列框架 HEAD/OPTIONS 的实际行为。`GET /health/ready/` 在 v1 外只测 Web/PG。`/admin/` 为受限 Django HTML 管理站，按角色验边界，独立于 JSON 契约。

| 路径 | 显式方法 | 当前 View / 领域代码（`backend/` 下） |
|---|---|---|
| `auth/csrf/` | GET | `accounts/views.csrf`，Cookie 与 token |
| `auth/register/` | POST | `accounts/views.register`，仅普通用户 |
| `auth/login/` | POST | `accounts/views.sign_in`，绑定 Session 代次 |
| `auth/logout/` | POST | `accounts/views.sign_out`，204 |
| `auth/password/` | POST | `accounts/profile.change_password`，成功后重新登录 |
| `me/` | GET PATCH | `accounts/profile.profile`，本人资料 |
| `me/favorites/` | GET POST | `accounts/favorites.favorites`，实体收藏与复访 |
| `me/favorites/{n}/` | GET PATCH DELETE | `accounts/favorites.favorite`，owner 与域 |
| `me/saved-queries/` | GET POST | `accounts/research_saved.saved_queries`，保存意图 |
| `me/saved-queries/{uuid}/` | GET PATCH DELETE | `accounts/research_saved.saved_query` |
| `me/saved-queries/{uuid}/run/` | POST | `accounts/research_saved.run_saved_query`，现权重跑 |
| `me/exports/` | GET POST | `accounts/research_exports.exports`，创建/列本人票据 |
| `me/exports/{uuid}/` | GET DELETE | `accounts/research_exports.export_ticket` |
| `me/exports/{uuid}/download/` | GET | `accounts/research_exports.download_export`，CSV 或拒绝 |
| `overview/national/` | GET | `observations/s04_national.read_national`，L 同刻集合 |
| `cities/{n}/` | GET | `observations/s04_city.read_city`，C 七槽 |
| `cities/{n}/trend/` | GET | `observations/s04_city.read_city_trend`，T 独立 AQI |
| `cities/{n}/stations/` | GET | `observations/s04_station.read_station_directory` |
| `stations/{n}/` | GET | `observations/s04_station.read_station`，站址期或逐项最近 |
| `observations/query/` | GET | `observations/detail.read_detail`，窗口/最近与键集分页 |
| `observations/current/` | GET | `observations/views.observations` → `queries.read_current`，S02 兼容读端含独立 D |
| `analysis/details/` | POST | `observations/research_api.drilldown` → `analysis_query.detail_rows` |
| `analysis/compare/` | POST | `observations/research_api.research_view` → `research_statistics.summarize` |
| `analysis/statistics/` | POST | 同上，单指标统计或 Pearson |
| `fixtures/observations/` | GET | `samples/views.observations`，S01 F 兼容域 |
| `admin/fixture-runs/` | POST | `samples/views.fixture_runs`，F 操作 |
| `admin/history-imports/` | POST | `observations/views.history_imports` → `history.submit`，202/503 带 attempt |
| `admin/observation-evidence/` | GET | `observations/views.evidence` → `queries.read_evidence`，字段级审阅 |
| `admin/collection-runs/` | POST | `collection/views.runs` → `services.submit`，运行和窗口 |
| `admin/collection-runs/{uuid}/` | GET POST | `collection/views.recover`，读 Run / 按原号重投 |
| `admin/collection-jobs/{n}/` | GET POST | `collection/views.jobs`，`control` 和 `services.change_job` |
| `admin/collection-windows/{n}/` | GET | `collection/views.windows` → `queries.read_window` |
| `admin/operations/health/` | GET | `accounts/operations.health`，组件与获准窗口 |
| `admin/accounts/{n}/actions/` | GET POST | `accounts/management_api.accounts` → `governance.account_action` |
| `admin/quality-issues/` | GET | `observations/quality_urls.issues` → `quality_governance.list_issues` |
| `admin/quality-issues/{n}/` | GET | `observations/quality_urls.detail`，影响窗口与事件版本 |
| `admin/quality-issues/{n}/actions/` | POST | `observations/quality_urls.actions` → `quality_governance.act` |
| `admin/stations/{n}/changes/` | GET POST | `observations/quality_urls.stations` → `catalog_governance.station_change` |
| `admin/disposition-products/{n}/` | GET POST | `observations/disposition_control.disposals`，预览/准备处置意图，由受控命令核销 |
| `admin/grants/{n}/actions/` | GET POST | `observations/disposition_control.grants`，现行依据和变更 |
| `admin/backup-products/{n}/segments/` | POST | `observations/backup_urls.prepare_segment`，准备合法段 |
| `admin/backup-segments/{uuid}/publish/` | POST | `observations/backup_urls.publish_segment`，核权发布 |
| `admin/backup-segments/{uuid}/restore-intents/` | POST | `observations/backup_urls.create_restore`，短期单次意图 |
| `admin/backup-authority/{action}/{uuid}/` | GET | `observations/backup_urls.authority_material`，限保管人及操作 |

### 3.2 必须解决或证实的端点语义

| 领域 | 候选修改、兼容性及验收 |
|---|---|
| 对象发现与权限 | P04 建限权产品/对象选择入口，返回可在下一跳使用的产品、实体、独立站址期、允许操作及可用历史锚。受限元数据逐字段判权。在 P05 为已具具名权限的管理员加最小 Job/Run/Window 检索、历史导入状态（限操作者或获准审阅者）。对 `history.submit` 入队事务中途撤权或停用也测实际提交顺序。允许已有路径增加 GET 或新增集合，后端生成可用链接。必要时 `/me/` 增当前本人粗粒度能力。所有列表在过滤之后才分页/计数，禁止照搬源名册或内部 grant。见 L16/L17。 |
| 状态、地图输入与质量 | P04/P05 共用一套领域投影，使用 PG 真实 Run/Batch 证据，并核该用户对相应产品、期间、状态字段的披露权。分别定义请求时的 `evaluated_at`、观测 `observed_at`、贡献 Batch 的 `fetched_at`、年龄、最近一次已证来源事件及其时间/有效期。`no_new` 只指这一次响应。可信发布时刻/发布节奏缺失时保留未知。区分已证停源、等待配额、数据过旧、明确空窗、目录外、质量异常及不可披露。C 七槽与 T 独立返回，历史槽不回填。`coordinates` 可空，附已证 CRS、有效期和身份，未知 CRS/位置依据归空。授权质量原因或受权明细能下钻，隐藏信息始终同形。见 L15/L18/L24。 |
| 筛选与分页 | `s04_http.params/page_size`、`detail.py` 当前明细默认 50 最多 100，每次至多 5 对象、7 指标、30 天且 3,600 候选格。显式 `[start,end)` 与 `latest_valid/as_of` 互斥，按观测时刻/键倒序。各路径另列排序、游标绑定的用户/选数/页大小/过期及已变页 409。研究端 POST 的 `selection` 使用数组、`units`/`standard_series` 映射、`scope=formal|synthetic`。通用 GET 的正式域默认省略 `scope`、对象/指标为逗号列表。规格明确两种编码。区分 `input_version`、内容版本、规则版本、控制版本。详情/图表/CSV 用同一组获权键和可复算分母。见 L23–L25。 |
| 回执与错误 | `collection.views.runs` 的 202/503 均可能有 `run_id/window_id/dispatch`，异步投递未确认按原号查账。既有紧急暂停兼容，其他高影响动作沿用具名权限、原因、规范 UUID、锁内 expected_version、重放与审计号。对新回执采用附加字段。按操作列 HTTP 状态、`code`、能否重试与重查入口。至少实测 200/201/202/204、合法空、403、私有资源 404、400、405、409、410、415、422、429、数据库 503 和标注注入的通用 500（核 v1 是否实际返回 JSON，原生 HTML 另记）。原生 Django 与 DRF 的媒体类型和响应头分别核验，按实际端点取适用子集。以错误响应的 `Content-Type`、实际 JSON 错误、零 CSV 数据字节、无下载头及 `Cache-Control` 作约束。 |
| 版本与非干扰 | 旧精确读版本依下表迁移，S05 研究版本及票据各按本端协议验收。D 保持地方日报独立：旧端精确报告日、通用明细不接 D、专用研究明细按源地方日边界。无权与不存在的可观察响应按对应端点比较，计入状态、内容、长度差异的原因、计数、链接、游标及缓存键。权利变化前后对本人再读。 |

**C02 旧精确读的消费者与迁移。** 当前 `GET observations/current/` 经 `views.observations → queries.read_current` 返回 `results[].input_version` 对象。`queries.input_version` 放入 `key=key.pk`、`revision=current.pk`、`qualification=key.qualification_version`、`rights=product.rights_version` 和 `grants=basis.ids`，并在响应结束前按当时依据重算。后两项分别暴露产品级计数和内部 grant ID。以下按该路径、`read_current`、版本字段及其间接调用核对当前仓内消费者。

| 当前使用位置 | P04/P06 的迁移及保留的断言 |
|---|---|
| `scripts/s04_checks.py:detail_http` 解析 `key/revision`，也被 S05 运行器复用 | 改用已获准的精确行 `revision_id` 及产品、实体、时标、指标，与 `observations/query/` 的 `key/revision` 和独立 PG 键对账。版本只作相等比较。 |
| `tests/test_observation_access.py` 在撤 display 后比较 analyze 版本的 `revision/rights` | 用受控 PG 验原修订及产品撤权事件，公开端验 display 拒读、仍有独立 analyze 许可的结果和标记稳定。 |
| `tests/test_s04_selection.py` 在部分依据到期后解析 `grants` | 保留结束时刻复核。由受控 PG 核剩余依据，对外核合法 `rights_attributions` 及标记与仅有剩余依据时相同。 |
| `scripts/s02_disposition_checks.py` 比较整枚标记。`scripts/{s04_checks,s05_runtime_checks}.py`、`tests/{test_history_policy,test_s04_favorites}.py` 含整包/升级前后比较 | 同格式继续验相等、同值水位与迟到不改当前精确内容。跨旧/新格式升级按权内字段逐项验身份、当前修订、值、署名和原 Session，再单验新标记。 |
| 业务字段、结果数与拒绝码的调用 | `scripts/s02_history_checks.py`、`s03_runtime.urllib_query` 及其 `s03_checks/verify_s03_products` 调用、`verify_cnemc.py`、管理命令 `cnemc_verify_once`，以及 `tests/collection/test_{cnemc_jobs,cnemc_disposition,cnemc_recovery_catalog,collection_postgres}.py`、`tests/test_{observation_catalog,observation_revisions,s05_rights,s06_disposition,s06_pending_http}.py`。保留路由、选数、`count/results/reason`、获权的 `revision_id` 和署名，用 F 回归字段。README、S04/CNEMC 运维示例和实源命令的读取路径作静态核对，真实输入另依来源授权。 |
| S05 同名研究版本（独立协议） | `analysis_query.Relation._versions` 生成 `s05-input` 域的 HMAC。`research_api.drilldown`、`accounts/{research_saved,research_exports}.py`、`ExportTicket.input_versions`、CSV、`s05_runtime_checks/s05_v16`、S05/S06 统计/票据测试和 `docs/operations/S05.md` 的 jq 样例都消费研究版本。保留 `s05.v1`、`s05.csv.v1`、64 位小写十六进制串、绑定父查询的游标、准备/下载的复核与原失效码。 |

P04 在**原字段**返回权内不透明标记，使用与研究版本分域的 HMAC。输入限当事人、用途、精确逻辑键、当前内容修订/资格及在结束时刻实际获准的字段和适用依据。产品全局序号、权外授权/隐藏行和每次读取的钟值均排除在摘要之外。公开响应、头和兼容别名同时清除旧授权元数据。保留按权应展示的来源署名。合法空窗或拒读沿原语义返回。P06 将对象改为字符串的兼容差异、旧子字段替代读法和受影响客户端写进 OpenAPI 与迁移说明。冻结前在交付树上重查路由、别名、测试与调用样例，仓外消费者以已登记的接口使用方为界。V10/V12 均有实测后方可签收。

### 3.3 规格、样例与自动检查

**推荐单一手写 OpenAPI 3.1 JSON。** 当前主要边界在函数 View、领域 parser 和响应投影，`samples/serializers.py` 只服务早期样本。P06 为每个 operation 写 `operationId`、参数/必填/默认/枚举/限制、成功与失败 schema、各状态媒体类型与响应头、时间偏移/小数和可空值。S04/S05 样例区分十进制观测值字符串和 JSON 数字，标明 ISO 8601 的时区偏移、源地方日与单位。用组件复用 Session cookie、CSRF header、选择、页、领域行与错误。在当前设置下，Django 的 `sessionid` 为 HttpOnly、SameSite=Lax，Session 有效期 8 小时。`csrftoken` 与 `X-CSRFToken` 配合使用，两类 Cookie 的 Secure 取 `COOKIE_SECURE`。受限 JSON 解析上限 16 KiB。分别说明匿名 CSRF 引导、注册、登录的请求体、同源要求以及登录后的 token 轮换。输出权和对象所有权写到操作级说明并有可运行示例。规范依据为 OpenAPI Initiative《OpenAPI Specification v3.1.0》§4.8 中的 Schema Object、Security Scheme Object 和 Security Requirement Object。

工具只进 uv 的 dev 组，候选 `openapi-spec-validator` 校验整份规格，兼容 JSON Schema 2020-12 的验证器校验真实响应实例。锁定精确版本，预置离线 wheel，仅允本地 `$ref`。自动发现方案如 drf-spectacular 适合 serializer 丰富的接口。当前仍需给大量函数逐项标注并验证动态结果，首选手写单源配漂移测试。V12 从 Django resolver 递归展开含动态 action 的方法，规范化 converter、方法和尾斜杠，测有无漏列/多列/重复 ID。自动 HEAD/OPTIONS、HTML Admin、readiness、未知路径分别列入边界表。CI 对规格、F 实例、受约束的拒绝样例和现有真实 Session HTTP 断言同时作差集。新增 route/字段后必须同一变更更新相应样例。

**合法调用样例的实施输入。** P06 从本轮 F 环境生成和执行两套脚本，仅使用 API 发现或本次夹具已显式授权的对象。每套使用独立 Cookie jar、同源地址和 JSON。凭证仅通过进程内存注入，收据脱敏。预期如下。

1. 用户 `GET auth/csrf/`，把 token 放进 `X-CSRFToken`，`POST auth/login/` 传 `username/password`。登录轮换后再取 CSRF 并 `GET me/`。选择 F 目录里的产品、城市、站址，用 `GET overview/national/?scope=synthetic&product=<L>` 取下一跳。示例明细调用 `GET observations/query/?scope=synthetic&product=<C>&cities=<city>&mode=window&indicators=CO&report_kind=hourly&window_definition=hourly_label_unspecified&start=2024-02-01T00:00:00Z&end=2024-02-02T00:00:00Z`。核 `results` 的产品、单位、观测时刻和原时窗。研究调用按 §5 的 JSON 形状用响应版本下钻，获准者准备/下载 F，拒绝者只得到 JSON。logout 204 后重读受保护资源应拒绝。
2. 管理员独立登录、轮换 token，从获权目录选择任务查 `admin/collection-jobs/{n}/` 和当前控制版本。用自有 F Job 触发，跟随响应的 Run/Window 至 `admin/quality-issues/` 和 `admin/operations/health/`，从内部回环断源追到最后获准批次。账号动作例为同一受限测试账号 `GET admin/accounts/{n}/actions/`，再 POST `{"action":"disable","request_id":"<本次小写UUID>","reason_code":"account_review","expected_version":<GET返回整数>}`。核 200 的 `action_id/active/version` 及被停用的旧 Session。换普通会话、旧版本、他人 ID、缺 CSRF 分别保留实际拒绝响应。备份签认与主机恢复依现有受控命令，前端只调用有权的意图/状态接口。

## 4. 综合验收矩阵

以下编号兼作证据目录名。每次运行在 `tmp/s07-evidence/<run>/Vnn/` 生成带运行时刻、提交/工程指纹、环境/镜像、输入类别、操作者角色、许可摘要、步骤、预期、实得、状态、证据路径和退出码的收据。公开索引只收获准的脱敏字段。**V01–V17 当前实得统一为 `NOT RUN`**。独立验证方在各条分别填实得值、原因和原件引用。不可执行的子项单独占行。A 与 B 均用独占 PG、Redis、Web 的真实同源 HTTP。凡涉及任务，启独立 Celery Worker 和适用的一名有效 Beat。R 是 S06 的独占恢复环境。

| 验收 / 输入、环境 | 操作与具体通过判据 | 保存的证明 |
|---|---|---|
| V01 归属与隔离，空库＋F/I，A/B/R | 创建随机实例、角色、空业务库、卷、网络、私有暂存及独占回环端口，先校验标签、项目、PG 标记、Broker 与许可模式。错误标记、错卷、意外宿主路由、共享项目名、旧环境变量、归属不明均在业务写入前报错。用任务自有假目标作拒绝例。守卫本身故障后只核销本次资源。 | `environment`、前后资源清单、代码/锁/镜像摘要、守卫首错和清理数量。 |
| V02 入口，F/I，A/B | 正式 WSGI 与显式开发模式分别启动。在 B 从根目录依次验证无参数、单组件和 check。A 留 base 兼容和正式配置输出。错端口、缺密钥、错误队列、迁移待办、恢复命令失败、子进程退出、父进程正常/异常退出逐项触发。观测 readiness 200/503、目标 Worker 订阅三队列并实际提交、Beat 新 tick、限时退出和资源归属。 | `startup`，子进程 argv 的脱敏版本、就绪/退出时刻及退出码、F Run/PG 回执、遗留进程清单。 |
| V03 调度互斥与停复，F/I，A/B 共用一个**专为此例创建**的 PG/Redis 实例 | 依 §2 让 `main.py`、原始 Compose、正式 Compose、直接 CLI 各自当一次持有者和竞争者，覆盖同入口双进程及跨入口配对。设一条可区分到期槽的 F 排程。先验双向 `flock` 争用，再做同时启动、SIGSTOP/恢复、仅断原 PG 会话而 Redis 可达、TERM/KILL 后两方竞争、路径同名异文件/替换及指定其他 scheduler。将旧 Beat 分别停在验锁前和验 PG 后的发布边界，故障中验证拒启或停发，原持有者确定退出后只允许一个新持有者。 | `singleton`。给测试 Beat 各配独立启动标识，关联到期条目/槽和 task ID，在隔离 F 队列暂缓消费，只读取实际 Redis 消息的测试标识和计数，再放行真实 Worker 消费。正常槽只有一个实际发布者。竞争失败者零条，原会话丢失或文件失验后持有者零次新发。故障前已进入发布调用的消息按 task ID 单列，核其入队与消费。仍在排程中的任务继续按未发处理。暂停期接管者零条，合法接管后旧进程零条。另从 PG 对账 `job/scheduled_for`、业务窗口与逐格首次认可各唯一，并区分 Worker 重投。留两锁身份、投递/消费时序和拒启收据。 |
| V04 CSV 未决失败，F，独立 A/R | `s06-a8f813ec1eac` 在票据准备断言处 FAIL，实际状态码和行数档未知。先对原节点串行至少 3 轮，再两套各自独立的 PG/服务/卷同时施压，各至少 3 轮，其中至少 1 轮两套通用全量同时运行，复核原负载。逐档在断言前记录实际状态/异常、关联号、行数档、字节、SQLSTATE、耗时、SQL 数、主机/容器资源及超时预算。原断言见下文。遇 503 查 10s 事务预算、5s 语句上限和 1s 锁等待，400 查行数/字节，409/410/403/429 分查版本、期限、用途、配额。复现须凭记录确定应用、测试准备或资源配置中的具体原因，修复责任点，再按同环境、数据和并发压力复验。 | `csv`。保留首错和修前后各轮 HTTP/PG 对照。规定轮次未复现时，保留历史 FAIL、各轮规模、环境、实际码和未定位风险，提交 GPT 独立裁决。该项在裁决前保持开放。 |
| V05 三链起点与身份，F＋获准 H，A/B | 新普通用户由 CSRF→登录→产品/城市/站址→收藏复访→研究条件走通。新受权管理员由任务/导入目录→运行→窗口→问题/受影响对象。可使用有据的受控配置，正常使用者须能从 HTTP 获取下一步标识。从全不可见与不存在两组核返回、分页和计数相同。各分支拒权或旧 Session 均留结果。 | `user`、`admin`，角色/状态、请求路径、投影 ID 的安全映射和拒绝对照。 |
| V06 产品、身份与来源状态，F/I，A/B | L 两个不共刻城市不可合榜，更换可见域核排序与分布。L 的 `*Level` 保持等级，C 七项直接来自同城 C，T 24h 独立，D 仅来源日报，S/H 即使重名也按命名空间/站址期分开。CO mg/m³ 与 µg/m³、小时 O₃/8 小时、未知标准及可披露原描述分测。迁址前后/停站/收藏回访、逐指标最近、指定历史空窗、仅有合法旧值、获准/缺据坐标及已知故障/未知分开。核观测/实际获取/执行三个时刻。用户 GET 后 Run 与上游请求增量为 0。 | `user`，手工 F 期望与 PG 键、修订、Batch 来源及同会话响应。来源状态证据时刻、作用域、撤权前后。 |
| V07 同质统计，F＋本次获准 H，A | 两站各 18/24 有效、共同 12/24：O/E、V/E、E共、P 单列，禁止给全天排名。再测刚好 75%、未知 E、部分在期、零/缺/删失/争议，Pearson 29/30 对及零方差。C 对 C、同城兼容 S 对 S、同一 H 数据集的有据同城 H 对 H，跨城站/跨产品/单位/窗口/标准不兼容受控拒绝。同窗独立算月加权、极值、冬季、箱线/热图。按 `input_version` 遍历支撑明细，核图/统计/获准 CSV 的同一选数。 | `research`，F 手算/SQL。真实 H 原件在许可域内 CSV/PG/HTTP 三方核数与署名。 |
| V08 业务窗/恢复，F/I，A（两真 Worker、一个 Beat）＋B 对照 | 将定时、手工及重试压到同一 Job/Window，校 Run/RunItem/Batch。注入提交前后断电式 Worker 退出、重复消息、过期租约、旧 owner 迟到、Redis 断连与投递未确认，实证 PG 原子性/首次认可唯一。分别制造部分成功、无新发布、合法空、429/结构异常、配额等待、可补缺口和仅最新不能回补。撤权后重试/补采不发请求。实证 F01/F02，管理员按已知关联号对账。 | `flows/collection`，两连接锁序、消费者与投递 ID、业务窗唯一键、独立 SQL E/O/V、内部源累计请求和失败链。 |
| V09 修订、争议及失效，F/I，A/R | 给同键 `10/v1→10/v3→20/v2`，值 10、版本水位 v3、当前键数 1，旧版入迟到账。再投无序异值→隔离→获准裁决/规则重算。按证据追事件到影响窗、现行统计、保存意图、票据和备份段，合法无关分组保持有效。同值内容版本与精确输入版本分别核。不允许 Admin 裸改官值。 | `quality`，按提交次序的修订图、当时的许可、当前计数、重算值、旧票结果和管理审计号。 |
| V10 账号、私有资源与导出，F/I，A/B | 两位普通用户、细分权限管理者及匿名分别验证登录/改密/停用再启/撤权/CSRF/限速，以及跨人收藏、保存查询、票据和分页。C02 用同一请求成对复读，① 在同一产品只改当前查询权外的对象、地域或用途的许可/隐藏行，权内标记和公开响应稳定，② 本人仍获权的字段、署名或输入变更，标记随之变且只影响其范围，③ 撤最后适用的展示依据后精确读拒绝且不返回标记。另撤导出依据，受影响的旧票拒绝下载、零 CSV 字节，④ 可信异值修订后精确标记改变，旧研究下钻/票据失效，无关研究和票据仍可用。按 S02 V06 和 `tests/test_s06_quality.py::test_v06_watermark_content_and_precise_ticket` 对照同值可信水位推进时两种协议的行为。精确读内容/资格未变时标记稳定，S05 研究输入换版、旧票失效。迟到旧版在两端均不回滚当前输入。下载与修订两种提交次序、提交失败、头/游标/错误及缓存也作对照。 | `private`，脱敏 Session、变更前后 PG 事实、同一用户标记相等/不等、实际拒绝码、CSV 字节/摘要及零输出证据。 |
| V11 用途、处置与分段恢复，F/I，A/R | 按产品×操作×受众×地域/对象/字段×有效期分别核取得、处理、留存、展示、分析、导出、备份、再分发。显式禁止优先，禁止拼权。撤展示时保留仍获准的行，撤最后留存或到期时先拒读写，再核受影响原字段/副本/票据/原卷待办和独立获准兄弟。隔离恢复用现行权威 PG、包外证明、限时意图、独立目标角色、一份私有读取。在两种提交次序下核目标回滚，旧证明、重授、失联、旧卷代次、提交失败均拒。已到达外部接收方的合法下载件依条款记录通知或下架义务。验收实查平台仍控制的副本及旧链接。 | `restore`，S06 专用 33 节点，PG 前后数、私有介质核销/待办、权限序号、目标残留和最小审计。 |
| V12 冻结前契约，F/I，A/B | 在 resolver 和真实同源 Session HTTP 对每个显式操作验成功及适用拒绝，高风险动作在 R 或自有实例。核 Cookie 轮换、Admin 边界、CSV/JSON、空/陈旧/部分、默认值/阈值及实际状态码。按 §3.2 逐一执行已迁移消费者，校验精确读原位置的字符串 schema，并以旧对象或在精确读公开响应中另挂 `rights_version`、`rights` 或 `grants` 的样例验证拒绝。用原 S05 研究版本实际续页、复访、准备和下载。核升级前签发的有效票在同密钥、期限及权内输入不变时仍可下载，精确读标记混用被拒，研究输入变化的旧版请求为 409。缺 operation 或 schema/领域不符亦须检出。 | `contract`，resolver 差集、OpenAPI、升级差异、旧调用迁移表、V10 的权内外用例及 S05 协议实测。泄露或必要兼容回归失败即停止冻结。 |
| V13 T09，百万 F，A。B 补测 | 用拟交付的 WSGI Compose 逐形状执行 §5。R3 按 §5.2 核五列小时分母、实际 `chart.points[].pos`、降采样极值/缺口及版本化全分页。保留冷态、两轮热 P95 与稳定性负载，调优后按同一清单复测。 | `performance`，数据和配置指纹、逐请求样本、分母/图点/明细对账、资源、SQL 计划及原始与复测轮。由 V16 核独立重建的同一配置与正式测量。 |
| V14 断网冷启与分服务故障，F/I＋有据 H，A/B/R | 提前预置镜像、锁定 wheel 与合法输入，关闭外部路由/DNS/代理，停止全部任务自有应用与基础进程，复用**同一自有数据卷**冷起。用原/新 Session、按署名发现合法 H 历史、复算统计。F 城市独立验证。再分别停 Web/PG/Redis/Worker/Beat，并以先 PG 后 Redis、先 Worker/Beat 后 PG 等逆序停复。核 PG 不通的 503、队列中断时仍可 PG 查询、心跳过期、最终待办/过期租约、零额外来源访问。真实 H 缺依据时保留该子项。 | `offline`，前后容器/进程/卷标记、路由与出站计数、回环 HTTP、分组件时序、恢复后独立 SQL 和 H 许可/来源。 |
| V15 同版全量与升级，F/I，A/R | 空库安装。用冻结 S06 在隔离卷造有账号/既存 Session/窗口/修订/处置义务的数据，停旧写者前向升级，二迁无待办。对照测试清单跑通用 suite 和恢复专用入口，去重并解释每个 skip。Ruff/format、mypy 配置范围、锁完整性、Django check/迁移漂移检查均保留原输出，补测新根入口和离线启动。 | `regression`，旧新叶、逐表核数、节点集合、静态原件/退出码、清理回执及受测树指纹。 |
| V16 独立交接，获准交付材料，干净 A/B | 由独立复核者从交包说明重建两入口，对照 V13 的最终 WSGI 配置、实际线程数和重建测量，再走三链关键成功/拒绝，复验所需高风险材料。核 CRC、成员清单、内容摘要、授权可分发性、源树与锁，复算公开证据索引，记录外部项。 | `handoff`，独立复核报告、Finding 与修复复验位置、版本化签收表，供 GPT 阶段审查。 |
| V17 重点 P1 与 S06 V19，F/I，A | 依 V07/V08/V09 分别签收三个重点 P1。另验已纳入的一般 P1 趋势：固定单产品、获准对象和有上限的日桶（拟最多 30 天），造完整/部分/失败窗、重试、无新发布及未知名册。以现有 PG 账本独立复算每桶 E/O/V、终态尝试及独立业务窗。响应率列确认响应尝试数/终态尝试数，新观测窗率列含首次新观测窗数/纳入业务窗数，明示区间、状态、部分及失败。重投不增加独立窗，部分成功保留首次认可。用 `started_at−created_at`、`finished_at−started_at` 核等待与执行时长，排期偏差另列。未知 E、零分母、缺时刻或缺可信发布时间时相应指标返回未知。桶内分子分母均只用可披露范围。 | `quality/trends`，逐桶手算/独立 SQL 与实际 HTTP、部分权限/撤权前后结果。一般 P1 趋势用现有 PG 汇总，三个重点 P1 各有单独结果。 |

**V04 保留的 CSV 判据。** 9,999 和 10,000 行准备均为 201、下载均为 200。核 `row_count`、唯一键/修订、CSV 版本、实际字节、4 MiB 上限及 SHA-256。10,001 行准备须为 `400/query_too_large` 且不新增票据。新增获权输入后旧票为 `409/input_changed`。新增 24 条权外记录不改导出版本、行数或摘要。另保留超候选格的 SQL 短路、低行数超字节拒绝，以及 S05 V17 原 SQL 次数、耗时和错误数断言。正式验收负载出现未解释的 HTTP 失败或数据错误，该轮及封版判定均受阻，其他 PASS 不能抵销。

### 后端范围与后续交接

| V3.0 用例 | 本版负责模块 / 决策 | 主验证、输入与后续负责人 |
|---|---|---|
| T01 登录至收藏 | M01/M02/M03/M07，D01/D05/D09/D10 | V05/V06/V10，F 会话、列表下钻与复访。S08 浏览器，S09 真实地图资源。 |
| T02 城市产品 | M01/M02/M04/M09，D02/D03 | V06/V07/V09，F 核 L/C/T/D 分离及 C 七项。真实城市对账限单独许可。 |
| T03 身份与断源 | M03/M04/M07，D03/D05/D07 | V05–07/V14，F/I＋有据 H。按指标、站址期、权限和显式窗核。 |
| T04 统计 | M02/M04/M05/M09，D03/D08 | V07/V09/V17，F 手算与获准 H 三方对账。S08 核图表交互。 |
| T05 任务 | M08/M10，D06 | V03/V08/V14/V17，F/I 真实 PG/Redis/Worker/Beat，完成后端全判定。 |
| T06 修订 | M06/M09，D04/D11 | V09/V10/V11，F/I 当前值、水位、争议、下游与旧票，完成后端全判定。 |
| T07 身份 | M06/M07/M10，D01/D10/D11 | V05/V10/V12，真实会话、对象范围和管理动作。浏览器端另归 S08。 |
| T08 用途 | M06–M09，D04/D07/D11 | V08–11/V14，处置前后、分段恢复、现行权威和最终授权，完成后端全判定。 |
| T09 负载 | M05/M06，D12 | V13 按 §5 仅 Compose 作正式后端判定。B 兼容测。S08/S09 记浏览器绘图。 |
| T10 离线 | M03/M10，D05/D07/D13 | V02/V11/V14，F 与合法 H 各自取证。S09 独立判真实城市离线与整机。 |
| T11 城市在线 | M02/M08，D02/D06/D13 | V01/V16 核 gate 仍关闭、记录既有 A03 单次。三地区各至少两真实发布周期、城市获准查询/重放/断源闭环、事先冻结的新鲜度/成功率/允许缺口、受限试运行自然 168h 和正式 GO 由来源方/S09 单判。 |
| T12 运行体验 | M01/M08/M10，D06/D09/D10 | V05/V06/V08/V14，断源/本地故障分别诊断。S08/S09 验列表/地图、窄屏、键盘和渲染。 |

## 5. T09 正式性能与稳定性

### 5.1 环境、数据和取数口径

**C03 受测配置随软件交付。** A 使用 `compose.yaml` 加拟交付的 `compose.wsgi.yaml`，包括 Gunicorn 单 worker、经验证的 gthread 线程数、真实 PG、Redis、独立 Worker、唯一 Beat 及同源回环 HTTP。运行手册固定这两个文件的顺序、启动命令、镜像 digest、锁文件及所有影响性能的非密参数。将配置文件摘要、有效配置的脱敏清单和实际进程 argv 一并入包。V13 至少一轮正式热测在按交付手册重建的干净实例上完成，可计入规定的两轮。V16 核对重建得到的配置指纹和成绩。临时调参须先写回交付配置，重新完成四形状的正式轮次及稳定性测量。

资源与其他验收错峰，先记录 CPU 型号/核数、内存、磁盘/余量、WSL/容器限额、镜像 digest、Python/锁版本、Web 线程、PG `work_mem/shared_buffers/max_connections`、连接寿命、网络及后台任务频率。保留正常健康与授权开销。每次测量记录有无其他负载、检查点和 autovacuum。禁止清全机页缓存、占用共享卷或调整共享服务。

**分母固定为 1,000,000 个不同的小时逻辑观测键。** 建两个有清晰 F 来源和 `synthetic` 标识的测试产品，各自具有 C 形状或 S 形状。按对象×指标建立 100 条序列。城市侧用 5 城，各有 CO 序列，其中 2 城各加其余六指标，另 3 城各加 PM2.5，共 20 条。站点侧用 20 个有有效期的站址，各取 CO、PM2.5、NO₂ 与 O₃，对应 80 条。每条从 `2024-01-01T00:00:00Z` 起覆盖 10,000 个预定小时，共 200,000 个城市键、800,000 个站址键。热点 F-C 取 `source_timezone=UTC`，使用 `research_coverage.fixture_product/evidence_calendar` 已支持的 2024 全年 F 历表、有效城市身份及普通全窗对象码。五城的 CO 使用 `mg/m³`、`hourly_label_unspecified` 和 `not_applicable`，其余字段沿 F 产品明示的单位、时窗与标准。F 站址分属登记城市，同城站比较使用同一城市。固定种子，在 R3 窗口内预置可复算的浓度、有效峰值、有键缺值和应有无键格。为了在图上出现真正的断档，在清单指定的应有小时留无键格，将这些键移到同对象主测试期以外的预定补足小时，在清单中记录键的去向，保持各分表行数。24h/100 行用全有效窗口，年度与比较含已知缺口。有效期、许可、覆盖历表、对象/指标选择、冻结时刻及生成器摘要随清单保存。

先经现有规范化/修订服务建立每类模板。参考 `tests/s05_volume.py:add_rows` 的安全约束，在本轮空库标记校验后按固定块写 key、真实当前 revision 及合法的贡献 receipt/batch，将收据按产品、实际小时/有界采集块归入贡献批次，维持唯一键、外键、约束和产品来源。记录仅供读性能造数。每张事实表分别报 `COUNT` 与分产品/对象/小时/质量计数。独立 SQL 验每个键恰一当前、抽核时区/值/回执和手算结果，源码路径及 ORM/SQL 索引计划均记录。修订表、当前行、关联回执、额外账号/任务行不重复折算百万。生成完在专库 `ANALYZE`，记录尺寸、全表/索引/WAL/临时用量和可用空间。生成中断后只处理本轮独占数据，复跑以确定性指纹核对。

建立五个实际启用的普通 F 用户，逐人通过同源 `auth/csrf/`、`auth/login/`、重新取 token 和 `me/` 核本人，分别保存内存 cookie jar。五人均有同一热点产品的适用展示、统计、绘图和明细下钻字段许可，源目录、运行器和测量结果标明合成。先用这些会话验证结果非空且形状正确。计时阶段复用各自连接、含 Session 查验与真实响应体传输，POST 携 CSRF，缓存/压缩/keepalive 策略写在收据。账号准备、校验与行为测试独立计时。为固定 B 的补测下限，至少复用同一百万键生成清单和五个 Session，每形状每人 10 次，保存正确性和观测分位数。B 的数据另行在其自有实例生成。

### 5.2 四个 HTTP 场景

全部请求都经过 `/api/v1/`。R1/R2 采用 `observations/query/` 的显式窗，`product=<F-C>`、`cities=<id>`、`indicators=CO`、`scope=synthetic`、`mode=window`、`report_kind=hourly`、`window_definition=hourly_label_unspecified`。R3/R4 用同一个 F-C 产品与该组真实用户，POST 的例子如下。产品号、对象号和 `source` 均从本次清单解析。

```json
{"selection":{"product":101,"source":"s07.fixture","scope":"synthetic","cities":[1,2,3,4,5],"indicators":["CO"],"units":{"CO":"mg/m³"},"standard_series":{"CO":"not_applicable"},"report_kind":"hourly","window_definition":"hourly_label_unspecified","start":"2024-02-01T00:00:00Z","end":"2024-03-02T00:00:00Z"},"method":"compare","grain":"day"}
```

| 场景及窗口 | 调用、结果验证 | 预热 HTTP P95 上限 |
|---|---|---|
| R1 单对象 24 小时 | GET 明细，24 个连续有效小时，页大小 50。核 24 个独立键、单位、时间和值。 | 1 秒 |
| R2 真正满页的明细 | GET 明细，单对象至少 200 个有效小时，`page_size=100`。固定首页/有效后继页序列，各取 100 行，核排序、游标、无重漏。计时中单页是一个请求，单报各页层。 | 1 秒 |
| R3 五对象 × 30 天单指标小时序列 | `POST analysis/compare/`，用示例的同一 F-C 产品、5 城、CO 与 30 天半开窗。校验 5×720=3,600 个真实小时候选格、逐列及共同分母、扁平 `chart.points` 和按版本遍历的支撑明细，做法如下。 | 2 秒 |
| R4 单对象全年按月浓度汇总 | `POST analysis/statistics/`，将示例改为 1 对象、`method=statistics,grain=month`，源地方日 `[2024-01-01,2025-01-01)`，8,784 小时、12 月桶。按各月全部有效小时独立核带权均值和分母。另以 365 日及长于 366 日的拒绝例验证边界。 | 2 秒 |

**R3 的小时选数、统计桶与绘图分别对账。**

1. 以 F 清单生成五列完整的逐小时时间格，独立 SQL 左连当前修订。对 `columns[].pos` 对应的实体各核 `E=720`、实到键数 O、有效数 V。`E_common=720` 按不同的共同应有时刻计，P 取同一小时五列全部有效的时刻数。逐项报告 O/E、V/E、P/E_common 与缺键、实到缺值的差异。`grain=day` 让 `statistics_buckets` 返回来源地方日的 `buckets`，日桶的覆盖和极值由原小时格校验。
2. `observations/research_statistics.py:chart` 将各列放在同一 `chart.points` 数组中，用 `pos` 关联 `columns`，按 `at,pos` 排序。有 series 披露权时，另取各列合计 ≤1,000 行 `slots` 的小窗作对照，验 `chart.resolution="exact"` 及逐格 `pos/at/quality/value/expected/key_id/censor_limit/censor_direction`。正式 R3 必须得到 `resolution="range"` 和 `chart.interval_seconds`。五列共用从请求起点算起的 200 个时间桶，该 30 天例的桶宽为 12,960 秒。逐 `(pos,at)` 对照 `minimum/maximum/valid_count/gaps`，极值和 `valid_count` 只计有效样本，全无有效样本的桶极值为 null。`gaps` 只计应有且无键的格。用已知峰值、空格及有键缺值作反例，核总点数 ≤1,000、五个 `pos` 均可还原。只有日均、图点缺失或误把点数作分母，均判 R3 结果错误。
3. 保持同一 Session、父查询和研究 `input_version`，向 `analysis/details/` 分别提交 `support=observed/effective/paired`，每页最多 100，跟随 `page.next_cursor` 到末页。用 PG 的 `(observed_at,pos,key,revision)` 集合核无重漏。全窗已知历表下，三组行数分别是 ΣO、ΣV、5×P。小时明细、日统计与图点共用这批输入。逐页核验及独立 SQL 在计时外执行，R3 计时每次读取完整的比较响应。

另作规模反例。完整序列超过 1,000 个绘图点时检查共用时间桶、min/max 与断档，核降采样前的分母/明细/CSV。以 6 对象、非年度 31 天、年窗超预算、过大页、研究 JSON 1 MiB、CSV 10,001 行或超过 4 MiB 测各自受控错误。原 CSV 的 9,999/10,000/10,001 阶梯及版本更换另见 V04。返回的 `truncated` 或游标必须能解释有限子集，任何静默丢格或把拒绝响应计作快速成功均判失败。

### 5.3 测量、判定和超预算处理

1. **冷态单列。** 每种形状分别在保留数据卷的独占 A 上重启 PG/Web/应用缓存，记录启动、就绪时间，从预先存在的五个 Session 发第一批请求。换新的进程各重复三批并列出每次值。报告 PG buffer 与应用冷、OS 页缓存可能温及已知准备请求，只有独立可销毁主机冷启并可证明时才标磁盘冷。R2 的冷测用合法第一页。冷数据与热样本分别出表。
2. **正式热态。** 固定采样种子、对象、窗、授权与资源。每形状五个客户端同时通过起跑栅栏，每人先热身 10 次，在固定预热结束时开始采样。每人每轮 100 次，每次等完整正文再发下一请求，每形状每轮 500 次。至少两轮独立计时，第二轮反转场景次序，至少一轮从交付配置重建运行。记录实际重叠的请求数与各会话起止，不把串行回放记五并发。单条时钟为客户端单调钟发送至读完最后一字节，包含排队、Web/Session、ORM/SQL、序列化与传输。超时、重定向、错误或错内容全部留样，不重试掩盖。四种形状分别分组，排序取第 `ceil(0.95*N)` 个值，N=500 时为第 475 个。每轮均须满足该预算，报告 N、P50/P95/P99/max、吞吐、每用户/各页结果、预期响应正确数与错误率。五人合法请求的 HTTP 失败、超时及数据错误数均须为 0。发现未解释错误直接按 C04 阻断。单独 20 分钟按四形状轮换，记录吞吐、长尾、连接/进程/临时空间与积压，异常增长须定位。
3. **诊断留证。** 在独立诊断轮采 `pg_stat_activity/pg_locks`、等待类型与时长、容器 CPU/RSS/I/O、连接数和磁盘临时文件。用等价 Session 记录每形状 SQL 数、数据库/视图/序列化分层耗时。在自有库对实际代表性读 SQL 取 `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`，保存统计量、索引扫描/估算偏差、临时块及年桶排序方式。专门对照 `research_access` 的同产品锁队列和 S05 V17 的年度排序外溢。诊断开销与正式 HTTP 样本分开记录。
4. **回归预算。** 有一形状超预算，保留第一次运行，按排队、锁、SQL、投影/序列化或传输定位。先评估旧索引与投影、批量取数、重复 CTE、排序。任何授权锁粒度变动要重新验证撤权、版本和零字节拒绝。每次变更固定负载/种子/资源从原形状复测，再测四形状和相关权限回归。调整 PG 参数也要记录前后与新环境基线。若实测仍不能达标，P08 保持 FAIL。分区、持久化汇总或额外缓存等重大变更须凭瓶颈和失效成本另审。B 按 §5.1 的补测下限报告其实际样本、规模及硬件。T09 判定来自 A 的规定负载。

## 6. 风险、封版与独立交接

### 6.1 实施追踪与回退

| 责任步骤 | 历史条目（详见台账） | 主要需求 / 失败时处理 |
|---|---|---|
| P01 | L01/L12/L31–L34 | D07/D13，T08/T10/T11。守卫失败立即保全本次收据，隔离条件重建后再允许业务操作。 |
| P02 | L19/L20/L28/L41 | M08/M10、D06/D12/D13，T05/T10/T12。端口或锁所有权不明时退出，只停本次启动的子进程。旧版本只在独立旧环境复现。 |
| P03 | L05–L07/L10/L12/L22 | M06/M08、D06/D11，T05/T07/T08。先捕获首错，再定因、修复、等压复验。规定轮次未复现的 L22 仅由 GPT 独立裁决。 |
| P04 | L08/L15/L16/L18/L23–L25/L33/L35 | M01–M07、D02/D03/D05/D07/D09/D11，T01–04/T07/T10。泄露、跨产品串值或项目权利无法支持所需字段时关闭该输出，保留既有获准子集并复核契约。 |
| P05 | L11/L15/L17/L27/L38 | M08–M10、D04/D06/D07/D10，T05–08/T12。旧控制版本、错范围、未知分母返回有界拒绝/未知。原操作继续按原号审计。 |
| P06 | L17/L21/L23–L25/L36 | M01–M10、D01/D10/D11，T01–08/T12。规格/实得/兼容三者有差异就停在候选基线。 |
| P07 | L02–L11/L13/L15–L18/L25/L27/L28 | 三条链、D01–D11，T01–08/T12。集成反例退回相应模块修复，再跑受影响链路的真实组件。 |
| P08 | L22/L26 | M05/M06、D08/D12，T04/T09。保留每次负载收据，按 §5.3 在同一交付配置重测。未解释的 HTTP 失败或数据错误直接阻断。 |
| P09 | L04/L09/L11/L14/L19/L20/L28/L31/L37 | M03/M08–M10、D05–D07/D13，T05/T08/T10。恢复证明缺失时拒绝读取、保全待办。当前 H 缺权由保管人补齐。 |
| P10 | 全部 L01–L41，特别是 L14/L29/L30/L32–L41 | D01–D13、T01–T12。逐条核验阶段和授权边界，保留历史失败。本版包缺证或与冻结树不一致，回到 V15/V16。 |

**可恢复操作。** 只有具名、已验证归属的任务资源可以重建。基线镜像、锁和测试输入 manifest 允许在第二个独立环境重演。迁移若有必要，先在自有旧版快照前向演练和比较原账，失败留现场，另建干净旧环境供调查。真实库的处置、反向迁移、签认、新的外部使用与部署由各自权利人另行决定。PG 或包外权威失联时恢复应停止，保管介质缺失时保留可核实的在途义务。

### 6.2 API 及封版验收包

| 交接件 | 独立核验方式 |
|---|---|
| 版本与重建 | 记录源码提交/完整变更、锁和镜像 digest、所有迁移叶、Python/Compose 两种配置的公开模板、正式 Compose 文件组合、Gunicorn 最终线程数、实际进程参数及 V13 重建测量指纹、wheel 与镜像离线准备方法、先检后启动/人工迁移/停复命令。扩大旧 `source_hash` 的范围至 `main.py`、所有运行用 overlay、schema 与 runner。 |
| 契约与调用者 | v1 路径/方法/参数/默认排序/分页及决定性语义，OpenAPI、样例、合法/拒绝的状态字典、权限/字段投影和来源归属、实际 Session 交叉校验、兼容性差异及消费者清单。附 §3.2 旧字段迁移与 C02 正反例、S05 旧票失效及有效票继续下载的实测。用新环境的 Session 从普通起点完成三条链。 |
| 验收与合法材料 | `docs/verification/S07.md` 按 M/D/T、台账、P/V 列实际输入与结果。`tmp/s07-evidence/<run>` 保存按权脱敏的原输出，索引记录路径、哈希、环境与失败。H、F、注入故障与北京单次历史实证分别标识。私钥、Cookie、口令、受限原件/可重构摘要及真实备份正文留在各自受控边界。可公开包仅放已核分发权的软件、数据说明、F 样例与可核验摘要。 |
| 封版记录 | 从干净副本校验 ZIP 完整性、逐文件 manifest 与独立运行结果。记录评审版本、Finding ID、处理、复验和未覆盖项。GPT 给阶段裁决后，登记接口基线版与日期供 S08 使用。S08/S09 变更记录理由、受影响端点/客户端/版本和权限，先做兼容性与真实 Session 回归，回到受影响的 V 再发修订版。破坏性变更须显式审批、迁移或新版本。来源授权/撤回依当时事实即时生效。 |

**S07 Exit Criteria。** 以下按同一待交付版本逐项判定。

1. 十模块后端在其适用合法输入上能从全新环境按手册完成三链，T05–T08 后端全部达标。L15–L18 的原反例和修复后实际结果齐，数据正确性、权限、事务、版本或恢复无开放阻断。L22 复现须定因、修复并在同等压力下通过原断言。未复现则保留历史 FAIL、全部轮次的规模、环境、诊断和未定位风险，交 GPT 独立裁决，在裁决前保持开放。任何正式验收负载出现未解释的 HTTP 失败或数据错误，直接阻断封版，其他 PASS 不能抵销。
2. `python main.py`、正式 Compose 与各单组件通过就绪、错库/错队列、故障停复与一致性测试。四个 Beat 入口通过 C01 的同实例排他测试，以真实 Broker 投递和 PG 窗口认可两套收据核准支持的组合，并实证其余组合拒启。合法历史 H 的离线冷启、查询和分段恢复有各自真实收据。若缺现行 H，本地 F/I 工程可继续，T10 后端 H 子项保留 BLOCKED/NOT RUN，暂缓“后端稳定，可启动前端”的完整判定。
3. 交付的正式 WSGI Compose 可按手册重建，同一配置在百万小时键、五 Session 下达到全部四项热 P95 和稳定性要求，R3 的分母、图点与分页明细通过 §5.2 的逐项对账。三项重点 P1 分别有 §4 的正反例。L38 作为一般 P1 由 P05/V17 交付跨窗质量覆盖、任务时延和成功率趋势的 HTTP 与 PG 对账。S06 V19 的历史状态保留为 NOT RUN。
4. 合法 API 样例与冻结版实际行为、权限、错误、字段和分页一致。C02 消费者迁移、精确读安全性与 S05 研究/导出票据兼容性均以 V10/V12 实测通过后才能冻结。V15 全量与专用集合、V16 独立复核/交包校验完成，所有必需 Finding 修复复验或由审查方和需求方对其具体剩余风险明确处置，涉及硬性 P0/预算/合法性的不达标项继续阻断。外部项以证据、负责人、条件交给 S08/S09 或来源责任人。

## 7. Codex 规划技术自审

**`READY FOR GPT REVIEW`（审查条件落实复核）。** GPT 已裁定三项技术选择，当前无须用户额外决定的重大技术问题。下表记录 Codex 对修订稿的自审。实际运行、来源授权和接口冻结依各自验收条件判定。

| 复核项 | 修订结果及可复核位置 |
|---|---|
| 决策与追踪 | P05/V17 和 L38 指定一般 P1 的有界趋势及独立 PG 对账，沿用现有账本。保留 10 项任务、17 组验收、41 项遗留及原有依赖，三项重点 P1 各自签收。L14 的专用回归与 L31 的 H 授权边界沿用原预检。 |
| C01 | 对照 `compose.yaml`、项目 Celery 应用、`ObservedScheduler` 和锁定版本的 Celery Beat 启动/发送顺序，在实际调度端共用双锁。§2/V03 补入文件同一性、暂停期间 PG 失联、接管竞争、实际投递与业务窗双重证据。 |
| C02 | §3.2 点出三处解析旧子字段的消费者、整体相等比较和独立 S05 协议。V10/V12 要求权内外成对实测、旧字段原位迁移、下钻与旧票回归，作为 API 冻结前提。 |
| C03 | 对照 `research_statistics.chart/summarize`、`analysis_query.detail_rows` 和 `research_api.drilldown` 修正 R3 的响应断言。§5 保留全部负载强度，以同一交付配置的原始样本及重建结果签收 V13。 |
| C04 | 对照 `test_s05_cost.py:359` 和 S06 首错，将定因、同压复验、未复现时 GPT 裁决及正式负载阻断写入 P03/V04 和退出条件 1。 |

文档静态检查 **PASS**。已核对 41 个 L、10 个 P、17 个 V、12 个 T 的编号，4 项条件至任务、台账和退出判据的对应关系，以及三份文档的 36 个本地链接。S07 运行验收仍为 **NOT RUN**。本稿供 GPT 复核条件落实情况，进入 P01 需用户后续实施授权。
