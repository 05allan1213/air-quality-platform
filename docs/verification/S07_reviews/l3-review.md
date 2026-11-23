# S07 独立 L3 工程审查与 V16 交付校验

Coverage: full

- 审查候选提交 `91f0d19b4cd6ce6be21b92d3b0980b67618ef833`。运行源码 SHA-256 `92f1ed695ac8222a592037ed4683344a8a15407104b6af49f46d8c5a9316b3bf`。
- 本地候选包 `tmp/s07-handoff/91f0d19b4cd6-6agneld1/S07.zip`。ZIP SHA-256 `0eeee211d90a74ab4edce82469ae9517cecce4334ca9b9fd6f9a623bfcb54beb`。清单 SHA-256 `30a28107108fb757c1d7de5559c869207446052361d73768a9dc3e9caabcbd79`。
- 审查标准为 S07 方案 §1–6、V3.0 §6–10、§12–13 和总体实施方案 S07、§7–10。输入为本仓 F/I 证据。
- 第一阶段工程结论为 **需返修**。V16 交付包完整性通过，独立实链处于 `NOT RUN`。

## 审查发现

### S07-L3-01 · P1 · 开放 · README 的开发启动指令遗漏 Beat 的实例登记

从干净候选包按 README 生成 `.env`，命令仅补三项秘密值，仍未设置 `AQ_INSTANCE_ID` 和 `AQ_BEAT_LOCK_SOURCE`。基础 Compose 的 Beat 会要求 bind mount 缺失的 `.unregistered-beat.lock`，且禁止自动创建路径。在干净目录运行 `docker compose config --format json` 实得 `AQ_INSTANCE_ID=unregistered`、绑定目标源缺失、`create_host_path=false`。登记要求还包括 PostgreSQL 库标记、模式、锁身份及 Redis 标记。因此 README 中整组 `docker compose up -d --build` 和「其余五个服务保持运行」无法同时成立，V02 的基础开发入口需要可复跑的运维指引。[README](../../../README.md#原-s01-开发-compose-与-f-演示) · [基础 Compose](../../../compose.yaml) · [Beat 校验](../../../backend/config/beat_guard.py)

对原 F 演示，显式启动所需组件并标清不包含 Beat。所支持的完整基础及正式 Compose 入口依手册补齐专用实例和双锁登记，再从空目录按手册实测两种配置。保留无登记拒启的安全行为。

### S07-L3-02 · P2 · 开放 · ZIP 缺少两份被交付手册直接引用的运维文件

在独立提取目录核到 `docs/api/v1.md` 的 S05 票据和 S06 备份指引引用到 `docs/operations/S05.md` 与 `docs/operations/S06.md`。清单无这两项。S07 宿主运维对受控备份也要求遵循 S06 手册。独立交付应补进可分发的说明，或在包内提供等价的必要操作指引及材料取得途径。真实数据和受控备份仍按使用许可单独保管。[v1 调用手册](../../api/v1.md) · [宿主运维](../../operations/S07.md) · [交包白名单](../../../scripts/s07_handoff.py)

### S07-L3-03 · P1 · 开放 · 来源状态可能归到同一时刻的另一观测时窗和标准

`collection_status()` 传入了观测键 ID，但 SQL 只把它作为输出索引。`RunItem` 和冻结名册按产品、对象、指标和观测时刻匹配。`changed` 和 `observed_receipt` 只核 `collection_item_id` 与结果，未核回执关联的实际观测键。S04 已有同产品、同一站址同一小时同时保存 `hourly_right_label` 和 `rolling_8h_right_label` 的 O₃ 用例。若一次任务只响应其中一个时窗，两行仍会共享同一来源状态。没有回执时，当前匹配也会把小时目标的故障误附到滚动窗；同一机制影响多标准系列。现有 S07 来源状态测试仅用一条 CO 序列。这是 V06 的静态反例，实际 Session 复验待执行。[状态投影](../../../backend/observations/source_status.py#L25) · [观测键用例](../../../tests/test_s04_selection.py#L300) · [状态用例](../../../tests/test_s07_catalog.py#L81)

由实际回执将响应匹配到对应观测键。缺少回执的故障只在任务契约能证明对应报告窗和标准时投影，其余返回 `unknown`。补验同刻双窗下的成功状态、故障和撤权。

## 已复核证据

- ZIP 成员、Git 绑定、CRC 和提取 `PASS`。用独立 Python 校验器对照指定提交的 `git ls-tree`、Git blob SHA-1 及 `git cat-file` 原始内容，322 个成员文件全部逐字节一致。计入内置清单共 323 个成员，CRC 全过。文件集与构建器白名单一致，322 个成员均为普通的 `100644` 文件。显式检查根路径、重复成员、文件模式和私钥标记。源码指纹在新的提取目录重算一致。
- 提取后依赖与本地静态检查 `PASS`。Python 3.13.18 下在独立目录运行 `uv sync --locked --offline`，使用本机缓存安装 65 个锁定包。`uv lock --check --offline`、Django check、Ruff lint 和格式校验通过。独立运行 OpenAPI、resolver 和性能样本计算单元测试，6 PASS。交包负例共 12 PASS。
- 公开机器索引 `PASS`。在验收工作区运行 `scripts/s07_evidence_index.py --check docs/verification/S07_evidence.json`，33 个索引条目通过原件及清单再生成校验。`scripts/s07_v12_index.py` 输出与 `S07_V12.json` 逐字节一致，66 个显式操作均有真实 HTTP 操作的成功收据和适用拒绝。
- 已对照 C01 代码和历史运行原件。四入口取实际 `flock`、独立 PG 会话的排他 advisory lock，并在真实发布入口复核。`s07-b-52e64218e66d/V03/host-compose.json` 记录基础和正式 Compose、宿主和直接 CLI 的竞争及 PG 一个业务窗、7 个首次认可、1 次 F 请求。`s07-b-801f08e44c89/V03/host.json` 分别核到停顿后在途 Redis 0→1 和已写入后 1→1。V03 对已到达的 one-off 槽在接管前执行了人工复核和关闭。
- 已重算第一轮 A 与 B 补测的单调时间样本，按第 `ceil(0.95×N)` 个值计算 P95，五个 Session 的峰值重叠均为 5。A `s07a-c4b5e8b14aba` 每组 500 条均答对，四个 P95 分别为 194.088、232.049、1223.301、10819.883 ms，R4 `FAIL`。B `s07-b-aac9193c92c4` 每组 50 条均答对，P95 为 197.340、248.717、798.014、1708.952 ms，补测 `PASS`。正式候选两轮热测、冷态及稳定性、V15 同版全量和 V16 两入口仍待复核。
- L22 九轮复验包括三轮串行、两对并行定向及一对并行全量。两套全量各 545 PASS、33 专用节点 NOT RUN。原 HTTP 状态和根因未知，首错继续开放，提交 GPT 裁决。T10 真实 H 为 `BLOCKED / NOT RUN`，T11 为 NO-GO。

## 验收边界

| 范围 | 工程审查状态 | 核对依据 |
|---|---|---|
| P01 / V01 与 P09 / V14 隔离 | 现有 F/I 收据 `PASS`。候选独立运行 `NOT RUN` | 核对 A 的任务标签、两网络、仅有回环发布、Web 启动后去默认路由并降权、先核归属再迁移。B 的 Unix socket PG/Redis、独立网络命名空间及错标记拒绝。`s07a-97a3cb462ece`、`s07-b-93e54b2a8ad6` 分别为冷起、分组件故障和同卷数据复读收据，资源清理归零。 |
| P02 / V02–V03 单 Beat | 核准组合有现有 `PASS` 收据。基础 Compose 手册见 `S07-L3-01` | 实际调度端还拒绝备用 scheduler 和 Worker 内嵌 Beat。丢 PG 后旧进程只能完成进入传输层的在途操作。已发布或状态不明的 ID 须完成手册规定的人工对账。 |
| P03 / V04 CSV | L22 `OPEN` | 已核原 FAIL 与后续九轮 F 复验。等 GPT 裁决。 |
| P04、P06 / V05–V07、V10、V12 | 状态归因见 `S07-L3-03`。版本与候选契约有 `PASS` 收据 | 核 `read_current()` 重新计算该行当前权限依据，以不透明 HMAC 标记用户、用途、观测当前修订及获权字段。调用文档保留原 `revision_id` 和独立的 S05 研究协议。`S07_V12.json` 实测矩阵与 resolver、OpenAPI 一致。`s06-4021ca35f5d9/upgrade.json` 对照升级前 Session、游标及有效、失效票据的行为。 |
| P05、P07 / V08–V11、V17 | 已核实现方运行证据，候选独立三链 `NOT RUN` | `s07a-71a482b8bf9f` 以正式 WSGI 的 Session 核到详情和 CSV 各三条、E/O/V 均为 3、均值 11。故障链留 18 窗、27 Run、2863 首次认可和零重复。治理按源/产品、账号、Session 和现行权利的锁序复核。恢复专用 F/I 的 33 节点原件 `s06-b88eed98e5dc`，数据处置和包外证明只在 R 空目标进行。 |
| P08 / V13 规模、预算与 R3 全量对账 | 首次 A R4 `FAIL`。B 补测 `PASS`。本 ZIP 的正式 T09 收据未闭合 | 已核实际计时脚本维持五个 Session、完整响应和每形状独立排序，分钟资源采样及冷起独立。`S07_T09.md` 随包版本只到首轮与诊断。已有调优测量的源码摘要与本 ZIP 摘要不同。需以最终配置指纹绑定热测、冷态、20 分钟稳定性、诊断及同源明细的复验。 |
| P10 / V15–V16 同版交接 | ZIP、依赖、静态和公开索引 `PASS`。同版完整回归、两入口及三链独立实链 `NOT RUN` | R 通用和专用集合的现存绑定为 `90274df332c97ad520eb73b41856d3cae2dea1f763eaa77652bbdf7d2b7cc81f`。候选同指纹的 `s07-static-96a133835323` 为静态收据。`s07a-4c71e1568918/regression.json` 记录 `a_regression_source_drift`，原失败须保留。独立干净副本已准备，实链等性能独占时段结束后开放。交付资料见 `S07-L3-02`。 |

真实 H 来源授权由来源保管人负责。T10 的 H 冷启、查询和分段恢复维持 `BLOCKED / NOT RUN`。T11 保持 NO-GO。地图与前端验收分别依 S08、S09 的计划。接口冻结与阶段结论由 GPT 最终裁决。
