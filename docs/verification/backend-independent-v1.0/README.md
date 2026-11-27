# 后端独立交付证据索引

- 适用版本：代码与契约提交 `d3248bcf91e241e4ff23d1bdf591fefda9bc4b05`，文档与证据提交（当前 tag 所指提交），本地 annotated tag `backend-independent-v1.0`。
- 测试日期：2026-11-27，时区以运行器记录为准。
- 数据域：F 夹具、自制双域权限样本、注入故障。未使用正式来源、私有数据或生产数据。
- 原始收据位置：`/tmp/aq-backend-check.nUBLRJTE/`。本目录只保存脱敏摘要、关键响应和哈希，不保存数据库、凭据、完整日志或私有载荷。
- 代码源摘要：`scripts/s01_runtime.py source_hash()` 为 `61fc8ec6c55f1cb82067b205af27660f9a329d20c27080173aedfd4df18aef2b`。该摘要覆盖运行配置、依赖、`backend/`、`tests/`、`scripts/`、Docker 配置和 `docs/api/openapi.v1.json`。

## 可追溯收据

| 文件 | 内容 | 原始入口 |
|---|---|---|
| `receipt-index.json` | 运行编号、代码源摘要、隔离边界、结果与原件 SHA-256 | `now/tmp/*/V01/result.json`、`finalall.log`、`finalall.xml` |
| `performance-summary.json` | 百万逻辑小时 F 性能四个场景的正确性和 P95 | `now/tmp/s07-evidence/s07-b-8a40f18fbf08/performance/` |
| `compatibility-receipt.json` | v1/v2 CSV、旧票和普通游标升级结果 | `compat-research/receipt.json` |
| `csv-boundary-summary.json` | 10,000 行、上限和输入变化字节验证 | `now/tmp/s06-evidence/s06-45cb91c47595/s05-regression/V12/` |

## 执行入口

- OpenAPI 与 resolver：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_s07_contract.py`，本次收尾复核 `4 passed`。
- 专用成本测试：`.venv/bin/python scripts/verify_s06.py pytest tests/test_s05_cost.py`，运行编号 `s06-45cb91c47595`，5/5 PASS。
- 专用恢复测试：`.venv/bin/python scripts/verify_s06.py pytest tests/test_s06_backup.py tests/test_s06_backup_preparation.py`，运行编号 `s06-4d5675ef8df2`，33/33 PASS。
- 百万 F 性能：`.venv/bin/python scripts/verify_s07.py performance`，运行编号 `s07-b-8a40f18fbf08`，四个场景各 50 次，正确率 100%。
- 综合回归的临时收据保留了 `finalall.log` 和 `finalall.xml` 的结果，但没有保留原始 shell 命令行，故该项命令字段标为 `NOT RECORDED`，不补写推测命令。
