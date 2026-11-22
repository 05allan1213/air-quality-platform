# CNEMC 接口与平台数据产品映射矩阵

2026-11-22，供 S04 前接入设计使用。研究在北京时间核验了同源 12 个业务端点。下表的规模和窗口取自该次研究，公开端点的服务上限、长期覆盖和许可范围仍需各自的证据。工程中的产品身份是 `Source + Product.code`，`shape` 只决定层级与指标规则。尤其要区分按城站点产品与全国站点地图产品。见 [V3.0 §6.1](城市空气质量监测数据分析平台_系统总体设计方案_V3.0.md) 和 [Product 模型](../../backend/observations/models.py)。

## 12 个端点

固定源为 `https://air.cnemc.cn:18007`。下表列实际方法、路径和参数名。除 A10 外，POST 为零长度正文，参数在 Query。A10 仅用表单键 `date`，格式为北京时间 `YYYY-MM-DD HH:00:00`。A03 返回单对象，其余业务端点返回数组。样本列为研究目录 `samples/` 下的文件名，仓库只引用名称。

| ID | 请求 | 实测产品、粒度及边界 | 对应平台产品与调用选择 | 样本 |
|---|---|---|---|---|
| A01 | `GET /CityData/GetAllCityRealTimeAQIModels` | 全国城市最新小时列表，338 条同一时次。AQI、源类别、首要污染物及六个污染物等级 | **L**。获准全国包后每小时至多一次常规读取。总览取同一真实 `TimePoint`，排名还需已确认的同一标准。区域请求权限不足时关闭 | `city-hour.json` |
| A02 | `GET /CityData/GetAllCityDayAQIModels` | 全国最新一个自然日，338 条。AQI、等级、首要污染物。未见日浓度、指定日期或官方排名参数 | **D 全国日报概览候选**。日产品及许可核定后，最多一次常规请求/日。基础投影只含日 AQI，源等级作为获准的描述信息 | `city-day.json` |
| A03 | `POST /CityData/GetAQIDataPublishLiveInfo?cityCode=…` | 单城最新小时对象，AQI 与六项浓度。实测北京，尚无 338 城详情完整性证据 | **C**。每个授权白名单城市每小时一次。城市七项及其历史来自该产品的直接发布与合法积累 | `city-live.json` |
| A04 | `GET /CityData/GetAQIDataPublishLive?cityName=…` | 城市全称查询。北京 23 站，ISO 时标，样本 `TimePointStr` 可空，城市码为 0 | **S 按城实况**。只获准部分城市站点时按城采。全国模式已覆盖相同需求时停用重叠的 A04 定时任务。站码和时态目录决定所属城市 | `city-stations.json` |
| A05 | `POST /HourChangesPublish/GetCityRealTimeAqiHistoryByCondition?citycode=…` | 单城近期小时序列，北京 24 个时次，含 AQI 和六浓度，无法指定历史起止 | **T**。项目只投影 AQI。获准且需要 T 时每城一次刷新整条近期曲线，或用一次滚动读取补到仍可见的 T 观测。重叠时次归实际收到它的 Run 对账。A03 的 C 缺口仍记录在 C | `city-hour-trend.json` |
| A06 | `POST /HourChangesPublish/GetCityDayAqiHistoryByCondition?citycode=…` | 单城滚动日趋势，北京 14 个自然日，可能乱序。AQI、五项日均浓度、日最大 8 小时平均 O₃ | **D 单城日报明细候选**。与 A02 分产品登记。按日、按需或一次恢复授权的近期报告日。与 A02 重叠的日 AQI 只按选定产品计一次，不作为 A02 历史补值 | `city-day-trend.json` |
| A07 | `POST /HourChangesPublish/GetAllAQIPublishLive` | 全国站点最新小时包，1,601 个唯一站码、341 个不同 `Area`，正文 1,254,452 字节。样本有非零城市码、站码和坐标 | **S 形状的独立全国站点产品**。全国包获准后每小时一次，用本地有权的子集支持城市/站点查询。A04 与 A07 同站同时值可对照，各自保留产品身份 | `station-live.json` |
| A08 | `POST /HourChangesPublish/GetAqiHistoryByCondition?stationCode=…` | 单站近期小时浓度及 AQI，实测一站 24 个时次，样本没有 `CityCode` | **S 形状的独立单站小时趋势候选**。特定站的短窗研究或有权的补充读取。优先查已存兼容 S 的历史，同站同小时仅做来源对账。不给 A04/A07 自动补值 | `station-hour-trend.json` |
| A09 | `POST /HourChangesPublish/GetIAqiHistoryByCondition?stationCode=…` | 单站各污染物 IAQI，实测一站 24 个时次，可乱序，另有 `*Level` | **独立 IAQI 研究产品，现有 L/C/S/T/D 无对应指标契约**。有具体分指数需求、指标模型及独立许可才增设适配器。A07/A10 的站点 AQI 或浓度不能还原该来源的 IAQI | `station-iaqi-trend.json` |
| A10 | `POST /HourChangesPublish/GetAQIHistoryByConditionHis`，表单 `date` | 一个指定整点的全国站点包，无 `CityCode`。相对最新发布时次 −1、−3、−23 h 有数据，−24、−25 h、−7 d 返回 `[]` | **全国站点 S 的精确补采候选**。同产品/窗口/指标口径与 A07 确认兼容，且 A10 整包获准后，每个有缺口的整点一次。未经确认仅作独立对照，不能关闭原 S 窗。无长期回溯承诺 | `station-at-12.json`、`station-at-10.json`、`station-at-23h.json`、`station-at-24h.json` |
| A11 | `GET /CityData/GetProvince` | 省级导航字典，34 条，无观测时间。返回的 `Id` 可作 A12 的参数 | **目录元数据**。已有许可目录可直接登记。仅在目录缺失或有变更证据时作受控初始化/复核，与空气质量窗口分开 | `provinces.json` |
| A12 | `GET /CityData/GetCitiesByPid?pid=…` | 指定省份导航城市列表。`pid` 取 A11.`Id`，与 `ProvinceCode`、`CityCode` 分属不同字段。实测一个省级项 | **目录元数据**。只读取需要且获准的省份。为 A03/A04/A05 等核对源城码和全称，不能证明历史站址或应到名册 | `province-cities.json` |

`china-map.json` 记录静态 `China.json` 的结构摘要，不计入这 12 项。坐标系、位置精度与底图权利按地图验收另核。表中的日期能力限于 A10 的单整点、A05/A08/A09 的近期小时、A06 的近期自然日和 A02 的最新报告日。长期城市或站点小时档案需提供方确认数据集、范围、审核状态、交付格式和许可，再安排独立登记或导入。H 的 UCI 资料有自身的数据集命名空间。

## 字段投影与质量规则

| 来源字段 | 规范化及查询约束 |
|---|---|
| `CityCode`、`Area`、`StationCode`、`PositionName` | 城码保留源命名空间，站码按字符串。A07 非零城码与观测时次所在的城市代码期、站址期交叉核对。A04 的 0、A08/A09/A10 缺少城码时，只能靠已核站码与当时的站址期。错配、迁址边界、历史时期证据缺失进入质量核查。站名和当前坐标单独使用都不足以建立历史身份。 |
| `AQI`、`Quality`、`AqiLevel`、`PrimaryPollutant`、`*Level` | AQI 无量纲，六个 `*Level` 是源等级。A09 的 `ISO2/INO2/ICO/IO3/IO3_8h/IPM10/IPM2_5` 是另一组无量纲分指数。源文字按字段授权保留，首要污染物的占位文字按描述处理。已核标准下，来源给出的 `AqiLevel` 若与 AQI 不符，写质量事件，退出默认有效样本。A03 的该字段可缺省。标准仅凭来源声明或有适用范围的文件证据选用。未知时保留获准源描述，平台推导等级、颜色与跨版排行关闭。 |
| `PM2_5`、`PM10`、`SO2`、`NO2`、`CO`、`O3` | 小时主字段取 1 小时浓度。`PM2_5` 映射 `PM2.5`，CO 为 mg/m³，其余五项为 µg/m³。使用 `Decimal` 和项目单位规则。小时标签的左右边界缺证时保留 `hourly_label_unspecified`。城市发布量由 A03 直接取得。 |
| A06 的 `SO2_24h/NO2_24h/CO_24h/PM10_24h/PM2_5_24h` 和 `O3_8h_24h` | 前五项按自然日日均，O₃ 为该自然日最大 8 小时平均。日身份为源地方报告日，明确每项统计量。A04/A07/A08/A10 同名扩展列的窗口或有效性待核。小时主值缺失时保留该缺口。`O3_8h`、`O3_24h`、`NO/NOx` 及其扩展列本期不入主指标。 |
| `TimePoint`、`TimePointStr`、HTTP `Date` | A01/A02/A03/A04 的无偏移 ISO 按 `Asia/Shanghai` 解释。A05–A10 的 `/Date(ms)/` 是 UTC epoch 毫秒。时序按规范时次排序。短标签只显示，完整可解析的第二时标与主时标矛盾须报错。日报零点转地方日键。所有端点均缺独立源发布时间或可信修订序号，字段留空，HTTP `Date` 仅是传输元数据。 |
| 缺键、JSON `null`、空串、`NA`、`/`、`<3`、`0` | 缺键是未收到该指标。显式空和 `NA` 为各有原因的无有效读数，`/` 按研究资料表示未开展监测。左删失记录方向与界值，数值零按指标和标准检查。非有限、非法类型和未知标记给出质量原因。`IsPublish=false` 的有效样本经研究对照网页，服务端语义未知，不能作为整行过滤条件。 |

本矩阵对 `API_CATALOG.md` 的字段并集与样本摘录分别核对。例如 A03 样本没有 `AqiLevel`，A04 的 `TimePointStr` 可为 null，A02 样本有污染物等级而没有浓度。要求字段取决于具体端点与指标，缺一个可选列不会把整包变成网络故障。研究取证程序的 `--limit` 和 `--match-*` 都在客户端生效，整包获取仍须单独获得授权。研究依据与核查方法见 [工程核查报告](../verification/CNEMC_PRE_S04_AUDIT.md)。
