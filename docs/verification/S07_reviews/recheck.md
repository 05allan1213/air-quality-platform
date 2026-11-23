# S07 独立 L3 返修复核与 V16 重建

本复核承接 `docs/verification/S07_reviews/l3-review.md`。初审的三项 Finding 保留原记录，本文件记录返修后的独立复验结果。

Coverage: targeted: S07-L3-01, S07-L3-02, S07-L3-03

复核使用运行候选 `bec88f468030deb3ea900d5ef07cef6f548d8d6b`，输入类型为本仓自制合成数据 F 与注入故障 I。未使用真实 H 数据，也未访问外部实源。

## 候选和交付包

| 项目 | 实得 |
|---|---|
| Git 候选 | `bec88f468030deb3ea900d5ef07cef6f548d8d6b` |
| ZIP | `tmp/s07-handoff/bec88f468030-rzpy19bz/S07.zip` |
| ZIP SHA-256 | `23f122990658087796008f6f2ca135bfd6c4045d905168d7da44c42e09dd0baf` |
| 清单 SHA-256 | `a6e62f4190f2d9d1ff53cd76444cc84bba30a68ce06f96478b07ac935dc1d052` |
| 运行源码 SHA-256 | `35843e833e9a12ceeb5bcc19a3a772f1e612b05da47395866c6f1aafe66a706a` |
| 独立提取目录 | `/tmp/s07-l3.epEcI4uN/app` |

ZIP 共 327 个成员，其中 326 个为交付文件。CRC、成员白名单、重复成员、路径、模式、私钥标记、Git tree、Git blob SHA-1、`git cat-file blob` 字节和提取后源码摘要均为 **PASS**。独立提取目录使用 Python 3.13.18 完成 `uv sync --locked --offline` 和 `uv lock --check --offline`。

候选包内的 `S07_T09.json` 能逐字节核对 82 个原始收据和 4,400 个正逆序 HTTP 样本。该文件中的性能收据仍绑定源码 SHA-256 `92f1ed695ac8222a592037ed4683344a8a15407104b6af49f46d8c5a9316b3bf`，对应早期候选 `91f0d19`。`bec88f4` 交付快照当时的同版 T09 状态为 **NOT RUN**。当前同版测量见下文。

`bec88f4` 包的 README 包含 7 个指向包外历史章节的相对链接，目标为 `docs/operations/S04.md`、`docs/verification/S04.md`、`docs/verification/S01.md`、`docs/plans/S02_IMPLEMENTATION_PLAN.md`、`docs/verification/S02.md`、`docs/verification/S03.md` 和 `docs/operations/CNEMC.md`。S07 交付所需的宿主运维、API、S05 票据和 S06 受控备份指引均已在包内，故该项作为文档遗留记录，不重新打开 S07-L3-02。

## 三项 Finding

| Finding | 返修判断 | 独立复验依据 | 结论 |
|---|---|---|---|
| `S07-L3-01` | README 的 F 演示只启动 Web、Worker 和依赖的 PostgreSQL、Redis。需要 Beat 的基础和正式 Compose 入口先登记实例、数据库、Broker 和双锁身份。 | `tmp/s01-evidence/s01v11d54bc30e04/s07-legacy.json` 验证四个常驻服务、迁移退出 0、真实 Session 提交返回 202、Worker 完成和普通用户回读 `1.250 mg/m³`。同一收据验证 Redis 故障时读取仍可用，提交返回 `503/dispatch_unconfirmed`，换代后同一 Worker 恢复。`tmp/s01-evidence/s01v3c8868847cf1/s07-legacy.json` 另验证登记 Beat 的五服务启动、PG 排他持有者、保卷重建和 Redis 恢复。两份收据均清理归零。 | **PASS** |
| `S07-L3-02` | 包内补充 S05 研究票据、S06 受控备份、保管人签认和隔离恢复指引，并统一宿主运维入口。 | 从最终 ZIP 提取后核对 `docs/api/v1.md`、`docs/operations/S07.md` 和 `docs/operations/S07_handoff.md` 的包内目标和锚点。API 样例使用目录卡片的 `source` 命名空间字符串。研究示例通过实际 `parse_spec` 和 `normalize`，票据输入包含 64 位研究版本标记。 | **PASS** |
| `S07-L3-03` | 来源状态按完整观测键、回执关联修订、任务窗口和适用标准归因。无回执时只使用可证明的冻结窗口和标准，无法归因的状态保持 `unknown`。 | 修前 `tmp/s06-evidence/s06-80b42120c9f5/pytest.json` 保留 3 个失败。修后 `tmp/s06-evidence/s06-62fbc887042c/pytest.json` 为 40 PASS，包含同刻双 O₃ 窗、AQI 双标准、无效冻结配置和撤销披露权。修后真实 HTTP 收据记录 14 次 `queryObservations` 调用。最终候选同源复跑 `tmp/s06-evidence/s06-e522058a6dc8/pytest.json` 为 6 PASS，`V01` 清理通过。 | **PASS** |

## `bec88f4` 独立入口

以下收据全部由 `/tmp/s07-l3.epEcI4uN/app` 内最终候选脚本生成，收据中的源码摘要为 `35843e833e9a12ceeb5bcc19a3a772f1e612b05da47395866c6f1aafe66a706a`。

| 入口或链路 | 收据 | 结果 |
|---|---|---|
| 宿主 B 启动、错误边界和停复 | `tmp/s07-evidence/s07-b-7ee4b5224e96/V02/startup.json` | **PASS**。包含待迁移、错库、缺密钥、错误端口、错误队列、独立 Beat、独立 Worker、父进程终止、残留 Beat 和重启恢复等 20 个场景。V01 资源清理通过。 |
| Compose A 启动 | `tmp/s07-evidence/s07a-32c6860fa791/startup.json` | **PASS**。WSGI 健康检查 200，Worker 和 Beat 心跳有效。V01 归属、无默认路由、双网络和清理通过。 |
| 三条业务链和故障链 | `tmp/s07-evidence/s07a-55c2d1863e17/flows.json`、`participant.json` | **PASS**。13 个场景全部通过。独立 SQL 记录 18 个业务窗、27 个 Run、2,863 个首次认可和 0 个重复认可。普通用户、管理员、研究查询和 CSV 票据均走真实 HTTP。 |
| 宿主 HTTP 契约 | `tmp/s07-evidence/s07-b-4411e3b51b14/V12/admin.json` | **PASS**。V01 清理通过。 |
| 宿主离线与故障 | `tmp/s07-evidence/s07-b-52fc7fd3e4fd/V14/host.json` | **PASS**。V01 清理通过。覆盖 `python main.py` 全组和 Web、Worker、Beat 单组件入口，以及 Beat 退出后的安全结果。 |
| Compose 离线与故障 | `tmp/s07-evidence/s07a-1d111c07403c/V14/compose.json` | **PASS**。V01 归属、隔离和清理通过。 |
| 来源状态专项 | `tmp/s06-evidence/s06-e522058a6dc8/pytest.json` | **PASS**。6 个针对性测试全部通过，R 环境清理通过。 |
| 处置和恢复专项 | `tmp/s06-evidence/s06-05124466f277/pytest.json` | **PASS**。33 个恢复节点全部通过，V01 清理通过。输入与目标均为本轮 F/I 隔离资源。 |

静态复核为 **PASS**。Django check、Ruff lint、Ruff format、OpenAPI 和 resolver 测试均通过。

## V15 同版收据复算

工作区的 `S07_V15.json` 对应 `bec88f4` 的 307 个运行文件。独立重算静态检查、A/R 节点对账、S06 前向升级、V17 趋势及归属清理原件的 SHA-256，均与索引吻合。A 实得 562 PASS、38 NOT RUN，R 通用入口 567 PASS、33 NOT RUN，R 恢复专用入口 33 PASS。按节点 ID 去重后 600 个全部 PASS，A 的 38 个跳过节点均有对应 R 通过记录。S06 旧写者退出后，旧 Session HTTP 200，60 张表对账，第二次迁移无待办。

打包工具 `scripts/s07_handoff.py` 本身计入运行文件摘要。最终包若更改该文件，须以最终源码摘要重新核对 V15 与 V13。仅修改未参与运行摘要的文档时，可逐文件比对被测与交付运行文件，并复用同摘要收据。

## V16 文档闭合与正式测量基线

`a69371838f2e7bafe18f1ee3b436b599c6e304b1` 待测包在独立目录 `/tmp/s07-l3.ky93V5g8/app` 提取。ZIP SHA-256 为 `4d6e6e2c713131ca94bc28ae5f9e96c7e7a405d5a7e0c68e0b3bf7e404c08b69`，清单 SHA-256 为 `b7b2205f9e2e2d00ded74f48f67b3eb5fc1a2a023323e4c18635f937f1fe777b`，共 329 个 ZIP 成员、328 个交付文件。独立检查 CRC、成员白名单、权限模式、Git tree 与 blob 字节均 **PASS**。README 将 7 份历史材料标为完整仓库路径，包内相对链接全部命中。包中含本复核与同版 V15，307 个运行文件与 `bec88f4` 包逐文件字节相同。

八轮独立运行的二十份已脱敏 JSON 原件已按各自 run ID 归档在当前工作区 `tmp/s07-evidence` 与 `tmp/s06-evidence`。复制后逐字节核对 **PASS**，同路径冲突数为 0。

## V13/T09 同版复算

A 的正序、逆序和诊断分别从 `a693718` 待测包提取重建。三轮的有效配置 SHA-256 均为 `02c3c6cd94cbf532e4630b490aa085cf2af2f62e73b3f9ca36ef2dae24912fd7`，包含现有发布文件次序、锁文件、镜像和实际 Gunicorn 单 worker、gthread、8 线程参数。三轮均造 1,000,000 个 F 小时键，数据指纹为 `9de22fe114c4caabc2f326163ea2f14ef1c74df9c4cbc3089254376c6d5727f3`。82 份原件的字节数与 SHA-256 均可独立复核。

| 入口和顺序 | 每形状正确样本 | R1 P95 ms | R2 P95 ms | R3 P95 ms | R4 P95 ms | 判定 |
|---|---:|---:|---:|---:|---:|---|
| A 正序 `s07a-fe3859b8b733` | 500 / 500 | 198.905 | 225.682 | 814.508 | 1,766.039 | **PASS** |
| A 逆序 `s07a-25e97781e247` | 500 / 500 | 191.019 | 237.900 | 840.756 | 1,790.999 | **PASS** |

两轮合计 4,000 个计时样本和 400 个预热样本全部正确。按各场景原始单调时钟的第 475 个值复算 P95，四项均低于 1,000 / 1,000 / 2,000 / 2,000 ms。两轮每形状均观察到 5 个请求重叠。R3 依据独立 SQL 的 3,600 个小时格重新计算覆盖，`E_common=720`，`P=710`，观察、有效和配对明细为 3,594、3,590 和 3,550 行。五个 Session 的图点、日桶和明细断言一致。

稳定性段的实际请求采样跨度为 1,201.614 秒，17,049 个请求全部正确。PG 和 WSGI 重启后的 12 批冷态各通过 5 个请求，保留操作系统页缓存。诊断 `s07a-1a4d792f7cca` 记录四个场景的诊断原件，R4 的 25 个诊断样本全部正确。三轮的容器、网络和卷清理均通过。

### 宿主 B 首错

`a693718` 的宿主补测 `s07-b-014a8f1de019` 已生成同指纹的 1,000,000 个 F 小时键。首个 `pg-before` 快照读取 Redis 队列时抛出 `ValueError`，四种形状的计时样本数均为 0。该轮为 **FAIL**，隔离命名空间和资源清理为 **PASS**。原件见 `tmp/s07-evidence/s07-b-014a8f1de019/performance/result.json` 与 `tmp/s07-evidence/s07-b-014a8f1de019/V01/result.json`。

### 修订待测包 `5c2073e`

`tmp/s07-handoff/5c2073eeefca-x87z63ck/S07.zip` 来自 `5c2073eeefca01fcc004010534b65e8da67bace7`。独立提取目录为 `/tmp/s07-l3.9PGz3d6V/app`，ZIP SHA-256 为 `8a3746ef2a33280e390e28536640ee7de02a3251e8815d2bb51512a5982af823`，清单 SHA-256 为 `0f5bc094541340631b4e7083a82d2f1b7081201afbe908f84272a9d51e798be6`。329 个成员的 CRC、328 个交付文件的白名单、模式、清单内容、Git tree 与 blob 字节、README 相对链接均 **PASS**。

307 个运行文件的 SHA-256 为 `1258e04db92db956deef816b06cef660cbdefd677aef7994b263cbd24d85f6b8`。相对 `a693718` 的 Git 路径差集和 ZIP 运行字节差集均仅有 `scripts/s07_performance_diagnostics.py`。该脚本把 Celery 的 `redis+socket://` 转成 redis-py 支持的 `unix://` 再读取队列深度。使用锁定环境的解析器独立核得旧 URI 抛出同类异常，修订 URI 选择 Unix socket，Compose 的 TCP URI 仍解析为同一连接参数。

三项 Finding 和前述独立 A/B/R 运行收据记录的是 `bec88f4` 实测。`5c2073e` 的启动入口、应用和 API、Compose 配置、恢复与接口验证器逐文件相同，性能诊断模块只由性能入口使用，旧收据继续覆盖这些路径。`5c2073e` 待测包内 V15 与 T09 报告引用历史运行摘要。当前 V13 的新投影与原件见下文。

### `5c2073e` Compose A 正式性能

从同一待测 ZIP 独立提取的 A 正序、逆序和诊断三轮通过。复算 82 份原件的 SHA-256、相同数据和有效配置摘要、五个已登录 Session 与实际 Gunicorn 参数。每个形状每轮有 500 个正确计时样本和 50 个正确预热样本，达到五请求重叠。P95 取原始单调钟时长的第 475 个值，四项预算为 1,000 / 1,000 / 2,000 / 2,000 ms。

| 轮次和 run ID | R1 P95 ms | R2 P95 ms | R3 P95 ms | R4 P95 ms | 判定 |
|---|---:|---:|---:|---:|---|
| 正序 `s07a-a4938d08d720` | 198.860 | 235.229 | 850.836 | 1,805.339 | **PASS** |
| 逆序 `s07a-66e91e3862bb` | 192.769 | 237.545 | 828.546 | 1,865.097 | **PASS** |

两轮均用 1,000,000 个 F 小时键，数据指纹与前版一致。由独立 SQL 小时格复算 `E_common=720`、`P=710`，观察、有效、配对明细分别为 3,594、3,590、3,550 行。五列统计日桶、1,000 个范围点、600 个精确点和分页明细均通过校验。有效配置 SHA-256 为 `b01f48f912dae269afc3f68a76e9cf5b1b72cd2d9312ba1e820e77c4bbce7a32`。

正序稳定段为 1,201.221 秒，17,038 个请求全部正确。19 次资源抽样的 PG 连接数为 8–11，临时字节增量为 0，三队列峰值均为 0。12 批保卷冷态每批 5 个请求全部正确，操作系统页缓存保留。诊断 `s07a-24f97a51b9b2` 覆盖四个形状，R4 的 25 个诊断请求全部正确，P95 为 1,756.110 ms。三轮清理均归零。

### `5c2073e` 宿主 B 补测

从修订待测 ZIP 提取运行的 `s07-b-fb463d0ad9fe` 为 **PASS**。独立复算 16 份原件，核对提取收据的 ZIP 和清单摘要、代码提交、锁文件及百万 F 小时键指纹。五个已认证 Session 每种形状各有 10 个预热和 10 个计时请求，200 个计时样本及 200 个预热样本均正确。每形状达到 5 个请求重叠，R2 首、次页各 25 次。R1–R4 的 P95 依次为 188.896、227.126、824.181、1,751.872 ms。前后队列快照均读到三条隔离队列深度为 0，V01 清理归零。

原件位于 `tmp/s07-evidence/s07-b-fb463d0ad9fe/`。正式 `S07_T09.json` SHA-256 为 `e0c6cec2c699a3f076505fb0f5e903a8123842858215a16c6780f02387641efc`。A/B 共 98 份原件与投影逐项核对 **PASS**，独立复算见 `/tmp/s07-l3.9PGz3d6V/t09-recheck.json`。

### `5c2073e` 同版 V15 和 V17

独立复算为 **PASS**。`S07_V15.json` 的同版候选为 `5c2073eeefca01fcc004010534b65e8da67bace7`，投影 SHA-256 为 `52a7fca055148e2640b41a913de4f46fc3b069577a4e0757e0b596a07d24fe4d`。12 份回归原件均与索引的 SHA-256 一致。A 全量 `s07a-1772967855ad` 为 562 PASS / 38 NOT RUN，R 通用 `s06-0d8287278ce3` 为 567 PASS / 33 NOT RUN，R 恢复 `s06-a748f47e1f50` 为 33 PASS。按 node ID 去重后，600 个不同节点均至少一次 PASS，无失败。A 的 38 个跳过由 R 通用容量 5 个和 R 恢复 33 个补齐。静态检查 `s07-static-d90616ca2292` 和 A 的 Django 检查、迁移漂移检查均通过。

前向升级 `s06-ddd07cc83576` 保留了全量 60 张业务表，二次迁移无待办，旧 Session HTTP 200。V17 `s07a-455ec621f23d` 的 5 个趋势检查通过，涵盖日桶的实际 HTTP、独立 PG 账本对账和撤权。各运行使用同一源码摘要，隔离清理通过。复算见 `/tmp/s07-l3.9PGz3d6V/v15-recheck.json`。

### `5c2073e` C01 排他和 C02 跨版本票据

复核 `s07-b-7f52a9278ba7/V03/host-compose.json` 和同轮 V01 收据，结果为 **PASS**。宿主入口、直接 CLI、基础 Compose 和正式 Compose 各有独立发布者与消息 ID。收据中的 9 个 Redis 任务 ID 均由 Worker 消费，PG 记录 1 个业务窗、7 个首次认可单元及 1 次 F 请求。验锁前丢失 PG 的发布者未入队，进入传输后才丢失 PG 的在途任务留下独立 ID。资源清理归零。

复核 `s06-7867ae8eb97c/upgrade.json` 和同轮 V01 收据，两者均为 **PASS**，守卫记录的运行源码摘要与待测包一致。升级验证复用 1 个旧 S05 Session，分别续用两组产品数据的原游标和保存意图。精确行的公开业务字段相同，新版标记通过不透明字符串断言。原有效票据下载分别为 1,452 和 1,458 字节，字节和摘要与升级前相同。把新精确标记用于旧研究下钻返回 `409/input_changed`，权内可信修订后的旧票也返回 `409/input_changed`。

### `5c2073e` V16 独立重建

在 `/tmp/s07-l3.9PGz3d6V/app` 从待测 ZIP 提取源码。`uv sync --locked --offline` 与 `uv lock --check --offline` 均退出 0。六轮实测全部退出 0。每轮的 V01、锁文件与运行源码摘要均通过校验，任务容器、网络、卷及宿主进程清理归零。

| 验证 | run ID | 独立核对结果 |
|---|---|---|
| A 启动 | `s07a-f34a691f1ce4` | **PASS**。正式 WSGI 就绪返回 200，Worker 和 Beat 心跳有效。 |
| B 启动 | `s07-b-295cada2892b` | **PASS**。`main.py`、单组件、拒绝场景及退出恢复共 20 项通过。 |
| A 三条业务链 | `s07a-54d2913f2587` | **PASS**。双 Worker 与唯一 Beat 通过 13 项故障场景。独立 PG 对账为 18 窗、27 次运行、2,863 个首次认可单元和零重复。两个已认证 Session 验证了统计的 E/O/V 各为 3、明细和获准 CSV 各有 3 行，以及跨人下载返回 404。 |
| B 契约 | `s07-b-9c2aa1b5883b` | **PASS**。20 条 Session HTTP 结果覆盖角色拒绝、三队列消费与注入断源。断源后 PG 仍可用，最后获准值保持一致。 |
| A 离线停复 | `s07a-7bf1f375959f` | **PASS（F/I）**。核对 19 个停复事件、PG 就绪 503、同卷冷启后新旧 Session 结果相同。 |
| B 离线停复 | `s07-b-8915ab7bd726` | **PASS（F/I）**。核对 19 个停复事件、PG 就绪 503、同卷冷启后新旧 Session 结果相同。 |

16 份脱敏原件按 run ID 逐字节归档到 `tmp/s07-evidence/`。汇总、原件大小与 SHA-256 见 `/tmp/s07-l3.9PGz3d6V/v16-recheck.json`。R 的恢复与 A 的趋势回归采用上述同版 V15/V17 收据。

### C04 历史 CSV 首错

独立核对 `S07_V04.json` 与历史首错及九轮复验原件的 SHA-256、测试节点状态、HTTP 行数档。历史 `s06-a8f813ec1eac` 的准备断言失败，HTTP 状态、行数档和根因均未知。3 轮串行、2 对并行定向和 1 对并行全量的原节点均通过。9,999 和 10,000 行准备均为 201，下载均为 200，10,001 行准备均为 400。保留原失败和未定位风险，L22 待 GPT 裁决。

真实 H 的离线冷启、查询和分段恢复为 **BLOCKED / NOT RUN**，缺少本轮授权材料。T11 城市在线验收保持 **NO-GO**。三项 Finding 的返修复验、正式候选 T09 复算及新版 V15/V17 复算均通过。V16 同版重建通过，最终包签收待核。S07 放行待 GPT 阶段裁决。
