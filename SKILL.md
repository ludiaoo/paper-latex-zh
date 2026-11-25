---
name: paper-latex-zh
description: "把论文转成 LaTeX：①英文/中文论文 PDF 译为中文并重排成版式贴近原文的 PDF；②中文 Word（.docx）直接转 LaTeX。适用于「翻译论文并保持排版」「把 PDF 做成中文 LaTeX」「复刻期刊版式」「Word 转 LaTeX」「论文转 LaTeX 模板」等场景；提供版式实测、图表裁剪、Word 转换、编译校验四组脚本与三个模板。"
agent_created: true
---

# 论文翻译与 LaTeX 版式复刻

本 skill 有两条独立路径，先判断用户给的是 PDF 还是 Word：

| 输入 | 走哪条 | 核心脚本 |
|---|---|---|
| 英文/中文**论文 PDF**，要译成中文并贴近原版式 | 路径 A：翻译 + 复刻版式 | `probe_layout.py` → `crop_figures.py` → `build_check.py` |
| **Word（.docx）** 论文/报告，要转成 LaTeX | 路径 B：结构化转换 | `docx2tex.py` → `build_check.py` |

## 用途

把一篇学术论文 PDF 转成中文译文 LaTeX 工程，输出**版式贴近原文**的 PDF：页面尺寸、页边距、
单/双栏、字号行距、页眉页脚、图表位置、公式编号都按原文复刻。

核心不是"翻译"，而是**版式测绘 + 精确复刻 + 可验证交付**。三者缺一，成品就会像"随便排的
Word 转 PDF"，而不是"看起来像原文"。

## 何时使用

- 用户给出英文论文 PDF，要求"翻译成中文，排版和原文一样"
- 用户要求"把这篇 PDF 做成 LaTeX 工程"、"复刻某某期刊的版式"
- 用户已有一份中文 Markdown 译文，需要排成期刊样式的 PDF
- 用户给出 **Word（.docx）**，要求"转成 LaTeX"、"排成期刊/学位论文格式" → 走路径 B

不适用：只需要摘要翻译（直接用普通翻译）；需要可编辑 Word（用 docx 技能）。

## 工作流（路径 A：PDF → 中文译文 LaTeX）

### 第 1 步：建工程、提取素材

```bash
mkdir -p <project>/{extract,pages,figs_raw,figs,build}
cp <source.pdf> <project>/source.pdf
cd <project>
pdftotext -layout source.pdf extract/fulltext.txt      # 文字底稿（公式会乱码，只作文字参考）
pdftoppm -r 200 -png source.pdf pages/page             # 整页渲染，作视觉复核基准
```

**必须**保留 `pages/page-*.png`：后续每一节排完都要与对应原页并排比对，这是"贴近原文"的
唯一客观依据。

### 第 2 步：实测版式 + 识别来源 + 生成模板

**不要套固定模板**。每篇论文的页面尺寸/边距/栏数/字号都可能不同，按三步生成它自己的：

```bash
python <skill>/scripts/probe_layout.py source.pdf --json layout.json
python <skill>/scripts/detect_venue.py source.pdf --layout layout.json --json venue.json
python <skill>/scripts/make_template.py layout.json --venue venue.json --out main.tex
```

- `probe_layout.py`：页面尺寸、四边距、栏数与栏距、字号行距、页眉左右、标题字号、图表 bbox；
- `detect_venue.py`：DOI 前缀优先，其次元数据与首页横幅，给出出版方 + 可用文档类（含 `kpsewhich` 可用性）；
- `make_template.py`：`replica` 按实测逐项落参数（页面尺寸精确到 0.01bp，四边距误差 ≤2pt）；
  `official` 改用出版方文档类（elsarticle / IEEEtran / acmart），按栏数自动选 `preprint` 或 `3p,twocolumn`。

判读要点：
- `columns` 决定单/双栏；栏距取自实测 `gutter_pt`；
- probe 给的是**原文**字号行距。要复刻就照填；要中文更舒适再按
  `references/layout-metrics.md` 换算（英文 8–9 pt ≈ 中文 9.5–10 pt）；
- 生成后必须用 `build_check.py` 复测一遍，必要时用 `--size/--columns/--latin-font` 微调。

### 第 3 步：裁剪插图

```bash
python <skill>/scripts/crop_figures.py source.pdf layout.json --out figs
```

若输出里有"未定位（纯位图）"，用 `--rect N=页码:x0,y0,x1,y1` 手动补：
坐标从 `pages/page-NN.png` 目测，1 pt = 渲染 dpi / 72 像素。

**必须核对**：图号与内容一一对应。曾经出现过"漏插图 1、图 2 导致后面全部前移两位"，
所以裁完先抽查前 3 张和最后 1 张。

### 第 4 步：搭骨架、试排首页

把 `assets/main-single.tex`（或 `main-twocol.tex`）复制为 `main.tex`，改顶部参数与首页区
（标题、作者、单位、关键词、摘要）。先只写第 1 节前两段，编译看首页：

```bash
python <skill>/scripts/build_check.py main.tex --preview 1
```

**首页定稿后再往下写**。首页的标题字号、摘要两栏、横线位置一旦定下，后面整篇的视觉基调就定了。

### 第 5 步：逐节翻译与排版

按原文节次拆成 `sec1.tex … secN.tex`，`main.tex` 用 `\input` 串联。每节完成后：

1. 编译；
2. 渲染该节所在页；
3. 与 `pages/` 对应原页并排比对（栏宽、公式编号位置、图表占位）；
4. 修正后再进下一节。

翻译约定：
- 公式用标准 LaTeX 数学模式重排，**不要**复制 pdftotext 的 Unicode 斜体字符；
- 公式编号必须与原文一致（用 `\label`/`\eqref`，不要手工写编号）；
- 术语首次出现给"中文（English）"，之后统一用中文；
- 图表编号交给 LaTeX 自动生成，`\caption` 里不写"图 N"。

转表三步（少一列比多数一列难发现得多）：
1. 先把源表列数写进源码注释：`% 源表 15 列：ε, Scale, 6×2, t̄`；
2. 再用 `pdftotext -layout` 抽出原表数据行，与 tabular 逐格对照；
3. 最后用 `check_tables.py` 机械核对列数，并核对表头译文与原表列名一一对应。
宽表优先降字号（`\scriptsize`）或拆表，**不要删列**；原表的加粗/斜体（如"每算例优解加粗"）要保留。

### 第 6 步：验收

```bash
python <skill>/scripts/build_check.py main.tex --preview 1,10,20
```

通过标准：
- 退出码 0，无 `!` 错误；
- Overfull hbox / vbox 为 0（或 < 3 pt 且不影响阅读）；
- 图 / 表 / 公式编号连续，且数量与原文一致；
- 页数为原文的 0.9–1.3 倍；
- 每个表格的列数与源表一致（`check_tables.py`），表头文字逐项对应；
- 原表的粗体、斜体强调标记保留（"每算例优解加粗"这类规则不能丢）；
- 每张图四边完整，轴标题、刻度、图例、色标标题都在框内（`verify_figures.py` + 裁图边缘警告）；
- 跨页表的表头重复，longtable 的 `\endhead` 齐全。

再抽 3–5 页与原页肉眼比对，确认图题在图下、表题在表上、页眉页脚正确。

## 工作流（路径 B：Word → LaTeX）

### 第 1 步：转换

```bash
python <skill>/scripts/docx2tex.py thesis.docx --out build_tex
```

一条命令完成：pandoc 转 LaTeX → 媒体文件落到 `figs/` → 清理 pandoc 产物里 xelatex
编不过的东西 → 按 `-template` 生成 `main.tex`（默认 `cn-a4`，A4 单栏、宋体小四、1.5 倍行距）。

常用参数：

| 参数 | 用途 |
|---|---|
| `--template cn-a4\|single\|twocol\|none` | 选版式；`none` 只出 `body.tex` |
| `--numbering keep\|auto` | 保留 Word 手写编号 / 交给 LaTeX 编号；默认按标题是否带编号自动判断 |
| `--tables flatten` | 复杂表单（嵌套表、合并单元格）编不过时用，丢表格线保文字 |
| `--no-auto-headings` | 不把"加粗+编号"的段落提升为标题 |
| `--textwidth-in` | 版心宽度（英寸），超过此宽度的图片按 `\linewidth` 缩放 |

### 第 2 步：读报告

转换会写 `docx2tex_report.json` 并打印摘要，**必须逐条看 warnings**，常见三类：

- **WMF/EMF 公式**：Word 老式公式是矢量图，xelatex 不支持，已替换成占位文字。
  需在 Word 里另存为 PNG 后替换，或手工改写成 `$...$`；
- **表格列数不匹配 / 嵌套表格**：已自动修复（并单元格或降级为 tabular），但表格线需复查；
- **编号方式**：提示"手写编号被重排"时，按原稿意图决定是否加 `--numbering keep`。

### 第 3 步：验收

```bash
python <skill>/scripts/build_check.py build_tex/main.tex --preview 1,2
```

标准同路径 A：退出码 0、无溢出、编号连续。再渲染 2–3 页目测首页标题区与表格是否正常。

### 路径 B 的能力边界

- **能覆盖**：标题层级（Word 标题样式 + "加粗/编号"段落）、正文、列表、图片、普通表格、
  公式（OMML，由 pandoc 转 `$...$`）、超链接、参考文献段落。
- **只做尽力而为**：Word 表单（嵌套表、大量合并单元格、无边框布局表）。这类文档的版式
  依赖 Word 的格子技巧，自动化转换只能保证"文字不丢、能编译"，不能保证"长得一样"。
- **不处理**：批注、修订痕迹、脚注编号重组、域代码（TOC/交叉引用域会变成死文本）。

## 资源索引

| 资源 | 用途 |
|---|---|
| `scripts/probe_layout.py` | 实测页面/栏数/栏距/字号/行距/页眉页脚/标题字号，定位图表 bbox |
| `scripts/detect_venue.py` | DOI + 元数据 + 首页横幅 → 出版方、期刊、可用文档类 |
| `scripts/make_template.py` | 按实测参数生成 main.tex（replica / official 两种模式） |
| `scripts/crop_figures.py` | 按 bbox 批量裁剪插图；支持 `--rect` 手动修正；裁完自动做边缘墨迹检查 |
| `scripts/verify_figures.py` | 核对 bbox 是否盖住全部内容，输出可复制的 `--rect` |
| `scripts/check_tables.py` | 核对译文表格列数与源表，附源表表头 |
| `scripts/docx2tex.py` | Word → LaTeX：pandoc + 媒体扁平化 + 表格修复 + 生成 main.tex |
| `scripts/build_check.py` | 编译两遍 + 校验编号连续性/溢出 + 渲染预览 |
| `assets/main-single.tex` | 单栏模板（兜底用；能用 make_template 生成就不用它） |
| `assets/main-twocol.tex` | 双栏模板（中文高校学报类，通栏标题 + 双栏正文） |
| `assets/main-cn-a4.tex` | 中文 A4 单栏模板（Word 默认版式，宋体小四 / 1.5 倍行距） |
| `references/layout-metrics.md` | 版式参数换算表与两类期刊的实测值 |
| `references/pitfalls.md` | 已知坑与对策（版式 17 类 + Word 转换 6 类） |
| `README.md` | 给人看的说明：安装、依赖、快速开始、已知限制 |

## 硬性要求

1. **先量后写**：不实测版式就动手排版，必然返工。
2. **首页先定稿**：首页版式是整篇的基准。
3. **每节编译比对**：攒到最后一起编译，错误会互相掩盖。
4. **先测再套模板**：`geometry` 的数字用 **bp**（PDF 点），写成 pt 会小 0.4%；页面尺寸与边距
   一律以 `probe_layout.py` 的实测值为准，不要凭 A4/Letter 假设。
5. **编号交给 LaTeX**：手工写编号一定会错位（Word 里已有手写编号时用 `--numbering keep`，
   让标题成为不编号的 `\section*`，而不是叠加两层编号）。
6. **图表内文字保留原文**：重绘图内中文成本极高且易失真，学术翻译惯例是保留原图。
7. **不复制出版社标识**：logo、横幅、DOI 页脚等不要复刻。
8. **版权**：期刊论文翻译权通常归出版社，产物仅限个人研读，不得公开分发或提交到公开仓库。
9. **Windows 上不要调用 `convert`**：那是磁盘格式转换工具（FAT→NTFS），不是 ImageMagick。
   需要图片格式转换时显式判断 `magick -version`，不要把 `convert` 当兜底。

## 常见返工点

PDF 路径按 `references/pitfalls.md` 顺序排查：图号错位 → 图题误判 → 同页多图 → 纯位图 →
字号误判 → 页眉缺失 → 浮动体空白 → 字体警告 → 宽表溢出 → URL 溢出 → 算法块溢出 →
pt/bp 单位 → 双栏误判 → 图边缘被切 → 表格少列。

Word 路径按同一文件末尾五条排查：WMF 公式 → 下划线不换行 → 嵌套表格 → 图片超宽 → 编号被重排。
