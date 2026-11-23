# S06 独立 L3 定向复核

复核覆盖 [首次终审](l3-review.md) 的 `S06-L3-01`～`S06-L3-05`，以及受返修影响的发布、恢复、处置、运行诊断和兼容性。基线为 `main@be6681298a28220ac97136087a4bdf316799c93d` 加工作树。按 `scripts/s01_runtime.py:source_hash` 的文件范围和算法独立读出 `598e00c2db1d39918b2e5eecb64f35956de26c38c4ebed944401c34caa66c632`。收据位置均相对 `tmp/s06-evidence/`。本轮只读检查源码、测试断言和已有收据。

## Finding 复核

| Finding | 判定 | 代码、实际运行及边界 |
|---|---|---|
| S06-L3-01 · 最终放行的授权顺序和期限 | **PASS** | `backup.release_file()` 在审计耐久确认后重新取来源/产品及操作者、保管人锁，重验当期权利、原 Session、包外证据和卷。写标记及 `fsync` 后再查证明和 PG 时钟，晚完成时撤去标记。`backup_restore.run()` 也在耐久确认后重新取同一范围锁并核权，在锁内交给私有 HTTP 单次发送。`s06-5153d698b193` 和 `s06-26f3fb56609e` 均通过 `test_v17_withdraw_regrant_after_durable_ack_closes_physical_gate` 的发布、恢复两支，另通过主事务与最终审计的跨期反例、慢标记跨期、两种撤回提交次序及合法 B 对照。 |
| S06-L3-02 · 核销独立 A 时保全合法 B | **PASS** | `disposition.reconcile_data()` 按实际丢失的观测、原值、原字段 locator 及可分离字段挑出需要涂除的批次。`s06-70f59adff0f5` 的 `test_v11_pending_regrant_cannot_revive_old_inputs_through_http` 两个原字段授权变体均通过。独立 PG 查询在扫前观察旧 A 仍存在，HTTP 仍禁止获取。扫后独立 B 的获准批次、回执、抓取时刻和原下载 CSV 字节与扫前一致。同一套包含混批真失权需去除原标识的对照。 |
| S06-L3-03 · 最终基线与两轮复现 | **PASS** | 同一源码指纹的替代全量 `s06-8bb305073ef4` 为 544 PASS、0 FAIL、33 NOT RUN。两个新建隔离恢复环境的 33 个 PASS 节点分别与通用全量的 33 个 NOT RUN 完全相同，合计 577 个不同节点通过。处置、服务、采集、升级和清理收据均对应同一指纹。证据索引可逐字节复现，同时保留首轮失败及单节点复跑。 |
| S06-L3-04 · 无放行标记副本的清扫 | **PASS** | `backup.sweep_orphans()` 先在清册锁下识别候选，再释放清册锁，按产品顺序取正常范围锁和段 advisory lock，核对原卷及标记。持发布锁或重新发现标记时跳过。两轮 `test_v13_unwitnessed_final_ack_is_unreadable_and_reconciled` 均确认即使早期审计为 `applied`，恢复意图仍 409，清扫后报告 `removed`/`withheld`，B 可继续发恢复意图。 |
| S06-L3-05 · 无标记副本删除后 PG 回滚的重试 | **PASS** | `backup.sweep_orphans()` 先用独立耐久事务提交 `blocked`、审计 `withheld` 和到期 `backup_publication_withheld` 待办，退出后才通过 `disposition.reconcile_product()` 删除。普通清理对仍 `available` 且没有见证的文件不会先 unlink。两轮 `test_v13_retry_after_unwitnessed_copy_unlinked_and_cleanup_commit_rejected` 均通过。真实 PG 约束触发器在删除事务结束时注入 SQLSTATE `23514`，断言文件确已去除且清单仍 `blocked`，原待办未销。换卷时仍未完成。换回原卷后 `dispose_due()` 清除状态并销账，第二次调用不重复登记，合法 B 可用。 |

## 同基线收据核对

- `s06-5153d698b193` 和 `s06-26f3fb56609e` 各 **33 PASS / 0 FAIL / 0 NOT RUN**。两轮有不同随机运行号、不同实际 PG 容器、不同私有网络命名空间。各运行声明独立恢复账号、两个隔离目标、只读包外材料，保留相同的全源指纹，清理成功且残留容器为 0。
- `s06-70f59adff0f5` **32 PASS / 0 FAIL / 0 NOT RUN**。定向覆盖 C02 的两种提交顺序、重新授予不能复活、旧暂存卷、损失当前原字段后的合法 B，以及已有阶段的处置锁序。
- `s06-b71acbf993f4/runtime.json` **PASS**。真实 Worker 开着但错误订阅时队列状态 `unavailable`。Worker 占有文件及 unlink 后 PG 回滚时，待办均未完成，恢复后同卷清扫幂等。自有 PG 暂停时 readiness 和管理均 503。进程残留为 0。
- `s06-66d11c74532c/runtime.json` **PASS**。原 Run 真实耗尽 8 次，两会话重投加扫描先于回调，Redis 增 1 条、重放增 0。真实回环 F 抓取后两组各 7 子项及 15 回执，只有获准组有质量事件。冷 Web 仍返回 F 原观测时刻和贡献 Batch 的抓取时刻，明确失败窗 `no_data`，无权 403，查询 Run 增量为 0。进程残留为 0。
- `s06-8081fa74d61b/upgrade.json` **PASS**。从冻结 S05 迁移 14 项，二次待迁移 0。原 Session 访问本人接口 200，管理 403。原授权、Job、私有意图和旧有限配额保持。运行器执行 Django `check` 与 `makemigrations --check --dry-run` 后才生成上面的成功收据。

- `s06-a8f813ec1eac/pytest.json` 保留首次最终指纹全量的 **543 PASS / 1 FAIL / 33 NOT RUN**。唯一失败是 `tests/test_s05_cost.py::test_v12_csv_rows_hidden_records_and_new_input` 第 359 行未获得 HTTP 201。脱敏收据未记录实际响应码，首轮失败原因未确定。相同指纹的 `s06-d023852110d6` 单节点 **1 PASS**，其 `s05-regression/V12/export-row-and-cell-bounds.json` 记录 9999 和 10000 行准备 201、下载 200，10001 行准备 400，新增数据后旧票 409。两轮运行的隔离守卫和清理均通过。
- `s06-8bb305073ef4/pytest.json` 替代全量 **544 PASS / 0 FAIL / 33 NOT RUN**。原有 543 个通过项和首次唯一失败项均通过。原始 `V01/result.json` 的 `result=PASS`、`guard=PASS`、`cleanup=true`，残留容器为 0。替代全量、两轮恢复及单节点复跑的测试收据、守卫起止指纹与本次独立计算一致。
- 使用 `env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/s06_evidence_index.py @docs/verification/S06_evidence.args | cmp - docs/verification/S06_evidence.json` 从 15 次运行重建索引，字节比对 **PASS**。交叉检查首轮及单节点复跑、替代全量与两轮恢复的节点集合和去重结果均 **PASS**。首轮失败及通用运行的原始 `NOT RUN` 保留。静态检查采用实施方同基线收据。

## 结论与范围

`S06-L3-01`～`S06-L3-05` 定向复核均为 **PASS**。H 支线、V19 完整趋势、真实城市在线和整机灾难接管为 **NOT RUN**。S07 及后续阶段由各自验收覆盖。
