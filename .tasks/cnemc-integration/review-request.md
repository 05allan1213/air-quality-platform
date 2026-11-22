# CNEMC 实现独立审查委派

审查对象为 `main@b8c30ae7bac22db03bc2d11ed27e4862b390571a` 之后的 CNEMC 接入工作树，包括新文件。独立审查者先读本任务的 `progress.md`，再完整读 `plan.md`。按 `docs/plans/CNEMC_IMPLEMENTATION_PLAN.md` 第 3–6 章核对产品、许可、累计额度、窗口、调度、有限补采、目录、PG 修订与处置、离线证据、迁移、S04 交接和分层验收。基线范围包括 `backend/collection`、`backend/observations`、设置及 Compose、`scripts/verify_cnemc.py`、已有 S03 验证器和 `tests/collection/test_cnemc*`。收据在任务自有 `tmp/s03-evidence` 的 `cnemc-result.json`。

请只读核查实际工作树和收据，按稳定 Finding ID 给出证据位置、影响和处置建议，列出未覆盖范围。只返回审查结论或写任务内独立审查报告。业务代码、Git、任务状态与共享运行环境由主实施者负责。
