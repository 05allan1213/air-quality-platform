# Phase 02 产品决策

**Phase 02 v1.0 · 2026-11-27 · 项目负责人已确认 PD-01 至 PD-11。** 后端维护正本 `air-quality-platform:docs/frontend/PRODUCT_DECISIONS.md`，前端只读副本为 `air-quality-frontend:docs/PRODUCT_DECISIONS.md`。变更须先核对两端页面、权限、数据来源和接口影响，修改正本后按字节同步，核对 SHA-256。本表冻结需求和验收口径。实现状态、错误分支及测试证据分别见 `CONTRACT_BASELINE.md` 和各仓台账。

## 决策与验收

| 决策 / 对应差异 | 采用的范围 | 可核验的结果及延期项 |
|---|---|---|
| PD-01 · A · BG-023 | 活跃账号默认拥有 `user`。服务端按具名动作追加 `manage`。安全的 `next` 仍须满足目标页所需动作。 | 普通账号可进 M01，有质量或运行权限的账号可回对应页面，撤权后清理私有状态。仅管理/空工作区保留客户端防御样例，正式角色交付沿现行账号模型。 |
| PD-02 · A · BG-024、040 | 保留原生 Django Admin 和现有 REST 的独立授权边界。原生入口要求 active staff 及相应权限，账号 REST 重核具名资格和可管辖目标。 | 分别核 staff 无动作、非 staff 有权限、目标越权和独立的 `diagnose_operations`。SPA 沿现有职责开放入口。 |
| PD-03 · A · BG-004 | 公众可以阅读产品、来源与许可说明。正式观测按当期受众、产品、用途、字段与对象授权披露。 | 匿名可读说明，不能由公开说明取得受限数据。L 的等级与 C 的浓度分开标示。 |
| PD-04 · A · BG-002、003、004、027 | 正式全国趋势按源地方时逐时取同刻 L。分母为该时刻获准参与汇总、结果有效、等级可判且统计标准可比的城市数；分子为该集合中优或良的城市数。 | 标明时刻、可比范围与实际分母，分母为零留缺，不以当前页或全国应报数代替。正式趋势按获权逐时样本复算，正式地图按合法素材、CRS、点位和同版获权联动单验。全量到报、缺报名单、首污构成、正式浓度/IAQI 各依目录/字段与权利分项验收。 |
| PD-05 · A · BG-008、014、042 | 保留获权目录、站址有效期、单窗排名和现有研究。增强站型检索、完整覆盖日历、城市站点空间差异、两期同城与省际比较保留原 P1 分项。 | 现行目录沿服务端链接续读，分段及排名交代对象和时间窗。增强项分别取得合法名录、应到日历/分母、空间依据或可比窗口后，验授权、覆盖和可复算结果。 |
| PD-06 · A+A · BG-015、016、017、043 | `detail/aggregate`，受控默认必备列。附加列选择及正式 D 日报附件延期。新版附件须含 `data_scope=formal/synthetic`。 | 从申请到下载复核父意图、字段用途、版本及真实 CSV 字节。缺域标识的 F 文件不能通过新版验收。附加列须先有授权发现及两次字段核验；正式 D 需合法样本、报告类型、源地方日/时区及新旧票策略后独验。获权 D 精确读保留。 |
| PD-07 · A+A · BG-018、021 | 票源不明回通用申请页。可见票据与可下载附件分别判定。有限页内无法判断收藏显示“待核对”。 | 两会话核对到期、删除、撤权及第 301 项，恢复时避免泄露旧对象。精准收藏反查及长期票源投影为可选扩展，若启用须定义授权与保留期限。 |
| PD-08 · A · BG-028、036 | 基本治理从已有获权 Job 或登记模板草案开始。关联优先使用获权链接。独立建 Job、给无计划 Job 首挂分别交付。 | 基本链验配置、预览、激活、Run/Window 与原命令回执。两项扩展各需写授权、幂等/版本、审计、真实回读。业务窗可关联多个 Job，无链接时不猜唯一归属或人员。F 全流程保留。 |
| PD-09 · 有条件 A，否则 B · BG-029、030、031、034 | 来源有合法用途、可追溯版本/修订声明或登记映射证据，并有明确签发责任时，才开放对应正式更正。否则保留获权审阅、隔离和 F 演练。 | 逐来源验签发人职责、候选、对象、动作、版本、过期和撤权。核回执、影响范围与无关组，前端说明和操作者身份不能替代签证。技术契约可先行，正式数据效果逐源取证。 |
| PD-10 · A · BG-038、039 | M10 展示获权窗口及源地方日日桶。抓取、等待、队列就绪、源发布时间、业务写入按各自证据解释。 | 验源时区、窗口、覆盖与零分母，缺证保留未知。全局健康、深度/速率及小时趋势另需指标定义、受众、采样责任、时间窗和缺测规则再单验。 |
| PD-11 · A · BG-024、040、042 | 保留 SPA 业务、原生 Admin 低风险治理、许可处置、备份恢复和主机专责边界，质量按单事件办理。 | 按入口、操作者及授权核对受理、签认和实际执行，恢复用隔离可丢弃样本。额外 SPA 交接与批量质量裁决保留分项，分别核对象授权、部分失败、审计及实际结果。 |

## 继承范围与外部证据

`air-quality-frontend:DESIGN.md` 的 S08-01～05 和 S09-01～02 继续生效。正式首页以同刻列表检索及获权下钻为基本路径，正式地图和全国趋势分别验收。已有获权 Job 或登记模板可启动基本治理。F 的页面、地图、图表、玻璃层次、动效和交互仍完整交付。历史 G、B、BG 和 P1/P2 原始条目可追溯至页面与差异台账，延期保留原目标与独立验收条件。

正式地图素材与点位、当期 D/H（含 H 同集同城双站）、在线采集权和逐源更正签发依据分别约束对应专项。真实会话、宿主路由、PostgreSQL/Redis/Worker/Beat 和文件字节在 Phase 09 用同版证据验收。上述条件允许公共层和使用现行接口的页面先行。

## 核对入口

- 页面与阶段依据在 `air-quality-frontend:docs/PAGE_REQUIREMENTS.md`、`air-quality-frontend:docs/ROUTES.md`、`air-quality-frontend:docs/BASELINE_REVIEW.md`。
- 授权与观测依据在 `air-quality-platform:backend/accounts/profile.py`、`air-quality-platform:backend/accounts/governance.py`、`air-quality-platform:backend/accounts/restricted_admin.py`、`air-quality-platform:backend/observations/s04_national.py`。
- 导出、收藏、治理和诊断依据在 `air-quality-platform:backend/accounts/research_exports.py`、`air-quality-platform:backend/accounts/favorites.py`、`air-quality-platform:backend/collection/s08_schedule.py`、`air-quality-platform:backend/collection/models.py`、`air-quality-platform:backend/observations/quality_governance.py`、`air-quality-platform:backend/collection/s07_admin.py`。
- 双端遵循 `air-quality-platform:docs/frontend/CONTRACT_BASELINE.md`。BG 处置与阶段任务由 `air-quality-platform:docs/frontend/BACKEND_GAPS.md` 和 `air-quality-platform:docs/frontend/BACKEND_REMEDIATION_PLAN.md` 维护。
