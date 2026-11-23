# S06 独立 L3 技术终审

审查基线 `main@be6681298a28220ac97136087a4bdf316799c93d` 加工作树。收到的后端路径与内容哈希为 `ef31fc551115de3cc676bbb620b2441699d4c99b137f81bf515862160ebe54eb`。随后对照返修工作树和新收据更新结论。

## 发现和处置

### S06-L3-01 · P0 · C01 最终放行与现行权利互斥（已返修并局部复验）

初始基线的 `backend/observations/backup.py:559` 发布事务内把段标为 `AVAILABLE` 并提交 `pending` 审计。实际可用性还要到 `require_published()` 看见 `applied`（`backup.py:536`）。这一步在锁已释放后的 `confirm_release()`（`backup.py:517`），只比较包外文件指纹和旧截止并更新审计。它不锁产品、不对照现行权利序号。`backend/observations/backup_restore.py:133` 有同一分界。恢复读取已经暂存于内存，持锁事务先提交 `pending`，锁外 `confirm_release()` 才准许 HTTP 写正文（`scripts/s06_restore_http.py:95`）。

复核时序应在 `confirm_release` 入口设栅栏。在另一个 PG 会话完成 A 的撤回并重授，保持旧包外证明文件可读且不变。允许放行方继续，此时 `proof.confirm()` 和 PG 截止谓词仍成立。发布会把旧审计改为 `applied` 并返回缓存的 `available`，尽管数据库行已可被置为 `blocked`。在恢复分支中，`read()` 会把先前的结果交给 HTTP 200。发布的段内 advisory lock 只约束 `lock_intent()` 等段入口，撤权 `policy.revoke_grant()` 不取这把锁。计划 §4.4 要求在放行时重取现行权威 PG 序号并与撤回互斥。事务完成与“可以读取”的提交点不能由错位的两个事务分别证明。

现有 `test_v17_restore_commit_failure_releases_no_body`、`test_v17_c01_*` 覆盖主事务失败、跨截止及较早的撤回顺序。建议增加真实双 PG 会话的上述栅栏，加恢复 HTTP 状态和字节数断言。另应在**放行审计 UPDATE 自身**的提交回调注入延迟。`confirm_release()` 只在 UPDATE 语句内取 `clock_timestamp()`，该语句提交完成仍可跨期限。本项需审计 `applied`、API 成功和段可用性共用可信放行顺序。

返修后的 `backup.release_file()` 在审计确认结束后重锁来源和产品，复核当前权利、会话、保管人及包外文件，仍持锁时写文件放行标记。`backup_restore.run()` 同样在耐久审计确认后重取范围锁和完整授权，在锁内交给私有 HTTP 处理器输出一次结果。`s06-4157d30d419c/pytest.json` 的两条「最终审计提交跨过期限」及两条「耐久确认后、实际放行前撤权重授」均 PASS，覆盖本发现。该运行另有无标记清扫 FAIL，由 S06-L3-04 追踪。最新 `release_file()` 在持锁 `fsync` 后再核包外证明和 PG 时钟，关闭标记写入跨期限的返回路径。新增慢盘注入在 `s06-4679897c9afd/pytest.json` PASS。恢复在持锁的最后授权检查后开始一次性回环发送。

### S06-L3-02 · P0 · V11 同产品合法分支核销后回归（已返修并复验）

同一 A/B 产品测试在停止补扫、撤权并重授后，以独立 SQL 看到 `(1, 1, None)`，受影响的当前观测、质量凭证、统计、支撑、复访和领票按契约拒绝，独立 B 的旧 CSV 仍可下载。`s06-69aa909d57a9/pytest.json` 通过这一组公开入口。增强 B 处置后保持原字节的断言后，`s06-cb44e3eab397/pytest.json` FAIL 于 `test_s06_pending_http.py:161`。A 的旧行清除时，B 无关批次的 `fetched_at` 变化使同一张票内容失配。测试分别用了独立 Batch，不能用混批共同投影本应变化解释这一失败。计划 §4.4、V11 要求合法兄弟输入保全已获准的字段和血缘。修复后须同样比对 A/B 的 PG 与 B 的旧下载、贡献抓取时间，保留真正混批的整包摘要去除正例。返修后的 `reconcile_data` 只把实际丢失观测、原字段或 locator 的批次判为涂除，并保留合规独立 Batch 的抓取血缘。`s06-360ca20d8eb3/pytest.json` 21 PASS，其中 V11 独立及混批两种 HTTP 用例均 PASS，并含定向 S05 成本与旧基线核销。该专项足以表明本发现已修复，最终全量见 S06-L3-03。

### S06-L3-03 · P0 验收缺证 · V18 最终基线与两轮复现待对账

首轮全量跑在只读源快照 `1d9c6bd54af9bc9998db9058264f3b9a5bf7f1f47d8928caef3d5f3161881a36`，`541 PASS / 0 FAIL / 26 NOT RUN`，26 项均与 `s06-4f1c6f76b4d0` 专项节点对应。专项 26 PASS、服务分探针、冷读及升级收据指向另一个全源快照 `d92814044b5977ea2f3dab4c1bf4a78cee3317c6ad195f6feb93f9d95715ea93`。`s06-a10e0437f08d` 补跑了该系列通用全量，计数为 542 PASS、0 FAIL、26 NOT RUN。该批之后发布/恢复及处置仍有实质变更。`s06-5269f0e43d65/upgrade.json` 的旧会话 200、管理 403、迁移 14 个且二次 0、质量严重度 unknown、旧配额/意图保存均有实证，不能当作新一轮实现的整项结论。需将最终源码指纹、发布/恢复和处置专项两轮、通用回归与升级/模型漂移检查、每轮清理收据映射到索引。若继续复用旧证据，应按跑时快照列出具体差分，并说明未被变动推翻的断言。已读到下一组同指纹 `68cf0cf54d4998242d20a1c188ed67478096718ef72004588a9fc77d0dbb81ee` 的 `s06-a948ce7c7fa6` 升级、`s06-6559510e9377`/`s06-a15c27d299df` 进程及 `s06-4679897c9afd`/`s06-8a4595e2be10` 定向收据均 PASS。`docs/verification/S06.md` 当前尚以“正在汇总”结束。

### S06-L3-04 · P0 · V13 无放行标记的副本清扫（已返修并复验）

`s06-f8d2d893369e`、`s06-4157d30d419c` 均在 `test_v13_unwitnessed_final_ack_is_unreadable_and_reconciled` 第 232 行 FAIL。隔离恢复入口 409 断言已通过。清扫测试随后直接调用 `reconcile_product()`。该入口只处理有到期待办的产品，判定有无到期待办发生在调用 `reconcile_segments()` 前。制作该夹具只登记 5 分钟准备超时及后续正式过期两笔。需明确预期为到期补扫，并以 PG 时钟确认待办已到，再核文件消失、`removed`、`withheld` 和合法兄弟段。返修后的周期扫描会主动发现未到期、无放行标记的段，跳过仍持发布锁的活跃写方。新用例从 `sweep_orphans()` 触发，`s06-4679897c9afd/pytest.json` 32 PASS，确认常规清扫及独立合法段。回滚重试仍见 S06-L3-05。

### S06-L3-05 · P0 验收缺证 · 无标记副本 unlink 后 PG 回滚的重试

修订中的 `backup.sweep_orphans()` 先用 `os.listdir()` 找到仍在磁盘且无标记的 `available`，然后在产品锁下由 `reconcile_segments()` 更新发布收据、移除文件、改段状态。首次清扫若在 unlink 与 PG 提交之间失败，目录不再有可枚举的文件，而数据库会回到 `available/applied`。下一次目录扫描不能发现这个 UUID。即便准备超时后从待办进 `reconcile_segments()`，`entry is None` 的 `available` 分支会报 `backup_volume_or_segment_unavailable`，直到正式段期限跨过后，`block_segments()` 才能改为 `blocked`。这一间隙可用真实 PG 延迟约束在 unlink 后注入回滚并在同卷重跑复核。目前业务入口仍失败关闭，受影响的是可恢复的核销收据和处置时延。V12/V13 的事务失败与安全重试断言需要覆盖新增的无标记分支，区分真正不可证实的卷丢失和已由同卷受控核销流程移除的文件。

## 既往发现复核

| Finding | 独立对照结果 |
|---|---|
| S06-R01 信任根写边界 | **PASS（已测范围）**。`backup_authority.authority_root()` 要求隔离的只读根，文件须是服务用户拥有的有限普通单链接文件。验签、锚点、公钥标识和包外现行 head 受控。`test_v13_unsigned_or_changed_material_never_opens_segment` 覆盖别目录材料、包外篡改和链不一致，`s06-4f1c6f76b4d0` 通过。宿主信任根布置由操作手册和隔离 runner 约束。 |
| S06-R02 发布终检 | **PASS（最新定向用例）**。主提交及最终审计提交跨截止的各自故障可复核，新增复验的两条截止断言 PASS。后续持锁放行已通过截止和现行权利的最新定向用例。见 S06-L3-01。 |
| S06-R03 登记跨期 | **PASS（已测范围）**。`policy.register_grant` 使用 outer on-commit 后数据库时钟首次见证，`loss.historical_rows()` 只承认截止前已见的独立允许依据，并在重授前冻结。`s06-b6ed5f64c922` 的强制提前执行延迟约束与外层跨期均通过。`backup_authority.GRANT_COLUMNS` 将 `registered_at` 纳入当前摘要，`test_v13_current_proof_requires_committed_registration_witness` 实测失败关闭。 |
| S06-R04 暂存代次 | **PASS（已测范围）**。`history.check_import` 绑定 `StagedInput.created_at`、卷代次、期限及连续性。打开前和解析后再核，旧件状态不复活。`test_s06_history_generation.py` 覆盖已核销后迟发读和已解析后撤权，通用收据 PASS。 |
| S06-R05 文件落盘 | **PASS（登记、卷断点及无标记清扫）**。预先登记 `BackupSegment`、卷代次、catalog 范围和待办，仅下一事务建 `.partial`。`controlled_storage.unlink_from` 持排他文件锁并验证同 inode、链接数、目录同步。`s06-2e9cdb9519c3` 与 `s06-4f1c6f76b4d0` 的卷替换、孤儿锁、待办及 manifest 提交失败 PASS。无标记清扫二次 FAIL 后的专项复验已经 PASS。见 S06-L3-04。新增分支的 unlink 后回滚见 S06-L3-05。 |
| S06-R06 权限快照 | **PASS（主事务）**。发布与恢复在同事务锁操作者/保管人及组，重读 DB Session、用途和权利。`verify_intent()` 前后失败关闭，专项覆盖失联、Session 和主体变动。最终放行持锁重核，双会话撤权竞争已通过。 |
| S06-R07 主体映射 | **PASS（已测范围）**。包中 token 与稳定 UUID 绑定，`checked_subjects()` 对现行账号名和稳定 ID，`ImportedSegment.confirm_subjects()` 在导入后和放行前核验。前次仅 token 的换绑路径已堵，专项 `test_v14_failure_after_preflight_clears_every_target_row` 换主体拒绝且目标回滚。 |
| S06-ACCESS-01 手动采集 Session | **PASS**。`collection.views.runs` 传入真实 request，`services.submit` 在窗口/Job 锁后检查会话。`test_v17_manual_submission_checks_session_after_job_wait` 与权限归属用例覆盖早撤权和旧 Session。 |
| S06-ACCESS-02 派发回调去重 | **PASS**。版本/窗口和 Run 状态围住实际资格，回调与扫描都消费到期派发状态。`s06-b464c5ebbb71/runtime.json` 独立会话 2，实际耗尽 8 次，扫描器先于提交回调，Redis 新增 1 条，重放 0 条。 |
| S06-ACCESS-03 最小运行账及明细 | **PASS（F/I）**。`collection.writer` 分 `LEDGER` 和 `QUALITY_DETAIL`，`read_window/read_run_issues` 各自投影。上述 runtime 的 `V05_quality_detail` 两组均有 7 子项和 15 个回执，仅有账本许可的组无结构/字段事件，撤明细后事件物理清除。 |
| S06-ACCESS-04 Worker 订阅 | **PASS（自有进程）**。`config.pulses` 记录实际消费者队列，`accounts.operations.pulse()` 区分经验证的缺订阅及无证据，`s06-26f77b7146df/runtime.json` 同名 Worker 的错误/正确队列和 TTL 均有证据。 |
| S06-ACCESS-05 同窗诊断 | **PASS（F/I）**。`read_run`、窗口投影和窗口专属 `health` 复用同一授权账。`s06-b464c5ebbb71/runtime.json` 对同一窗的两个实际尝试、SQL 与 HTTP E=7/O=0/V=0、影响位置、重启后的旧有效观测和空窗作了对账。 |

## P/V 与早期阶段兼容核对

| 实施范围 | 证据与判断 |
|---|---|
| P01 / V01–V03 | **PASS（可归属快照）**。原始 V01 都有拒绝数 8、仅回环、无外部路由、无共享运行环境、停止退出 0、残留容器 0。`test_s06_accounts.py` 实际 HTTP/CSRF、Cookie 永久失效、保留本人合法访问、目标/操作 UUID 约束、同一事务审计及并发回滚。 |
| P02 / V04–V05 | **PASS（可归属快照）**。`control.parse/version`、PG 控制版本迁移、`services.record_failure/redeliver` 与 `test_s06_collection.py` 对五个动作闭环；C03 旧仅 `pause` 接受、同 Source/Product 两 Job 一次通过、自动同理由重停后拒绝旧版。Worker 实投证据见 ACCESS-02/03。 |
| P03 / V06–V08 | **PASS（可归属快照）**。`quality_governance.snapshot/correction_evidence` 含跨工单全部活跃候选与受信水位，F 规则 v1/v3/v2 判据，统计内容与精确输入各自判断。`test_s06_quality.py`、`test_s06_catalog.py` 给出候选/裁决两序、过时证据、映射目录交叉锁和站址保留原身份。真实来源排序适配未准入。 |
| P04 / V09–V10 | **PASS（已测契约）**。`research_exports.download_export` 在 `request_transaction` 退出后才构造正文响应。C04 的订正/下载两序、角色交换及编码后提交失败由 `test_s06_quality.py`、`test_s05_races.py` 验证零 CSV 与正例。S05 的独立 CSV/use、临时 deny、部分多产品授权和 owner 路径纳入全量。V11 核销后的独立合法票回归已由 S06-L3-02 复验。 |
| P05 / V11–V12 | **V11 定向复验 PASS，V12 PASS（原快照）**。`test_s06_disposition.py::test_v17_c02_*` 实测先撤回，再由重授／扫描分别取得范围锁，历史见证不能补填。字段和混合批次的订正、文件卷及暂存代次有 SQL 与文件正反对照。V11 的同产品独立 B 及真实混批复验 PASS。见 S06-L3-02。V12 用 `worker_disposition` 观察 Worker 停机、截止重授、占用、unlink 后 DB 回滚及二次补扫。 |
| P06 / V13 与 P07 / V14 | **V13/V14 定向复验 PASS，新增分支回滚未覆盖**。包外证明、隔离库、完整性、两目标、历史主体和一次读的专项 26 条曾 PASS。最新发布/恢复的撤权顺序已通过。无标记清扫已经通过，回滚后重试仍待补证，见 S06-L3-05。 |
| P08 / V15–V16 | **PASS（F/I，自有进程）**。真实 Redis、PG 故障与恢复、停止各组件、单 Beat、同名订错队列、broker ready 与额度等待区分，见 `s06-26f77b7146df/runtime.json`。V16 真实 Worker 和回环 F、同窗与冷读见 `s06-b464c5ebbb71/runtime.json`。H 支线 **NOT RUN**。 |
| C01–C04 / V17 | **C01–C04 相关两序的定向用例均已有 PASS**。C01 的主事务两序有独立 PG 阻塞收据，后加耐久审计后、实际放行前的竞争。C02 按冻结边界解读两序。C03 以实际目标版本与原 Run 投递净增量核对。C04 以候选事件版本与持锁下载事务提交核对。 |
| P09 / V18–V19 | **V18 最终对账 NOT RUN，V19 部署范围的 P1 NOT RUN**。手册具备账号、采集、治理、处置和保管/恢复方案；最终复验与清理证据索引仍有 S06-L3-03。V19 手算单窗为已知子项，完整分桶趋势未交付。 |

S02 历史导入、目录、修订及安全处置相关自动化已在原通用快照通过。S03 的窗口、配额、受控重试、旧调用方的 G2 新旧矩阵，以及 S04 的登录/收藏、同源与私有隔离纳入通用验证。S05 的查询、固定输入、双产品锁序及成本组纳入通用验证，G2 外仍须重跑受当前 P05 改动的独立合法分支。`git diff --check` 在审查期间退出 0。终审只核读源、SQL／运行收据和测试断言。真实 H、城市在线、整机灾难接管、S07 和前端后续阶段均无覆盖。独立技术终审结论为 **暂不通过**。仍需对账 S06-L3-03 的最终基线和 S06-L3-05 的回滚重试。
