# 城市空气质量监测数据分析平台

这是城市和站点空气质量监测分析平台。PostgreSQL 保存权利、观测和任务事实，Web、Celery Worker 和唯一 Beat 分进程运行。当前交付进入 S07 后端独立验收准备阶段。

## S07 后端入口

Linux / WSL2 上，按运维手册准备独立 PostgreSQL、Redis、锁文件及实例配置，然后在仓库根目录启动。`check` 检查归属和配置，无参数命令负责启动与停止整组应用进程。

```sh
. .venv/bin/activate
python main.py check
python main.py
```

- 宿主、正式离线 Compose、单组件、故障排查和停复顺序见 [S07 运维手册](docs/operations/S07.md)。
- 从用户会话到管理员和研究接口的请求样例、版本迁移与错误见 [v1 候选契约](docs/api/v1.md) 和 [OpenAPI 3.1](docs/api/openapi.v1.json)。
- 阶段判定、证据、历史 CSV 风险及外部授权见 [S07 验收报告](docs/verification/S07.md)。

本包包含上述 S07 操作和验收材料。下文 S01 至 S04 的历史文档路径以完整 Git 仓库根目录为起点。

## 查询与用户业务

登录后可从全国同刻概览进入城市七项、独立趋势、站点和历史明细，并在“我的关注”复访已收藏的实体。查询链路只需 Web、PostgreSQL 和已获准的存量数据。查询参数、分页、字段可用性、改密与收藏示例见 S04 用户 API（`docs/operations/S04.md`）。开发验证和真实 H 核对见 S04 工程交接（`docs/verification/S04.md`）。

## 原 S01 开发 Compose 与 F 演示

需要 Git、带 Compose 和 BuildKit 的本地 Docker，以及 Python 3.13。以下命令在仓库根目录执行。第一次构建需要获取固定 digest 的基础镜像和 `uv.lock` 中的包。本地开发环境使用 `uv sync --locked`。容器自带锁定的 Python、uv 和验证工具。

首次运行时从模板生成权限为 `0600` 的 `.env`。若文件已存在，命令会提示 `FileExistsError` 并保留原文件。

```bash
python3.13 - <<'PY'
import os, secrets
from pathlib import Path
text = Path('.env.example').read_text()
for key in ('DJANGO_SECRET_KEY', 'POSTGRES_PASSWORD', 'POSTGRES_ADMIN_PASSWORD'):
    text = text.replace(f'{key}=\n', f'{key}={secrets.token_hex(36)}\n')
with os.fdopen(os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
    file.write(text)
PY
docker compose config -q
docker compose up -d --build web worker
docker compose ps -a
curl --fail --retry 10 --retry-connrefused http://127.0.0.1:8000/health/ready/
```

如果 8000 已占用，修改 `.env` 的 `WEB_PORT` 并对下文示例使用同一端口。PG 和 Redis 仅使用内部网络。`migrate` 退出码应为 `0`，Web、Worker、PostgreSQL 和 Redis 四个服务保持运行。手工投递 F 的示例只启动这四个常驻服务。基础或正式 Compose 需要 Beat 时，先在该独立实例登记 PG、Redis 与锁文件，再启动整组。见 [S07 运维手册](docs/operations/S07.md#4-compose-与-beat-切换)。`{"web":"ok","pg":"ok"}` 表示 Web 可读 PG。迁移只预置三项 F 许可，观测表初始为零行。

PG 初始化密码和应用角色密码独立。首次初始化时，应用角色获得本应用库的所有权。已有卷保留原角色和密码，重建时继续使用原 `.env` 和同一 Compose 项目名。Web、Worker、Beat 和迁移共用同一镜像。此基础配置的 Web 是单进程 Django 开发服务，`DEBUG=False`。默认回环 HTTP 使用 `COOKIE_SECURE=0`，HTTPS 环境应设为 `1`。

## 从管理员提交到普通用户回读

先通过可信本地终端建立管理员，按提示输入用户名和密码。公开注册接口只创建普通用户。

```bash
docker compose exec web python backend/manage.py createsuperuser
```

下面示例使用仓库自带的标准库 HTTP 客户端。它在内存中维护 Cookie，注册和登录前通过 `GET /api/v1/auth/csrf/` 取得令牌，登录后重新取令牌。所有 POST 均以 `X-CSRFToken` 发送当前令牌。在整段命令运行前准备好管理员密码和一个新普通账号的密码。

```bash
PYTHONPATH=scripts python3.13 - <<'PY'
from getpass import getpass
from time import monotonic, sleep
from s01_runtime import AUTH, FIXTURE_POST, OBS, RUNS, Client

base = 'http://127.0.0.1:8000'
admin = Client(base).login('admin', getpass('管理员密码: '))
reader = Client(base)
reader.csrf()
name = 'local_reader'
password = getpass('普通账号密码: ')
status, body = reader.call(AUTH + 'register/', {'username': name, 'password': password})
print('register', status, body)  # 已注册过同名账号时可继续用其原密码登录
reader.login(name, password)
status, body = admin.call(RUNS, FIXTURE_POST)
print('submit', status, body)
if status != 202:
    raise SystemExit(1)
until = monotonic() + 30
while monotonic() < until:
    status, data = reader.call(OBS)
    if status != 200:
        raise RuntimeError((status, data))
    if data['count'] == 1:
        print(data['results'][0])
        break
    sleep(0.2)
else:
    raise SystemExit('未在 30 秒内读到观测，请按 run_id 对账')
reader.call(AUTH + 'logout/', {})
admin.call(AUTH + 'logout/', {})
PY
```

将示例中的 `admin` 换成实际管理员用户名。另一个选择是让维护者在可信 Django shell 中给活动的日常操作账号单独授予 `samples.run_fixture`。`is_staff` 本身没有该权限，所有账号均须通过当前 F 用途检查。普通注册、登录、任务触发分别限每 60 秒 8、12、12 次，前两者按直连来源计数，触发按账号计数。

| 请求 | 结果 |
|---|---|
| `POST /api/v1/admin/fixture-runs/`，JSON 为 `{"fixture_key":"co-hour-v1","reason":"local_verification"}` | `202` 返回 `run_id`。执行结果由 Worker 在 PG 中记录 |
| `GET /api/v1/fixtures/observations/?product=F&observed_at=2001-02-03T00:00:00Z` | 观测日期来自夹具。`1250 µg/m³` 换算为 `"1.250" mg/m³`，区间为 `23:00Z` 至次日 `00:00Z`，左闭右开 |
| 将时刻换成 `2001-02-03T01:00:00Z` | `200`、`count=0`、`no_observation_at_time`。时间参数必须带时区，URL 中的 `+` 需要编码为 `%2B` |
| 匿名读取、普通用户触发、无效 CSRF、缺用途 | `403`，分别返回 `not_authenticated`、`permission_denied`、`csrf_failed`、`use_denied` |

## 按 UUID 对账与保卷重建

收到 `503/dispatch_unconfirmed` 时记下 `run_id`。查看 `worker` 日志中该 UUID 的收件、`fixture_end` 记录，再通过 PG 核对 `dispatch`、`state`、`observation_id`。`pending/unconfirmed` 需要人工核对，重复提交会产生新的运行号。同一冻结 F 的成功请求关联同一观测。

```bash
docker compose logs --no-color worker
docker compose exec web python backend/manage.py shell
```

```python
from samples.models import FixtureRun

FixtureRun.objects.values("id", "dispatch", "state", "error_code", "observation_id").get(
    pk="替换为运行 UUID"
)
```

在同一项目中保留 PG 卷重建服务，随后重新登录并精确查询。

```bash
docker compose down
docker compose up -d web worker
docker compose port web 8000
```

## 运行 S01 自检

当前脚本验证现版本的 F/I、`ObservedScheduler` 和受控 Admin。复现封存的 S01 原始结果时，在 `0656071655247fd7db250dc00e4b50e34b7a18c3` 的独立检出中运行该版本的同名脚本。

```bash
python3.13 scripts/verify_s01.py
```

默认顺序运行两个全新、随机命名的 Compose 项目，每轮使用随机回环端口、独立 PG 卷、临时秘密和独立的 `test_s01` 库。`--rounds 1` 可用于开发复测。运行器会停启、注入故障并最终清理自己创建的服务、卷和应用镜像标签，保留脱敏证据 `tmp/s01-evidence/<项目名>/{checks,result}.json`。本地演示项目可以保持运行。首次获取依赖与两次保卷重建都需要时间。

每轮在应用镜像内执行 `uv lock --check`、Ruff 检查和格式检查、纯规则 mypy、Django 系统和迁移检查、pytest。运行器另外通过真实 HTTP、PG、Redis 和 Worker 完成跨进程断言。阶段报告含两轮标识、手算与实测对账、故障注入和覆盖范围，见 S01 开发验证记录（`docs/verification/S01.md`）。

`backend/accounts/` 管会话和入口保护，`backend/samples/` 只管本地 F。正式城市、站点身份与版本规则归 S02，采集业务窗口和可靠恢复归 S03。

## S02 北京历史站点小样

S02 提供正式观测目录、分时段站点身份、逐指标修订和受控历史导入。新库的正式用途许可和观测均为空。两个真实北京站点的数据来自 UCI 数据集 501。平台保留原始小时标签，以 `Asia/Shanghai` 解释源时区并标记假设。CO 从 µg/m³ 换算为 mg/m³。每站取 2013 年 3 月 1–2 日、2017 年 2 月 27–28 日，两段之间的小时保留为空。

**署名**　Chen, S. (2017). *Beijing Multi-Site Air Quality* [Dataset]. UCI Machine Learning Repository. DOI `10.24432/C5RK5G`。选站、选时、逐指标映射及 CO 换算为本项目加工。发布物及许可声明见 [UCI 数据集](https://doi.org/10.24432/C5RK5G) 和 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。

### 在隔离环境复现

操作者确认数据集许可和本次本地使用范围后，准备计划 §5.1 指定的官方内层原始 ZIP，将 `S02_UCI_ZIP` 指向仓库外的该文件。冻结文件为 7,959,991 字节，SHA-256 为 `d1b9261c54132f04c374f762f1e5e512af19f95c95fd6bfa1e8ac7e927e3b0b8`。应用容器只接收本地字节。运行器建立自有 Compose 项目、独占卷和 pytest 库，完成后释放，收据写入 `tmp/s02-evidence/`。

```bash
python3.13 scripts/verify_s02.py --uci-archive "$S02_UCI_ZIP" --profile uci501-edges-v1
```

### 在已明确授权的本地项目导入

由受信维护者执行登记。`--actor` 指定活动账号，登记和撤回需要 `observations.manage_grants`，暂存及提交需要 `observations.import_history`，治理还需要 `observations.review_observation`。系统另按产品、用途、受众、日期、对象和字段核对当前来源许可。注册命令重复运行会保留已有的撤回记录。

```bash
docker compose exec worker python backend/manage.py s02_register_uci --actor "$S02_OPERATOR"
docker compose exec -T worker python backend/manage.py s02_stage_uci --actor "$S02_OPERATOR" < "$S02_UCI_ZIP"
```

第二条命令返回 `stage_id`。用同一账号及有效 CSRF 的登录会话提交。以下 `session` 为上文已登录的 `s01_runtime.Client`，`stage_id` 为返回的 UUID。

```python
status, result = session.call(
    "/api/v1/admin/history-imports/",
    {
        "profile": "uci501-edges-v1",
        "stage_id": stage_id,
        "reason": "historical_verification",
    },
)
print(status, result)
```

`202` 和投递状态未确认的 `503` 都返回可对账的 `attempt_id`。在可信终端查询 `observations.models.ImportAttempt` 的 `state`、`error_code`、`batch_id` 和 `finished_at`。需要重投时用原账号执行 `docker compose exec worker python backend/manage.py s02_redeliver --actor "$S02_OPERATOR" --attempt "$ATTEMPT_ID"`。Worker 在每次实际打开文件及入库时重查许可。已终结的尝试保留原结果。暂存过期或失败后，需要重新暂存获权原件。

普通登录用户用 `GET /api/v1/observations/current/` 精确查一笔。指定 `product` 和 `site_epoch` 的数据库 ID（城市层用 `city`），再指定 `indicator=CO`、`report_kind=hourly`、`window_definition=hourly_label_unspecified`、`standard_series=not_applicable`、`observed_at=2013-02-28T16:00:00Z`。日报用 `local_report_date`。自制 F 请求另带 `scope=synthetic`。授权治理读 `GET /api/v1/admin/observation-evidence/` 指定 `product` 及 `batch`、`station_key` 或 `city_key` 之一，用 `after` 继续读取。只返回仍获准查看的字段。UCI 署名随观测和治理响应提供。

### 撤回与恢复

Worker 独享 `32 MiB` 的 `/run/s02-history` tmpfs，每个暂存件有 20 分钟操作期限。撤权命令先提交授权失效，再核对该产品的独立留存依据、数据库明细及暂存件。仅在有明确授权的目标项目执行。运行器的撤回及故障演练仅使用其自有资源。

```bash
docker compose exec worker python backend/manage.py s02_revoke_grant \
  --actor "$S02_OPERATOR" --grant "$GRANT_ID" --reason local_withdrawal
docker compose exec worker python backend/manage.py s02_dispose_due
```

限期后可恢复处置的依据以 `expiry_mode=recoverable` 登记，使用资格在数据库截止时刻终止。`DispositionJob.completed_at` 标记数据库与文件已实际核对。漏投和清理失败保留 PG 待办，由 Worker 启动核查、Beat 补偿或上述命令重试。要求在整机停机期间物理删除的硬期限无法由本地部署保证，系统在写入时拒绝该项保存依据。平台内控撤回不改变 UCI 已发布的 CC BY 4.0 许可。详见 S02 实施计划（`docs/plans/S02_IMPLEMENTATION_PLAN.md`） 和 S02 开发验证报告（`docs/verification/S02.md`）。

## S03 离线采集与窗口对账

在仓库根目录运行下面的验证器，可从一次管理员 HTTP 请求追到 Worker、模拟来源、PG 批次和窗口对账。验证器创建独占的 Compose 项目和 PG 卷，给测试 Worker 接入只在内部网络可见的 `s03-origin`。测试完毕后释放自有资源，JSON 收据留在 `tmp/s03-evidence/<项目名>/`。首次构建所需的镜像及锁定包须在本机可用。

```bash
python3.13 scripts/verify_s03.py                  # V01～V19，F / 注入故障
python3.13 scripts/verify_s03.py --case V08       # Redis 投递和同 UUID 恢复
python3.13 scripts/verify_s03.py --case V10       # 分块回滚、重新取得响应、ACK 前退出
```

默认 Compose 的 Worker 监听 `s01.fixture,s02.history,s03.collection`。排期、运行预算及完成事实在 PostgreSQL 中。空库的在线 Job 数为 0。原 S03 的四种 F 契约使用 `s03.fixture` 命名空间。`f-exact-v1` 的模拟来源能按原标签取数，`f-latest-v1` 仅能取当前包。真实来源的许可、回溯能力与联网验收单列在验证报告（`docs/verification/S03.md`）。

CNEMC 接入复用采集 Worker 实现和 PostgreSQL 账本。首次实源 A03 使用独立验收项目和专用 TLS 出口。运行 `python3.13 scripts/verify_cnemc.py`，可用内网合成来源验证 L、C、按城 S、全国 S 和 T。正式访问默认关闭，实源登记、限定验证与 S04 精确读取参数见操作说明（`docs/operations/CNEMC.md`）。

### 产品及时间

| 形状 | 请求 | 获准处理的数据 |
|---|---|---|
| L | 全国一次 GET | 同一来源小时的城市 AQI 和六项污染物等级。等级单位为 `grade` |
| C | POST `cityCode`，空正文 | 城市直接发布的 AQI 和六项浓度。CO 为 mg/m³，其余五项为 µg/m³ |
| S | GET `cityName` | 按站点代码、城市归属和站址有效期核对的逐站七项 |
| T | POST `citycode`，空正文 | 选定终点及之前 23 个小时内实际出现的 AQI 点 |

`target` 须带时区并对齐整点。查询会保留字段缺失、显式空值、删失和真零的差别。对象及时段有独立名册依据时，逐格报告应到 E、字段实到 O 和当前有效 V。未知分母和相应比率返回 `null`。同窗的实际获取次数、首次认可数与当前 V 可各不相同。

### 操作及恢复

以下请求在已经登记了合成目录、完整来源许可和配额的隔离项目中使用。可从 `s03_register_job --help` 查看建立初始停用 Job 的参数。登记及管理需要 `observations.manage_collection`，实际执行主体和发起者需要 `observations.run_collection`。来源准入分别核整包获取、指定范围处理与留存。治理读和独立复核另需 `observations.review_observation`。POST 使用登录 Session 与当前 CSRF 令牌。

| 入口 | 请求与操作结果 |
|---|---|
| `POST /api/v1/admin/collection-runs/` | JSON 包含 `job`、`target`、`request_id`（调用者生成的 UUID）和 `reason`。`manual` 为普通触发，`review` 为独立取证，`backfill` 为可回溯的缺格补采。返回 `run_id`、`window_id`、`dispatch`。幂等号冲突返回 409 |
| `POST /api/v1/admin/collection-jobs/<id>/` | `{"action":"pause"}` 暂停该 Job。`resume`、`resume_product`、`resume_source` 各自只恢复对应范围。来源返回 429 后按 PG 冷却期重新准入 |
| `GET /api/v1/admin/collection-windows/<id>/` | 查看目标时段、逐格资格、Run 及来源批次。用 `object_code`、`indicator` 选范围，用 `run=<uuid>` 定位一次尝试。`next_after` 与 `next_run_after` 分别传回 `after` 与 `run_after`。有明细截断时再按对象、指标或 Run 查询 |
| `POST /api/v1/admin/collection-runs/<uuid>/` | 对已耗尽投递轮次且仍为 `unsent` 的 Run 提交 `{"action":"redeliver"}`，保留 UUID 和操作者审计 |

收到 503 且带 `run_id` 时先按该 UUID 查 PG。`unsent` 表示还未预留访问，`possible` 表示已预留且可能送达来源，`responded` 表示已有响应证据。Redis 恢复后可运行 `docker compose exec worker python backend/manage.py s03_reconcile_due`，Worker 启动及已登记的 Beat 扫描也会恢复待办。真正重新获取使用新的 Run、前驱关联和独立预算。进程崩溃后保留已经提交的块，后继重新取得来源响应。

来源配额不足时，原 Run 显示 `pending / unsent / budget_exhausted`。`next_retry_at` 是依据 PG 预留及当前许可算出的下一次检查时间。Beat 到期后复核权限、暂停和窗口，再投递同一 UUID。等待本身保持原 `attempt`，已预留且可能发出的访问仍计入尝试。响应为 `quality_only` 时，可按 Batch 核对获准的质量问题。`partial` 可能同时包含可用格和无法归属的子项。

升级已有 S03 验证库时，先停止该项目的 Beat 和 Worker 并暂停新触发，再运行 `python backend/manage.py migrate`，然后启动新版本。`collection.0006` 按旧账本中明确 `unsent` 且无预留的配额拒绝校正重试序号，为可恢复的链尾登记到期复查。降级迁移保留已校正的数据。

在**获准的测试库**中用 `psql` 只读核对下例，将变量替换为 HTTP 返回的 Run UUID。若需查看对象、时刻、原因和现行资格，请使用受权窗口 API。重复认可查询的预期结果为零行。

```sql
\set run_id '00000000-0000-0000-0000-000000000000'
SELECT window_id, id, predecessor_id, attempt, status, request_state, dispatch, batch_id, error_code, next_retry_at
FROM collection_run WHERE window_id = (SELECT window_id FROM collection_run WHERE id = :'run_id')
ORDER BY created_at;

SELECT window_id, item_key, count(*) FILTER (WHERE accepted) AS accepted_count
FROM collection_runitem WHERE window_id = (SELECT window_id FROM collection_run WHERE id = :'run_id')
GROUP BY window_id, item_key HAVING count(*) FILTER (WHERE accepted) > 1;
```

排查缺口时先对比目标时段、来源观测时刻和 `fetched_at`。遇到 `backfill_unavailable` 查看该 profile 的回溯范围，遇到 `permission_lost` 查看当前许可与处置记录，遇到 `disputed` 用 Batch/Receipt 定位 S02 的同键裁决。只剩部分获权对象时按 `object_code` 查询，撤权处置后的整包清单不再参与覆盖对账。闭窗后的独立复核保留首次收口和每格首次认可，可改变当前质量资格。
