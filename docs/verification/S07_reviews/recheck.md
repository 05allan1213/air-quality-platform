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

候选包内的 `S07_T09.json` 能逐字节核对 82 个原始收据和 4,400 个正逆序 HTTP 样本。该文件中的性能收据仍绑定源码 SHA-256 `92f1ed695ac8222a592037ed4683344a8a15407104b6af49f46d8c5a9316b3bf`，对应早期候选 `91f0d19`。因此这些历史热测不能作为 `bec88f4` 的最终 T09 签收依据，最终候选的正式负载仍为 **NOT RUN**。

候选包的 README 仍包含 7 个指向包外历史章节的相对链接，目标为 `docs/operations/S04.md`、`docs/verification/S04.md`、`docs/verification/S01.md`、`docs/plans/S02_IMPLEMENTATION_PLAN.md`、`docs/verification/S02.md`、`docs/verification/S03.md` 和 `docs/operations/CNEMC.md`。S07 交付所需的宿主运维、API、S05 票据和 S06 受控备份指引均已在包内，故该项作为文档遗留记录，不重新打开 S07-L3-02。

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

静态复核为 **PASS**。Django check、Ruff lint、Ruff format、OpenAPI 和 resolver 测试均通过。最终候选的正式 V13 百万小时键、五会话正逆序热测、稳定性和冷态测量为 **NOT RUN**，原因是包内现有 T09 机器收据绑定早期源码摘要，不能跨源码签收。

## V15 同版收据复算

工作区的 `S07_V15.json` 对应 `bec88f4` 的 307 个运行文件。独立重算静态检查、A/R 节点对账、S06 前向升级、V17 趋势及归属清理原件的 SHA-256，均与索引吻合。A 实得 562 PASS、38 NOT RUN，R 通用入口 567 PASS、33 NOT RUN，R 恢复专用入口 33 PASS。按节点 ID 去重后 600 个全部 PASS，A 的 38 个跳过节点均有对应 R 通过记录。S06 旧写者退出后，旧 Session HTTP 200，60 张表对账，第二次迁移无待办。

打包工具 `scripts/s07_handoff.py` 本身计入运行文件摘要。最终包若更改该文件，须以最终源码摘要重新核对 V15 与 V13。仅修改未参与运行摘要的文档时，可逐文件比对被测与交付运行文件，并复用同摘要收据。

真实 H 的离线冷启、查询和分段恢复为 **BLOCKED / NOT RUN**，缺少本轮授权材料。T11 城市在线验收保持 **NO-GO**。三项 Finding 的返修复验均已完成，最终 S07 阶段是否放行仍取决于正式候选 T09、L22 裁决和 GPT 阶段验收。
