# paper-latex-zh

把论文变成 LaTeX，两种输入都支持：

- **PDF**（英文/中文）→ 译成中文，用 LaTeX **重排成版式贴近原文的 PDF**；
- **Word（.docx）**→ 直接转成 LaTeX 工程（pandoc + 结构修复），套 A4 中文论文模板编译。

核心不是"翻译"，而是三件事：**版式测绘 → 精确复刻 → 可验证交付**。缺任何一环，成品就会像
"Word 转出来的 PDF"，而不是"看起来像原文"。

---

## 版式从哪来：先测、再认、后生成

不是"三选一模板"。任何一篇论文进来，走三步拿到**它自己的**版式参数：

```bash
# ① 量：页面尺寸、四边距、栏数与栏距、字号行距、页眉页脚、标题字号
python scripts/probe_layout.py paper.pdf --json layout.json

# ② 认：DOI / 元数据 / 首页横幅 → 出版方与期刊，并检查本机有没有对应文档类
python scripts/detect_venue.py paper.pdf --layout layout.json --json venue.json

# ③ 生成：按实测参数写 main.tex（或改用出版方官方文档类）
python scripts/make_template.py layout.json --venue venue.json --out main.tex
```

`make_template.py` 两种模式：

| 模式 | 做什么 | 什么时候用 |
|---|---|---|
| `replica`（默认） | 用 `extarticle` + `geometry` 复刻**已发表版式**：页面 544.25×742.68bp、边距 38.9/38.7/54/47.1bp、正文 8pt、行距 10.5pt、栏距 4pt 全部照填 | 想"长得像原 PDF" |
| `official` | 套出版方文档类（`elsarticle` / `IEEEtran` / `acmart` …），选项按栏数自动选（单栏 `preprint`、双栏 `3p,twocolumn`） | 要拿去投稿 |

实测对照（Elsevier TR-E 那篇 30 页论文）：

| 项 | 原文 | 生成 |
|---|---|---|
| 页面 | 544.25 × 742.68 pt | 544.25 × 742.68 pt |
| 左/右/上/下边距 | 38.9 / 38.7 / 54.0 / 47.1 pt | 38.9 / 38.7 / 56.1 / 49.3 pt |
| 正文字号 / 行距 | 8.0 / 10.5 pt | 8.0 / 10.3 pt |
| 栏数 | 1 | 1 |

字体不回退成"固定宋体+Times"：probe 会报出 PDF 里的正文字体名（如 `CharisSIL`），
生成时按映射表选最近的可安装字体，并用 `\IfFontExistsTF` 做存在性回退，换机器不会直接编译失败。
中文字体可用 `--cjk-font` 指定。

## 它能做到什么

输入 `Zhou et al. (2026), TR-E` 那样 30 页的 Elsevier 单栏论文，输出 37 页中文 PDF：

| 项 | 复刻情况 |
|---|---|
| 页面尺寸 | 544.25 × 742.68 pt，与原版一致 |
| 页边距 | 39 / 42.5 pt |
| 栏数 | 单栏正文 + 首页"左关键词 / 右摘要"双栏区 |
| 字号行距 | 中文 10 pt / 行距 1.30 |
| 页眉页脚 | 左 `S. Zhou et al.` 斜体、右期刊卷期、页脚居中页码 |
| 公式 | (1)–(42) 编号逐条与原版对齐 |
| 图 / 表 | 图 1–13、表 1–11 编号全对齐，图按 300 dpi 从原 PDF 裁出 |
| 参考文献 | 58 条，作者—年份格式 + 悬挂缩进 |

**不做**的事：不复刻出版社横幅、logo、DOI 页脚；不重绘图内文字（保留原图英文）。

---

## 安装

### 1. 放进 skills 目录

```bash
# 把整个目录拷到 skills 下
cp -r paper-latex-zh ~/.workbuddy/skills/
```

其他工具同理，放对应位置即可：

| 工具 | 路径 |
|---|---|
| WorkBuddy | `~/.workbuddy/skills/paper-latex-zh/` |
| Antigravity / zcode | `<项目>/.agents/skills/paper-latex-zh/` |
| Codex | `~/.codex/skills/paper-latex-zh/`（需启用 skills） |

### 2. 依赖

```bash
pip install pymupdf          # PDF 路径用（版式实测 + 裁图）
```

Word 路径还需要 **pandoc ≥ 3.0**（做 docx → LaTeX 的底层转换，不引入 Python 库）：

```bash
winget install --id JohnMacFarlane.Pandoc   # Windows
brew install pandoc                          # macOS
sudo apt install pandoc                      # Linux
```

LaTeX 侧需要 TeX Live（含 `xelatex`）与 `ctex`、`booktabs`、`fancyhdr`、`unicode-math`、
`algorithm`、`algpseudocode`；预览需要 poppler（`pdftotext` / `pdftoppm`）。

字体默认按 Windows 配置：`Times New Roman` + `SimSun`（宋）/ `SimHei`（黑）/ `KaiTi`（楷）
+ `STIX Two Math`（公式）。macOS / Linux 请在模板顶部换成等价的本地字体
（如 `Songti SC` / `Heiti SC` / `Kaiti SC`）。

---

## 快速开始

```bash
# 0) 建工程
mkdir -p proj/{extract,pages,figs,build} && cp paper.pdf proj/source.pdf && cd proj
pdftotext -layout source.pdf extract/fulltext.txt    # 文字底稿（公式会乱码，只看文字）
pdftoppm -r 200 -png source.pdf pages/page           # 整页渲染，作视觉比对基准

# 1) 量版式
python ~/.workbuddy/skills/paper-latex-zh/scripts/probe_layout.py source.pdf --json layout.json

# 2) 裁图
python ~/.workbuddy/skills/paper-latex-zh/scripts/crop_figures.py source.pdf layout.json --out figs

# 3) 复制模板、填参数、写正文
cp ~/.workbuddy/skills/paper-latex-zh/assets/main-single.tex main.tex

# 4) 编译 + 校验 + 预览
python ~/.workbuddy/skills/paper-latex-zh/scripts/build_check.py main.tex --preview 1,10,20
```

**Word 转 LaTeX：**

```bash
python ~/.workbuddy/skills/paper-latex-zh/scripts/docx2tex.py 我的论文.docx --out build_tex
# 需要换模板 / 复杂表单编不过时：
python ~/.workbuddy/skills/paper-latex-zh/scripts/docx2tex.py 我的论文.docx --out build_tex \
       --template cn-a4 --tables flatten --numbering keep

python ~/.workbuddy/skills/paper-latex-zh/scripts/build_check.py build_tex/main.tex --preview 1
```

在 agent 里也可以直接说：「把这篇论文翻译成中文，排版和原文一样」，或「把这个 Word 转成 LaTeX」。

---

## 六个脚本

### `probe_layout.py` — 版式测绘

```bash
python probe_layout.py <input.pdf> [--json layout.json] [--dpi 200]
```

输出人类可读摘要 + JSON：

```
页面尺寸  : 544.25 x 742.68 pt  (192.0 x 262.0 mm)
栏数      : 1 栏
左右边距  : 38.9 / 38.7 pt
字号/行距 : 8.0 pt / 10.5 pt   字体 CharisSIL
           候选字号 [8.0]（正文通常取占比最大者，需人工复核）
页眉      : Transportation Research Part E 216 (2026) 105167  ||  S. Zhou et al.
页脚      : 2
图 13 张、表 11 个
  Fig.1  p5  bbox=[103.2, 53.4, 439.4, 434.2]
  Fig.10 p24 bbox=未定位（纯位图，需人工裁剪）
```

图题识别用了四条规则叠加，都是踩坑换来的：只认块首匹配 → 合并 block 前两行（应对图题拆行）
→ 居中或与上块间距 > 18 pt（区分正文引用）→ 同编号取裁剪面积最大的候选。

### `crop_figures.py` — 批量裁图

```bash
python crop_figures.py <input.pdf> <layout.json> [--out figs] [--dpi 300]
python crop_figures.py <input.pdf> <layout.json> --rect 10=24:58,56,498,380
```

按 `probe` 给出的 bbox 裁 300 dpi PNG。`--rect` 用于修正纯位图（地图截图、照片），
格式 `编号=页码:x0,y0,x1,y1`，坐标是 PDF pt，可从 `pages/page-NN.png` 目测
（1 pt = 渲染 dpi / 72 像素）。

### `detect_venue.py` — 识别来源

```bash
python detect_venue.py paper.pdf [--layout layout.json] [--json venue.json]
```

判据按可靠性排序：**DOI 前缀**（`10.1016`→Elsevier、`10.1109`→IEEE、`10.1145`→ACM…）
→ 元数据 `creator/producer` → 首页横幅与页眉文字。每条结论都带证据，识别不出时如实标注。

同时用 `kpsewhich` 检查本机装了哪些文档类（TeX Live 一般有 `elsarticle`、`IEEEtran`、`acmart`、
`revtex4-2`，`sn-jnl`/`mdpi` 之类常缺），缺了会在报告里说明而不是默默失败。

中文文献（汉字占比 >15%）直接判 `ctexart`，不走外文模板。

### `make_template.py` — 按实测生成 main.tex

```bash
python make_template.py layout.json [--venue venue.json] [--mode replica|official] \
       [--out main.tex] [--columns 1|2] [--latin-font NAME] [--cjk-font NAME] \
       [--title 标题] [--header-left 左页眉] [--header-right 右页眉]
```

要点：
- 单位用 **bp**（PDF 点）。写成 `pt` 会因 TeX pt ≠ bp 让页面小 0.4%，30 页后越差越明显；
- 只给标题时才 `\maketitle`，空标题会白占一段竖直空间，把正文顶下去 20pt；
- 页眉左右分开填 `\fancyhead[L]/[R]`，取自实测的 `header_left/header_right`；
- 字体走 `\IfFontExistsTF` 三级回退，缺字体不报错。

### `docx2tex.py` — Word 转 LaTeX

```bash
python docx2tex.py <thesis.docx> [--out build_tex] [--template cn-a4|single|twocol|none]
                   [--numbering keep|auto] [--tables keep|flatten]
                   [--no-auto-headings] [--textwidth-in 5.7]
```

pandoc 负责主干转换，脚本负责清理 xelatex 编不过、或中文文档特有的东西：

| 处理项 | 说明 |
|---|---|
| 媒体扁平化 | `media/media/image1.png` → `figs/image1.png`，路径同步改写 |
| WMF/EMF 公式 | xelatex 不支持，替换成占位文字并在报告里列出，需人工转 PNG 或改写成 `$...$` |
| 过长下划线 | `\underline` 不换行，超过 12 字的去下划线保留文字（否则溢出几百 pt） |
| 图片超宽 | Word 常给绝对宽度（如 6.49in），超过版心改为 `width=\linewidth` |
| 嵌套表格 | 单元格里套表格时内层 longtable 降级为 tabular |
| 列数错位 | 声明 1 列却有 4 个 `&` 时，多出的单元格用「；」并回同一格 |
| 标题识别 | Word 标题样式 + "加粗/编号" 段落；手写编号可按 `--numbering keep` 保留 |
| 复选框 | ⬜/☑ 等换成 `\fbox` 画法，不依赖字体字形 |

产物：`body.tex`、`figs/`、`main.tex`（按模板生成）、`docx2tex_report.json`（含全部警告）。

### `build_check.py` — 编译校验

```bash
python build_check.py main.tex [--preview 1,5,12] [--xelatex 路径]
```

编译两遍后检查：`!` 错误、Overfull hbox/vbox、未定义引用、页数、
以及 `图 N` / `表 N` / `(N)` 编号是否连续。退出码 `0` 通过、`1` 有问题、`2` 环境问题。

```
退出码      : 0
PDF 页数    : 37
错误        : 0
Overfull h  : 0
图编号      : 1–13，缺 无
表编号      : 1–11，缺 无
公式编号    : 1–42，缺 无
```

---

## 三个内置模板（可不用）

| 模板 | 适用 | 版式 |
|---|---|---|
| `assets/main-single.tex` | Elsevier / TR 系列 | 窄页单栏，首页左关键词右摘要，页眉左右分栏 |
| `assets/main-twocol.tex` | 中文高校学报 | A4 双栏，通栏标题，栏距 7 mm |
| `assets/main-cn-a4.tex` | Word 论文 / 开题报告 | A4 单栏，宋体小四，1.5 倍行距，黑体标题 |

这三个模板是「懒得测」时的兜底。**优先级：`make_template.py` 生成 > 内置模板**——生成的是这篇论文的实测值，模板只是常见配置的近似。

两者顶部都把页面尺寸、边距、页眉文字抽成宏（`\PageW`、`\MargL`、`\HeadL` …），
改参数即可，不用翻 `geometry` 定义。

---

## 工作流要点

1. **先量后写**。不实测版式就动手，必然返工。
2. **首页先定稿**。首页的标题字号、摘要两栏、横线位置定下来，全篇视觉基调就定了。
3. **逐节编译比对**。攒到最后一起编译，错误会互相掩盖。每节排完与 `pages/` 对应原页并排看。
4. **编号交给 LaTeX**。`\caption` 里不要写"图 N"，手工编号一定错位。
5. **公式重排，不复制字符**。`pdftotext` 出来的是 Unicode 斜体假字符，必须用数学模式重写。

验收线：无错误、无溢出、图/表/公式编号与原文一一对应、页数为原文的 0.9–1.3 倍。

---

## 已知限制

- **纯位图抓不到**。地图截图、照片类插图 `get_drawings()` 无对象，`probe` 会标
  `bbox=未定位`，需要 `--rect` 手填坐标。
- **图题拆行仍可能判偏**。四条规则能覆盖多数情况，但不保证 100%。**裁完必须逐张核对**，
  工具只做初筛。
- **浮动体前后会留白**。体量接近整页的表/算法，LaTeX 会推到下一页。要完全贴合原页序需逐页微调。
- **官方文档类 ≠ 已发表版式**。`elsarticle preprint` 是投稿版，和 ScienceDirect 上排版好的 PDF
  不是一回事；要「长得像」用 `replica`，要投稿用 `official`，两者不能兼得。
- **栏距只能测到整栏空白**。CJK 两端对齐会把行撑到栏边，实测栏距偏小 2–4pt；<4pt 会判成单栏。
- **中文字体依赖系统**。模板默认 Windows 字体名，换平台须改模板顶部。
- **Word 表单只能尽力而为**。嵌套表格、大量合并单元格、无边框布局表的版式依赖 Word
  的格子技巧，自动转换只保证"文字不丢、能编译"，不保证"长得一样"；编不过时加
  `--tables flatten`，会丢掉表格线但保留全部文字。
- **Word 老式公式（WMF/EMF）无法转换**。它们在 Word 里是矢量图而非 OMML，xelatex 不认，
  转换后会留占位文字，需另存 PNG 或改写成 `$...$`。
- **字号与页眉需人工复核**。probe 已修过 8 pt 正文被过滤、页眉只取半行两个 bug，
  但字体名与候选字号仍建议对照 `pages/` 原页确认一次。

`references/pitfalls.md` 记了 12 类坑的"症状 → 根因 → 对策"（另附版权合规一节），
返工时按目录顺序排查最省时间。

---

## 目录结构

```
paper-latex-zh/
├── SKILL.md                      # agent 读的主文件：工作流与硬性要求
├── README.md                     # 本文件：给人看的说明
├── scripts/
│   ├── probe_layout.py           # 版式测绘 + 图表定位（PDF 路径）
│   ├── crop_figures.py           # 批量裁图（PDF 路径）
│   ├── detect_venue.py           # 识别期刊/出版方与可用文档类
│   ├── make_template.py          # 按实测参数生成 main.tex
│   ├── docx2tex.py               # Word → LaTeX（Word 路径）
│   └── build_check.py            # 编译校验（两条路径共用）
├── assets/
│   ├── main-single.tex           # 单栏模板（Elsevier 类）
│   ├── main-twocol.tex           # 双栏模板（高校学报类）
│   └── main-cn-a4.tex            # A4 中文论文模板（Word 默认版式）
└── references/
    ├── layout-metrics.md         # 版式参数换算表 + 两类期刊实测值
    └── pitfalls.md               # 14 条 PDF 坑 + 6 条 Word 坑 + 版权合规
```

---

## 版权

期刊论文的翻译权通常归出版社所有。本工具产出的译文与裁剪图表**仅供个人研读**，
不要提交到公开仓库或对外分发；复刻版式时也不应复制出版社 logo、横幅、DOI 页脚等标识。
