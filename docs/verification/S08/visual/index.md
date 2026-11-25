# S08 正式前端视觉样本

截图来自实际 Chromium 运行的生产构建，后端使用隔离的 F 合成数据。源文件及收据按 `tmp/s08-evidence/<run>/visual/` 定位，验收状态取自各次运行的 `result.json`。收录图保留原始像素，裁剪范围见表。账号栏在收录区域内以洋红遮罩覆盖或在窄屏隐藏。新增图已经逐张目视检查，可见内容为界面文案及 F 合成数据。

| 样本 | 场景与筛选 | 源 run、用例及图 |
|---|---|---|
| [当期双环总览 1440](overview-current-1440.png) | 1440×900 桌面完整首屏。F 的 L 产品 1，2024-02-02 00:00 UTC，HJ633-2012。显示 `51 / 52` 和 `51 / 51` 双环、地图未开放提示、AQI 分布与前五城市。 | `s08-b-78b65d708a1a` · `presentation.spec.ts` · `presentation-observation-1440.png` · 所在运行 7/7 PASS |
| [历史全国总览 1440](overview-desktop-1440.png) | F 的 L 产品 1，2024-02-02 00:00 UTC，HJ633-2012。含覆盖摘要、列表回退、等级分布与 AQI 较高的五城排名。 | `s08-b-cd1cded816d0` · `presentation.spec.ts` · `presentation-observation-1440.png` · PASS |
| [图例键盘焦点 320](overview-aqi-focus-320.png) | 同一快照，键盘展开六档区间。 | `s08-b-cd1cded816d0` · `presentation.spec.ts` · `presentation-legend-320.png` · PASS |
| [实体抽屉 768](navigation-opaque-drawer-768.png) | 同一快照，显式实体主题。关闭按钮有焦点，背后的列表有遮罩。 | `s08-b-cd1cded816d0` · `presentation.spec.ts` · `presentation-opaque-drawer-768.png` · PASS |
| [站点详情 390](observation-station-390.png) | F 的 S 产品 3。当前站址期、已保存观测和获准历史窗口。站名与城市名为合成样本。 | `s08-b-9b5d3c5196c2` · `monitor.spec.ts` · `monitor-station-390.png` · 该用例 PASS |
| [指标关联](research-pearson-24-pairs.png) | F 的 C 产品 2，同一城 CO / PM2.5，UTC `2024-02-01 01:00` 至 `2024-02-02 01:00`，结束时刻不含。24 个有效配对，Pearson r 留空，保留趋势及数据表入口。 | `s08-b-b9d929933c38` · `analysis.spec.ts` · `analysis-pearson-1440.png` · PASS。裁剪 `(258,1566)–(1424,2229)` |
| [治理窗口诊断](governance-window-diagnostics.png) | F 的 C 产品 7，源地日期 `2024-02-06` 至 `2024-02-07`，结束日期不含。获权窗口、E/O/V、三种时标和窗口日趋势。 | `s08-b-38147d72b409` · `operations.spec.ts` · `operations-windows-1440.png` · 该用例 PASS。裁剪 `(257,970)–(1425,1764)` |
| [字体加载中的双环心 390](overview-ratios-font-pending-390.png) | F 的 L 产品 1，同刻覆盖 `51 / 52`、可排名或汇总 `51 / 51`，字体加载中两处数字均可见。 | `s08-b-c91fbc8b8bf8` · `overview-ratios.spec.ts` · `regressions/overview-ratios-font-pending-full-390.png` · 1/1 PASS。裁剪 `(0,0)–(390,467)` |
| [查询模态内的实体日历 320](query-calendar-solid-320.png) | F 数据查询的时段模态。实底日历展开，键盘焦点在 2 月 1 日。 | `s08-b-6f9553c52280` · `overlays.spec.ts` · `overlays/query-calendar-solid-320.png` · 所在运行 6/6 PASS |
| [Chrome 原生 400% 总览](overview-native-zoom-400.png) | F 的 L 产品 1。1280×800 外窗内的 320×178 CSS px 视口，键盘跳到主内容后标题完整可见。 | `s08-b-ebd3c51a600b` · `zoom.spec.ts` · `zoom-400-overview.png` · 1/1 PASS |

两条标为“该用例 PASS”的来源运行均含一条失败的 `presentation.spec.ts` 用例。历史展示复测的三条通过记录位于 `s08-b-cd1cded816d0`，两处裁剪只用于说明对应业务状态。

## 新增样本的源码快照与收据

下列 SHA-256 取自各生产运行 `result.json` 的 `source_sha256`。四轮的外层和隔离环境均记录 `PASS`、`cleanup=true`。

| 源 run | 源码快照 SHA-256 | 同轮证据 |
|---|---|---|
| `s08-b-78b65d708a1a` | `3ba043a01929442f5dcf787dfc1682b73351d52b267cc120c5e69aeebdbe30cc` | `presentation-layout.json` 记录桌面与窄屏布局、键盘和材质。`regressions/overview-ratios.json` 记录同轮双环心的像素和命中。 |
| `s08-b-c91fbc8b8bf8` | `3b4eaf26aa193f75dd83c2b86dd0d16fa455417ea344fd48b33666212587cd13` | `regressions/overview-ratios.json` 记录字体加载中、就绪后、滚离与返回、320 重排、横屏及实体主题的环心像素和命中。 |
| `s08-b-6f9553c52280` | `c60640b88c31141cebceec0c7ddd1fb794e250c599171eb16d2b6b0d624759de` | `overlays/query-window-overlays.json` 记录 1440 的统计窗口菜单、390/320 的模态内日历、背景锁定及 Esc 逐层关闭。`overlays/query-daily-calendar.json` 记录日窗按源地日期及键盘选择。 |
| `s08-b-ebd3c51a600b` | `5a1d6e2396d626c595c0ce257064bb8536e34118133bb5c55d4d256879061fce` | `zoom-layout.json` 记录 Chrome 158.0.8127.36 原生 100%、200%、400%，CSS 视口宽度 1280、640、320 px，DPR 1、2、4。400% 下检查标题命中、抽屉聚焦和归焦、图例及表格局部滚动。归档图为 1280×713 设备像素。 |

## 边界与复核

生产记录的 1440 和 320 CSS px 键盘跳转测试中，主标题上缘分别为 82 px 和 68 px，粘性顶栏下缘分别为 64 px 和 58 px，两处标题中心点均可命中。排名中的四字标签在 1920、1440、1280、768、390、320 CSS px 均为单行，30 个样本全部位于标签盒及排名行内。记录见 `s08-b-cd1cded816d0/visual/presentation-layout.json`。

同一收据的三处定向 axe 扫描均报告零自动判定违规。首页玻璃相关界面有 179 个对比节点待人工核验，抽屉有 7 个。下述取样只覆盖所列文字和焦点，保留其余节点的待核状态。

### 合成颜色与键盘焦点抽样

以 PNG 左上为 `(0,0)`，从实际合成后的 RGB 像素取文字笔画或描边的主色，与附近同一表面的无内容背景像素配对。用 sRGB 线性化后的相对亮度计算 `(亮 + 0.05) / (暗 + 0.05)`。抽样覆盖顶栏左右、侧栏上下与选中项、卡片和两种焦点。按 S08 §9.2 的普通文字 4.5:1、适用非文本 3:1 阈值，所列样本均达到相应阈值。

| 图与位置 | 前景像素 | 邻近背景像素 | 对比 |
|---|---|---|---:|
| 总览，玻璃顶栏左侧品牌 | `(151,25)` `#142f47` | `(151,20)` `#d5eaf9` | 11.11:1 |
| 总览，玻璃顶栏右侧数据域标签 | `(1016,27)` `#405f75` | `(1016,21)` `#cee6f9` | 5.24:1 |
| 总览，玻璃侧栏上部常规导航 | `(99,203)` `#405f75` | `(99,198)` `#d4eaf9` | 5.44:1 |
| 总览，玻璃侧栏底部常规导航 | `(124,842)` `#405f75` | `(124,836)` `#cee6f8` | 5.24:1 |
| 总览，玻璃侧栏选中项 | `(69,154)` `#ffffff` | `(69,149)` `#1371c3` | 5.03:1 |
| 总览，地图占位说明 | `(394,437)` `#405f75` | `(394,432)` `#e5f3f9` | 5.95:1 |
| 总览，排名卡说明 | `(1051,332)` `#405f75` | `(1051,324)` `#ecf6fc` | 6.16:1 |
| 总览，排名方向选中项 | `(1161,385)` `#ffffff` | `(1161,380)` `#1673b9` | 5.02:1 |
| 图例，展开控制文字 | `(93,417)` `#075aa4` | `(93,414)` `#f1f9fd` | 6.55:1 |
| 图例，焦点上边与下边 | `(70,396)` 和 `(70,447)` `#1475b9` | `(70,392)` 和 `(70,451)` `#f1f9fd` | 4.61:1 |
| 实体抽屉，关闭按钮焦点上边 | `(275,9)` `#095e9f` | `(275,7)` `#dbeefa` | 5.66:1 |
| 实体抽屉，常规导航文字 | `(82,121)` `#405f75` | `(82,116)` `#dbeefa` | 5.66:1 |

320 px 图例焦点上边在 `x=70` 处覆盖 `y=395–397` 共 3 个原图像素。实体抽屉关闭按钮上边在 `x=275` 处覆盖 `y=9–10` 共 2 个原图像素。样本包含两种滚动位置的静态图。动态滚动时的最亮或最暗合成背景、玻璃抽屉未收录的表面和焦点面积仍待专项测量。

覆盖范围限列出的 F 场景、尺寸和 Chromium。原生 200% 页面缩放下，列表代表字形的设备像素高度由 16 增至 31。独立的 200% 文字缩放和真实屏幕阅读器走查均为 **NOT RUN**。当前环心证据覆盖 F 全国总览的两个覆盖卡片。正式地图、真实数据源运行、全页无障碍符合性、实体触屏与其他浏览器引擎仍需独立证据。

### 文件摘要

```text
c96c9039c4168021861db7a4163fbcb72db5cf32bc8aca254c68c176fb40068e  governance-window-diagnostics.png
2eb5b0c67456b62c2dc170a9c3f1389509f3b3e662d5cf73c2ad68d360e7236a  navigation-opaque-drawer-768.png
50fd0a518b6df47aba6d71d8ebe657c5497b7429abbb3de2c0d9965fac3dd0e2  observation-station-390.png
844a039844a0fdf2cad4120451b9ba7de7e5381520958d2033f24bf048db0827  overview-aqi-focus-320.png
6a09cc3812fdcbb503a9477cf8b3747140e9b3f9a5a19033955cf8af97cf79bc  overview-current-1440.png
2a1412db8c5df7554f82d07578f37a4368d660d042b169b1afd6323c771e8d82  overview-desktop-1440.png
db1452450826d3053bce99de827925cda2872339771c0f05b048957303dacfbd  overview-native-zoom-400.png
8cbf7b001d685dcbaa0f5bfa20437be252b69c04933c0cd5712f1d60cc539520  overview-ratios-font-pending-390.png
1494138b27c642008d7c2b71c58fd81463ce216a2bfc55a015d28d088f3fd4c8  query-calendar-solid-320.png
152951549d2ec6aaaad571a218ac71fa92720d7ad4bf8eb1c1dac6b8f025d1be  research-pearson-24-pairs.png
```
