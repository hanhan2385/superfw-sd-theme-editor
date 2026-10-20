# SuperFW Theme Studio · 设计规格 v0.9

> 配套评审页：`docs/design-review.html`（含 7 张界面预览）
> 代码基线：`ref/superfw` @ `1c9ff09`（master）

---

## 1. 审计摘要

### 1.1 结论

仓库内 **没有任何 Web 前端**（`.html/.js/.ts/.css` 为 0），Web 侧只有仓库外的 Hugo 文档站。
所谓「现有界面」= **卡带上的 240×160 菜单** + 文档站。主题是编译进固件的常量，不是资源文件。

### 1.2 关键代码坐标

| 关注点 | 位置 | 事实 |
|---|---|---|
| 顶级标签 | `src/menu.c:49-60` | 7 个：Recent / ROM / NOR / Settings / UI-Lang / Tools / Info |
| 渲染主循环 | `src/menu.c:2061-2114` | 顶部 16px 图标条 + 16px 行高列表 + window 弹窗 |
| 主题表 | `src/menu.c:213-228` | 6 组 × 6 色（White/Orange/Blue/Green/Purple/Dark），`RGB2GBA` 转 BGR555 |
| 主题应用 | `src/menu.c:2029-2042` | 写入调色板槽 16–19，IGM 用 240–243 |
| 主题设置页 | `src/menu.c:1930-1957` / `3072-3108` | `< n >` 左右切换，仅此一种改法 |
| 持久化 | `src/settings.c:106-133` | `/.superfw/ui-settings.txt`：`theme= / langcode= / recent_menu= / anim_speed= / hide_hidden=` |
| 图标 | `src/res/icons.h` | 17 个 16×16 OBJ tile，256 色 OBJ 调色板 |
| 字体 | `res/fonts/*.hex` → `fonts.pack` | unscii-16，8×16 位图；14 个语言包 |
| 游戏内菜单 | `src/ingame_menu.c` | 独立渲染器，复用 `IGM_PAL_*` |

### 1.3 五个诊断

1. 改色 = 改数组 + 重编译 + 刷机，创作闭环分钟级。
2. 只有整包 6 套，无法「只改强调色」。
3. 无预览、无对比、无量化反馈。
4. 无版本语义（1.4/1.5/1.6 能力不同却无提示）。
5. 图标/字库/语言三套 Python 脚本，门槛高。

---

## 2. 设计目标

| 编号 | 目标 | 验收标准 |
|---|---|---|
| G1 | 画布优先 | 打开即 240×160，编辑在画布上完成，≤3 步改一个角色 |
| G2 | 语义角色 | 7 个角色映射到硬件槽位，用户不见下标 |
| G3 | 约束前置 | 槽位/对比度/ΔE/字库覆盖实时可见，导出零意外 |
| G4 | 一条路径到产物 | 浏览器内完成解析→编辑→校验→导出 |

非目标：不做像素画编辑器（只做导入、量化、映射、着色）。

---

## 3. 设计令牌

```
--bg        #0B0B09   画布外壳底色（中性暖黑，避免干扰判色）
--panel     #131311   面板
--panel-2   #191916   次级面板
--line      rgba(236,232,222,.075)
--text      #ECE8DE
--muted     #8C8779
--dim       #5D594F
--accent    #D9FF57   唯一强调色（选中/可交互/活跃）
--ok        #7FE3A8
--warn      #FFC46B
--err       #FF7A66
```

- 字体：UI `Segoe UI Variable / Microsoft YaHei UI`；数字与代码 `JetBrains Mono / Cascadia Mono`。
- 字号：10 / 11 / 13 / 15 / 27，小标题 10px + `.12em` 字距大写。
- 圆角：2–4px（仪表感，不用大圆角）。
- 间距：4 的倍数；面板内 8/10/14；区块间 14。
- 阴影：仅设备外框使用，其余靠 1px 细线分层。

---

## 4. 信息架构

```
ThemeDocument（一份文档，自动保存）
├── 画布 Compose     语义角色 / 屏幕状态 / 画布 / 检查器
│   └── 配色实验台    种子派生（沙盒覆盖层）
├── 资产 Assets      图标库 / 调色板槽位 / 字库覆盖
├── 行为 Behavior    启动路径 / 标签顺序 / 按键映射
└── 发布 Ship        目标版本 / 产物清单 / 兼容矩阵 / 校验
```

- 全局：顶栏（文档名、目标设备与固件、约束状态、撤销/重做、对比、导出）+ 左轨（4 模式）+ 底部状态栏。
- 画布常驻 8 帧胶片：Logo / 最近 / 浏览 / 设置 / 界面 / 工具 / 关于 / IGM。
- 模式切换不丢上下文，无「保存」概念。

### 4.1 与旧结构对照

| 维度 | 旧 | 新 |
|---|---|---|
| 导航 | 7 个顶级标签循环 | 4 个模式 + 8 帧胶片 |
| 对象 | 主题 = 序号 0–5 | 主题 = 文档（角色/资产/行为/目标） |
| 编辑单位 | 整包 | 单个角色（可锁定/派生） |
| 反馈 | 刷机后 | 实时画布 + 量化预览 |
| 保存 | 模态确认 | 自动草稿，发布才落产物 |

---

## 5. 交互规格

### 5.1 画布

- 3× 显示（720×480），整数缩放；`BGR555`、`网格`、`像素对齐` 三个开关。
- 命中检测按**区域 → 角色**映射表（图标条 / 行背景 / 行文字 / 选中行 / 弹窗描边 / 底部提示）。
- 单击选中角色并在左栏高亮；其余使用点画淡色描边；双击进入该角色的检查器。
- 拖拽 = 把某角色「刷」到另一区域（写入映射覆盖，不改固件结构）。

### 5.2 配色实验台

- 输入：种子色（取色器/HEX/屏幕取色/图片）。
- 推导：OKLCH 明度阶梯 + 色相带 ±12° + 中性面饱和 ≤8% → 吸附 BGR555。
- 输出：每角色 HEX / BGR555 / ΔE；三套候选（冷静/高对比/暖色）。
- 沙盒：未点「应用」不写文档；顶栏常驻「未应用生成结果」。

### 5.3 对比

- 中线擦除（clip-path 50%），空格交换左右，拖动改比例。
- 差异清单按角色列出「基线 → 当前 → ΔE」，单行可回退。
- 只读模式，退出才可编辑；基线可选出厂 6 套或上一版草稿。

### 5.4 行为

- 节点图：Boot → Logo → 主菜单 → 7 标签 → 载入 → 游戏 → IGM。
- 左侧标签列表可拖拽排序，图上同步重排；隐藏的标签在图上变虚线。
- 量化解算：步数、启动耗时、可达性、死路数。

### 5.5 发布

- 产物：`theme.bin`（TLV）/ `ui-settings.txt` 片段 / 预览 PNG / 回滚脚本 / 注入式 `.fw`。
- 兼容矩阵按「能力 × 固件版本」渲染：原生 / 补丁 / 不可用。
- 校验 9 项，任一失败 → 阻塞并定位到对应模式。
- 安装两条路径：1.7+ 运行时加载；1.6.x 注入固件。

---

## 6. 数据模型（草案）

```ts
type RoleId = 'surface' | 'surfaceAlt' | 'ink' | 'inkMuted'
            | 'accent' | 'highlight' | 'shadow';

interface RoleValue {
  hex: string;              // #RRGGBB
  rgb555: number;           // 0x0000-0x7FFF (BGR555)
  locked: boolean;          // 不参与派生
  source: 'manual' | 'seed' | 'derived' | 'legacy';
  deltaE?: number;          // 量化偏差
}

interface ThemeDocument {
  id: string;
  name: string;
  version: string;                       // 语义化版本
  target: { device: 'sd' | 'minisd' | 'lite';
            firmware: '1.4' | '1.5' | '1.6' | '1.7' };
  roles: Record<RoleId, RoleValue>;
  assets: {
    icons: { id: string; tile: number; variant: 1|2|4; slots: number[] }[];
    fonts: { lang: string; coverage: number; bytes: number }[];
  };
  behavior: {
    defaultTab: string; tabOrder: string[];
    animSpeed: 0|1|2|3|4; recentMenu: boolean; hideHidden: boolean;
  };
  screens: { id: string; issues: string[] }[];
  meta: { createdAt: string; updatedAt: string; baseFirmware: string };
}
```

### 6.1 导出管线

```
theme.json
  → validate()          槽位/对比/字库/命名/尺寸
  → encodeTLV()         theme.bin（未知字段可跳过，前向兼容）
  → emit snippets       ui-settings.txt
  → injectFirmware()    base.fw + theme.bin → patched.fw（CRC 重算）
  → report.json         体积、ΔE、兼容性、校验和
```

---

## 7. 兼容性策略

| 能力 | 1.4 | 1.5 | 1.6 | 1.7(建议) |
|---|---|---|---|---|
| 6 色角色 | 原生 | 原生 | 原生 | 原生 |
| 第 7 角色 Highlight | 合并降级 | 原生 | 原生 | 原生 |
| 图标替换 | 注入 | 注入 | 注入 | theme.bin |
| 标签顺序 | — | 补丁 | 补丁 | theme.bin |
| 运行时切换 | — | — | — | 加载器 |

- **基线声明**：产物头写入 `fw_version` + build id，加载时校验，不匹配拒绝而非静默失败。
- **能力降级**：按目标版本裁剪角色/字段，并在报告中标注丢失项。
- **前向兼容**：TLV 跳过未知字段；版本号单调递增。

---

## 8. 实施阶段

| 阶段 | 内容 | 产出 |
|---|---|---|
| P1（2 周） | 解析器 + 画布 + 8 帧 + 角色映射 + 对比 | 只读工作台 |
| P2（3 周） | 派生/锁定 + 资产导入 + 片段导出 + 注入 .fw | 可编辑可导出 |
| P3（2 周） | 兼容矩阵自动化 + 分享链接 + 移动端 + 快照 | 可分享可回滚 |

**技术选型**：Vite + React + TS；Canvas 2D 画布；Zustand + 命令栈（撤销）；culori（OKLCH/ΔE2000）；解析与打包抽成独立 TS/wasm 包供 CLI 复用。

---

## 9. 待确认

1. 导出先做「注入 .fw」还是先推固件侧 `theme.bin` 加载器？
2. 首版只做颜色 + 行为，还是一次做到图标与字库？
3. 视觉保持深色仪表台，还是改浅色「图纸」风格？

---

## 10. 结构层 v2（深水区）

> 详见 `docs/ui-redesign.html`。第 1–9 节只覆盖「表现层」；本节覆盖「布局与导航层」。

### 10.1 问题

`themes[6][6]` 只能换色。行高、栏宽、标签顺序、弹窗/页面都写死在 `menu.c` 的立即数与 `switch` 里，所以任何主题工具的天花板都是「皮肤」。

### 10.2 三个抽象

| 层 | 内容 | 归属 |
|---|---|---|
| L0 像素 | 240×160、8×16 字形、16×16 图标、BGR555 | 硬件，不变 |
| L1 布局 | Rail / List / Hero / Sheet / Meta / Hint 六个原语 + 度量 + 绑定 | 主题包（数据） |
| L2 导航 | 场所（5）+ 页面栈 + START 命令面板 | 主题包（数据） |

### 10.3 主题包 v2

```
HEAD    magic "SFTH" · ver 2 · target_fw · doc_hash
TOKENS  color.* · metric.row_h/rail_w · style.*
LAYOUT  screen → template(T1..T6) + block{度量, 绑定}
ASSETS  icon[] · font_subset[] · logo
FALLBACK 内置主题引用
```

未知 TLV 跳过；缺 LAYOUT 段则回退旧布局（1.6 行为）。

### 10.4 新导航

- 5 个场所：首页 / 游戏 / 工具 / 设置 / 系统（40px 竖栏，二维移动）
- 页面栈：Home → Section → Detail，B 只弹栈
- START：命令面板（T4，2×3）
- 详情从弹窗改为整页（T3）
- 设置改为分类 + 选项（T5）
- 底部 12px 提示条随焦点变化

### 10.5 预算

VRAM +0；ROM +2.5–3.5KB（运行时）；RAM +0.6KB；描述 1.2KB/主题；布局仅在切屏时计算。
Lite（256KB）可能需裁到 T1/T3。

### 10.6 兼容

| 版本 | v2 包 | 行为 |
|---|---|---|
| 1.4 / 1.5 | 不可读 | 仅 C 数组重编译 |
| 1.6 | 只读 TOKENS | 新配色 + 旧布局，启动提示降级 |
| 1.7 | 完整 | 布局 + 导航；未知块跳过 |

旧主题自动映射为 TOKENS 子集，布局取默认 T1。
