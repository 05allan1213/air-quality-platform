# 城市空气质量监测数据分析平台

S01 提供一条可在本机复现的链路。管理员提交项目自制的 F 夹具，独立 Celery Worker 从 Redis 收件，在 PostgreSQL 中记录结果。普通用户登录后按观测时刻读取 CO 浓度。当前数据的用途为本地演示，外采业务计划为零。

## 启动本地环境

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
docker compose up -d --build
docker compose ps -a
curl --fail --retry 10 --retry-connrefused http://127.0.0.1:8000/health/ready/
```

如果 8000 已占用，修改 `.env` 的 `WEB_PORT` 并对下文示例使用同一端口。PG 和 Redis 仅使用内部网络。`migrate` 退出码应为 `0`，其余五个服务保持运行。`{"web":"ok","pg":"ok"}` 表示 Web 可读 PG。迁移只预置三项 F 许可，观测表初始为零行。

PG 初始化密码和应用角色密码独立。首次初始化时，应用角色获得本应用库的所有权。已有卷保留原角色和密码，重建时继续使用原 `.env` 和同一 Compose 项目名。Web、Worker、Beat 和迁移共用同一镜像。当前 Web 是单进程本地 Django 服务，`DEBUG=False`。默认回环 HTTP 使用 `COOKIE_SECURE=0`，HTTPS 环境应设为 `1`。

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
docker compose up -d
docker compose port web 8000
```

## 运行 S01 自检

```bash
python3.13 scripts/verify_s01.py
```

默认顺序运行两个全新、随机命名的 Compose 项目，每轮使用随机回环端口、独立 PG 卷、临时秘密和独立的 `test_s01` 库。`--rounds 1` 可用于开发复测。运行器会停启、注入故障并最终清理自己创建的服务、卷和应用镜像标签，保留脱敏证据 `tmp/s01-evidence/<项目名>/{checks,result}.json`。本地演示项目可以保持运行。首次获取依赖与两次保卷重建都需要时间。

每轮在应用镜像内执行 `uv lock --check`、Ruff 检查和格式检查、纯规则 mypy、Django 系统和迁移检查、pytest。运行器另外通过真实 HTTP、PG、Redis 和 Worker 完成跨进程断言。阶段报告含两轮标识、手算与实测对账、故障注入和覆盖范围，见 [S01 开发验证记录](docs/verification/S01.md)。

`backend/accounts/` 管会话和入口保护，`backend/samples/` 只管本地 F。正式城市、站点身份与版本规则归 S02，采集业务窗口和可靠恢复归 S03。
