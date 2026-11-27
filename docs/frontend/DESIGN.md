---
version: alpha
name: 城市空气质量监测数据分析平台 · Clear Sky Glass（晴空玻璃）
description: 浅色、玻璃材质、高信息密度的数据产品前端规范。浅色主题的原始值、语义角色和组件规格取自本 YAML。布局、材质、动效和图表的组合取自正文规范块。
x-aq:
  primitives:
    color:
      hex-0865b8: &aq-hex-0865b8 "#0865b8"
      hex-ffffff: &aq-hex-ffffff "#ffffff"
      hex-075aa4: &aq-hex-075aa4 "#075aa4"
      hex-074e91: &aq-hex-074e91 "#074e91"
      hex-f5fcff: &aq-hex-f5fcff "#f5fcff"
      hex-1674c5: &aq-hex-1674c5 "#1674c5"
      hex-d7e9f6: &aq-hex-d7e9f6 "#d7e9f6"
      hex-124f80: &aq-hex-124f80 "#124f80"
      hex-142f47: &aq-hex-142f47 "#142f47"
      hex-405f75: &aq-hex-405f75 "#405f75"
      hex-48667a: &aq-hex-48667a "#48667a"
      hex-d0e6f4: &aq-hex-d0e6f4 "#d0e6f4"
      hex-b2d8ed: &aq-hex-b2d8ed "#b2d8ed"
      hex-b9d9ee: &aq-hex-b9d9ee "#b9d9ee"
      hex-d8eaf5: &aq-hex-d8eaf5 "#d8eaf5"
      hex-f2fcff: &aq-hex-f2fcff "#f2fcff"
      hex-93c8eb: &aq-hex-93c8eb "#93c8eb"
      hex-e6f0f7: &aq-hex-e6f0f7 "#e6f0f7"
      hex-dceaf5: &aq-hex-dceaf5 "#dceaf5"
      hex-f8fcff: &aq-hex-f8fcff "#f8fcff"
      hex-eff6fb: &aq-hex-eff6fb "#eff6fb"
      hex-dbeefa: &aq-hex-dbeefa "#dbeefa"
      hex-eff7fc: &aq-hex-eff7fc "#eff7fc"
      hex-f1f9fc: &aq-hex-f1f9fc "#f1f9fc"
      hex-dfedf5: &aq-hex-dfedf5 "#dfedf5"
      hex-f5faff: &aq-hex-f5faff "#f5faff"
      hex-eaf3fa: &aq-hex-eaf3fa "#eaf3fa"
      rgba-210-232-248-64: &aq-rgba-210-232-248-64 "rgb(210 232 248 / 64%)"
      rgba-242-250-255-65: &aq-rgba-242-250-255-65 "rgb(242 250 255 / 65%)"
      hex-f5fafff2: &aq-hex-f5fafff2 "#f5fafff2"
      hex-e7f2faf0: &aq-hex-e7f2faf0 "#e7f2faf0"
      hex-f6fbffdc: &aq-hex-f6fbffdc "#f6fbffdc"
      hex-e8f3fbe3: &aq-hex-e8f3fbe3 "#e8f3fbe3"
      hex-ffffffbc: &aq-hex-ffffffbc "#ffffffbc"
      hex-e9f5fe9c: &aq-hex-e9f5fe9c "#e9f5fe9c"
      rgba-83-125-153-36: &aq-rgba-83-125-153-36 "rgb(83 125 153 / 36%)"
      rgba-96-139-168-27: &aq-rgba-96-139-168-27 "rgb(96 139 168 / 27%)"
      rgba-22-54-80-24: &aq-rgba-22-54-80-24 "rgb(22 54 80 / 24%)"
      hex-21465e: &aq-hex-21465e "#21465e"
      rgba-61-102-134-17: &aq-rgba-61-102-134-17 "rgb(61 102 134 / 17%)"
      hex-acc6d8: &aq-hex-acc6d8 "#acc6d8"
      hex-54768e: &aq-hex-54768e "#54768e"
      hex-095e9f: &aq-hex-095e9f "#095e9f"
      hex-126a50: &aq-hex-126a50 "#126a50"
      hex-e4f3ea: &aq-hex-e4f3ea "#e4f3ea"
      hex-825118: &aq-hex-825118 "#825118"
      hex-f6efda: &aq-hex-f6efda "#f6efda"
      hex-923739: &aq-hex-923739 "#923739"
      hex-f9e7e8: &aq-hex-f9e7e8 "#f9e7e8"
      hex-e6f3ff: &aq-hex-e6f3ff "#e6f3ff"
      hex-435f72: &aq-hex-435f72 "#435f72"
      hex-e4edf4: &aq-hex-e4edf4 "#e4edf4"
      hex-00e400: &aq-hex-00e400 "#00E400"
      hex-ffff00: &aq-hex-ffff00 "#FFFF00"
      hex-ff7e00: &aq-hex-ff7e00 "#FF7E00"
      hex-ff0000: &aq-hex-ff0000 "#FF0000"
      hex-99004c: &aq-hex-99004c "#99004C"
      hex-7e0023: &aq-hex-7e0023 "#7E0023"
      hex-196042: &aq-hex-196042 "#196042"
      hex-d7eddf: &aq-hex-d7eddf "#d7eddf"
      hex-6b521e: &aq-hex-6b521e "#6b521e"
      hex-f3eaca: &aq-hex-f3eaca "#f3eaca"
      hex-81461e: &aq-hex-81461e "#81461e"
      hex-fae2c8: &aq-hex-fae2c8 "#fae2c8"
      hex-943b40: &aq-hex-943b40 "#943b40"
      hex-f6dcdc: &aq-hex-f6dcdc "#f6dcdc"
      hex-79264f: &aq-hex-79264f "#79264f"
      hex-edd9e5: &aq-hex-edd9e5 "#edd9e5"
      hex-6a293c: &aq-hex-6a293c "#6a293c"
      hex-e9d3da: &aq-hex-e9d3da "#e9d3da"
      hex-1673bc: &aq-hex-1673bc "#1673bc"
      hex-087b76: &aq-hex-087b76 "#087b76"
      hex-84569d: &aq-hex-84569d "#84569d"
      hex-9a571e: &aq-hex-9a571e "#9a571e"
      hex-475e73: &aq-hex-475e73 "#475e73"
      hex-dceef7: &aq-hex-dceef7 "#dceef7"
      hex-85b9dd: &aq-hex-85b9dd "#85b9dd"
      hex-206fa8: &aq-hex-206fa8 "#206fa8"
      hex-3c5b72: &aq-hex-3c5b72 "#3c5b72"
      hex-cedeeb: &aq-hex-cedeeb "#cedeeb"
      hex-68859a: &aq-hex-68859a "#68859a"
      hex-19394f: &aq-hex-19394f "#19394f"
      hex-92b2c6: &aq-hex-92b2c6 "#92b2c6"
      hex-6591ac: &aq-hex-6591ac "#6591ac"
      hex-ffffffc9: &aq-hex-ffffffc9 "#ffffffc9"
      hex-c3d9e62e: &aq-hex-c3d9e62e "#c3d9e62e"
      hex-35618040: &aq-hex-35618040 "#35618040"
      hex-ffffffed: &aq-hex-ffffffed "#ffffffed"
      hex-ffffff63: &aq-hex-ffffff63 "#ffffff63"
      hex-23486365: &aq-hex-23486365 "#23486365"
      hex-193f5f70: &aq-hex-193f5f70 "#193f5f70"
      hex-ffffff40: &aq-hex-ffffff40 "#ffffff40"
      hex-b7dbf429: &aq-hex-b7dbf429 "#b7dbf429"
    dimension:
      2px: &aq-2px 2px
      4px: &aq-4px 4px
      5px: &aq-5px 5px
      6px: &aq-6px 6px
      6px-10px: &aq-6px-10px "6px 10px"
      8px: &aq-8px 8px
      10px: &aq-10px 10px
      12px: &aq-12px 12px
      13px: &aq-13px 13px
      14px: &aq-14px 14px
      15px: &aq-15px 15px
      16px: &aq-16px 16px
      18px: &aq-18px 18px
      20px: &aq-20px 20px
      22px: &aq-22px 22px
      24px: &aq-24px 24px
      28px: &aq-28px 28px
      32px: &aq-32px 32px
      34px: &aq-34px 34px
      36px: &aq-36px 36px
      40px: &aq-40px 40px
      44px: &aq-44px 44px
      46px: &aq-46px 46px
      64px: &aq-64px 64px
      280px: &aq-280px 280px
      472px: &aq-472px 472px
      999px: &aq-999px 999px
    font:
      ui-stack: &aq-font-stack "Inter, 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif"
      weight-400: &aq-weight-400 400
      weight-500: &aq-weight-500 500
      weight-600: &aq-weight-600 600
      weight-700: &aq-weight-700 700
  component-compositions:
    # [建议新增] 只在这三个色块加深边。填色和外框尺寸仍取 components。
    aqi-legend-outline:
      applies-to: [legend-swatch-aqi-1, legend-swatch-aqi-2, legend-swatch-aqi-3]
      color: "{components.point-outline-aqi.backgroundColor}"
      width: "{components.point-outline-aqi.size}"
colors:
  # —— 品牌与动作：只表示「可操作」或「正在选择」 ——
  primary: *aq-hex-0865b8            # [实测] 实心主动作；强选中渐变的深端
  on-primary: *aq-hex-ffffff
  primary-hover: *aq-hex-075aa4      # [实测]
  primary-pressed: *aq-hex-074e91    # [候选]
  link: *aq-hex-075aa4               # [实测]
  ghost-hover: *aq-hex-f5fcff        # [实测] 透明动作悬停底
  selected-start: *aq-hex-1674c5     # 强选中渐变浅端
  selected-soft: *aq-hex-d7e9f6      # [候选] 行/选项选中底，配勾或侧标
  on-selected-soft: *aq-hex-124f80
  # —— 文字 ——
  text-primary: *aq-hex-142f47       # [实测] 标题、正文
  text-secondary: *aq-hex-405f75     # [实测] 辅助正文
  text-muted: *aq-hex-48667a         # [候选] 低强调时间/单位，仅用于浅色稳定底
  # —— 画布（氛围）——
  canvas: *aq-hex-d0e6f4             # [实测] 天空中段；也是画布实体回退
  sky-1: *aq-hex-b2d8ed              # [实测] 123° 天空渐变起点
  sky-3: *aq-hex-b9d9ee              # [实测]
  sky-4: *aq-hex-d8eaf5              # [实测] 终点
  sky-glow: *aq-hex-f2fcff           # [实测] 左上径向亮区
  sky-deep: *aq-hex-93c8eb           # [实测] 右上径向亮区
  # —— 表面：实体值（保证无滤镜时可读）与玻璃值（含透明度，仅在 @supports 增强时使用）——
  surface-group: *aq-hex-e6f0f7      # [候选] 稳定分组
  surface-header: *aq-hex-dceaf5     # [实测] 表头
  surface-field: *aq-hex-f8fcff      # 输入框稳定底
  surface-content: *aq-hex-eff6fb    # [实测] 内容表面实体回退
  surface-nav: *aq-hex-dbeefa        # [实测] 导航实体回退
  surface-float: *aq-hex-eff7fc      # [实测] 工具/浮层/Tooltip 提示实体回退
  surface-map: *aq-hex-f1f9fc        # [实测] 地图底起点（终点 map-end）
  map-end: *aq-hex-dfedf5
  surface-table: *aq-hex-f5faff      # [实测] 长表底起点（终点 table-end）
  table-end: *aq-hex-eaf3fa
  glass-nav: *aq-rgba-210-232-248-64      # [实测]
  glass-tool: *aq-rgba-242-250-255-65     # [实测] 地图工具、临时浮层
  glass-content-start: *aq-hex-f5fafff2         # [实测] 内容面板 112° 起点
  glass-content-end: *aq-hex-e7f2faf0           # [实测]
  glass-kpi-start: *aq-hex-f6fbffdc             # [实测] KPI 108° 起点
  glass-kpi-end: *aq-hex-e8f3fbe3               # [实测]
  lens-start: *aq-hex-ffffffbc                  # [实测] 玻璃内按钮 138° 起点
  lens-end: *aq-hex-e9f5fe9c                    # [实测]
  edge-glass: *aq-rgba-83-125-153-36      # [实测] 功能玻璃 1px 边
  edge-content: *aq-rgba-96-139-168-27    # [实测] 内容表面 1px 边
  scrim: *aq-rgba-22-54-80-24             # [实测] 遮罩
  tooltip-bg: *aq-hex-21465e                    # 短文本 Tooltip 实底
  on-tooltip: *aq-hex-ffffff
  # —— 边、焦点 ——
  border-subtle: *aq-rgba-61-102-134-17   # [实测] 分隔；只作装饰
  border-field: *aq-hex-acc6d8                  # 装饰或已有可辨字段边界时使用
  border-strong: *aq-hex-54768e                 # 识别控件所需的边界
  focus: *aq-hex-095e9f                         # 键盘焦点外环
  # —— 系统语义（运行与质量），各配图形/文字，不单靠颜色 ——
  success: *aq-hex-126a50            # [候选]
  success-container: *aq-hex-e4f3ea
  warning: *aq-hex-825118
  warning-container: *aq-hex-f6efda
  danger: *aq-hex-923739             # 危险实心按钮用此色配白字
  danger-container: *aq-hex-f9e7e8
  info: *aq-hex-095e9f
  info-container: *aq-hex-e6f3ff
  unknown: *aq-hex-435f72            # 未知/受限/缺测共用，避免透露受限对象
  unknown-container: *aq-hex-e4edf4
  # —— AQI：等级填色见 HJ 633—2012、HJ 633—2026 规范性附录 A 表 A.1 ——
  aqi-1: *aq-hex-00e400
  aqi-2: *aq-hex-ffff00              # 良
  aqi-3: *aq-hex-ff7e00              # 轻度污染
  aqi-4: *aq-hex-ff0000              # 中度污染
  aqi-5: *aq-hex-99004c              # 重度污染
  aqi-6: *aq-hex-7e0023              # 严重污染
  aqi-outline: *aq-hex-405f75        # [候选] 亮色点浅底加深轮廓 1–2px
  aqi-1-text: *aq-hex-196042         # [实测] 标签字
  aqi-1-bg: *aq-hex-d7eddf
  aqi-2-text: *aq-hex-6b521e
  aqi-2-bg: *aq-hex-f3eaca
  aqi-3-text: *aq-hex-81461e
  aqi-3-bg: *aq-hex-fae2c8
  aqi-4-text: *aq-hex-943b40
  aqi-4-bg: *aq-hex-f6dcdc
  aqi-5-text: *aq-hex-79264f
  aqi-5-bg: *aq-hex-edd9e5
  aqi-6-text: *aq-hex-6a293c
  aqi-6-bg: *aq-hex-e9d3da
  # —— 图表 ——
  chart-1: *aq-hex-1673bc            # [实测] 首页主线
  chart-2: *aq-hex-087b76            # [候选] 分类比较，按稳定对象键分配
  chart-3: *aq-hex-84569d
  chart-4: *aq-hex-9a571e
  chart-5: *aq-hex-475e73
  chart-scale-low: *aq-hex-dceef7    # [候选] 有序覆盖/数量
  chart-scale-mid: *aq-hex-85b9dd
  chart-scale-high: *aq-hex-206fa8
  chart-axis-text: *aq-hex-3c5b72    # [实测]
  chart-grid: *aq-hex-cedeeb         # [实测] 1px
  chart-axis-line: *aq-hex-68859a    # [候选]
  chart-tip-text: *aq-hex-19394f     # [实测]
  chart-tip-border: *aq-hex-92b2c6   # [实测]
  chart-pointer: *aq-hex-6591ac      # 轴指示虚线；用于装饰时，重点仍由标记或文字表达
  # —— 玻璃边缘与投影；由 Elevation & Depth 的材质组合引用 ——
  highlight-panel-top: *aq-hex-ffffffc9
  highlight-panel-bottom: *aq-hex-c3d9e62e
  shadow-panel: *aq-hex-35618040
  highlight-float-top: *aq-hex-ffffffed
  highlight-float-inset: *aq-hex-ffffff63
  shadow-float-near: *aq-hex-23486365
  shadow-float-far: *aq-hex-193f5f70
  highlight-elevated-start: *aq-hex-ffffff40
  highlight-elevated-end: *aq-hex-b7dbf429
typography:
  # 字号以 1440 CSS px 视口和默认根字号为参考。单位转换与流式规则见 Typography。
  page-title:       # 每页一个 H1
    fontFamily: *aq-font-stack
    fontSize: *aq-28px
    fontWeight: *aq-weight-700             # 实测 760
    lineHeight: 1.28
  panel-title:      # 面板 H2
    fontFamily: *aq-font-stack
    fontSize: *aq-18px
    fontWeight: *aq-weight-700             # 实测 720
    lineHeight: 1.35
  group-title:      # 组内标题
    fontFamily: *aq-font-stack
    fontSize: *aq-15px
    fontWeight: *aq-weight-600
    lineHeight: 1.45
  metric-primary:   # 当前任务的核心读数
    fontFamily: *aq-font-stack
    fontSize: *aq-28px
    fontWeight: *aq-weight-700             # 实测 690
    lineHeight: 1.16
    fontFeature: "tnum"
  metric-popup:     # 城市弹窗主读数
    fontFamily: *aq-font-stack
    fontSize: *aq-46px
    fontWeight: *aq-weight-600             # 实测 665
    lineHeight: 1.1
    fontFeature: "tnum"
  metric-secondary: # 次级读数
    fontFamily: *aq-font-stack
    fontSize: *aq-20px
    fontWeight: *aq-weight-600
    lineHeight: 1.25
    fontFeature: "tnum"
  body:
    fontFamily: *aq-font-stack
    fontSize: *aq-14px
    fontWeight: *aq-weight-400
    lineHeight: 1.55
  ui:               # 按钮、标签、菜单项
    fontFamily: *aq-font-stack
    fontSize: *aq-14px
    fontWeight: *aq-weight-500
    lineHeight: 1.5
  data:             # 表格单元格、数据读数
    fontFamily: *aq-font-stack
    fontSize: *aq-13px
    fontWeight: *aq-weight-400
    lineHeight: 1.5
    fontFeature: "tnum"
  table-header:
    fontFamily: *aq-font-stack
    fontSize: *aq-12px
    fontWeight: *aq-weight-600
    lineHeight: 1.5
  caption:          # 来源、时间、单位、图例
    fontFamily: *aq-font-stack
    fontSize: *aq-12px
    fontWeight: *aq-weight-400
    lineHeight: 1.5
  chart-tick:       # 图表刻度；最小字号见 Typography
    fontFamily: *aq-font-stack
    fontSize: *aq-12px
    fontWeight: *aq-weight-400
    lineHeight: 1.45
rounded:
  nested: *aq-6px
  tooltip: *aq-8px
  control: *aq-10px       # 字段、按钮、工具
  menu: *aq-12px
  panel: *aq-16px
  sheet: *aq-20px         # 侧栏、对话框
  capsule: *aq-999px      # 连续分段、短身份标记、紧凑导航
spacing:
  xs: *aq-4px
  sm: *aq-8px
  md: *aq-12px
  lg: *aq-16px
  xl: *aq-20px
  xxl: *aq-24px
  xxxl: *aq-32px
components:
  app-canvas:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.text-primary}"
  nav-solid:
    backgroundColor: "{colors.surface-nav}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.sheet}"
    width: *aq-64px              # 收起宽度
  nav-item-selected:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.control}"
    typography: "{typography.ui}"
    height: *aq-44px
  surface-content:
    backgroundColor: "{colors.surface-content}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.panel}"
    padding: *aq-16px
  surface-group:
    backgroundColor: "{colors.surface-group}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.panel}"
  surface-float:
    backgroundColor: "{colors.surface-float}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.menu}"
    padding: *aq-12px
  table-header:
    backgroundColor: "{colors.surface-header}"
    textColor: "{colors.text-primary}"
    typography: "{typography.table-header}"
  table-row:
    backgroundColor: "{colors.surface-table}"
    textColor: "{colors.text-primary}"
    typography: "{typography.data}"
    height: *aq-40px             # 常规行；触屏命中要求见硬约束 7
  table-row-compact:
    typography: "{typography.data}"
    height: *aq-34px
  table-row-selected:
    backgroundColor: "{colors.selected-soft}"
    textColor: "{colors.on-selected-soft}"
    typography: "{typography.data}"
    height: *aq-40px
  map-surface:
    backgroundColor: "{colors.surface-map}"
    textColor: "{colors.text-primary}"
  map-surface-end:
    backgroundColor: "{colors.map-end}"
    textColor: "{colors.text-primary}"
  table-surface-end:
    backgroundColor: "{colors.table-end}"
    textColor: "{colors.text-primary}"
  metric-card:
    backgroundColor: "{colors.surface-content}"
    textColor: "{colors.text-primary}"
    typography: "{typography.metric-primary}"
    rounded: "{rounded.panel}"
    padding: *aq-16px
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.ui}"
    rounded: "{rounded.control}"
    height: *aq-36px             # 桌面默认；触屏命中要求见硬约束 7
    padding: "{spacing.lg}"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.on-primary}"
  button-primary-pressed:
    backgroundColor: "{colors.primary-pressed}"
    textColor: "{colors.on-primary}"
  button-secondary:
    backgroundColor: "{colors.surface-field}"
    textColor: "{colors.text-primary}"
    typography: "{typography.ui}"
    rounded: "{rounded.control}"
    height: *aq-36px
    padding: "{spacing.lg}"
  button-ghost-hover:
    backgroundColor: "{colors.ghost-hover}"
    textColor: "{colors.link}"
  button-danger:
    backgroundColor: "{colors.danger}"
    textColor: "{colors.on-primary}"
    typography: "{typography.ui}"
    rounded: "{rounded.control}"
    height: *aq-36px
    padding: "{spacing.lg}"
  icon-button:
    backgroundColor: "{colors.surface-field}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.control}"
    size: *aq-36px               # 桌面默认；触屏命中要求见硬约束 7
  input:
    backgroundColor: "{colors.surface-field}"
    textColor: "{colors.text-primary}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    height: *aq-36px
    padding: "{spacing.md}"
  date-cell:
    size: *aq-36px
  checkbox-radio-indicator:
    size: *aq-18px
  switch-track:
    width: *aq-36px
    height: *aq-22px
  icon-inline:
    size: *aq-16px
  icon-navigation:
    size: *aq-20px
  icon-supporting:
    size: *aq-14px
  select-menu:
    backgroundColor: "{colors.surface-float}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.menu}"
    padding: *aq-5px
  select-option:
    textColor: "{colors.text-primary}"
    typography: "{typography.ui}"
    height: *aq-34px
  select-option-selected:
    backgroundColor: "{colors.selected-soft}"
    textColor: "{colors.on-selected-soft}"
    typography: "{typography.ui}"
    height: *aq-34px
  segmented-item-selected:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.ui}"
    height: *aq-34px
  tooltip:
    backgroundColor: "{colors.tooltip-bg}"
    textColor: "{colors.on-tooltip}"
    typography: "{typography.caption}"
    rounded: "{rounded.tooltip}"
    padding: "{spacing.sm}"
  popover:
    backgroundColor: "{colors.surface-float}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.menu}"
    width: *aq-280px             # 默认宽度上限，受视口约束
  dialog:
    backgroundColor: "{colors.surface-float}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.sheet}"
    padding: *aq-22px
    width: *aq-472px
  chart-tooltip:
    backgroundColor: "{colors.surface-float}"
    textColor: "{colors.chart-tip-text}"
    typography: "{typography.caption}"
    rounded: "{rounded.tooltip}"
    padding: *aq-6px-10px       # CSS 上下/左右；ECharts 对应两项 padding
  tag-success:
    backgroundColor: "{colors.success-container}"
    textColor: "{colors.success}"
    typography: "{typography.caption}"
    rounded: "{rounded.nested}"
    height: *aq-22px
  tag-warning:
    backgroundColor: "{colors.warning-container}"
    textColor: "{colors.warning}"
    typography: "{typography.caption}"
    rounded: "{rounded.nested}"
    height: *aq-22px
  tag-danger:
    backgroundColor: "{colors.danger-container}"
    textColor: "{colors.danger}"
    typography: "{typography.caption}"
    rounded: "{rounded.nested}"
    height: *aq-22px
  tag-info:
    backgroundColor: "{colors.info-container}"
    textColor: "{colors.info}"
    typography: "{typography.caption}"
    rounded: "{rounded.nested}"
    height: *aq-22px
  tag-unknown:
    backgroundColor: "{colors.unknown-container}"
    textColor: "{colors.unknown}"
    typography: "{typography.caption}"
    rounded: "{rounded.nested}"
    height: *aq-22px
  tag-aqi-1:
    backgroundColor: "{colors.aqi-1-bg}"
    textColor: "{colors.aqi-1-text}"
    typography: "{typography.caption}"
    rounded: "{rounded.nested}"
    height: *aq-22px
  tag-aqi-2:
    backgroundColor: "{colors.aqi-2-bg}"
    textColor: "{colors.aqi-2-text}"
    typography: "{typography.caption}"
    rounded: "{rounded.nested}"
    height: *aq-22px
  tag-aqi-3:
    backgroundColor: "{colors.aqi-3-bg}"
    textColor: "{colors.aqi-3-text}"
    typography: "{typography.caption}"
    rounded: "{rounded.nested}"
    height: *aq-22px
  tag-aqi-4:
    backgroundColor: "{colors.aqi-4-bg}"
    textColor: "{colors.aqi-4-text}"
    typography: "{typography.caption}"
    rounded: "{rounded.nested}"
    height: *aq-22px
  tag-aqi-5:
    backgroundColor: "{colors.aqi-5-bg}"
    textColor: "{colors.aqi-5-text}"
    typography: "{typography.caption}"
    rounded: "{rounded.nested}"
    height: *aq-22px
  tag-aqi-6:
    backgroundColor: "{colors.aqi-6-bg}"
    textColor: "{colors.aqi-6-text}"
    typography: "{typography.caption}"
    rounded: "{rounded.nested}"
    height: *aq-22px
  legend-swatch-aqi-1:
    backgroundColor: "{colors.aqi-1}"
    size: *aq-12px
  legend-swatch-aqi-2:
    backgroundColor: "{colors.aqi-2}"
    size: *aq-12px
  legend-swatch-aqi-3:
    backgroundColor: "{colors.aqi-3}"
    size: *aq-12px
  legend-swatch-aqi-4:
    backgroundColor: "{colors.aqi-4}"
    size: *aq-12px
  legend-swatch-aqi-5:
    backgroundColor: "{colors.aqi-5}"
    size: *aq-12px
  legend-swatch-aqi-6:
    backgroundColor: "{colors.aqi-6}"
    size: *aq-12px
  point-outline-aqi:
    backgroundColor: "{colors.aqi-outline}"
    size: *aq-2px
  legend-swatch-chart-1:
    backgroundColor: "{colors.chart-1}"
    size: *aq-12px
  legend-swatch-chart-2:
    backgroundColor: "{colors.chart-2}"
    size: *aq-12px
  legend-swatch-chart-3:
    backgroundColor: "{colors.chart-3}"
    size: *aq-12px
  legend-swatch-chart-4:
    backgroundColor: "{colors.chart-4}"
    size: *aq-12px
  legend-swatch-chart-5:
    backgroundColor: "{colors.chart-5}"
    size: *aq-12px
  scale-swatch-low:
    backgroundColor: "{colors.chart-scale-low}"
    size: *aq-12px
  scale-swatch-mid:
    backgroundColor: "{colors.chart-scale-mid}"
    size: *aq-12px
  scale-swatch-high:
    backgroundColor: "{colors.chart-scale-high}"
    size: *aq-12px
  caption-on-canvas:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.text-secondary}"
    typography: "{typography.caption}"
  caption-on-content:
    backgroundColor: "{colors.surface-content}"
    textColor: "{colors.text-secondary}"
    typography: "{typography.caption}"
  caption-on-group:
    backgroundColor: "{colors.surface-group}"
    textColor: "{colors.text-secondary}"
    typography: "{typography.caption}"
  muted-on-content:
    backgroundColor: "{colors.surface-content}"
    textColor: "{colors.text-muted}"
    typography: "{typography.caption}"
  muted-on-group:
    backgroundColor: "{colors.surface-group}"
    textColor: "{colors.text-muted}"
    typography: "{typography.caption}"
  link-on-content:
    backgroundColor: "{colors.surface-content}"
    textColor: "{colors.link}"
    typography: "{typography.body}"
  chart-axis-label:
    backgroundColor: "{colors.surface-content}"
    textColor: "{colors.chart-axis-text}"
    typography: "{typography.chart-tick}"
  nav-item-selected-gradient-start:
    backgroundColor: "{colors.selected-start}"
    textColor: "{colors.on-primary}"
    typography: "{typography.ui}"
  focus-ring:
    backgroundColor: "{colors.focus}"
    size: *aq-2px
---

# 城市空气质量监测数据分析平台 · 前端设计基线

**S08 正式审计版 · 2026-11-27**。本次定稿只更新设计规范，不表示前端整改或 S09 正式联调已经完成。

## Overview

独立 S08 仓库以已认可的合成 F 全国总览首页为起点。用户从观测时刻和覆盖范围下钻到城市、站点与研究明细，管理员从任务窗口、质量事件追到授权处置。[页面需求](docs/PAGE_REQUIREMENTS.md)负责页面范围和任务流程，[业务规则](docs/BUSINESS_RULES.md)负责数据含义和授权条件，[接口契约](docs/API_CONTRACT.md)负责字段、响应和请求能力。

首页 CSS、图表选项与通用图表容器是复用入口。素材及许可见[素材清单](docs/ASSET_RIGHTS.md)。[首页样式](src/national/sky.css) · [基础字体与排版](src/style.css) · [图表容器](src/shared/ChartView.vue)。这些相对路径在项目内使用，文件与落地版本见 Verification 的待验证事项。

### 如何使用本文（Agent 必读）

1. **确定变更范围。** 视觉与组件读 Overview 至 Do's and Don'ts。筛选、选择、结果和页面流转读 Interaction Patterns 与 Page Patterns。图表和地图另读 Data Visualization。交付前按 Verification 选取受影响场景。审计优先保留现有页面已认可的视觉，先核公共层，再核各页面及其特定变体；发现规范冲突先登记 `CONFLICT`，不得直接按其中一条重做页面。
2. **核对规则归属。** 页面需求定义范围和流程，业务规则定义数据口径与授权，接口契约定义请求、字段和响应。本文定义视觉与交互呈现，优先于原型样式。发现冲突时，列出具体条款及受影响的行为，交由对应维护方确认。标为「待验证·契约」的内容只用于呈现设计和 Mock，正式联调需要项目契约及样本。
3. **解析并复用。** 解析 YAML 后先取组件规格，再取共享语义及对应正文组合。修改同一职责的值时更新其正式来源。保留原稿语义键，以同一解析结果生成 CSS、Element Plus 与 ECharts 适配产物。若仓库已有主题入口，先核对实际实现与版本。
4. **交付证据。** 先做规范 lint、diff 与主题检查，再构建和验收实际界面。验收记录列明环境、样本、结果及未覆盖项。变更的决策权限见下表。
5. **新增组件时先选职责及变体。** 优先组合既有材质、状态、尺寸与反馈。需要新增 token 或更改公用规则时按 Maintenance 记录影响与迁移。

### 状态标记

| 标记 | 使用方式 |
|---|---|
| 已确认 | 用户已明确采用的视觉方向、页面职责、交互约束和正式取舍，含本次 S08-01 至 S08-05。表示规范地位，实施情况单独记录。 |
| 确认的例外 | 有条款编号、限定页面或组件、适用条件和验收方法的正式变体。适用范围内依例外验收，其余仍用通用规则。 |
| 实测记录 | 原稿的 `[实测`] 或历史原型观测值，测量日期为 2026-11-24。项目中的[桌面截图](docs/verification/1440x900.png)和[窄屏截图](docs/verification/390x844.png)与构建的对应关系待验证·浏览器。 |
| 已定待实现 | 原稿规定的阶段性工作，例如真实会话、跨页下钻、撤权清理。S08 用 Mock 验界面，S09 用真实后端验业务。 |
| 候选 | 原稿列出的候选默认值，按所列规格起步。微调按决策权表记录。 |
| 建议新增 | 本版补足的设计规则、工程输出或验收流程。表内默认值用于首次实现和评审，标为候选的增强以通过实测为启用条件。涉及已确认项的实质变化仍按「询问」处理。 |
| 待验证·契约 | 缺少可定位的项目版本、正式字段或样例、拥有者或集成证据。 |
| 待验证·浏览器 | 缺少可复现的实际渲染、行为或辅助技术证据。 |

**审计记录分三个维度。** 规则状态为已确认／候选／确认的例外；实现状态为已实现／部分实现／未实现（未核实则标明未知）；验收状态为 `PASS`／`FAIL`／`NOT RUN`／`N/A`。部分实现不得直接判为通过。缺少运行证据标 `NOT RUN`，不以截图或自述代替。规则间有冲突时标 `CONFLICT` 并列出双方条款，待定稿后再按正式规则验收。

### 本次定稿的规则索引

| 编号 | 正式取舍 | 规范位置 |
|---|---|---|
| S08-01 | F 成功态三项摘要，窄屏默认同排 | Overview、Layout、Page Patterns（M01） |
| S08-02 | 玻璃外壳与稳定阅读面分工；SYS-403／404 单层玻璃主卡例外 | 硬约束 2–4、Elevation & Depth、Page Patterns |
| S08-03 | 按列职责对齐；M04／M05 主统计表与 M08 桌面主账本列内居中例外 | 硬约束 10、Typography、Components、Page Patterns |
| S08-04 | 日期与时间统一视觉语言，区分四种输入语义 | Components、Elevation & Depth、Page Patterns |
| S08-05 | 位移与淡入、短颜色反馈、局部密码区折叠及单站历史切换 | 硬约束 14、Motion、Page Patterns |
| S09-01 | 同刻列表为正式首页基本联调路径；正式地图与全国趋势分项验收 | Page Patterns、Data Visualization、Verification |
| S09-02 | 从已有获权 Job 或登记模板起步；独立新建无计划 Job 并挂首个计划非阻塞 | Page Patterns、Verification |

### Token 三层与取值边界

| 层级 | YAML 位置及引用方向 | 消费者 |
|---|---|---|
| Primitive 原始值 | `x-aq.primitives` 存 sRGB（含 alpha）、尺寸、字体栈和字重的原始字面量。使用 YAML 锚点在文档内复用。两个角色即便当前同值，仍可各自改绑。 | Semantic、组件专用尺寸。界面和适配器读取解析后的角色。 |
| Semantic 语义 | `colors`、`typography`、`rounded`、`spacing` 保留原语义路径。颜色及基础尺寸经 YAML 别名解析，字阶组合定义字重、行高和数字特性。 | Component、应用壳、可视化适配和共享样式。 |
| Component 组件 | `components` 定义可复用默认规格。`x-aq.component-compositions` 承载核心格式缺少的组合，如 AQI 色块轮廓。`{colors.primary}` 这类引用由项目解析器解引用，组合可引用组件字段。组件专用尺寸可直接使用 Primitive 别名。 | 组件样式、主题适配、验收。 |

**数值来源。** YAML 定义可复用值。Typography、Layout、Elevation & Depth、Motion 和 Data Visualization 中的代码块定义跨角色的公式、组合及行为参数。正文表格用路径或角色名引用，历史观测值、业务区间和验收阈值有独立用途。组件变体按 Components 的字段继承规则处理。

**格式边界。** 采用 Google Labs DESIGN.md alpha 的 YAML 前置区、原有核心章节及引用语法。`x-aq` 是项目扩展，锁定的 CLI 可读取 YAML 锚点解析后的核心值，项目校验须另查扩展。参考 [Google Labs DESIGN.md 格式](https://github.com/google-labs-code/design.md/blob/main/docs/spec.md)与 [Design Tokens Community Group Format Module](https://www.w3.org/community/reports/design-tokens/CG-FINAL-format-20251028/)的类型、别名和复合值设计。

### 决策权

| 标签 | 含义 | 适用范围 |
|---|---|---|
| `[自主]` | 直接采用默认值，或决定不影响外观、交互及契约的内部实现。 | 默认组件高度、间距、圆角及可复用实现。默认值的例外需有对应条款授权。 |
| `[记录]` | 可按实机微调，须在同一补丁写明原值、新值、取舍和实测范围。 | 未改变已确认方向的透明度、模糊、高光、投影、动效时长、字阶、非首页构图。新增分发资源须核许可、按许可署名并登记。 |
| `[询问]` | 获用户确认后实施。 | 视觉方向、正式品牌名、工作区入口、主流程、页面职责、功能范围及其他已确认项发生实质变化；改变业务规则或接口须同时走对应项目审查。 |

### 视觉语言

**Clear Sky Glass · 晴空玻璃。** 开页先看清「哪个对象、哪个时刻、测到了什么」，再看分布、趋势和证据。浅蓝光从静止的天空背景透过工具表面，数据落在安静的云白底上。用户端以地图或查询结果为视觉中心，管理端把空间留给账本、证据和动作。两端共用一套颜色、字阶和焦点语言。

保留首页的不等宽地图构图、轻盈侧栏、玻璃内容面板与较高信息密度。首版采用浅色。冷灰 SaaS、大屏霓虹、重反射、全站风景照片、营销式巨幅标题及用图片代替控件均排除在当前方向之外。

**展示优先（已确认）。** 本项目以毕业设计展示和视觉完成度为首要设计目标。在满足数据真实性、授权边界、基本可读性、可达性及本规范硬约束的前提下，优先选择更接近 Apple 式材质层次、光学细节和连续反馈的方案；不能仅因实现简单、组件库默认样式、代码行数、抽象复用或未经测量的性能顾虑而弱化已认可的玻璃、渐变、高光、阴影、圆角、动效和页面构图。性能优化优先采用减少重复渲染、复用共享实现等不损害正常视觉的方式；有实测瓶颈时按决策权调整局部实现，并用同场景前后截图及运行证据验证。不能以展示效果为理由伪造正式数据、绕过权限或取消必要回退。

**首页视觉参照（S08-01）。** 以当前认可的 F 成功态为展示基线，对照天空底色、侧栏、三项摘要、地图与榜单比例、控件浮层和通栏表。页首单独标明观测时刻、时区、产品及 F 数据身份；覆盖城市数归覆盖区。较新[首页对照](docs/verification/m07-home-comparison-1440x900.png)及源码为本次三项构图依据；[早期桌面](docs/verification/1440x900.png)与[早期窄屏](docs/verification/390x844.png)中的五项摘要只作历史追溯，不再作为整改目标。F 非成功态及正式 L 域分别验收，不得以正式接口缺项为由删减 F 的完整展示。其他模块按 Page Patterns 登记构图规则、实装记录及验收状态。原型中的「澄空观测」仍是候选品牌名。截图与源版本对应关系按实际证据确认。

**材质可以实测后微调。** 保留既定的角色和颜色体系。按 `[记录]` 微调透明度、模糊、高光和投影，用真实浏览器验证功能层的分离与数据读数的稳定性。实体回退沿用同一套色义和层次。

#### Apple 参考的取舍

Apple 对功能层和内容层的区分指导本项目的材质分工。以尽可能接近 Apple 的视觉质感和交互连续性为目标，Web 使用填充、边缘光、高光、投影、有限模糊及可中断动效实现层次；这是本项目的 Web 近似方案，不宣称原生 Liquid Glass 等价。原生实时折射、环境采样与形变不属于本次已确认实现或强制审计范围；若以后增加相关视觉增强，按决策权单独评审。

| 采纳 | 有意偏离（不要因「更像 Apple」而改回去） |
|---|---|
| 功能玻璃用于导航、工具和浮层；SYS-403／404 主卡按 S08-02 例外；数据使用稳定的阅读面 | Web 通过填充、边、投影和有限模糊表现层次，内容卡外壳可保留无滤镜的云白装饰 |
| 实体回退先行，减少透明度、减少动效和高对比均有对应反馈 | 保留数据产品的较高信息密度与紧凑标题 |
| 触屏主要操作采用项目 44×44 CSS px 目标，键盘焦点始终可见 | 用 Inter 和 Noto Sans SC 字体栈，按各平台回退 |
| 按压、选中、展开反馈即时且可中断 | AQI 填色按适用国家标准，操作、信息、焦点与序列蓝分别按语义使用 |

参考 [Apple Materials](https://developer.apple.com/design/human-interface-guidelines/materials)、[Apple Color](https://developer.apple.com/design/human-interface-guidelines/color) 和 [Apple Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility)。Apple 的触屏尺寸以平台 pt 计，本文的尺寸以 Web CSS px 计。

### 硬约束

以下要求随补丁的影响范围验证。表中证据为每项的验收依据，涉及契约或实测的证据尚未取得时在记录中标明 `NOT RUN`。已验证的违规项须在相应整改任务中修正后交付；本次纯文档定稿不把已有代码缺口改记为通过。

| 编号 | 约束 | 验收证据 |
|---|---|---|
| 1 | **MUST** 组件消费共享语义 token，通用颜色、尺寸、阴影和动效不在组件内另行定义。 | 检查主题映射和变更代码。 |
| 2 | **MUST**（S08-02）功能玻璃用于导航、工具、筛选、日期、抽屉、账号面板及临时浮层的规定外壳；SYS-403／404 主卡是独立单层滤镜例外。常规内容卡和 KPI 可使用无滤镜的半透明云白外壳。必要读数与绘图须验证合成对比度，未达标时给相应区域稳定底。长表、数据行、地图点位及绘图区、密集日期网格使用稳定阅读面，长表单与长清单的文字和控件有稳定填充。 | 视图分层、样式与浏览器采样。 |
| 3 | **MUST**（S08-02）实体主题独立可用，以 `@supports` 启用增强。强制色、高对比、减少透明度、显式实体模式、无滤镜支持及已验证需降级的配置按材质回退优先级关闭滤镜和透明装饰，覆盖所有应用壳、浮层、内容卡、KPI、镜片及 SYS-403／404 主卡；边界与焦点仍可辨。 | 浏览器偏好、显式模式与无滤镜分支。 |
| 4 | **MUST**（S08-02）每个可见玻璃工具组、应用浮层或获准的状态页主卡至多由其选定外壳模糊，内部控件无滤镜，按材质表使用稳定或光面填充。 | 样式与实际 DOM。 |
| 5 | **MUST** 普通文字 ≥4.5∶1，大字 ≥3∶1，适用的必要控件与图形线索 ≥3∶1。等级、选中、缺测和质量状态有颜色外线索。 | 静态计算、实际合成采样、状态走查。 |
| 6 | **MUST** 选中有勾、当前项标记或选中线。焦点环取共享尺寸和间隔，不改变控件布局，鼠标移开不会取消键盘焦点。 | 键盘走查，检查有无裁切、遮挡及相邻颜色对比。 |
| 7 | **MUST** 触屏主要操作目标 ≥44×44 CSS px，其余目标 ≥24×24 CSS px，或满足 WCAG 2.5.8 适用例外。紧凑视觉尺寸仍须满足命中要求。 | 测量实际命中区，记录采用的例外及依据。 |
| 8 | **MUST** 按后端已确认的系列和等级映射 AQI，图例可读等级名称、区间及系列。未知或待核状态按 Colors 呈现。**MUST NOT** 用浏览器阈值重分级。 | 契约、合法样本与异常样本。 |
| 9 | **MUST** 区分真实浓度零、缺测和删失，缺测处断线，不插补。 | 测试样本的图表、明细和可读说明。 |
| 10 | **MUST**（S08-03）数字用等宽数字，同列精度、单位位置与对齐边一致。默认对象和长文本靠起始边，可比较数值靠结束边；短状态与定宽操作可居中。仅 M04／M05 三个模式的主统计表与 M08 四个桌面主账本可按 Components 的确认例外列内居中。 | 表格、读数和实际字体样式。 |
| 11 | **MUST** 观测时刻注明日期和产品时区，逐指标最新值分别标时，采集与任务执行时间各自标注。日产品按契约展示源报告日期。 | 契约与跨日数据样本。 |
| 12 | **MUST** 全国总览通过一个稳定城市 ID 同步当前可用的地图、榜单、表格、详情及获准的 URL 状态。悬停只预览，焦点独立。F 完整展示必须覆盖所有入口；正式地图独立验收不取消已可用入口的同步义务。 | 多入口选择、翻页、下钻、返回及复位。 |
| 13 | **MUST** 撤权、切换数据域或不兼容产品时清理对应私有状态、缓存和 URL，取消在途请求并忽略迟到响应。 | 会话、条件与响应交错的用例。 |
| 14 | **MUST**（S08-05）位移和淡入显式声明 `transform`／`opacity`，可中断；按钮、选项和表行允许 Motion 规定的短颜色过渡。阴影、滤镜和布局属性默认即时变化，仅账号密码区登记局部布局过渡例外。单站历史同批记录整表切换按 Motion 执行。ECharts 数据插值遵守独立条件，减少动效时取消上述过渡、保留即时反馈。 | 检查过渡属性、图表配置及偏好行为。 |
| 15 | **MUST** 应用浮层使用同一叠放管理器，局部图面提示仅使用局部层级。**MUST NOT** 用任意大的 `z-index` 回避挂载或裁切问题。 | DOM、挂载点、遮罩和叠放测试。 |
| 16 | **MUST** CSS、Element Plus 和 ECharts 消费同一颜色源，修改后运行锁定版 lint 门禁。 | 生成产物与 lint JSON。 |
| 17 | **MUST** 分发字体、图标和素材有许可记录，按许可要求署名。 | 素材清单与许可。 |
| 18 | **MUST** 把已确认的浅色、静态天空、稳定读数面和既有首页构图作为视觉评审基准。按展示优先原则保护已认可的材质、层次、构图和交互质感，不因机械统一而抹平。 | 受影响视图的同尺寸截图与已确认基线并列，由设计或产品负责人判定。 |
| 19 | **MUST** 一个任务区只突出一个主动作。任务区指围绕同一目的的页面主体、表单或弹层；同一组的独立次要动作降低强调。危险动作带动词，且不会随默认回车提交。 | 操作区走查和回车测试。 |
| 20 | **MUST** 在 Dialog 中验证 Select 与 Date Picker 的叠放、可达性、按层 Esc 和焦点归还。 | 真实 DOM、可访问树及键盘记录。 |

## Colors

**四套色义各自维护。** 品牌蓝指可操作或正在选择，系统色指运行和质量，序列色编码数据，AQI 色表达**已核标准**下的等级。蓝色折线的变化不表达质量好坏。蓝色在本项目里承担多种角色（`primary`、`link`、`info`、`focus`、`chart-1`），不能靠色相区分，必须同时配图形、文字或位置：选中配勾或侧标，信息配图标与文字，序列配名称与线型。

下列 token 名是**角色名**；值只在 YAML。组件优先消费组件角色，共享场景消费语义角色。

| 角色 | token | 用法与状态 |
|---|---|---|
| 画布与氛围 | `canvas`、`sky-1`、`sky-3`、`sky-4`、`sky-glow`、`sky-deep` | 线性底和两个径向亮区见 Elevation & Depth。普通辅助文字直接落在画布时按底色验收，最深区域使用达标文字色或稳定底。 |
| 稳定表面 | `surface-group`（候选）、`surface-header`、`surface-field`、`surface-content` | 分组、表头、输入框与内容面板的实体底。玻璃的组件映射见 Elevation & Depth。 |
| 文字 | `text-primary`、`text-secondary`、`text-muted` | 标题与正文用 `text-primary`，辅助正文用 `text-secondary`。`text-muted`（候选）只用于低强调的时间或单位，且仅在浅色稳定底上。较低层级先调字重，再调颜色。 |
| 主动作 | `primary`、`primary-hover`、`primary-pressed`、`on-primary` | 实心主动作配白字。悬停用 `primary-hover`，按下用 `primary-pressed`（候选）。 |
| 链接与透明动作 | `link`、`ghost-hover` | 透明动作悬停用 `ghost-hover` 底与 `link` 字。正文链接须有颜色以外的线索（下划线）。 |
| 选中 | `selected-start → primary`、`selected-soft`、`on-selected-soft` | 强选中渐变见 Elevation & Depth。原型旧浅端 `#177bce` 配纯白约 4.41∶1，供测量追溯。新版仍须在实际文字位置取样。行与选项配勾或侧标。 |
| 边与焦点 | `border-subtle`、`border-field`、`border-strong`、`focus` | 分隔边和浅字段边可作装饰。输入框、搜索框、选择器等依赖边界识别时，默认用 `border-strong`。稳定浅底用深色焦点环，品牌实底可用白色内环，跨明暗表面采用双色环。尺寸和相邻色验收见 Elevation & Depth。 |
| 系统语义 | `success`、`warning`、`danger`、`info`、`unknown` 及各 `-container` | 各前景和背景配对，另有文字或图形。危险实心按钮用 `danger` 配 `on-primary`。未知、受限和缺测可共用中性底，名称和图形各自表达，受限态只使用获准的通用信息。 |

### AQI 色义

| 等级 | 优 · 1 | 良 · 2 | 轻度污染 · 3 | 中度污染 · 4 | 重度污染 · 5 | 严重污染 · 6 |
|---|---|---|---|---|---|---|
| 等级填色 | `aqi-1` | `aqi-2` | `aqi-3` | `aqi-4` | `aqi-5` | `aqi-6` |
| 标签字 / 浅底 | `aqi-1-text` / `aqi-1-bg` | `aqi-2-text` / `aqi-2-bg` | `aqi-3-text` / `aqi-3-bg` | `aqi-4-text` / `aqi-4-bg` | `aqi-5-text` / `aqi-5-bg` | `aqi-6-text` / `aqi-6-bg` |

填色依据 [HJ 633—2012](https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/jcffbz/201203/W020250407406362045613.pdf) 与 [HJ 633—2026](https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/jcffbz/202602/W020260225366493492011.pdf) 规范性附录 A 表 A.1，两版 RGB 一致。2012 一级为 0–50，2026 一级为 1–50，2026 的 AQI 定义域为 1–500。已核系列的静态图例可引用标准的完整区间，用于解释已确认等级；后端若提供对应元数据，按契约呈现。系列标识和历史产品的适用规则待验证·契约。

| 静态图例系列 | 优 | 良 | 轻度污染 | 中度污染 | 重度污染 | 严重污染 |
|---|---|---|---|---|---|---|
| HJ 633—2012 | 0–50 | 51–100 | 101–150 | 151–200 | 201–300 | >300 |
| HJ 633—2026 | 1–50 | 51–100 | 101–150 | 151–200 | 201–300 | >300（定义域上限 500） |

区间分别依据两版的表 2 和表 1。HJ 633—2026 自 2026-03-01 实施，HJ 633—2012 同日起废止。识别历史数据时仍以服务端核定系列为准，图例按系列展示；观察日期本身不能确定记录采用的系列。[生态环境部发布公告](https://www.mee.gov.cn/xxgk2018/xxgk/xxgk01/202602/t20260224_1144403.html)

| 输入状态 | 地图、详情、榜单及分布 |
|---|---|
| 已知系列，等级获后端确认 | 直接用等级映射到填色及名称，图例显示系列和区间。浅底标签显示等级文字。 |
| 系列未知或等级缺失 | 使用中性标记和已获准的源描述。未确认系列或等级的个体不以正式等级显示，也不参与要求已知标准或等级的排名呈现。 |
| 已知 2026 系列的源值为 0 或大于 500，或服务端明确标明值与等级冲突 | 保留获准的源值及待核状态，个体用中性标记。若同一响应同时提供正式等级或名次，将受影响呈现标为待核，保留合法的独立结果。字段和服务端裁决优先顺序待验证·契约。 |
| 汇总或榜单返回部分结果 | 汇总分组和分母由服务端给出。前端不按个体行增减计数或重排；若响应明确自相矛盾，标明受影响部分待核。具体响应和降级条件待验证·契约。 |

- 用等级名称或其他可辨标记补足点位颜色。浅底亮色点用 `aqi-outline` 及 `point-outline-aqi.size`，验证外轮廓对实际相邻底达到非文本对比。等级分布和图例边界见 Data Visualization。
- 源浓度的有效零值、缺测和删失各有独立表示，观测是否有效随服务端质量状态。AQI 等级只消费后端映射，前端校验值域用于呈现冲突，不能据值域推断等级。

## Typography

中文正文优先本地、有许可的 Noto Sans SC，拉丁字母和数字优先本地 Inter，回退顺序取 YAML 共享字体栈。系统字体可作为回退。分发中文字体时对子集及 `font-display` 做首屏检查，新增分发资源按决策权表登记。字体、许可和实际加载效果分别待验证·契约、待验证·浏览器。

字号参考值转换为 rem，根字号尊重用户设置。流式公式以默认根字号为 16 CSS px 时的桌面参考视口为锚点，以下角色中的 `--aq-font-…-size` 由 YAML 字号转换生成。视口比例项只调整桌面字号，字号范围和锚点随根字号缩放。

```css
--aq-type-reference-width: 90rem;
--aq-type-page-title: clamp(1.5625rem,
  calc(var(--aq-font-page-title-size) + (100vw - var(--aq-type-reference-width)) * .02), 2.1875rem);
--aq-type-panel-title: clamp(1rem,
  calc(var(--aq-font-panel-title-size) + (100vw - var(--aq-type-reference-width)) * .0123), 1.3125rem);
--aq-type-metric-primary: clamp(1.5rem,
  calc(var(--aq-font-metric-primary-size) + (100vw - var(--aq-type-reference-width)) * .0195), 2.125rem);
--aq-type-page-title-compact: 1.5rem;
--aq-type-required-min: .6875rem;
```

| token | 用法 |
|---|---|
| `page-title` | 每页一个 H1。单列布局用 `--aq-type-page-title-compact`。 |
| `panel-title`、`group-title` | 面板 H2 和组内标题。长标题可换行，工具移到独立列或下一行。 |
| `metric-primary`、`metric-popup`、`metric-secondary` | 任务主读数、城市弹窗主读数和次级读数。单位用 `caption` 或 `body`，与数值共享基线。 |
| `body`、`ui`、`data` | 正文、交互标签与数据文字；长说明每行约 32–42 个汉字。 |
| `table-header`、`caption`、`chart-tick` | 表头、来源/时间/单位/图例和图表刻度。必要信息遵守最小字阶，空间不足时优先减少刻度或换行。 |

数字字号及字重取所用角色。YAML `fontFeature: tnum` 映射为 CSS `font-variant-numeric: tabular-nums`。ECharts 若无法设置等宽数字，使用已核字体或渲染层的等效方案，保留表格和详情对齐。待验证·浏览器。可比较的表格数值默认靠结束边，同列精度、单位位置和对齐边一致；S08-03 的两处主表例外可列内居中，仍保持等宽数字和一致单位、精度。精度按来源或契约。

**图标。** 沿用 `@element-plus/icons-vue` 的轮廓、笔重和对齐方式。尺寸取 `icon-inline`、`icon-navigation`、`icon-supporting`。装饰图标对读屏隐藏。图标按钮需要独立的可访问名称，Tooltip 可补充说明。

**污染物与单位。** 污染物写作 PM₂.₅、PM₁₀、NO₂、SO₂、CO、O₃，浓度明确 µg/m³ 或 mg/m³，AQI 无量纲。数值和单位组成可随布局移动的短组。观测时间带日期和产品时区，逐指标最新值分别标时。采集或获取时间与任务执行时间各自标注，所用名称随契约字段。日报日期和时间字段语义待验证·契约。

**建议新增 · 数字排版。** 短读数的数值与单位同基线，间距用 `spacing.xs`，默认成组；可在极窄空间把单位放到下一行，仍作为读数的标签呈现。长值允许加宽列或重排整个读数组，保留符号、有效位数和精度，必要信息字号遵守 `--aq-type-required-min`。有来源依据的负值照实呈现，数量级和舍入规则由来源或契约决定。若视图使用简写，同时提供可达的原值。语言环境仅影响呈现格式，产品时区、原报告日期、单位及精度按原约定处理。

| 数据语义 | 默认呈现 |
|---|---|
| 质量获准且浓度为 `0` | 展示 `0` 和对应单位，折线落在零位。 |
| 已知缺测 | 可用「—」作为紧凑占位，有可读的「缺测」及可提供的原因，曲线断开。 |
| 删失，如获准源表示 `<3` | 保留比较符和可披露检测限，表格和图表说明明确「删失」。 |
| `null`、未知或受限 | 按契约解释并显示相应状态，保持披露边界。「应到未知」等业务专用文案见 Page Patterns。 |

## Layout

间距用 YAML 的 `spacing` 阶梯。首页的流式布局、抽屉和浮层限宽按以下参数。组件宽度和内距从 YAML 生成，视口留白允许随内容和安全区调节。

```css
--aq-page-margin: clamp(12px, 1vw, 20px);
--aq-grid-gap: clamp(10px, .9vw, 16px);
--aq-panel-pad-x: clamp(13px, 1.05vw, 21px);
--aq-panel-pad-y: clamp(12px, .85vw, 17px);
--aq-nav-expanded: clamp(190px, 14.6vw, 224px);
--aq-nav-drawer-width: min(305px, 85vw);
--aq-research-filters-width: 280px;
--aq-home-main-grid: 2.12fr 1fr 1.13fr;
--aq-home-secondary-grid: 1fr 1.47fr 1.04fr;
--aq-overlay-inset-inline: var(--aq-space-md);
--aq-overlay-inset-block: var(--aq-space-md);
--aq-anchor-gap: 6px;
--aq-tabs-inset: 2px;
```

Dialog、Popover 的目标宽度取各自 `components.*.width`，总宽度使用 `border-box`，上限为视口可用宽度减两侧间隔和安全区。对话框总高受动态视口、上下安全区和间隔限制，标题和操作区保持可达，需要时正文独立滚动。位置、翻转和限高也要覆盖软键盘展开。待验证·浏览器。

同一排共享标题槽、控件高度、读数基线和网格边缘。绝对定位用于地图坐标、局部覆盖层等有明确定位依据的位置，内容流使用正常布局。

| 空间 | 选用的构图 |
|---|---|
| 宽桌面全国总览 | 收起侧栏取 `nav-solid.width`，展开取 `--aq-nav-expanded`。F 成功态页首上下文与三项等宽摘要在前，主排为地图、排名、分布及提示，次排按对应网格排布，城市表通栏。正式模式依 S09-01 及获权投影自然重排。 |
| 其他宽屏页面 `[记录]` | 查询、治理账本使用可用宽度。研究可用默认条件栏加流式结果，也可顶部排列条件。其余按拥挤点折叠，详情保留读数和证据区。 |
| 常规与紧凑密度 | 常规用 `table-row`，治理账本紧凑用 `table-row-compact`。触屏有动作的行按硬约束 7 保证命中区。无法在紧凑行满足时提高该行或提供足够尺寸的独立操作入口。 |

**响应式断点**按下表叠加，窄屏规则仅覆盖对应项目。区间以 CSS 媒体查询表达，测试相邻宽度。原型阈值、触屏和文字放大后的拥挤情况待验证·浏览器。

| 宽度 | 行为 |
|---|---|
| ≥1650px | 复核可用空间，允许在既定流式字阶内增长。 |
| >1050px 且 ≤1250px | 收紧间距。 |
| ≤1050px | 侧栏转抽屉。地图和榜单先排两列，分布和提示进入下一行。 |
| >640px 且 ≤810px | 下排两列，覆盖摘要跨行。 |
| ≤640px | 主区单列，使用窄屏页标题。F 成功态三项 KPI 默认同排，筛选分行。390px、320px 默认字号下保留三列；文字放大或内容无法完整阅读时可重排，保持阅读顺序。 |
| ≤379px | 筛选重新排布，长文案及操作可换行。 |

**建议新增 · 中间宽度保底。** `1050 < width ≤ 1250` 先收紧既有间距。首页三列采用正文比例及项目初始内容宽度下限：地图 `340px`、榜单 `200px`、分布/提示列 `208px`。以工作区扣除实际侧栏、边距、内距和列间隙后的内容盒判断。使用 `minmax(下限, 既定比例)`；放不下三列或榜单对象、数值、单位及关键控件在正常换行下仍冲突，则将分布/提示移至下一行，地图与榜单保留前述最小宽度。下限和比例是响应式候选，文字放大优先保留可达内容，按下述两列或单列重排。

- `640 < width ≤ 1050` 保留原稿的侧栏抽屉与地图/榜单双列。若实际空间容不下两列，先将标题工具换行，再把地图和榜单按阅读顺序上下排。`640 < width ≤ 810` 的下排优先保留原两列及覆盖摘要跨行，放不下时顺序堆叠。
- 中间宽度 `1100`、`1152`、`1200` 分别验左栏收起、展开及长城市名。测试 `379`、`640`、`810`、`1050`、`1250`、`1650` 两侧及边界。宽度越过临界点时保留选城、已应用条件、可达焦点和工作区入口。
- 宽屏仍以地图总览及查询/治理账本使用可用宽度为基线。只对阅读性内容单独限宽，长说明沿用 Typography 的行长，Dialog 和 Popover 沿用其上限。工作区级最大宽度若需新增，按 `[记录]` 对比 `1650px` 与 `1920px` 后确定。

窄屏按阅读顺序排版。图例区间、来源、时区、单位、质量和覆盖有可达入口。地图及确需二维布局的整张数据表可局部二维滚动，单元格内容及表外操作应能重排。保留到关键对象列、详情操作和列表替代入口的键盘路径。

## Elevation & Depth

### 材质职责与变体

已确认的材质职责为 `atmosphere`、`functional`、`content` 和 `elevated`。建议新增的 Web 变体为 `glass-regular`、`glass-clear` 和 `glass-solid`。它们用有限的背景滤镜、填充和边缘表现层次，借鉴 Apple Liquid Glass 的功能层原则；原生实时折射、环境采样与形变不属于本次强制审计范围。[Apple HIG · Materials](https://developer.apple.com/design/human-interface-guidelines/materials)

| 变体 | 填充、边缘与滤镜 | 启用条件 |
|---|---|---|
| `glass-regular` | 按组件表选填充。`edge-glass`、`--aq-edge-width`、`--aq-shadow-float` 和 `--aq-filter-glass`。浮层可在非文字区域用 `--aq-elevated-edge`。 | 规定的功能外壳、浮层或 SYS-403／404 主卡。经实际背景验收后启用；长内容依下表使用稳定阅读面。 |
| `glass-clear` | `glass-tool`、`edge-glass`、`--aq-edge-width`、`--aq-shadow-float` 和候选 `--aq-filter-glass-clear`。 | 限地图上的短工具组。底图繁杂、外轮廓或状态识别不达标时，按组件表回退；工具组用稳定字段底承托短文字。 |
| `glass-solid` | 实体表面取对应组件规范，保留可辨的边、焦点和状态。允许静态普通投影辅助分层。 | 基础样式和强制回退。无背景采样、滤镜、透明装饰或透明光面按钮。 |

| 组件与层 | 基础样式 | 增强与选择规则 |
|---|---|---|
| 应用画布 · `atmosphere` | `app-canvas` ／ `canvas` | 静态天空渐变加径向亮区，无滤镜、不接收指针。 |
| 收展侧栏和窄屏导航抽屉 · `functional` | `nav-solid` ／ `surface-nav` | `glass-regular` + `glass-nav`。宽度、选中、导航状态沿用原规格。长滚动导航若达不到最差背景对比，采用对应实体底。 |
| 地图工具组 · `functional` | `surface-float`；内部字段用 `surface-field` | `glass-clear` + `glass-tool`；清透合成不合格时切 `glass-regular`，以 `surface-float` 调配增强填充；仍不合格则用工具实体底。工具之间不叠加滤镜，读数、文字和必要线索按实际位置验收。 |
| 内容面板、指标卡 · `content` | `surface-content` 或 `metric-card`；嵌套稳定分组用 `surface-group` | 无滤镜。外壳可用 `--aq-gradient-content` 或 `--aq-gradient-kpi` 和静态轻影。文字、读数与图表实际背景达标才可落在该层；否则在其区域使用稳定阅读面。 |
| 地图绘图区、表头、行、长表 · `content` | 地图用 `map-surface` 至 `map-surface-end` 的不透明渐变；表头用 `table-header`，普通行与长表用 `surface-table` 至 `table-end` 的不透明底 | 稳定底；点位、必要数据图形与表头使用独立可辨的边界，保留密度及选中规则。 |
| 短 Select、动作菜单、短 Popover · `elevated` | `select-menu` 或 `popover` 的 `surface-float` | `glass-regular`；同底增强填充见下方。先核选项文字、选中、键盘焦点和挂载，再启用。存在内部滚动、密集日期/选项格、多段长说明或连续复核数据时，内部文字、控件和网格采用稳定阅读面；外壳可使用规定玻璃变体。 |
| 城市详情浮卡 · `elevated` | `popover` ／ `surface-float`，读数用 `metric-popup` | 短浮卡外壳可用 `glass-regular`，必要读数和单位保持成组。密集内容或地图最差背景对比不足时采用稳定读数面或实体外壳。维持原有选城、预览和下钻行为。 |
| Dialog / Drawer · `elevated` | `dialog` 或适用的实体 `surface-float` | 短确认、任务抽屉和账号面板的外壳可用 `glass-regular`。长文、滚动表单、长账本和日期网格的文字、读数及控件采用稳定填充；长外壳按下方 alpha 范围并通过浏览器验收，不给内部再加滤镜。导航抽屉用侧栏映射。 |
| 日期及时间选择浮层 · `elevated`（S08-04） | 对应 `input`、`date-cell`、`surface-float`、`surface-field` | 筛选及日期外壳可使用规定玻璃变体；密集日历格、时间选项及长清单的文字和控件使用稳定阅读面，内部无滤镜。按实际挂载点验证回退。 |
| SYS-403／404 主卡 · 确认的例外（S08-02） | `surface-content` 实体底，边界、状态文字和操作可辨 | 短状态文案、恢复操作和静态插画共用单一 `glass-regular` 外壳，`@supports` 下使用 `--aq-filter-glass`；卡内控件无滤镜，所有偏好及无支持分支回退稳定背景。不得扩展为普通内容卡滤镜。 |
| 短 Tooltip、图表 Tooltip、Toast / Alert | `tooltip`、`chart-tooltip` 或适用的语义实体底 | 只读短提示和反馈默认实体。图表临时玻璃属于独立增强，按 `elevated` 验证，保持同一信息的键盘或明细路径。 |
| 功能玻璃内部按钮或分段项 | `surface-field` 或相应按钮规格 | 非强调项可用 `--aq-gradient-lens` 提供光面。强选中用既定渐变或实心主色，配勾、标记或选中线。所有内部控件均使用无滤镜填充。 |

### 取值（正式来源）

颜色、透明色及默认组件规格来自 YAML。以下组合由共享主题承载。`--aq-filter-glass-clear` 和滚动边缘参数为建议新增候选。

```css
--aq-edge-width: 1px;
--aq-focus-offset: 2px;
--aq-shadow-panel: inset 0 1px var(--aq-color-highlight-panel-top),
                   inset 0 -1px var(--aq-color-highlight-panel-bottom),
                   0 2px 4px -3px var(--aq-color-shadow-panel);
--aq-shadow-float: inset 0 1px 1px var(--aq-color-highlight-float-top),
                   inset 0 0 0 1px var(--aq-color-highlight-float-inset),
                   0 3px 9px -5px var(--aq-color-shadow-float-near),
                   0 14px 36px -16px var(--aq-color-shadow-float-far);
--aq-filter-glass: blur(18px) saturate(1.24);
--aq-filter-glass-clear: blur(12px) saturate(1.14);
--aq-scroll-edge-size: var(--aq-space-md);
--aq-long-overlay-alpha-min: 82%;
--aq-long-overlay-alpha-max: 94%;
--aq-elevated-edge: linear-gradient(135deg, var(--aq-color-highlight-elevated-start),
                   transparent 48%, var(--aq-color-highlight-elevated-end));
--aq-gradient-sky: linear-gradient(123deg, var(--aq-color-sky-1), var(--aq-color-canvas),
                   var(--aq-color-sky-3), var(--aq-color-sky-4));
--aq-gradient-content: linear-gradient(112deg, var(--aq-color-glass-content-start),
                       var(--aq-color-glass-content-end));
--aq-gradient-kpi: linear-gradient(108deg, var(--aq-color-glass-kpi-start),
                   var(--aq-color-glass-kpi-end));
--aq-gradient-lens: linear-gradient(138deg, var(--aq-color-lens-start), var(--aq-color-lens-end));
--aq-gradient-selected: linear-gradient(155deg, var(--aq-color-selected-start),
                        var(--aq-color-primary));
```

**建议新增 · 增强填充。** 地图工具的 regular 回退及短浮层，使用把 `surface-float` 以 `--aq-long-overlay-alpha-min` 叠于透明底的组合，保留原 sRGB，默认填充 alpha 为 82%。长浮层玻璃外壳在既定最小至最大范围内按 `[记录]` 调整，内部稳定阅读面不随之外溢为透明。生成器可离线导出解析后的透明色，或使用同时通过能力检测的 CSS 组合；组合不可用时用 `surface-float`。`glass-nav` 与 `glass-tool` 的既有值分别服务导航和清透工具，不用容器 `opacity` 调节。

两个径向亮区的圆心与半径沿用已核对版本的 `src/national/sky.css`，迁入同一主题。位置、版本、最深区文字和滚动合成待验证·浏览器。缺少原型证据时以画布实体底或本节正式线性渐变作为待验基线。

### 滚动边缘

**建议新增。** 只有内容确实从固定导航或工具栏下滚过时，才在其与滚动内容交界处使用一条装饰性的软边。以对应实体底至透明的过渡为候选，限 `--aq-scroll-edge-size`，置于控件和焦点下方，随滚动边界切换，无指针事件。无相交内容时隐藏。粘性表头始终保留实体底，用底边或轻影标出与滚动行的分界。实体和强制颜色分支用实边，检查键盘滚入视野时标题、内容和焦点环均可辨。[Apple HIG · Scroll views](https://developer.apple.com/design/human-interface-guidelines/scroll-views)

### 遮罩与叠放

- **遮罩**用 `scrim`，浮层本身提供分离。原型的整屏模糊仅作观察记录。透明度施于背景层，前景读数保持完整。活跃玻璃外壳的祖先避免整层透明度动画，以免改变取景范围。
- **层级**在各自的局部上下文中使用：画布 0，图面与局部提示 1–4，粘性列与表头 5–10，应用导航与工具栏 20–30。Element Plus 应用浮层共享起始 2000 的叠放管理器。Dialog 内子面板在所属 Dialog 之上，背景局部提示在遮罩下。Toast 在当前活跃层反馈，不抢焦点、不穿透遮罩。实际挂载与管理器行为待验证·浏览器。
- **焦点**宽度取 `focus-ring.size`，外环间距取 `--aq-focus-offset`。浅底默认 `focus`，强选中或品牌底若与深环接近，改用 `on-primary` 内环或双色环。空间受限时使用可辨内环。按实际相邻色检查识别焦点所需的外圈或内圈是否达到非文本对比，确保粘性栏和浮层不会完全盖住焦点。

### 回退与偏好

1. **先渲染完整实体样式。** 只在 `@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px)))` 成立且实现的增强合格时启用玻璃。需要前缀的分支同时测试实际合成。无滤镜模式使用稳定底并关闭透明装饰，覆盖所有应用壳、浮层、内容卡、KPI、镜片与 SYS-403／404 主卡。
2. **覆盖优先级从高到低。** `forced-colors: active` → 项目显式实体模式、浏览器可读且匹配的 `prefers-reduced-transparency: reduce`，已验证需降级的性能档，或匹配的 `prefers-contrast: more` → 已通过验收的可选增强 → 实体基础。高对比偏好匹配时同时使用更明确的边界并验证对比。`prefers-reduced-motion: reduce` 独立关闭 Motion 规定的动效。
3. **显式实体模式可达。** 使用现有外观入口或设置中与会话无关的等效控件。键盘和触屏可用，切换不丢选择和焦点，且不依赖媒体查询支持。保存到现有外观偏好机制；未核对机制前按单次会话验收。增加独立页面、同步设置或接口须按决策权另审。
4. **系统强制色。** 禁用背景滤镜与透明装饰，包括 SYS-403／404 主卡及实际挂载的日期和账号浮层。使用系统色和可辨边框及焦点，不以阴影作为唯一边界。图表、地图可转换为高对比的线型/符号并保留文字与数据表。避免在整页或单纯装饰上使用 `forced-color-adjust: none`。AQI 在普通模式中维持标准填色，强制色下等级识别还依赖名称、区间和图形。
5. **首版浅色。** 固定当前主题输出为 light。未来如批准深色，增加同名语义的独立模式覆盖，组件继续读取角色，AQI 标准填色独立维护。先验实体、强制颜色及全部数据/控件状态，再开放新模式。系统暗色偏好目前只用于验收浅色主题的兼容性。[CSS Media Queries Level 5](https://www.w3.org/TR/mediaqueries-5/)

## Shapes

圆角按容器包围关系分配（值见 YAML `rounded`）：

| token | 用于 |
|---|---|
| `control` | 字段、按钮、地图工具。原型字段与工具的微小差异仅保留在测量记录。 |
| `nested` | 嵌套在容器内的小元素：Badge、Tag。 |
| `tooltip` | Tooltip、图表提示。 |
| `menu` | 菜单、Select 面板、Popover。 |
| `panel` | 面板、卡片。 |
| `sheet` | 侧栏、对话框、抽屉。 |
| `capsule` | 连续分段、短身份标记和紧凑导航。 |

内外等距时，用「外半径 − 内距」作内层圆角的光学起点。

## Motion

**S08-05（已确认）。** 按压、选中和展开立即反馈，操作与状态提交可中断。页面、导航指示器、菜单、抽屉及结果切换的位移和淡入显式声明 `transform`／`opacity`。按钮、选项和表行可显式声明短 `color`、`background-color`、`border-color` 过渡，复用 `press`／`hover`。高光可以用静态装饰层的透明度变化；阴影、滤镜及布局属性默认即时切换，禁止无范围的 `transition: all`。焦点环、状态文字、禁用语义同步更新。正常模式保留已认可的轻盈进出、选中和按压质感，不以机械性能优化把全部反馈改成即时跳变。

仅账号设置内的密码区可采用局部 `grid-template-rows` 180ms 配合淡变（确认的例外），专测短屏内滚、快速连点反转和焦点裁切。单站历史同批次记录切换保留整表 180ms 淡入与轻移，首载和换批由正常加载／结果状态接替。

```css
--aq-motion-press:       90ms ease-out;
--aq-motion-hover:       140ms ease-out;
--aq-motion-menu-in:     180ms cubic-bezier(.2, .8, .2, 1);
--aq-motion-menu-out:    120ms cubic-bezier(.4, 0, 1, 1);
--aq-motion-switch:      200ms cubic-bezier(.2, .8, .2, 1);
--aq-motion-content:     200ms ease-out;
--aq-motion-sheet:       260ms cubic-bezier(.2, .8, .2, 1);
--aq-motion-dialog:      220ms cubic-bezier(.2, .8, .2, 1);
--aq-motion-exit:        160ms cubic-bezier(.4, 0, 1, 1);
--aq-motion-press-shift: 1px;
--aq-motion-menu-shift:  4px;
--aq-motion-tooltip-delay: 180ms;
--aq-motion-account-security-duration: 180ms;
--aq-motion-history-record-duration: 180ms;
```

| 角色 | 用法 |
|---|---|
| `press`、`hover` | 按压改变高光或以 `--aq-motion-press-shift` 位移，释放复原。精细指针可见悬停反馈，允许显式短颜色过渡（90／140ms），键盘焦点立即出现。 |
| `menu-in`、`menu-out` | 轻量菜单从触发边缘进出，最大平移取 `--aq-motion-menu-shift`。Tooltip 触发延迟取 `--aq-motion-tooltip-delay`。 |
| `switch`、`content` | 导航和 Tabs 指示器可以平移，查询结果可淡入。状态文字和下一次输入即时可用。 |
| `sheet`、`dialog`、`exit` | 抽屉、侧栏、窄屏导航和 Dialog 分别取进场角色，退出共用 `exit`。遮罩可单独淡变。 |

图表数据更新是单独的 ECharts 配置：密集图默认 `animationDurationUpdate: 0`。能帮助追踪同一口径的同一序列时，可用 240ms 的渐缓更新作候选并实测。换口径、序列或缺口分布时直接更新。插值只影响既有数据的呈现过渡，读数、证据计数和坐标及时使用真实值。

快速反向操作必须从当前状态反转或取消，结束时焦点与最终状态一致。`prefers-reduced-motion: reduce` 分支取消上述位移、淡入、颜色过渡、密码区布局过渡、整表入场、缩放、平滑滚动和图表插值，文字、选中环和状态立即生效。固定大背景、长表和密集地图点避免持续滤镜和逐个进场。减少动效、连续反向和滚动性能待验证·浏览器。

**建议新增 · 材质反馈。** 按下时优先改变当前控件的静态高光强度或使用共享位移，选中同时更新持久标记。Popover 或菜单从实际定位后的锚边进出，翻转后跟随新锚边。快速开关根据最新意图中断或反转，在最终可见状态下归还或安置焦点。选中、错误与加载语义随状态立即生效。高光淡变放在不承载文字、也不负责背景滤镜的装饰层。

## Components

**先看动作语义，再选择外壳。** 立即操作用按钮，去另一个资源用链接，一组互斥视图用分段，需要保存的条件用表单。原型日期胶囊是静态时间标签，正式日期选择器有独立可辨的触发器。用 YAML 的组件角色取尺寸、圆角和内距。实际命中区、可达性和结果反馈优先于视觉尺寸，遵守硬约束 7。

### 状态

| 状态 | 可见反馈及行为 |
|---|---|
| Default / Hover | 默认已有辨认所需的边界或动作标签。精细指针悬停改变填充或高光，触屏可独立操作。 |
| Pressed / Focus visible | 按下有即时反馈，可在释放前取消。过渡取 Motion。焦点环取 Elevation & Depth 的共享规则。 |
| Selected / Expanded | 选中用勾、当前项标记或选中线。展开用方向、`aria-expanded` 和受控面板关系，可同时看出选中项和焦点。 |
| Disabled / Read-only | 禁用原因或替代入口在禁用控件外也可读。只读值保留正常对比、标签和复制能力，界面呈现只读语义。 |
| Loading / Error | 保留按钮尺寸与可读状态，阻止重复提交。失败保留获准的非敏感草稿，字段错误与对应字段关联。提交后聚焦首个错误字段或错误摘要，状态播报以必要内容为限。 |

### 组件规格

| 组件 | 结构、尺寸与材质（默认值，`[自主]`） | 操作与边界 |
|---|---|---|
| Button / Icon Button | 取 `button-primary`、`button-secondary`、`button-danger` 和 `icon-button`。低强调用文字或 `--aq-gradient-lens`，图标取 `icon-inline`。 | 每个任务区一个主动作，危险按钮有动词，触屏尺寸按硬约束 7。支持 Enter/Space。加载有状态文字。 |
| Navigation | 行高按 `nav-item-selected.height`，图标取 `icon-navigation`。展开后有分组和文字。收起保留名称与悬停/焦点提示，窄屏用抽屉。 | 当前页用 `aria-current`，折叠开关报告展开状态。工作区切换保留返回入口，权限变化按 Interaction Patterns 清理。 |
| Tabs / Segmented | 外轨边取 `--aq-edge-width`，内嵌取 `--aq-tabs-inset`，指示块保持宽度。行高取 `segmented-item-selected.height`，连续互斥组可用 `capsule`。 | 标签页用 tablist/tab/tabpanel，支持方向键及可见焦点。地图指标与时间窗用有名称的按钮组和 `aria-pressed`。慢查询可用显式激活。 |
| Input / Search | 取 `input`；可见标签、输入、清除、帮助和错误分别定位。依赖轮廓识别输入区域时用 `border-strong` 与 `--aq-edge-width`。 | 标签与字段关联。清除后保留焦点。简单搜索有结果状态并支持中文输入法组词。必填、错误和只读有可读提示。输入提示只作补充。 |
| Select / Combobox | 取 `select-menu`、`select-option` 及选中变体。字段显示当前值和展开状态，可搜索列表保留活动项。按 Layout 的锚点间距、限宽和翻转规则定位。 | 方向键移动、Enter 选择、Esc 关闭。采用输入保焦方案时用 `aria-activedescendant` 报告活动项。无匹配可清除，单选后关闭，多选保留列表。 |
| Date Picker | 日期格取 `date-cell`，共用可辨标签、月导航、时区或源地方日说明。起止端点、范围带及文本输入按 S08-04 变体启用。窄屏可用单月面板，密集网格使用稳定阅读面。 | 保留键盘移日，分别标示选中、今天和焦点，Esc 先关最内层并还焦。按下方四种变体核输入、起止和窗口语义，不强制所有日期控件支持文本输入。 |
| Checkbox / Radio / Switch | 框或圆取 `checkbox-radio-indicator`，开关轨道取 `switch-track`。可见标签并入命中区，实际命中尺寸按硬约束 7。 | 支持勾或半选、单选点、开关位置及文字。Switch 表达已知布尔偏好，异步失败恢复已确认值并提示。高影响治理动作单独确认。 |
| Card / Metric Card | 用 `content` 外壳，文字和读数按 Elevation & Depth 的稳定底条件。指标卡顺序是标签、读数和单位、观测依据。 | 有基准才显示变化值。空值和部分结果保留位置与原因。仅可下钻区域给点击反馈，避免嵌套交互目标。 |
| Chart Panel | 有名称、时间和单位、图例、预留绘图区、覆盖摘要与数据表。绘图与相邻图共享文字和坐标基线。 | 加载、无数据、部分结果和说明就地呈现。重点和下钻落在有名称的控件。见 Data Visualization。 |
| Table | 有名称、列头/行头、排序、选中和分页。表头用 `table-header`，行用 `table-row` 或 `table-row-compact`，选中用 `table-row-selected` 的颜色和侧标，行高仍随所用密度。 | 列头与值共享列宽及列职责对齐，S08-03 的主表例外见下方。排序说明方向及当前口径。翻页后首行或摘要可达，零结果可清筛。只暴露服务端支持的排序、游标和计数，待验证·契约。 |
| Badge / Tag | 使用各 `tag-*` 变体，以文字配小点或图标。 | 筛选标签移除按钮有独立名称和命中区。来源、权限和质量的补充说明可展开。 |
| Dropdown / Menu | 按短、长菜单选材质，项高同 Select。常用项在前，危险项分组。 | 菜单用于动作，Select 用于选值。支持方向键、Enter、Esc、点外关闭和还焦。窄屏可改用抽屉或列表。 |
| Tooltip / Popover | Tooltip 用 `tooltip` 和 Motion 的触发延迟。Popover 用 `popover`，宽高和安全区参考 Layout。 | Tooltip 可用悬停或聚焦触发，指针可移入，按 Esc 可关闭。在悬停或聚焦仍有效时保持可读，直至主动关闭或信息失效。交互内容使用 Popover 并管理焦点。触屏关键信息有持久文字或点击入口。适用于图表和地图的同类自定义提示。 |
| Dialog / Drawer | 标题、关闭、可滚动正文与动作区。短确认用 `dialog`，导航抽屉用 `--aq-nav-drawer-width`。尺寸受 Layout 限制。 | 证据可用 Drawer，可分享或多步资源用详情页。模态外部不可交互，打开时设置合适焦点，Tab 限在模态内，Esc 或显式取消关闭。焦点归还触发器；触发器已消失时按下文处理。未保存工作有保留或放弃选择，危险按钮不作为默认回车动作。 |
| Toast / Alert | 短反馈用 Toast，持续错误、部分失败和需动作的情况就地显示 Alert。 | 用合适的 `role=status`、`aria-live` 或 `alert`。同一事件避免重复播报。成功不抢焦点，重要结果可回看或进入详情。 |
| Skeleton / Loading | 外框和占位按将展示的行或图建立；局部刷新保留标题及可用操作。 | 结果区设置 `aria-busy` 与可读状态，减少动效时静止。若保留前次结果则不在其上叠骨架，决策见 Interaction Patterns。 |
| Empty / Error | 显示状态标题、已应用范围、可公开原因和适用的下一步。 | 区分 Page Patterns 中的状态。局部错误就地显示，会话失效覆盖并清理受保护区域。 |

### 日期与时间变体（S08-04）

统一触发器、圆角、文字、边缘光、玻璃外壳、日历样式、焦点和展开反馈，不把不同业务输入强行改成同一种时间控件。

| 变体 | 审计要求 |
|---|---|
| 任意时刻或时间范围编辑 | 在允许编辑的时刻内提供日历及时间文本输入，校验格式和有效起止。应用前可取消草稿，明确查询时区及契约区间（例如半开区间 `[start,end)`），保留未改端点原有精度。适用 M04／M05、M06 查询及对象审计。 |
| 已有观测日期与小时筛选 | 单站址期、多指标分组历史，从当前获权观测选日期，日历选择无需文本输入；选日后显示「全部时刻」及 00:00–23:00，UTC+8 本地小时匹配，读数显示实际时刻。换日清除先前小时及所选时刻；刷新只恢复仍有效的选择，空小时保留入口及空态。 |
| 源地方纯日期 | 仅按产品报告日、业务日或诊断约定选择日期，遵循端点日期范围和区间规则。适用 M06 日报、M08／M09 目录筛选和 M10 诊断。 |
| 模板固定分钟 | M08 首次到期选择日期及小时，分钟继承当前登记模板并明确显示，保留模板版本和时区约束。 |

各变体验收短屏边界、月导航、键盘选择、焦点归还、今天及选中状态；按契约样本复核跨日、端点精度、空小时、游标变化及无权条件恢复。

### 表格对齐（S08-03）

常规数据表的列头与数据沿同一边对齐。对象、名称和长文本靠起始边；可比较的数值、计数、百分比靠结束边并用等宽数字，同列单位和精度一致；短状态及定宽行操作可居中。日期与标识符按文本或时间列处理。

| 确认的例外 | 限定范围 |
|---|---|
| M04／M05 | 三个分析模式的已验紧凑主统计表，列头与数据均可列内居中。 |
| M08 | Job、Window、Run、历史导入四个已验桌面主账本，列头与数据均可列内居中。 |

两处例外仍须保持表头与数据对应、等宽数字及长文本可读换行。明细、弹层及新增数据表按通用规则审计；窄屏记录卡保留字段名称及阅读顺序。用长任务名、混合位数、负值、小数、缺测和单位换行样本走查。

关闭浮层时优先回到仍存在且可操作的触发器。若翻页、筛选或撤权使触发器消失，把焦点放到当前权限允许的稳定入口，如所属结果标题、筛选触发器或可继续的下一步。DOM、状态播报和焦点归属待验证·浏览器。

### 变体继承、组合状态与尺寸

**建议新增。** 变体继承的是规格字段和行为，按「基础 → 变体覆盖 → 适用状态覆盖」解析。圆角、尺寸、排版及可访问语义从基础继承。焦点指示叠加于当前状态，选中和错误的可读标记独立保留。材质和系统偏好按 Elevation & Depth 单独裁决。

| 组件族 | 基础、变体及覆盖 |
|---|---|
| Button / Icon Button | `button-secondary` 提供按钮排版、圆角、默认尺寸与内距。Primary 和 Danger 分别取 `button-primary`、`button-danger`，主按钮按 `button-primary-hover` 与 `button-primary-pressed` 仅覆盖颜色。Secondary 和文字/图标低强调动作可用 `button-ghost-hover` 作精细指针悬停。Danger 保留危险填色和动词，按压可用共享高光或位移。图标按钮另用 `icon-button`，保留独立名称。 |
| Navigation / Tabs | `nav-solid` 定义侧栏实体和尺寸。收展变化只更新壳及排列。当前导航项选中用 `nav-item-selected` 基础，并按材质表应用强选中组合。分段项用 `segmented-item-selected` 或同密度默认项，方向与选中语义按组件规格。 |
| Input / Select / Date Picker | 共用 `input` 的文本、尺寸、边界。非文本型触发器保留标签和当前值。Select 展开增加 `select-menu`、`select-option`，选中只覆盖用色和标记。Date Picker 展开采用 `date-cell`，按 S08-02 选择玻璃或实体外壳，内部保持稳定阅读面；按 S08-04 变体组合今天标记、选中端点和范围。 |
| Card / Metric / Table | 内容面板用 `surface-content`，KPI 用 `metric-card`。表行按常规/紧凑密度选择，`table-row-selected` 覆盖颜色，行高按当前密度和命中要求。 |
| Popover / Dialog / Drawer | 先取实际组件的尺寸、圆角、内距及可访问语义，再按长短内容选择材质。短 Tooltip 保持自身实底，长说明和交互内容选择相应组件。 |
| Badge / Tag / AQI 图例 | 系统语义 Tag、AQI Tag、标准填色图例各用自己的角色。图例前三个色块应用 YAML 的轮廓组合，其余用实际可辨性规则。可移除标签另有命名动作和命中区。 |

| 可同时出现的状态 | 裁决 |
|---|---|
| Hover / Pressed / Focus visible | 可操作状态下 Pressed 的填充优先于 Hover。焦点环独立显示，移开鼠标后可见。 |
| Selected / Expanded / Focus visible | 标记分别表达选择、展开和焦点。菜单子项激活和本地焦点变化同步可访问树。 |
| Loading / Disabled | 请求中锁定该动作的再次触发并保留状态和尺寸。如果原控件持有焦点且仍在当前任务内，保留可读焦点归属。真正不可用时说明原因或给出替代路径，任务区中其他可用操作继续可达。 |
| Read-only / Error | 只读保留名称、正常对比和复制能力。错误由文本关联到字段，保留获准草稿，错误提示和焦点环同时可见。 |

**建议新增 · 长文本和操作列。** 有字按钮最小可见宽度取其默认高度和「内容、内距与图标间距」之较大者，图标与文字间距取 `spacing.sm`，同组动作可用 `spacing.sm`。窄区先让操作组重排，必要时按钮允许完整换行、按内容增高，命中范围按硬约束 7。表格操作列最小宽度按当前可见入口的最长完整标签、内距和命中区计算；空间不足时用已有且可达的溢出操作或局部表格滚动。Dialog 的动作区可折行或纵排，并保留取消和主操作。

**候选实现映射 · 待验证·契约。** Button、Input、Select、Date Picker、Dialog、Drawer、Dropdown 先核对现有 Element Plus 包装层及锁定版本。Metric Card、图例和图表容器先核对现有项目结构；原稿所列 `src/shared/ChartView.vue` 是候选入口。组件与版本以实际仓库证据确认。具体适配见 Verification。

### Element Plus 展开态

建立全局主题映射，让主题作用到实际挂载的 DOM。Dialog、Drawer、遮罩及各类 Popper 使用对应语义变体。实际选项、日期格、箭头、选中、禁用和键盘焦点一同检查。

分别在 Dialog 内打开 Select 和 Date Picker。第一次 Esc 关闭子面板并回到字段，第二次 Esc 关闭 Dialog 并还焦。查看实际 DOM 与可访问树，确认子面板可用且在当前模态语义范围内，背景内容保持不可交互。缩放、窄屏、翻转及内滚时确认裁切和层级。待验证·浏览器。

用项目锁定版本文档确认挂载接口、可用的弹层类名、主题继承和默认 Esc 行为，再选择可用的 `append-to`、`teleported`、`popper-class` 或等效接口。待验证·契约。

## Do's and Don'ts

**Do**

- 组件引用共享语义 token，按 Overview 的来源规则更新主题。
- 开页先给「哪个对象、哪个时刻、测到了什么」，再给分布、趋势和证据。
- 空值、部分结果、陈旧和未知各自保留位置并写明原因与下一步。
- 局部刷新时保留标题、图框和可用控件；加载与结果同框。
- 在真实浏览器里核对玻璃浮于内容、云白表面承托读数的效果，并同时检查实体回退。
- 选中同时用标记与底色；图表另提供文字或数据表。
- 正常模式优先追求 Apple 式视觉完成度，保留已认可的材质与动效；读数、状态和必要操作满足硬约束。氛围层静止且不接收指针。

**Don't**

- 不在数据行、长表、地图点位及密集日期网格叠加滤镜。功能玻璃和 SYS-403／404 主卡例外遵守 S08-02；内容卡外壳遵守材质表及合成验收。不得为追求形式上的统一把已确认玻璃外壳全部替换为实体。
- 不用冷灰 SaaS、大屏霓虹、重反射、全站风景照片、营销式巨幅标题，不用图片代替控件。
- 不靠颜色单独表达等级、选中、缺测或质量；不把缺测画成 `0`。
- 不在浏览器里重分级 AQI，也不用站点均值代替官方城市观测、用小时 AQI 均值代替官方日报。
- 不给可读标签做假按钮；整卡有独立按钮时不嵌套交互目标。
- 不用负 margin 或绝对定位校正内容流；不用任意大的 `z-index` 修裁剪。
- 刷新时的前次值遵守 Interaction Patterns 的准入条件。撤权后立即清理已有私有展示与迟到响应。
- 组件应对应真实界面用途。纯材质和辅助图形 token 的 lint 允许清单见 Verification。

## Data Visualization

```css
--aq-chart-line-width: 2px;
--aq-chart-marker-size: 6px;
--aq-chart-overview-height: 150px;
--aq-chart-analysis-height: 320px;
```

| 角色 | 规格与用法 |
|---|---|
| `chart-series` | 首页主线 `chart-1`，宽度、标记尺寸取上方规范块。比较序列按稳定对象键分配颜色，兼用线型、点形和序列名。超过五条时保留稳定颜色映射，增加直接标注、可操作图例或分图，避免只能凭颜色辨认。线条重叠可通过高亮一条或明细查看。 |
| `chart-scale` | 有序覆盖/数量用 `chart-scale-low → chart-scale-mid → chart-scale-high`，带端点、单位和可读数值说明。浅色块需要识别轮廓时使用 `border-strong`，核对实际相邻色。缺测用中性空格或纹理配文字。围绕有意义零点的差值可用双向色阶和零轴。 |
| `chart-axis`、`chart-grid` | 字取 `chart-axis-text` 和 `chart-tick`，网格 `chart-grid` 仅作辅助。必要轴线用 `chart-axis-line` 并查实际背景。横轴标时间尺度和时区，纵轴带单位。比较图共享尺度或明显标出差异，柱形以零为基准。 |
| `chart-plot` | 高度取对应 CSS 角色，按实际标签宽度预留边距，覆盖长单位、负数及文字放大。极窄时重排图例或在确需二维布局的图形内提供局部滚动。 |
| `chart-tip`、`chart-focus` | 短图表提示默认用 `chart-tooltip` 的字、内距及实底。临时玻璃变体使用 `elevated`，需达到合成对比要求。若边框是必要线索，用 `border-strong` 及 `--aq-edge-width`；`chart-tip-border` 可作装饰。轴指示用 `chart-pointer` 作辅助，必要的焦点、位置用可辨标记及文字。提示含对象、时刻、指标、值、单位和状态，随键盘或明细可读。 |

- **趋势与降采样。** 缺测留空断线。有效浓度 `0` 落在零位，`<3` 等删失保留状态和获准的源表示。时间桶显示区间和采用的统计值。默认交互预算为 1,000 点，超量遵守服务端限制或缩小请求。降采样保留断点，选中实际观测时提示绑定原始时间与值。选中聚合点时明确标示区间和方法，不把代表点伪装成该区间的每个原始观测。图表、明细和导出共享父查询与版本，粒度和字段待验证·契约。
- **数据表与操作。** 图表旁有可达的数据表入口，至少覆盖图表展示的对象/序列、观测时刻或时间桶、指标、数值、单位、缺测/删失/质量状态及适用分母。具有下钻能力的图形同时给键盘或列表入口，保持相同的授权和版本。
- **分布与颜色边界。** 环图或条形图有文字图例、计数、总数和分母。AQI 保留标准填色，亮色标记用 `aqi-outline`，图例显示等级及必要的区间提示。相邻区段需要视觉区分时，用留缝、达标分隔、纹理和直接标签，按实际相邻色检查依赖的边界。比较、研究可用箱线、散点和热图，并提供对应数据与方法。
- **覆盖与比较。** 比较限同层、同产品和兼容的单位、标准、窗口。服务端 E（应有）、O（实到）、V（有效）、E共（共同应有）、P（共同有效）解释 `O/E`、`V/E`、`P/E共`。如各站 18/24、共同 12/24，只展示获准曲线、样本数和证据不足原因。分母未知或为零仅列可披露计数，缺少分析授权时不推算。字段和覆盖计算待验证·契约。

**地图。** 现有 F 演示用本地地形与 SVG 交互层。S08 对正式地图按 ECharts、合法本地资产和契约模拟场景验证，S09 按 S09-01 独立验收真实同源响应、正式点位、坐标映射与地图许可；全国趋势单独验收，不阻塞同刻列表基本路径。待验证·契约、待验证·浏览器。

- 全国 L 投影的稳定城市键和获准链接以契约为准。城市坐标用另有依据和授权的映射，记录来源、适用时间与 CRS。站址期坐标还要满足字段权限。
- 地形、指标、缺测和没有点位各有文字或图形说明。亮色点轮廓规则见 Colors。若只展示当前页，标「本页已定位 x/y」，计数限可披露范围。
- 下钻使用服务端获准链接。数据、几何或许可依据不足时保留可搜索列表入口，地图验收单列。
- 提供可点击、可聚焦的缩放、方向平移或地点定位、复位操作，覆盖用户靠拖拽能到达的区域。密集点位在真实视口核对命中、焦点、选中及预览。待验证·浏览器。

### AQI 图例边界与图表增强验收

**建议新增 · AQI 图例轮廓。** `legend-swatch-aqi-1` 至 `legend-swatch-aqi-3` 取 `x-aq.component-compositions.aqi-legend-outline` 的颜色和宽度。以 `border-box` 绘制 12px 色块中的 2px 实边，中心填色仍取各自标准值。每项与等级、区间和已核系列成组，计数和分母就近可读。对于其余等级，填色与底面能清楚界定边界时直接使用标准填色；不满足时加经验证的边或移到稳定图例底。可点击图例由外围整项承载命中区和键盘焦点，色块只承担可视线索。

在实际图例和地图上验证外轮廓或所依赖的标记边界与相邻底达到 3∶1。需区分相邻区段时，对实际依赖的分隔线、纹理或直接标签另验；如一条边的内外背景无法同时满足所用线索的要求，采用间隙、复合轮廓、不同形状或稳定底，保留各等级标准填色。普通模式不以低对比阴影替代必要边界。

**建议新增 · 图表补充验收。** 色觉模拟辅助检查序列名、线型、点形和可辨标签；键盘、触屏和读屏通过明细或等价控件完成同样的读数和下钻。桌面基线与高 DPR 浏览器分别检查线宽、标记、长中文及分页表。若项目已提供图表打印或图片导出，查图例、轴、单位、必要来源、实际背景及当前披露范围。核对缩放、采样与重复开关时的更新耗时、掉帧及实例回收。性能量化预算按已核环境和数据量另行记录，继续使用原稿 1,000 点默认交互预算。

## Interaction Patterns

- **编辑条件与已应用条件。** 页面区分二者。轻量搜索可即时执行，多维分析用「应用」。结果旁可读其条件、锚点和输入版本。条件变化重置相应游标。返回恢复合法条件、滚动和对象。请求可取消，响应须与当前查询、会话及用途匹配。
- **环境与数据发现。** S08 默认进入 Mock 的 F 演示，S09 接入真实 API 后默认进入正式域。主动进入 F 时工作区和下钻页持续标「合成演示」。目录查询从获权对象和产品出发，按兼容指标和有证据的日期引导。只有观测锚点时可从该时刻进入，其余日期覆盖标未知，按契约允许选窗查询。对象、产品与日期能力待验证·契约。
- **稳定城市选择。** 地图、榜单、表格、详情及获准的 URL 取同一稳定城市 ID。读数另绑定数据域、产品和快照。名称仅作展示，站点下钻核对站址期。首次进入先确认 URL 对象权限和已应用条件，缺少合法选择时用产品确认的默认对象。选择来自任一入口则同步更新，悬停只预览，键盘焦点独立。无坐标标「仅列表可用」。默认对象、获准导航投影待验证·契约。
- **选择保留。** 翻页不补行，保留暂未出现的合法选择。改排序、切显示指标、刷新同一窗口也保留仍合法的选择。地图复位只改变视野。应用条件确实排除选中对象时提示原因，清空或显式改选。切换数据域或不兼容产品、撤权时清理对应读数、选择、缓存和 URL，并使旧请求失效。状态与真实切页行为待验证·浏览器。
- **下钻与处置。** 图表点击、键盘下钻、可搜索列表和详情共用口径。定位、复位、选择等动作名称明确。高影响操作确认时展示对象、作用范围、版本和结果入口，先反馈受理，再依账本更新结果。

### 结果保留与失效

以下规则用于 UI 状态切换。查询身份至少包含会话、用途、数据域、产品、已应用条件和适用快照或输入版本。如何获知权限变化、请求身份和可保留结果范围，待验证·契约。

| 情形 | 界面行为 |
|---|---|
| 首载或当前条件无前次结果 | 显示所属结果区的骨架、加载文字与 `aria-busy`，不展示其他查询的读数。 |
| 同条件刷新，前次结果仍获准 | 可保留已确认结果，标明“正在刷新”、前次结果属于哪个时刻或版本。观测已陈旧时另标其时效，不把刷新进度当作观测时效。无前次结果时使用首载行为。 |
| 数据域、已应用条件或不兼容产品变更 | 不把旧结果显示为当前数据。清除不再合法的状态，等待匹配新查询的响应。草稿条件和仍合法的选择按前文规则保留。 |
| 来源异常，仍有获准的最后有效观测 | 标明来源异常、最后有效时间及适用产品，将观察数据与当前请求状态分别呈现。 |
| 用途变化、对象撤权或会话失效 | 立即停止显示受影响内容，清理相应私有缓存、选择和 URL，取消请求并忽略迟到响应。只保留现有会话和用途仍准许的其他结果。 |
| 响应迟到、窗口或版本不再匹配 | 丢弃该响应。按当前查询状态继续显示，不用过期响应替换界面。 |

## Page Patterns

**分析工作台实装。** 1440×900 保持 280px 条件栏与右侧紧凑统计布局。条件、日期弹层及统计表沿用首页和 M06 的控件。850px 及以下改用条件抽屉，辅助图按阅读顺序改为单列。三种分析模式的主统计表按 S08-03 确认例外，表头和数据均列内居中，呈现当前指标、E、O、V 和有效覆盖；明细及弹层表按通用列职责对齐。日期范围编辑使用 S08-04 任意时刻或时间范围变体。数值用品牌蓝突出，样本结论标签随服务端状态显示绿、琥珀或中性色。分布图复用首页到报率的蓝青渐变，并为频次轴留出刻度空间。时间热图使用绿到黄、橙红的连续浓度色阶，灰格标识缺测，逐格数据可用键盘读表。图表数据和放大窗口归还触发焦点。[实装截图和浏览器证据](docs/verification/M04_M05_ANALYSIS.md)

**已确认的导航。** 用户工作区设「监测总览、分析工作台、数据查询、我的空间」，城市和站点各有详情页。正式前端中管理员登录后默认进入用户工作区。管理入口依当前会话的具名能力提供「采集任务、数据质量、系统运行」和返回用户区的切换。后端继续按对象与用途授权。M01 F 成功态、M07 已明确确认的构图及 S08-01 至 S08-05 的具体取舍为正式基准；其余页面保留已有实装构图作为视觉审计参照，未有明确确认依据的细节仍为候选（`[记录]`），不得把实装或历史截图直接当作完整验收通过。`GET /api/v1/me/capabilities/` 的具名导航投影及能力条件待验证·契约。

**产品代号。** 本文用 F 指合成演示，L 指全国汇总所用城市行投影，C 指城市官方观测，T 指近期 AQI，D 指官方日报，S 指站点观测，H 指获权历史数据集。这里的简称用于阅读，下钻、指标、时窗、时区与允许的比较以[业务规则](docs/BUSINESS_RULES.md)、[接口契约](docs/API_CONTRACT.md)和[页面与接口映射](docs/PAGE_API_MAPPING.md)为准。正式代号及用途待验证·契约。

| 页面 | 阅读 / 操作顺序 | 核对要点 |
|---|---|---|
| 监测总览（M01） | F 成功态：时刻与产品 → 三项摘要 → 地图、高值榜与 AQI 分布 → 覆盖及城市列表；正式 L：同刻列表及获权下钻，支持的汇总和其他面板按投影呈现 | 保留 F 完整首页构图与密度（S08-01）；正式模式按 S09-01 验收基本路径，不把不可用地图或趋势伪装成正式数据。L 取共同快照，按已核的 `*Level` 投影显示等级，异常呈现见 Colors。全国计数与分母见下文。 |
| 城市 / 站点详情（M02/03） | 对象身份、主读数和逐指标时刻 → 趋势 → 站点/历史明细 → 返回 | 城市官方七项取 C，近期 AQI（T）、C 已存小时历史、官方日报（D）分区呈现。站点突出站址期和逐指标最近有效，显式空窗不回填。H 标明获准历史来源。任何研究性均值只按已验证契约展示并标明方法和样本，不能用站点均值代替官方城市观测或用小时 AQI 均值代替官方日报。 |
| 分析工作台（M04/05） | 「趋势与分布」「对象对比」「指标关联」各自保留已应用条件，从结果下钻至 M06 再返回 | M04 沿统一时间轴对齐 C 对 C、同城兼容 S 对 S 或同一获权历史数据集内同城 H 对 H 的曲线和明细，保留缺测时槽。整期比较须依据共同应有窗口内的共同有效样本及覆盖。M05 按契约呈现趋势、分布及指标间的配对统计，附单位、窗口、方法和样本限制。相关性只描述关联，不能据此推断因果关系。 |
| 数据查询（M06） | 来源、对象、指标 → 显式 `[start,end)` 或最近有效 → 高级筛选（单位、标准系列）→ 表格/游标 → 获准导出 | 显式窗口可筛质量，最近有效固定 `quality=valid`。明细表按获权投影列数值、单位、观测时刻（D 日报列源地方报告日期）、采集时刻、来源产品、统计窗口与质量提示。采集时刻缺少依据时标「未知」。承接 M04/M05 的已应用条件与版本，下载单独核票据和当前许可。 |
| 我的空间（M07） | 我的关注用于复访城市/站点，分析方案支持重跑或编辑，近期导出可查票据状态并按权下载 | 三项均限本人。关注保留稳定身份，方案保存查询意图，复访按当前权限和版本取数。账号资料与修改密码由账号入口提供。 |
| 采集任务（M08） | 计划/任务（Job）→ 业务窗口及各次 Run → 子项、批次、缺口与异常 | 分开显示投递、运行结果及窗口覆盖。手动触发、暂停/恢复或重投按获准动作执行，凭回执定位原运行与关联质量事件。 |
| 数据质量（M09） | 质量事件 → 受影响的对象、字段与窗口 → 观测证据、可信版本及争议 → 受控处置 | 并列显示当前修订和相关证据，争议值标为待核。按权限、版本和理由执行服务端允许的裁决，显示回执与影响范围。官方观测的更正走修订流程。 |
| 系统运行（M10） | 服务健康 → 来源与产品状态 → 有界运行趋势 → 获权诊断入口 | 分列 Web、数据库、Redis、Worker、Beat 的健康证据。来源异常定位到相应产品与窗口，诊断从获权账本进入。缺少或过期的证据标「未知」。 |
| 公共访问区 | 平台说明、数据来源与使用说明 → 登录 / 注册 | 说明访问条件、数据出处和允许用途。身份页沿用晴空材质与窄表单，支持密码管理器、粘贴、键盘提交。注册后引导登录。 |
| 系统状态（SYS-403 / SYS-404） | 公共品牌顶栏 → 玻璃插画和 H1 → 状态说明 → 恢复操作 → 平台说明与提示 | 桌面恢复操作并排，640 CSS px 及以下纵排并保留 44 CSS px 命中。进入时聚焦标题。主卡按 S08-02 确认例外使用单层背景滤镜，先有实体底，卡内控件无滤镜；强制色、高对比、减少透明度、显式实体模式及无滤镜支持时回退稳定表面并保留可辨边界与焦点。顶栏使用公开入口，主操作按最新能力恢复。[桌面与移动端实拍](docs/VERIFICATION.md#系统状态页) |

**M07 已确认构图。** 用同一应用壳承载单一「我的空间」标题、通栏身份摘要、胶囊分段导航、三列资源预览、快捷入口和资源目录。桌面采用等宽预览卡，中间宽度两列，720 CSS px 及以下单列并折行摘要。保留现有侧栏选中态和移动抽屉。账号设置使用全站共用面板。列表用稳定阅读面，筛选、批次和状态提示复用现有组件。页面顶部保留账号与数据域。全国快照时刻及其产品上下文只出现在观测页。默认 Tab 为资源概览。[路由细则](docs/ROUTES.md) · [浏览器证据](docs/verification/M07_PERSONAL.md)

**全站账号设置。** 桌面从工具栏账号入口下方打开右侧悬浮玻璃面板，保留四周圆角、轻遮罩和原页轮廓。面板宽度随视口高度收窄，常见桌面短屏通过间距和控件密度调整容纳完整展开内容。标题固定，可用高度不足时正文内部滚动。身份、个人资料、账号安全和淡红退出按钮从上到下排列。账号安全默认收起，打开后提供三项可独立显隐的密码输入。仅密码区按 S08-05 使用局部 180ms 折叠与淡变，减少动效时即时重排；核验反转、内滚及焦点。外壳玻璃与内部稳定填充按 S08-02。600 CSS px 及以下使用全宽底部面板，个人资料改为单列。未保存修改的关闭和退出由面板内确认，退出遵循当前会话的 CSRF 和路由检查。[原型对照和浏览器证据](docs/verification/ACCOUNT_SETTINGS.md)

**M07 资源详情视图。** 关注用三列城市和站点观测卡，方案用双列卡片和分层操作，导出用带排序表头的紧凑票据表。选中和主按钮沿用首页的蓝色渐变，图标与状态按类别使用青绿、绿色、紫色和琥珀色。由观测回执提供的 AQI 等级保留首页等级色。720 CSS px 及以下卡片纵排，票据表在自身区域内滚动。账号设置位于三个详情视图的身份摘要右侧，移动端移至摘要下方。[原型对照](docs/verification/M07_PERSONAL.md#三个资源-tab-原型对照)

**M07 概览和关注时间。** 三个概览卡共用玻璃底色和品牌蓝标题，行内图标按资源或方法使用不同颜色，状态色保留业务含义。方案更新时间和状态沿同一右沿对齐。关注卡在读数下方显示「观测时间」标题，随后以「指标名 + 时间」与上方读数逐项对应，窄屏按可用宽度换行。[浏览器截图](docs/verification/M07_PERSONAL.md#概览与观测时间复查)

系统状态页自 1536 CSS px 起采用紧凑版。居中卡片以 58 rem 为上限，收紧插画、标题和垂直间距，主要按钮至少高 46 CSS px。[宽屏实测](docs/VERIFICATION.md#1600px-系统状态页)

**公共与身份页实现记录。** 平台说明桌面依次呈现 Hero、三项能力、访问说明和底部入口，手机按原型纵排。宽桌面公共内容以 1320 CSS px 为上限。来源页的玻璃长栏在顶栏下方的左栏区域内居中，高度最多 576 CSS px。「本页目录」在上方紧凑排列，「关键提示」在下方保留标题、说明和条目间距。提示区按可用高度分配，手机抽屉内复用相同结构，较短的分区可在内部滚动。正文占满右侧，定位后将焦点移到顶栏下方的目标标题。登录与注册在 1000 CSS px 起共用左侧通高、贴左的品牌分屏，右侧表单卡片以 700 CSS px 为上限。更窄的窗口采用云山头图与居中表单。凭据输入框占满字段，空字段聚焦时隐藏占位提示，实际输入仍可选择或编辑。验证截图见[独立工程验证记录](docs/VERIFICATION.md#公共与身份页)。

**管理边界。** 管理工作台沿用晴空玻璃，长账本可用更紧凑稳定的表面。计划呈现获权的账本、质量、健康、趋势、管理产品目录、获权周期计划配置及预览与激活、本人回执与对象审计。来源、产品和周期计划模板按受控主机流程登记，网页负责契约开放的计划配置与动作（S09-02）。Django Admin 承担受控账号与城市检索别名维护。历史导入暂存注册、备份签认、备份清理和实际恢复使用受控主机流程，网页呈现契约开放的状态、动作或意图。实际开放范围见[接口契约](docs/API_CONTRACT.md)，待验证·契约。

**M08 已实施的构图。** 采集工作台复用共享侧栏、顶部账号入口、来源提示、下拉框和日期编辑器。用户侧栏通过「切换工作区」进入。Job、Window、Run 和历史导入四个主账本共用玻璃外壳和数据查询的表格基础样式，按 S08-03 确认例外在桌面表头和数据列内居中，数据行使用稳定阅读面；明细和弹层表遵守通用规则。Job、Window、Run 和历史导入共用右侧抽屉外框，覆盖原账本，头尾固定且正文独立滚动。窄屏抽屉全宽，账本按记录卡片重排。概览用双列元数据呈现主要信息，到报子项与批次记录使用带状态的列表。历史导入详情上方呈现编号与状态，下方沿时间线展示受理、开始与结束。采集工作台使用全站共用账号设置面板。对象审计先显示对象与时间筛选，再呈现记录。共享日期编辑器随小时列表展开重新定位，并在视口内滚动。目录筛选按 S08-04 源地方纯日期变体，对象审计使用允许的任意时刻或范围编辑。首次到期选日期及小时，明确显示继承的模板分钟、时区和模板版本。页签、确认和追踪使用同一蓝色交互体系，投递、运行结果和窗口覆盖各用对应的状态语义。[M08 浏览器验证](docs/verification/M08_COLLECTION.md)

**M09 已实施的构图。** 复用首页固定侧栏、顶部布局、账号、下拉框与浅蓝选中态。管理台顶部为通栏筛选，目录日期按 S08-04 源地方纯日期变体；桌面队列与详情左右并排。详情依次显示事件、影响范围、当前与候选修订双卡、稳定表底的字段差异、横向证据卡和裁决表单。四张证据卡按用户操作独立读取。窄于 768 CSS px 时，队列与详情分屏，筛选可折叠，差异表在自身区域内滚动。当前和候选的绿色与琥珀色表示版本角色。裁决确认初始焦点位于取消按钮。实测视口及业务字段差异见[浏览器证据](docs/verification/M09_QUALITY.md)。

**M10 已实施的构图。** 复用首页侧栏、顶栏、筛选输入、状态色和图表容器。桌面先排五张健康卡，再排独立队列条、双列诊断和双列趋势。证据入口位于诊断下方，桌面约三分之一宽的右侧抽屉使用云白实体底，移动端切换为底部面板。窄屏诊断条件使用独立面板，诊断日期按 S08-04 源地方纯日期变体，趋势上下排列。写入、窗口和 Run 分别使用对应语义，图表和数据表均采用后端源地方日桶。[实际截图与原型差异](docs/verification/M10_OPERATIONS.md)

**M01 全国总览摘要（S08-01，已确认）。** F 默认成功态在页首上下文之后依次展示「优良城市」「污染城市」「样本到报率」。页首标明观测时刻、时区、产品及 F 身份，覆盖城市数归覆盖区。桌面三等宽卡，390px 和 320px 默认字号下保留同排；文字放大或单卡内容无法完整阅读时可重排。合成快照范围和到报分母须可查。正式 L 域按同一获权快照、标准分组及可披露分母显示已支持的统计，区分零值、未知分母和不可用。F 成功、F 非成功、正式模式分别验收。[当前指标](src/national/DemoOverview.vue) · [布局](src/national/sky.css) · [正式分支](src/national/NationalPage.vue)

**S09-01 正式首页基本路径（已确认）。** 正式域以当前契约下可搜索、可按获权链接下钻的同刻列表作为基本联调路径。支持的摘要、榜单、分母和不可用面板按正式投影呈现、自然重排，不用 F 图表或当前分页行数补造全国数据。正式地图（G01）须另有合法点位、素材许可、坐标对应、同一快照及真实响应证据；全国趋势（G02）按正式汇总契约及时间窗另行验收。两项未取得证据时分别记为 `NOT RUN` 或相应缺口，不阻塞基本同刻列表路径，但不得宣告正式地图或全国趋势通过，也不得删减 F 的完整展示。正式下钻仍需真实会话和城市、站址期等合法样本。

**S09-02 治理起点（已确认）。** 核心治理链从已有获权 Job 或按登记模板建立停用草案开始，沿当前计划配置、预览、获权激活、Run／Window 及裁决契约验收。正式新建入口以登记模板草案为准；独立新建无计划 Job 后再挂首个计划（G16，M08）不是本轮 S09 阻塞项，缺口仍保留，若后续启用须另定写入契约、权限、版本与回执。不得将 F 独立建 Job 路径当作正式域已支持。模板登记仍属受控主机。研究合法 H 双站样本及可信来源更正等其他契约和证据缺口不因本次范围取舍自动关闭。[接口契约](docs/API_CONTRACT.md) · [治理契约](docs/api/GOVERNANCE.md) · [联调缺口](docs/INTEGRATION_GAPS.md)

**全国计数与分母。** `summary.state=unavailable` 显示「当前不可提供汇总」。`state=ok` 时，等级分布按每个已核标准分别展示，分母用该组 `ranked_city_count`，分子用组内 `distribution` 的等级计数。`summary_eligible_city_count` 标「获准汇总城市数」。覆盖摘要使用 `covered_city_count` 和 `expected` 表达当前查询覆盖数和应到数。前者为 `null` 时写「覆盖数不可完整提供」，后者为 `null` 时写「应到未知」。已知零显示 `0`，分母为零或未知时只列可披露计数。权限、分组与分母全部以服务端结果为准，前端不以列表计数推算。字段、空值与样例见[全国汇总及响应结构](docs/api/openapi.v1.json)，待验证·契约。

**M02 独立工程实现。** 沿用首页侧栏、顶部工具区、玻璃表面和图表容器。城市身份置于页面标题区，C 主读数叠在浅天色与通用城市天际线上。六项指标在桌面采用三列，手机采用两列。T 独立锚点、半开区间和时区随图表口径披露，数据表提供逐槽读数。正文居中，最大宽度 1280 CSS px。趋势图独占一行，下方的小时历史和 D 日报在桌面并排，在手机上纵排。S 目录在窄屏重排为站址卡片。[宽屏 Chrome 实测](docs/verification/m02-prototype-layout-1600x830.png)

**M02 原型对照。** F 北京样例显示城市代码。北京、上海、广州、深圳、武汉、南京、杭州、成都、重庆、西安和天津各有天际线插画。专属图与通用图都连续绘制一张图，以 AQI 数字的位置确定渐变，数字中线处可辨认图像。远景适应宽度，右侧地标按比例显示，手机上观测时间另起一行。页面沿用 M01 背景、导航、表头和读数样式。指标卡为浓度、单位和等级状态预留固定列，以颗粒大小和简化分子构图区分六项污染物。中宽视口保留分隔线及同行读数，窄屏重排名称与读数。T 图共用首页悬停提示框与时间轴指示线，显示对应时槽的 AQI。T 图口径可从标题行展开，报表保留独立锚点与范围。F 站点表按名称、类型、站址期、最近观测时间、AQI、主要污染物、操作排列。窄屏把类型和 AQI 放在站名旁。站点名称和类型先匹配全城 F 目录，再按游标分页。总数与匹配数分别显示。[天津主图](docs/verification/m02-aqi-tianjin-1224.png) · [郑州通用主图](docs/verification/m02-aqi-zhengzhou-1224.png)

**M02 趋势报表与小时历史。** 两处沿用站点详情的玻璃材质和观测状态样式。T 报表把城市、时区、标准、锚点与范围放在独立表格滚动区上方，滚动时表头固定。C 小时历史把半开查询范围和报告窗口放在查询面板，六列结果置于独立面板，在手机上排成带字段标签的卡片。[趋势报表](docs/verification/m02-trend-report-937x813.png) · [小时历史](docs/verification/m02-hourly-history-937x813.png)

**M02 观测条件。** 与趋势报表共用玻璃弹窗材质，输入框和下拉框沿用首页控件。城市观测、近期趋势和站点目录按产品分组，桌面两列，窄屏单列。短屏滚动表单区域，标题、应用按钮及校验提示留在原位。「应用条件」以蓝色填充和白字呈现，悬停加深底色，按下增加内阴影，键盘聚焦显示外描边。[桌面悬停](docs/verification/m02-condition-apply-hover-937x813.png) · [手机按下](docs/verification/m02-condition-apply-pressed-390x844.png)

**M03 站点详情。** 沿用首页的晴空背景、侧栏、顶部工具栏、统计卡、表头和观测控件。身份卡依次排列站名和标签、同行站点资料、观测来源。最新观测按原型展示六项污染物，卡内保留数值、单位、状态和独立观测时刻。与截至时刻同日且同一时区时仅显示时分。AQI 按授权在历史中可查。切换站址期时收起上期读数和曲线。趋势线在连续观测点之间平滑绘制，缺测处断线。最近观测表与指标卡共用最近观测，按 PM₂.₅、PM₁₀、SO₂、NO₂、O₃、CO 的顺序列出已有记录，各行保留实际观测时间。实装记录曾采用表头与记录沿列首对齐；S08-03 定稿后按通用列职责审计，可比较数值列靠结束边，不将该历史样式扩大为第三处例外。窄屏指标卡分两列，最近记录重排为卡片。[奥体中心站桌面](docs/verification/m03-dashboard-1440x900.png) · [奥体中心站手机](docs/verification/m03-dashboard-390x844.png) · [哈尔滨站桌面](docs/verification/m03-dashboard-harbin-1548x813.png)

**M06 数据查询工作台。** `/app/query` 复用首页晴空背景、导航、顶部胶囊、分段按钮、玻璃筛选和表格材质。桌面采用固定表头的批次滚动表格，右栏随当前行显示证据，技术字段按需展开。窄屏时证据置于结果下方，表格在自身容器内横向滚动，打开证据后聚焦标题，关闭后回到原入口。时间编辑浮层沿用筛选菜单材质；任意时间窗口及日报纯日期分别取 S08-04 对应变体，外壳与内部稳定阅读面按 S08-02。F 样本独立标识，应用后的条件、游标与当前记录联动。[工作台桌面](docs/verification/m06-query-1586x992.png) · [工作台手机](docs/verification/m06-query-390x844.png)

**单站点历史。** `/app/query/history` 保留单站专用日期和小时筛选。数值与单位同字号、按基线对齐并留出间距。多指标站点历史沿用查询条件和接口游标读取范围内的获权记录，将同一观测时刻的记录合为一页，获权 AQI 排在前面，六项污染物按 M03 的顺序展示。初始筛选区显示日期选择器与 UTC+8，日历打开在最近有记录的月份。选中日期后，在右侧显示该日的小时选择器，包含「全部时刻」及 00:00 至 23:00。日期输入区为完整日期和清空图标预留空间，按 S08-04 已有观测日期与小时变体无需文本输入。换日恢复「全部时刻」并清除先前所选时刻。小时条件匹配整点起至下一整点前的记录，分页在筛选结果内按实际观测时刻倒序切换。日期、小时及当前页时刻随地址恢复，仅恢复当前获权且仍有效的选择；空小时保留筛选入口及空态。小时选择器淡入并轻移，菜单淡入淡出并轻移，选中状态平滑过渡。同批次记录内筛选或翻页切到另一观测时刻时，表头与表体按 S08-05 一起淡入并轻移 180ms；首载或换批由正常状态接替。启用减少动效时即时切换。[哈尔滨站初始状态](docs/verification/m06-harbin-date-first-1548x813.png) · [手机小时菜单](docs/verification/m06-harbin-hour-cascade-390x813.png)

### 业务状态的界面反馈

下表保留 S08 Mock 所依据的呈现语义。产品字段、错误码、票据、冲突处理与 Run/Window 状态映射待验证·契约，交互可达性待验证·浏览器。

| 实际状态 | 界面反馈与下一步 |
|---|---|
| 首载 / 局部刷新 | 按 Interaction Patterns 的结果保留表，在所属区域显示可读加载或刷新状态。已应用条件和刷新动作可辨。 |
| 无匹配 / 指定窗无数据 / 尚未覆盖 | 分别给出「调整筛选」「此窗口无观测，可改时间」「所选范围尚无覆盖」。仅在有覆盖依据时标「尚无覆盖」。空集保留零行，最近有效另有明确入口。 |
| 部分缺失 / 陈旧 / 时效未知 | 按指标显示缺口与有效数。可用旧值附观测时间、年龄和后端时效状态。未知写「时效未知」，保留可用依据。 |
| 来源异常 / 系统错误 | 来源中断或结构异常注明产品及可访问的最后有效观测。系统错误就地重试并留可公开定位信息。服务健康与来源状态分开。 |
| 样本不足 | 展示已获准的共同样本、分母与原因，允许缩小/调整范围或读明细。整期结论、排名或相关系数以服务端可提供的结果为限。 |
| 会话失效 / CSRF 失败 | `403/not_authenticated` 清理私有状态并引导登录。`403/csrf_failed` 重新取 token，按该操作的重放约定决定能否重试。 |
| 用途拒绝 / 对象权限不足 | `403/use_denied` 就地说明当前用途不可用，保留仍获权的工作区。`403/permission_denied` 按端点限制相应操作，私有对象的 `404/not_found` 统一提示「无法访问该对象」。保留有效会话，撤权按 Interaction Patterns 清理。 |
| 分页或研究输入变化 | 游标的 `409/page_changed` 或分页输入变化，保留安全条件，从第一页重查。研究明细、导出的 `409/input_changed` 需要重跑父查询，以新 `input_version` 重取明细或重新申请票据。 |
| 票据到期 / 高影响写冲突 | 下载遇 `410/expired` 时按现权重新申请票据。高影响写入发生版本冲突时重新读取当前对象及版本，再判断原意图是否适用。 |
| 任务受理 / 投递 / 执行 | 回执提供 Run 入口。分别显示投递状态、Run 执行阶段和对应业务窗口，执行中仅显示已知进度。投递未确认时沿原 `run_id` 查账。暂停、拒绝、执行失败各说明可公开原因和获准动作。 |
| 单次运行结果 | 分列本次 `Run.status`、`dispatch` 和 `Run.result`。`new` 显示新增观测，`no_new` 显示「本次未产生新观测」，`quality_only` 显示「本次仅有质量结果」，`changed` 可查变化收据，`partial` 给出获准的缺项。 |
| 业务窗口 / 部分可见 | 单列 `Window.state`、`Window.result` 与窗口覆盖。例如后一次 Run 为 `finished + no_new`，同一 Window 仍为 `open + partial`，分别显示「本次运行已结束，未产生新观测」与「窗口仍开放，部分到报」。`partially_authorized` 且 `result=null` 时只显示获准部分及适用动作。[业务规则](docs/BUSINESS_RULES.md) |

## Verification

### 验证矩阵

| 验证视口 | 对应的布局行为 |
|---|---|
| 1920×1080、1440×900、1280×800 | 桌面侧栏收展、标题、图表、表格及网格边缘。 |
| 768×1024 左右 | 条件栏变摘要或抽屉，已应用条件和结果保持可读。 |
| 390×844、320 CSS px | 主区单列，地图或表格按适用条件局部二维滚动，关键对象列、详情、外围筛选和分页可达。 |
| 响应式断点两侧、软键盘、横竖屏 | 检查下排折叠、Popover 翻转和 Dialog 正文滚动，触发器及主操作均可达。 |

### 可访问性基线

采用适用的 [WCAG 2.2 AA](https://www.w3.org/TR/WCAG22/)。普通文字、大字、必要非文本线索和实际命中区按硬约束 5–7 验收。大字指常规至少 18pt，粗体至少 14pt。检查焦点顺序与遮挡、从点击/触摸和键盘完成流程、控件的名称与状态、错误关联、状态播报以及适用的登录粘贴和密码管理器行为。交互的等价入口保持同样权限和数据范围。

验证 200% 文字放大、320 CSS px 重排（例如 1280 CSS px 视口的 400% 缩放）及文字间距覆盖。按 WCAG 1.4.12 测试行距 1.5 倍、段后 2 倍、字距 0.12 倍、词距 0.16 倍，并按语言适用性检查。核对标签、数值、筛选和反馈无截断或功能丢失。浏览器与辅助技术组合待验证·浏览器。

**建议新增 · 关键 WCAG 条款的项目证据。**

| 适用条款 | 应保留的证据 |
|---|---|
| 1.4.1、1.4.3、1.4.11 | 状态、AQI 与序列具有可读冗余线索。文字及必要控件、图形按实际背景测比值。字号换算以常规 24 CSS px、粗体约 18.67 CSS px 对应大字门槛。3∶1 和 4.5∶1 的下限以未舍入比值判定。 |
| 1.4.4、1.4.10、1.4.12 | 文字放大、重排与间距覆盖分别运行。有二维必要性的地图、整张数据表可使用局部例外，页面外层、单元格及表外操作仍检查适用的重排。 |
| 1.4.13、2.1.1、2.1.2、2.4.3、2.4.7、2.4.11 | 检查悬停/聚焦内容可关闭、可悬停和持续显示，完成键盘路径、模态层退出与焦点恢复。焦点组件不可被作者内容完全遮蔽。项目共享焦点环另按硬约束验收。 |
| 2.5.7、2.5.8 | 地图中依赖拖动的功能另有单指非拖动入口。按实际目标查 24×24 CSS px 及适用例外。使用间距例外时检查小目标的 24 CSS px 直径圆与相邻目标或相邻小目标圆的关系，记录证据。触屏主要操作另执行项目 44×44 门槛。 |
| 3.3.1、3.3.2、3.3.8、4.1.2、4.1.3 | 记录错误关联、帮助、名称/角色/值与动态状态，登录可用辅助方式通过认证。人工走查实际可访问树、粘贴和密码管理器。 |

**建议新增 · 玻璃合成检查。** 以测试数据让最亮、最暗、高纹理和滚动后的背景确实出现在文本或线索下方。保持滤镜、父级、边缘、渐变、遮罩和几何状态，隔离要测的前景后采集同位置的有效背景色，或使用等效的浏览器渲染取样。边、焦点和图形记录接触的各实际相邻色。用 CSS 前景或其有透明度时的合成值与相应底色按 WCAG sRGB 相对亮度计算，保存视口、DPR、状态、像素位置和最低通过比值。避免直接拿抗锯齿边缘像素做文字前景，也避免用单一平均背景代替梯度上的最差位置。

只要有一处低于适用阈值，先切可读前景或调整稳定底，再按材质表从 clear 降至 regular 或实体，重测该组件矩阵。`text-muted` 继续限制在浅色稳定底。内容卡的必要读数和图形未获得合成证据时使用稳定底。自动计算、渲染取样、键盘和辅助技术走查各自记录。[WCAG 2.2](https://www.w3.org/TR/WCAG22/) · [W3C 非文本对比度说明](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast)

### 设计验收按变更选场景

1. 记录路由、数据版本、构建与实际浏览器环境。用同视口、同状态的前后截图核对布局、裁切、整体横溢和局部滚动。
2. 使用鼠标、触屏/触摸模拟和键盘走默认、展开、选中、预览、快速关闭或反转。检查最终状态、实际命中和焦点。选城覆盖「点 A → hover B → 切指标 → 榜单选 C → 表选 D → 复位视野 → 下钻详情 → 返回」，另验翻页、筛选排除和撤权中旧响应迟到。
3. 在有字或图形的实际位置取合成背景，覆盖最亮、最暗、滚动后、选中及弹层。用计算样式或隔离前景的截图取得背景，避开字形抗锯齿像素。分别记录前景、合成底、线索相邻色、适用阈值与结果。边界、焦点与图表状态一同采样。
4. 走一遍减少动效、实体模式、支持的减少透明度、强制颜色与无滤镜分支。验证 Dialog 内两类选择面板的 Esc 顺序、还焦，以及触发器消失后焦点去向。用长中文、缺口、删失、零、未知标准、慢响应、权限拒绝和版本变化样本测状态与播报。
5. 观察控制台、请求、滚动/缩放掉帧、浮层反复开关和图表实例回收。材质、定位、焦点改动按实际影响在 Chromium、Firefox、WebKit/Safari 复核。触摸模拟和真机证据分别记录。
6. 分别记录自动检查、浏览器验收与真实会话联调。S08 用 Mock 验证交互，S09 验证真实 Session、合法业务响应及城市在线证据；同刻列表和从已有获权 Job 或登记模板开始的治理链为基本路径，正式地图、全国趋势分项验收，独立建无计划 Job 后挂首个计划不作为阻塞。未运行的项目注明原因。

### 视觉回归与跨浏览器验收

**建议新增。** 可使用项目现有截图工具或 Playwright。按变更影响选择覆盖集。主题、共享组件、字体或布局系统变更覆盖下表的代表视图，并增加受影响的同族状态。

| 参考场景 | `1440×900` 和 `390×844` 的证据 |
|---|---|
| M01 全国总览 | F 成功态三项摘要、正常覆盖、图例与选中城市；桌面另有侧栏收展，移动另有抽屉及局部滚动，补验 320px 与 200% 文字。F 非成功及正式同刻列表分别留证，正式地图与全国趋势单列。 |
| M02 城市详情 | 主读数、单位、长对象名、缺口和趋势替代数据表。 |
| M06 数据查询 | 已应用条件、密集表格、长标签、空/失败反馈。 |
| M04/05 分析工作台 | 至少一个代表性比较或分析场景，包含图例和共同覆盖提示。 |
| 管理工作区 | 至少一个已获权的代表性账本或治理视图，含结果反馈。 |
| 共享组件与回退 | Select 和 Date Picker 各在 Dialog 内展开并走还焦，地图工具和 Tooltip 完成选中/焦点，实体模式固定覆盖。 |

1. **固定输入。** 记录提交或构建 ID、测试身份、可脱敏的 fixture、时钟、时区、语言、字体、浏览器版本、操作系统、视口、DPR、渲染器及偏好。图表等到字体、数据和渲染状态稳定。快照使用相同环境和稳定的数据，不遮盖被验组件、读数、焦点或边界。记录动态区域例外。
2. **比较基线。** 每个引擎与自身基线比较。对比整页和关键组件，保留基线、实际和差异图。阈值按同环境的重复性及变更范围记录。关键对齐、文字裁切、焦点遮挡、语义颜色和数据辨识须单独核对，无论变化面积。首次基线由相应维护方确认，后续截图批准随变更记录。基线未定位时先采集候选并标明待验证。
3. **实测回退。** Chromium、Firefox、WebKit 分别验适配功能层、实体模式和无滤镜分支。Safari 实机单列系统偏好、前缀滤镜及触摸表现，WebKit 自动化证据独立记录。检查系统减少透明度的支持及触发行为，环境不支持或无法模拟时将该分支记为 `NOT RUN` 并实测显式实体模式。验证强制颜色、减少动效与高对比偏好；实际覆盖随操作系统和浏览器证据记录。
4. **保留复现材料。** 对关键交互单独验证响应式中间宽度、缩放、可访问树和时序；失败保留复现步骤、相邻状态与渲染采样。截图通过后仍需通过相应交互和数据权限检查。[Playwright 视觉比较](https://playwright.dev/docs/test-snapshots)

### 本次定稿的专项验收与公共层整改

| 条款 | 专项证据要求 |
|---|---|
| S08-01 | 三项摘要顺序、390／320 CSS px 默认同排、放大后重排、分母与单位；F 成功／非成功及正式 L 分别记录。 |
| S08-02 | 玻璃最亮及最暗背景、卡内滤镜层数、日期及长浮层稳定填充、实体及系统偏好、200% 放大、标题聚焦和键盘恢复。 |
| S08-03 | 主统计表与四个桌面主账本例外，常规表列职责，等宽数字、负值、小数、长任务名、缺测、移动字段标签。 |
| S08-04 | 四种日期变体，短屏翻转、跨日与精度、小时空态、草稿取消、游标、有效 URL 恢复、嵌套 Esc 和焦点。 |
| S08-05 | 短颜色反馈、菜单和结果切换、首载与换批、快速反转、账号密码区折叠与焦点、图表更新及减少动效。 |
| S09-01／S09-02 | 真实会话与合法样本下分别记录同刻列表、地图、全国趋势、治理起点、计划、运行与裁决；不将演示通过写成正式联调通过。 |

**已知缺口（不在纯文档定稿中修代码）。** 以下为现有候选审计记录及送审代码提供的整改线索，不是全部缺口清单：

| 范围／条款 | 已知证据与状态 | 后续整改归属 |
|---|---|---|
| SYS-403／404；硬约束 3、S08-02 | 候选稿记录 Chrome 强制色媒体查询匹配后，主卡仍为 `blur(18px) saturate(1.24)`：`FAIL`（历史运行证据）。 | 公共层整改，保持普通模式玻璃构图；复验 `/app/forbidden` 及未注册 `/app` 子路径的强制色与其余回退。 |
| 全国选城；硬约束 12 | 候选稿记录地图将名称写入 `city`，榜单和表格仅打开详情，未实现稳定 ID 全入口同步；按源码核实并修正。 | 公共选择状态／M01；F 场景多入口同步、回退、复位。 |
| 主题来源；硬约束 1、16 | 送审代码使用 `--sky-*`、`src/style.css` 的 Element Plus 覆盖和 `src/shared/chartTheme.ts`；未发现从 DESIGN YAML 到三端的生成／校验链路。不得把文档数值正确当成运行主题通过。 | 公共主题先行，映射既有消费入口；`--sky-*` 可作为兼容别名或适配入口，须来自同一解析来源，不因变量前缀不同单独重写页面。 |

逐页整改应先识别上述公共缺口与本页真实偏差，再比较同视口同状态的前后图；已确认的正常模式视觉不做无依据的扁平化或整体重构。新发现问题逐条登记，不能仅因当前实现如此就扩大例外。

### 可自动执行的检查

使用 Google Labs [DESIGN.md CLI 0.4.0](https://github.com/google-labs-code/design.md/releases/tag/0.4.0)。其 lint 检查核心 YAML、引用、章节顺序与可识别的静态配色。项目扩展、页面合成与完整流程各用相应的项目检查。

```sh
npx --yes --package "@google/design.md@0.4.0" designmd lint DESIGN.md --format json
npx --yes --package "@google/design.md@0.4.0" designmd diff DESIGN.prev.md DESIGN.md --format json
```

在 Windows 如需避开脚本执行策略，用 `npx.cmd`。纳入仓库依赖时用 `npm i -D --save-exact @google/design.md@0.4.0` 并提交锁文件。将仓库基线提交中的原稿导出为比较用 `DESIGN.prev.md`，记录提交 SHA。独立交付时使用经核对的原附件副本，记录其 SHA-256。比较文件只是验收输入。

**机器门禁。** 以 lint JSON 的条目为依据。CLI 对 warning 可返回成功，因此遇到任一 error、`contrast-ratio` 或清单以外的 warning 均失败。仅允许下面列出的既有装饰/图形色路径和 `x-aq` 的项目扩展提示。项目可在 lint 的管道后运行下列检查。

```sh
npx --yes --package "@google/design.md@0.4.0" designmd lint DESIGN.md --format json | node -e '
const fs = require("node:fs");
const { findings } = JSON.parse(fs.readFileSync(0, "utf8"));
if (!Array.isArray(findings)) throw new Error("Missing lint findings");
const allowed = new Set([
  "sky-1", "sky-3", "sky-4", "sky-glow", "sky-deep",
  "glass-nav", "glass-tool", "glass-content-start", "glass-content-end",
  "glass-kpi-start", "glass-kpi-end", "lens-start", "lens-end",
  "edge-glass", "edge-content", "scrim", "border-subtle", "border-field", "border-strong",
  "chart-grid", "chart-axis-line", "chart-tip-border", "chart-pointer",
  "highlight-panel-top", "highlight-panel-bottom", "shadow-panel",
  "highlight-float-top", "highlight-float-inset", "shadow-float-near", "shadow-float-far",
  "highlight-elevated-start", "highlight-elevated-end"
].map(name => "colors." + name));
const isAllowed = ({ rule, path }) =>
  (rule === "orphaned-tokens" && allowed.has(path)) ||
  (rule === "token-like-ignored" && path === "x-aq");
const failures = findings.filter(item =>
  item.severity === "error" || item.rule === "contrast-ratio" ||
  (item.severity === "warning" && !isAllowed(item)));
if (failures.length) {
  console.error(JSON.stringify(failures, null, 2));
  process.exitCode = 1;
} else {
  console.log("DESIGN.md lint gate passed");
}'
```

`diff` 记录核心 token 变化，并可能把警告数量增加作为回归。对比差异应逐项归因。同一 `x-aq` 扩展产生的已知提示使用上方精确允许项，其余新增警告照常失败。项目专用组合及核心字段值另做严格解析差异，避免依赖 CLI 对扩展的视野。原稿中 DTCG 导出关于组件、字阶和 alpha 的观察仍属原稿记录。若启用跨工具交换，先比对实际转换产物和 [DTCG 颜色格式](https://www.designtokens.org/tr/2025.10/color/#format)。

### 共享主题生成与适配

**建议新增 · 建立清晰的生成与检查契约。** 项目主题入口和锁定版本待验证·契约。下表是待实现、待验证的输入输出规格。

1. 从 YAML 前置区读取数据。拒绝重复键、非法类型、未知的项目扩展字段、无法解析的 YAML 别名，以及找不到目标、类型不匹配或循环的 `{路径}` 引用。前置区 YAML 锚点先由解析器展开，再解析花括号引用。组件的组合引用保留到完整路径，错误点报路径和出处。
2. 以完全解析的 light 语义与组件值、正文明确标出的组合为输入。组合 CSS 声明块可作为构建输入，或在主题工具侧建立可比较的对应清单。生成器消费组合时校验输入覆盖及引用，所有运行端产物在相同来源变化时同步更新。字阶保留单位、行高与 `fontFeature`，颜色保留 alpha，组件内距保留轴向含义，供各平台各做一次类型转换。
3. 产物以同一份来源指纹或版本定位。用相同输入生成两次并核对稳定性。取既有主题入口整合后，按项目正常入口构建。在真实 DOM 和图表实例抽查关键映射，并对缺失路径、未使用的颜色及适配器本地色值记录原因。符合所有已定约束后，才把生成与运行验证写为 `PASS`。

| 消费端 | 角色与映射 | 验证方式 |
|---|---|---|
| CSS | `colors.text-primary` → `--aq-color-text-primary`，`rounded.panel` → `--aq-radius-panel`，`spacing.lg` → `--aq-space-lg`，`typography.body` → `--aq-font-body-*`。输出已解析组件字段，应用壳、状态与回退使用相同命名和作用域。字号参考值转 rem，`tnum` 转 `font-variant-numeric: tabular-nums`。 | 比较生成值和计算样式，抽查 alpha、默认字体和行高、字段尺寸、玻璃与实体、AQI 填色与轮廓。 |
| Element Plus 全局主题 | `colors.primary` → `--el-color-primary`，`text-primary`、`text-secondary` → `--el-text-color-primary`、`--el-text-color-secondary`，`rounded.control` → `--el-border-radius-base`。系统成功、警告、危险及信息角色映射到同名全局色。组件的 hover、pressed、边框与读数覆写优先消费对应项目语义或组件 token。依库所需生成衍生色并审查实际文字配对，保留主动作的既定 hover/pressed 值。 | 按锁定版本核对可用变量及样式加载顺序，测按钮、输入错误、focus、禁用、加载。 |
| Element Plus 浮层 | Select、Date Picker、Dropdown、Popover、Dialog、Drawer、遮罩按材质组件表映射到实际挂载节点。把共享变量放在应用与 portal 都能读取的位置，实体/偏好模式与受控类同步到两处；组件级映射以锁定版本确认。 | 实际查看 teleport 后的变量继承、滚动翻转、模式切换、按层 Esc 与焦点归还。 |
| ECharts 共享主题 | 从解析值生成主题和可复用 option，向 Canvas/SVG 传可识别的最终色值。序列 palette 取 `chart-1…chart-5`，`textStyle.fontFamily` 取共享字体，通用文字和图表提示按对应角色。使用 `registerTheme` 与初始化主题参数的项目集成须经版本核对。 | 核对项目依赖、实际初始化、图表尺寸、渲染器、主题改变后的图表状态和实例回收。 |
| ECharts 页面 option | `axisLabel` 取 `chart-axis-text` 和 `chart-tick`；`axisLine.lineStyle` 与 `splitLine.lineStyle` 分别取轴和辅助网格色；`tooltip` 从 `chart-tooltip` 取得实底、字色、圆角及 `[6, 10]` 上下/左右内距，`axisPointer.lineStyle` 使用 `chart-pointer` 作辅助。AQI 颜色由获后端确认的等级按独立角色分配，页面加上缺测、序列标记、数据表及可达状态。 | 实例检查实际 option 与主题覆盖顺序，抽查同语义色、线宽、缺测断线、单位、读数、图例、地图与明细。若项目版本支持，可用 aria/decal 增强图形表达。 |

适配参考 [Element Plus Theming](https://element-plus.org/en-US/guide/theming)、[ECharts API](https://echarts.apache.org/en/api.html)、[ECharts Aria](https://echarts.apache.org/handbook/en/best-practices/aria/)。以项目锁定版本及实际 DOM 和图表输出为验收依据。

### 待验证事项

| 类别 | 所需证据 |
|---|---|
| 待验证·契约 | 仓库文档、相对路径、共享主题入口和 Element Plus / ECharts 锁定版本。页面、业务及接口的职责，正式 F/L/C/T/D/S/H 标识和用途，素材与坐标资料，来源许可及版本。 |
| 待验证·契约 | 样本与接口中 AQI 的 `*Level`/系列/待核规则，授权与聚合分母、零和 null，覆盖、单位、时区及窗口。确认查询身份与版本、明细/导出粒度、撤权与重放、能力投影、票据、版本冲突、Run/Window 的状态和动作。 |
| 待验证·浏览器 | 原型截图与构建对应，实际字体、字阶与对齐，画布和玻璃文字、控件轮廓、焦点及必要图形的合成对比。实体、减少透明度、强制颜色与减少动效。 |
| 待验证·浏览器 | 响应式、缩放、文字间距覆盖、软键盘和触屏命中。Dialog 子面板、焦点归还、状态播报，图表数据表/Tooltip、单指可达地图，选城、下钻、返回及迟到响应。 |

### 验收记录模板

| 条款／页面／受影响状态 | 规则状态 | 实现状态 | 验收状态 | 源版本、数据模式、环境及视口 | 操作、可打开的证据与未覆盖范围 |
|---|---|---|---|---|---|
| | 已确认／候选／确认的例外 | 已实现／部分实现／未实现 | `PASS` / `FAIL` / `NOT RUN` / `N/A` | F／正式／响应注入；提交、构建、浏览器、视口、偏好 | |

条款冲突记 `CONFLICT`，列明相互冲突的条文及处理决定，不按冲突条款机械改代码。截图只支持布局和可见文案，计算样式、键盘、动效和真实数据另有对应证据。

组装验收证据时，补充基线与目标版本、场景输入与访问范围、自动检查产物、测试报告或截图位置、对比度最低点及差异处理。通过项写 `PASS`，发现偏差写 `FAIL`，未能运行写 `NOT RUN` 并注明缺少的条件。区分独立文档检查、S08 Mock 与 S09 真实会话的范围。以受影响功能实际可运行的入口复查。

### 历史独立文档检查（2026-11-25，不代表当前版本）

历史比对输入为原附件 `DESIGN(1).md`，SHA-256 为 `68683a37f15d3ebc40f4843d66cfc1c9eb266382d6c9836ed535ff57423d882f`。

| 检查 | 结果 |
|---|---|
| 严格 YAML、两类引用、章节结构、原业务与交互章节 | `PASS`。原有 14 个二级章节及核心 token 路径和值保留，项目扩展引用可解析。 |
| Google Labs CLI 0.4.0 lint 与核心 token diff | 项目门禁 `PASS`。0 条错误，32 条原图形或装饰提示，新增 1 条允许的扩展提示。核心 token 的新增、删除、修改均为空，CLI 原生 diff 因提示数量增加返回非零，已核对归因。 |
| 静态配色与 AQI | `PASS`。47 组组件实体文字配对最低 4.84∶1。AQI 轮廓对样本不透明底最低 5.24∶1。数值为静态计算，运行环境证据另记。 |
| 项目主题构建、实际页面、浏览器合成、视觉回归和辅助技术 | `NOT RUN`。本次输入是独立规范附件，需项目构建、实际页面及测试环境。 |

### 2026-11-27 定稿来源及证据边界

本版底稿为送审第二版（与代码包内 `DESIGN.md` 一致），SHA-256：`b14f4cda6ff15c6d6d35fb96b80f1b14b1dfa4181e472257ebc2c186e70c7618`。依据 `DESIGN_AUDIT_CANDIDATE.md` 五项候选条文及用户确认的 S09 验收范围定稿；该候选稿 SHA-256：`d270e21159e87709f78a225382413f2613c90f58e887bebaa395d3d764245ad5`。本版自身指纹由交付文件外部记录或提交 SHA 定位，不在文件内自引用哈希。

| 证据 | 范围与结果 |
|---|---|
| 文档定稿 | 五项规则及两项 S09 决策同步到相应章节；原 YAML 前置区和原核心章节保留。本轮仅文档变更。 |
| 本次本地静态文档检查 | `PASS`：YAML 前置区与底稿逐字一致，严格重复键检查及 179 个花括号引用目标和循环检查通过，保留 97 个颜色角色、71 个组件规格、14 个二级章节和 20 条硬约束；Markdown 表格列数和代码围栏基本检查通过。此检查不替代锁定 CLI 门禁。 |
| 候选稿历史 Chrome 复验 | Windows Chrome 158.0.8127.88、F 开发服务：候选稿记录三项摘要在 1440／390／320 CSS px 同排，403／404 普通和减少透明度材质、分析表列内居中、站点历史日期／25 项小时选项及 180ms／减少动效的专项检查 `PASS`。这些是候选稿所载证据，不是本次定稿重新运行结果。 |
| 候选稿历史强制色检查 | 403／404 主卡滤镜未关闭：`FAIL`，进入公共层整改。 |
| 当前全站运行验证 | `NOT RUN`：未重新运行项目构建、类型、单测、CLI lint 门禁、全站视觉回归、实际玻璃对比度、其他浏览器、真机及真实 Django／正式授权数据。后续按本版重新验收。 |

## Maintenance

共享主题按 Overview 与 Verification 的来源规则生成一次。维护时给新增角色写明用途、状态、回退和验收示例。

- 改动 YAML 时运行 Verification 的 lint 门禁和基线 diff，并在说明中列出变化的 token。
- 实机微调仅适用于决策权表的 `[记录]` 项，记录原值、新值、取舍、受影响的视图及浏览器范围。
- 已确认的方向、功能范围与重大交互变更由用户确认。业务语义或冻结契约的变化遵循项目变更审查。

### 设计治理与迁移

**建议新增。** 采用现有维护角色。实际负责人、存放位置及仓库版本策略待验证·契约。

| 情况 | 维护动作 |
|---|---|
| 新增或调整值 | 变更说明列出意图、原值/新值、语义路径、组件和页面消费者、回退路径、受影响状态与验证证据。相同职责修改时先复用已有键。 |
| 值或组合兼容调整 | 依 `[自主]`、`[记录]` 或 `[询问]` 提交。同步规范、主题生成物和有意变更的视觉基线，记录三者对应的提交或构建指纹。 |
| 添加、替换、废弃 token | 维护记录包含旧键、替代键、受影响适配器、拟移除版本及负责角色。在消费者迁移并通过生成与视觉检查前保留语义兼容的别名；用基线 diff 和消费者搜索确认迁移。 |
| 破坏性改动 | 改键、改类型、改角色或影响已确认规则时给迁移清单、回退方式及项目影响，由相应维护方审查；触及 `[询问]` 的内容按原决策权执行。 |
| 争议、例外与基线更新 | 记录具体现象、依据、作用域、验收及确认人。按实际差异批准截图，不批量接受无法解释的差异。仓库实现或契约与本文有偏差时回到相应职责文件核对。 |

设计维护角色维护语义、材质与基线，前端维护角色维护生成、适配和兼容，业务/接口维护角色确认数据、权限及协议口径。`version: alpha` 表示交换格式版本。项目修订以本文修订记录和可定位的来源版本记录。

### 默认值索引

| 项 | 正式来源 |
|---|---|
| 组件尺寸、内距、圆角、字体与间距 | YAML 的 `components`、`rounded`、`typography`、`spacing` 及 `x-aq` 扩展。 |
| 首页构图、视口宽度约束、字阶流式规则 | Layout、Typography 的规范块和断点表。 |
| 阴影、高光、滤镜、渐变与叠放 | YAML 颜色及 Elevation & Depth 的组合参数与分层规则。 |
| 过渡、Tooltip 延迟、图表插值和绘图区尺寸 | Motion 和 Data Visualization 的规范参数。 |

### 修订记录

| 日期 | 内容 |
|---|---|
| 2026-11-24 | 原稿记载的示范首页测量日期，截图和构建对应关系待验证·浏览器。 |
| 2026-11-25 | 原稿按 Google Labs DESIGN.md alpha 与 CLI 0.4.0 整理，引入 YAML、决策权、硬约束及 Apple 取舍。原 §6、§8 的行为章节合并至 Interaction Patterns 与 Page Patterns，旧稿差异待版本证据核对。 |
| 2026-11-25 | 原稿记载的审计修订，统一主题来源、材质分工、结果失效、AQI 异常呈现、可访问性与自动验收。 |
| 2026-11-25 | 历史版本补齐 Token 分层、材质和组件变体、AQI 图例边界、中间宽度、主题适配与视觉回归。 |
| 2026-11-27 | S08 正式审计定稿：展示优先及 Apple 式质感、三项首页摘要、状态页主卡玻璃例外、两处主表居中例外、四种日期语义、动效及局部折叠例外；明确 S09 两项基本验收边界，保留公共层已知缺口。只更新文档，运行验证另行执行。 |
