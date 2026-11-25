# 版式参数判读与换算

## 一、从 probe_layout.py 输出到 LaTeX 参数

| probe 输出 | LaTeX 落点 | 说明 |
|---|---|---|
| `page_size_pt` | `paperwidth` / `paperheight` | 直接填，单位 pt |
| `margins_pt.left/right` | `left` / `right` | geometry 的左右边距 |
| 正文顶部 y 与页眉 y | `top` + `headsep` | 正文首行 y ≈ top；页眉 y ≈ top−headsep |
| 页面高 − 正文底部 y | `bottom` | 底部到页边的距离 |
| `columns = 1` | `ctexart` 单栏 | 不要用 twocolumn |
| `columns = 2` | `ctexart[twocolumn]` + `columnsep` | 栏距取 `gutter` 宽度 |
| `body_font_pt` | 字号 | 英文 9pt ↔ 中文 10pt 视觉密度接近 |
| `leading_pt` | `\linespread` | 中文乘 1.2–1.35 更舒适 |

行距换算经验（中文）：

```
\linespread = 目标行距 / (字号 × 1.2)
```

例：10pt 字号、目标行距 15.5pt → 15.5 / 12 ≈ 1.29。

## 二、已实测的两类版式

### Elsevier（Transportation Research Part E 类）

| 项 | 值 |
|---|---|
| 页面 | 544.25 × 742.68 pt（191.9 × 262.0 mm） |
| 左右边距 | 39 / 42.5 pt |
| 正文宽 | ≈ 462 pt |
| 栏数 | **单栏**（多数 TR 系列正文单栏，图可通栏） |
| 正文字号 / 行距 | 8 pt / 10.5 pt（实测 TR-E 216 卷；部分卷为 9 pt，以 probe 输出为准） |
| 字体 | 正文 Charis SIL；标题 Univers LT Std；公式 STIXTwoMath |
| 页眉 | 左 `S. Author et al.`（斜体）+ 右 `Journal Vol (Year) 文章号` |
| 页脚 | 居中页码 |
| 首页 | Elsevier 横幅 → 标题 → 作者 → 单位 → 左「Article info / Keywords」+ 右「Abstract」 |

复刻建议：不要复制 Elsevier 横幅与版权行（出版商标识），从标题开始即可。

### 中文高校学报（双栏）

| 项 | 值 |
|---|---|
| 页面 | A4 210 × 297 mm |
| 版心 | 常见左右 25 mm、上 28 mm、下 25 mm |
| 栏距 | 6–8 mm（实测两栏间空白） |
| 正文 | 宋体 10–10.5 pt，行距 1.2–1.25 |
| 摘要 | 楷体，小于正文一号 |
| 一级标题 | 楷体/黑体 12–14 pt |
| 中英图题 | 双行，中文在上 |

## 三、字号的中西文对应

英文 9 pt 与中文 10 pt 的视觉密度接近，原因是汉字字面率更高。换算时以"每行字符数"为准：
英文单栏 462 pt 宽、9 pt 字号约 95 字符/行；中文 10 pt 约 46 字/行，视觉相当。

**8 pt 英文**（Elsevier 常见）对应约 **9.5 pt 中文**；若同时把行距从 10.5 pt 抬到 15–16 pt
（`\linespread` 1.5 左右），版面会明显比原版松，需要按"每页行数"回算：

```
中文行距 ≈ 原文行距 × 1.5      # 8pt/10.5pt 英文 ≙ 10pt/15.75pt 中文
```

`probe_layout.py` 给出的是**原文**字号行距，不是目标中文值，不要直接填。

## 四、验收阈值

| 指标 | 合格线 |
|---|---|
| 页数偏差 | 译文页数通常为原文 0.9–1.3 倍（中文更紧凑但行距更大） |
| Overfull hbox | 0（或 < 3 pt 且不影响阅读） |
| Overfull vbox | 0 |
| 图/表/公式编号 | 连续无缺号，且与原文一一对应 |
