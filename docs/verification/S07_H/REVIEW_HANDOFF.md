# 申请 GPT 完整复审 S07

请将本补充包与原 `S07.zip` 一起审阅，按 S07 方案 §6 的四条退出判据给出阶段签收决定。原包提交 `1ddb3ed3bf15c48da79f9547071583633f4cfd64`，SHA-256 `d9c2de122bda5751070708846852ad08256877b080ad7a2906e14c7e88763075`。两包的正式运行源码均对应 `1258e04db92db956deef816b06cef660cbdefd677aef7994b263cbd24d85f6b8`。用户在本轮交接中确认 GPT 已接受 L22 的历史残余风险。原 FAIL、九次复验、F/I 独立验收与百万 F 的 T09 仍可由原包复算。[S07 阶段报告](../S07.md) · [遗留台账](../S07_LEGACY_LEDGER.md)

## L31 的依据和实测

先核对 UCI 页面、条款、当次两层副本和导入器指纹，再按受众、字段、历史窗及 PG 生效时段检查用途。本次只选同一 UCI 数据集的 Aotizhongxin、Changping 两站，每站两段 48 小时、六项浓度。每个隔离库的初始授权数量、操作及到期时刻可在 `evidence.json` 对应入口查询。[逐用途依据与署名](RIGHTS.md) · [来源指纹与访问时刻](source.json) · [A/B/R 脱敏收据](evidence.json) · [独立复核](review.md)

| H 子项 | 本轮计入的运行 | 可复核结果 |
|---|---|---|
| V05，普通用户发现 | A `s07a-b517464d7343`、B `s07-b-2e635f488607` | 从一页一站的目录取得产品和站址标识，经返回链接读取本站详情、历史并复访收藏。A 验证全部隐藏与不存在同形。 |
| V07，同质研究 | A | 原 CSV、独立 PG/SQL、HTTP 的 1,152 个指标键一致。12 次两站比较、24 次单站统计、144 页支撑明细一致。源 CSV 是只读输入。平台明细、汇总 CSV 和越窗分析均返回 403，导出票据 0。 |
| V11，用途和留存 | A、R `s06-91ab1e38511a` | A 撤展示后保留 48 键，再撤最后留存后为 0，合法兄弟键仍为 48。R 按现行用途发布 3 段。两个空目标位于同一隔离 PG 实例，使用独立角色。每个合法恢复意图各放行一次 42 字节核验响应，回读 48 键及 48 修订，目标回滚清零。撤恢复权或备份权后均交付 0 字节，2 个处置任务完成，合法兄弟段仍可受控回读。 |
| V14 / T10，后端离线部分 | A、B、R | A/B 各全停五个服务并从同一自有 PG 卷冷启。A 原会话复访目录、收藏，新登录会话复算首窗 CO。B 原、新会话分别复访、复算，并逐点核对 `statistics` 响应的 `chart.points`。每个会话在两站各读 48 点，缺测分别为 0、1，与原 CSV 和 PG 一致。两站共同覆盖 48 小时、有效配对 47 小时。R 在无外部路由的隔离环境恢复。各入口的自有运行资源均已清理。浏览器绘制由 S08 验收，本轮为 NOT RUN。 |

`evidence.json` 的 `previous_runs` 保留 A 首轮旧回环端口、B 首轮旧 ORM 连接、R 保管消息长度、处置 null 条件和暂存清理时序的失败收据，也列出后续通过的工程轮。早轮 R `s06-d2b4536e4ecb` 的文件在结束后追加了重复的脚本摘要字段，当前文件指纹以该列表为准。最终采用的新 R 在运行时记录授权、源指纹及结果。B 工程轮 `s07-b-5970ca3f0d23` 和 `s07-b-6fed591073a4` 均通过当时断言。最终 `B-bw6cwibr` 另有两个会话的 192 个图表点核对。这些原件仅供有权维护者在验收工作区复算。

## 校验和重建

对照交接提供的包外 SHA-256，在干净目录检查 ZIP 的 CRC 和 `SHA256SUMS`。补充包更新阶段报告、遗留台账及运维交接索引，可与原包中的同名文档比对。其余新增成员均位于 `docs/verification/S07_H/`。包内 H 证据只含引文、哈希、覆盖计数、HTTP 状态和复验程序。UCI 包、网页快照、逐时数值、授权签发件、密钥、Session 凭证和备份段留在各自的受控范围。

```sh
unzip -t S07-H-supplement.zip
unzip -q S07-H-supplement.zip -d h-review
(cd h-review && sha256sum -c SHA256SUMS)
```

本地维护者在拥有私有收据的原工作区可复算两个独立索引。公开补充包可以用相同文件重建，其 `package.py` 复用原 S07 工具的固定时间戳与 Unix 文件模式。

```sh
python3 -B scripts/s07_evidence_index.py --check docs/verification/S07_evidence.json
python3 -B docs/verification/S07_H/receipt_index.py --check
python3 -B docs/verification/S07_H/package.py /path/to/new/S07-H-supplement.zip
```

若经授权重跑，准备原包对应的干净源码、离线依赖/镜像和本次具名输入。使用原包提取树时只复制新增的 `docs/verification/S07_H/` 目录，保留原树清单所绑定的其余文件。先独立核对届时适用的官方许可、输入和用途，保管复核后的 UCI 页面与 CC 英文法律文本。按 `source.json` 的地址从 UCI 外包读取具名内包，设置 `H_INNER`、`UCI_PAGE`、`CC_PAGE` 为各自路径。以下命令从项目根目录运行，环境 A/B/R 每次新建。PG 与 Redis 不接受共享实例参数。[两入口离线环境前提](../../operations/S07_handoff.md)

```sh
install -d -m 700 tmp/s07-h-supplement
PYTHONPATH=scripts:backend:tests .venv/bin/python -B docs/verification/S07_H/replay/h_compose.py < "$H_INNER"
.venv/bin/python -B docs/verification/S07_H/replay/h_host.py --uci-archive "$H_INNER"
.venv/bin/python -B docs/verification/S07_H/replay/h_restore.py \
  --archive "$H_INNER" --uci-metadata "$UCI_PAGE" --license-metadata "$CC_PAGE"
```

本次只申请 S07 后端完整封版复审。T11 保持 NO-GO。S08 准入与 S07 签收交 GPT 裁决。
