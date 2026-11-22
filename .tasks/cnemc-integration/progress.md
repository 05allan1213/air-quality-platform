# Progress

Plan: ./plan.md
Status: complete
Last checkpoint: 2026-11-22

## Current

- Current step: 步骤 1–6 的基础软件与独立验收完成。五个产品、六个端点在隔离 F/I 软件链上通过，全国容量、有限补采、并发窗口和精确回读通过。限定实源和 T11 结果已分别记录。
- Next action: 无
- Blockers: 无

## Verification

- Result: PASS
- Evidence: ./evidence/acceptance.json
- 任务始于 `main@b8c30ae7bac22db03bc2d11ed27e4862b390571a`，index 为空。原计划、接口矩阵、工程核查和计划审查为已有未跟踪文件。已核计划审查所列三份 SHA-256，研究目录的接口说明、15 份业务样本及样本索引可读。
- 最终运行快照 `15d62d2248e125fd22ac045ac1438e7aa2b72b6a2592cb13e822c93bbeec5ef4`。`chain`、`pg`、`quota`、`partial`、`capacity` 和补充探针全部 PASS。每个主验证用例带 Git 基线、真实 Worker 镜像以及首尾源码指纹。全项目 pytest 311 PASS，定向 PG 环境 50 PASS。Ruff、项目配置的 3 个 mypy 文件、Django system check、迁移漂移检查和工作树空白检查通过。
- 全国 A07 用一次 F 请求读写 11,207 格，88 块，213.62 秒。全量查询、撤权后冷启处置通过。所有实源请求数为 0。限定实源、三地区跨期、自然 168 小时均 NOT RUN，T11 NO-GO。可选日产品等第 7 步留待其各自授权和需求落实。
- S03 主运行包含 197 个检查点，产品子项目包含 173 个。两项目首尾源码指纹一致，自有资源回收。独立 H 档案 V20 缺本轮获准输入，NOT RUN。
- 九个最终验收 Compose 项目的容器、卷和网络余量均为 0。用于 pytest 的专用 PG 容器已按确切 ID 回收。
- 历史诊断：开发期导入时序错误已由冻结 pytest 排除。S03 比对改为逐项核原授权及新增的安全默认值。A01 未知标准的官方描述在授权 Receipt 读取。发生源码漂移或签名断言失败的过渡收据不计入以上 PASS。补做登记命令演练时，一个开发期临时库已记录尚未定稿的同名迁移，缺新额度列。以最终迁移从空 PG 库演练，登记两次复用一个禁用的 Job，重复键和 off 模式均按契约拒绝，演练库已回收。

## Reviews

### 独立实现初审

- Level: L3
- Independence: separate-session; independent Codex task `01a201e0-5916-7e62-8ec1-5ca5ff99a54b`，只读独立上下文
- Scope / baseline: 整个接入计划第 3–6 章，相对 `b8c30ae` 的初次集成工作树
- Coverage: full
- Report: ./reviews/initial.md
- Conclusion: FAIL
- Findings: F-CNEMC-R01 open，F-CNEMC-R02 open，F-CNEMC-R03 open，F-CNEMC-R04 open，F-CNEMC-R05 open，F-CNEMC-R06 open。审查会话原编号为 CNEMC-R01 至 CNEMC-R06。
- Follow-up verification: 主任务按同 ID 修复，后续定向复核记录如下。
- Uncovered: 初审时最终容量、S03 回归仍在进行中，实源受条款条件约束。

### 实现问题复核

- Level: L3
- Independence: separate-session; Codex task `01a201e0-5916-7e62-8ec1-5ca5ff99a54b`，复核变更后的代码与 PG 证据
- Scope / baseline: R01 至 R06，源码 `15d62d2248e125fd22ac045ac1438e7aa2b72b6a2592cb13e822c93bbeec5ef4`
- Coverage: targeted: F-CNEMC-R01, F-CNEMC-R02, F-CNEMC-R03, F-CNEMC-R04, F-CNEMC-R05, F-CNEMC-R06
- Report: ./reviews/fixes.md
- Conclusion: PASS
- Findings: F-CNEMC-R01 fixed，F-CNEMC-R02 fixed，F-CNEMC-R03 fixed，F-CNEMC-R04 fixed，F-CNEMC-R05 fixed，F-CNEMC-R06 fixed。
- Follow-up verification: 双清单分配、迁址精确读、独立额度事实处置、PG 单页跨轮游标、首尾指纹、名册外授权读。冻结全套 311 PASS。
- Uncovered: 本次定向核代码处置。最终完整进程收据另作验收复核。

### 完整矩阵首轮复核

- Level: L3
- Independence: separate-session; Codex task `01a201e0-5916-7e62-8ec1-5ca5ff99a54b`，矩阵首轮复核
- Scope / baseline: full task and acceptance，源码 `15d62d22`，含当前实现及收据
- Coverage: full
- Report: ./reviews/matrix.md
- Conclusion: FAIL
- Findings: F-CNEMC-R01 fixed，F-CNEMC-R02 fixed，F-CNEMC-R03 fixed，F-CNEMC-R04 fixed，F-CNEMC-R05 fixed，F-CNEMC-R06 fixed。F-CNEMC-R07 open，F-CNEMC-R08 open，F-CNEMC-R09 open。
- Follow-up verification: 补三项受控 HTTP 与 PG 断言。
- Uncovered: 实源、168 小时、真实 H 和可选第 7 步。

### 独立验收终审

- Level: L3
- Independence: separate-session; 独立 Codex task `01a201e0-5916-7e62-8ec1-5ca5ff99a54b`，终审 turn `01a2020e-7e5f-7180-8fe8-c74ff0cd5b72`
- Scope / baseline: full task and acceptance，结合整套工作树初审、冻结 PG 进程链和最终窗口证据，`b8c30ae` / `15d62d22`
- Coverage: full
- Report: ./reviews/final.md
- Conclusion: PASS
- Findings: F-CNEMC-R01 fixed，F-CNEMC-R02 fixed，F-CNEMC-R03 fixed，F-CNEMC-R04 fixed，F-CNEMC-R05 fixed，F-CNEMC-R06 fixed，F-CNEMC-R07 fixed，F-CNEMC-R08 fixed，F-CNEMC-R09 fixed。
- Follow-up verification: 前六项的冻结测试、代码和原始收据已复核。最终补证项目 `s03ve0c6422e2755` 核实 A10 零预留拒绝、T 相邻发布窗精确回读、两个等价 Job 一次实际获取。
- Uncovered: 真实 CNEMC 单次、三地区跨期、168 小时、独立 H 档案 V20 和可选第 7 步。
