# CNEMC 接入独立终审

**PASS。** 按城模式与全国模式的本地基础采集软件均通过计划第 5 章验收。CNEMC-R01 至 R09 全部关闭，独立审查者未报告新的 Finding。

独立 Codex 会话 `01a201e0-5916-7e62-8ec1-5ca5ff99a54b` 于 turn `01a201f8-db21-78a3-9082-5cb7e6fe757a` 复核完整矩阵，于 turn `01a2020e-7e5f-7180-8fe8-c74ff0cd5b72` 给出终审结论。审查者重算基线 `main@b8c30ae` 后工作树源码 SHA-256，得到 `15d62d2248e125fd22ac045ac1438e7aa2b72b6a2592cb13e822c93bbeec5ef4`。补充窗口探针 SHA-256 为 `4717ac1ebdeda0db67b015ee7fb368addbffda39908b95f07823abd705974ded`。审查者核实原始收据、Run、Window、拒绝结果和汇总一致。[验收收据](../evidence/acceptance.json)

| Finding | 处置 | 复核依据 |
|---|---|---|
| CNEMC-R01 | fixed / PASS | 同一验证轮跨清单累计分配，含已撤回份额。 |
| CNEMC-R02 | fixed / PASS | 同一长期站码覆盖迁址前后两个站址期，合法历史可读，留存撤回后两期均处置。 |
| CNEMC-R03 | fixed / PASS | 验证账本失权时封存份额、清除关联和预留。普通预留按独立依据处置，清除已用预留触发同源冷却。 |
| CNEMC-R04 | fixed / PASS | 单页上限一窗时，PG 游标越过首轮无权小时，次轮取得获准窗，随后三轮零重复调用。 |
| CNEMC-R05 | fixed / PASS | 收据核实 Git 基线、实际 Worker 镜像以及调用、构建前和收尾的源码指纹。 |
| CNEMC-R06 | fixed / PASS | 来源地方标签按上海时区解释。名册外获准格可精确读取，名册内 E/O/V 保持 `1/0/0`。 |
| CNEMC-R07 | fixed / PASS | 两小时回看配置拒绝锚前三小时的目标，返回 `backfill_unavailable`。仅保留 A07 获取权时，锚前一小时的 A10 请求返回 HTTP 403 `use_denied`。两种拒绝的 HTTP 和预留增量均为 0。 |
| CNEMC-R08 | fixed / PASS | 北京时间 2026-11-22 的 14 时与 15 时两个 F 包使 12 时的精确读从空变为 AQI 51。两批对该修订的回执数为 0、1，共 25 个唯一键，窗口覆盖分别为 `24/23/23`、`24/24/24`。 |
| CNEMC-R09 | fixed / PASS | 选择等价的两个手动 Job（2、3）产生两个 Run，共用 Window 2。一次 A07 HTTP、一份预留、一个认可格，E/O/V 为 `null/1/1`。 |

五份 CNEMC 进程收据、质量与隐私探针、窗口边界探针均通过。全国 1,601 站数据由一次受控 HTTP 请求取得，88 块写完 11,207 格，耗时 213.62 秒。全套 pytest 311 项、隔离 PG 定向 50 项、S03 V01–V19 均为 PASS。[窗口边界探针](../evidence/window_boundaries.py)

限定实源采集和三地区跨期验证均为 **NOT RUN**，实源请求数为 0。端点、地域、用途、留存处置、实际出口、频率及累计额度尚待落实，A07/A10 的实源同口径认定仍待核验。获批试运行后的自然 168 小时对账为 **NOT RUN**，T11 为 **NO-GO**。本次范围以外还有 V20 真实 H 档案、可选第 7 步及后续导出、备份和地图验收。
