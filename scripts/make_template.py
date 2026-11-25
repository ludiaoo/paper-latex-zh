#!/usr/bin/env python3
"""按 probe_layout.py 的实测参数（可选：detect_venue.py 的来源）生成 main.tex。

用法:
    python make_template.py layout.json --out main.tex
    python make_template.py layout.json --venue venue.json --mode official --out main.tex
    python make_template.py layout.json --mode replica --body body --title "论文标题"

两种模式：
  replica  —— 复刻「已发表版式」：页面尺寸、页边距、栏数、字号、行距、页眉页脚全部取自实测；
  official —— 套用出版方的官方文档类（elsarticle / IEEEtran / acmart …），版式由该文档类决定，
              适合要投稿而不是要「长得像原 PDF」的场景。

字体：默认按 PDF 里探测到的正文字体名映射，并用 fontspec 的 \\IfFontExistsTF 做存在性回退，
      所以换机器不会因为缺字体直接编译失败。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

# extarticle 支持的字号
VALID_SIZES = [8, 9, 10, 11, 12, 14, 17, 20]

# PDF 里探测到的字体名 → 候选字体（按顺序尝试，第一个存在即用）
LATIN_CHAIN = [
    (("charis",), ["Charis SIL", "Times New Roman"]),
    (("nimbusrom", "times", "termes"), ["Times New Roman", "TeX Gyre Termes", "Nimbus Roman"]),
    (("stix",), ["STIX Two Text", "Times New Roman"]),
    (("palatino", "pagella"), ["Palatino Linotype", "TeX Gyre Pagella", "Times New Roman"]),
    (("minion",), ["Minion Pro", "Times New Roman"]),
    (("cambria",), ["Cambria", "Times New Roman"]),
    (("libertine",), ["Linux Libertine O", "Times New Roman"]),
    (("arial", "helvet", "nimbussan"), ["Arial", "Helvetica", "TeX Gyre Heros"]),
    (("calibri",), ["Calibri", "Arial"]),
    (("lmodern", "lmroman"), ["Latin Modern Roman", "Times New Roman"]),
    (("cmr", "computer modern", "cmu"), ["Latin Modern Roman", "Times New Roman"]),
]
CJK_CHAIN = ["SimSun", "Songti SC", "Noto Serif CJK SC", "Source Han Serif SC"]
CJK_SANS_CHAIN = ["SimHei", "Heiti SC", "Noto Sans CJK SC", "Source Han Sans SC"]
CJK_KAI_CHAIN = ["KaiTi", "Kaiti SC", "STKaiti"]


def nearest_size(pt: float | None) -> int:
    if not pt:
        return 10
    return min(VALID_SIZES, key=lambda s: abs(s - pt))


def pick_latin(detected: str | None, override: str | None) -> list[str]:
    if override:
        return [override]
    name = (detected or "").lower().replace(" ", "").replace("-", "")
    for keys, chain in LATIN_CHAIN:
        if any(k in name for k in keys):
            return chain
    return ["Times New Roman", "TeX Gyre Termes"]


def font_cmd(cmd: str, chain: list[str]) -> str:
    """生成带存在性回退的字体设置命令。"""
    if not chain:
        return ""
    inner = f"\\{cmd}{{{chain[-1]}}}"
    for name in reversed(chain[:-1]):
        inner = f"\\IfFontExistsTF{{{name}}}{{\\{cmd}{{{name}}}}}{{{inner}}}"
    return inner


def localize_header(header_left: str, header_right: str) -> str:
    """把原文英文 running head 变成中文默认值，如 "S. Zhou et al." → "Zhou 等（2026）"。

    只做保守替换：取作者姓氏 + 等；年份从右页眉的 (20xx) 里取，取不到就不写。
    """
    left = (header_left or "").strip()
    year = ""
    m = re.search(r"\((\d{4})\)", header_right or "")
    if m:
        year = m.group(1)
    if not left:
        return ""
    # 去掉 et al. 之类的尾巴，取第一作者
    core = re.split(r"\bet\s+al\.?|\band\b|,", left)[0].strip()
    tokens = [t for t in re.split(r"\s+", core) if t]
    surname = tokens[-1] if tokens else ""
    surname = re.sub(r"[^A-Za-z\-\u4e00-\u9fff]", "", surname)
    if not surname:
        return ""
    return f"{surname} 等" + (f"（{year}）" if year else "")


def build_replica(L: dict, V: dict | None, args) -> str:
    size = nearest_size(args.size or L.get("body_font_pt"))
    cols = args.columns or int(L.get("columns") or 1)
    lead = L.get("leading_pt") or size * 1.2
    spread = max(0.9, min(2.0, lead / (size * 1.2)))
    m = L.get("margins_pt") or {}
    pw, ph = L.get("page_size_pt") or [595.28, 841.89]
    left, right = m.get("left") or 72, m.get("right") or 72
    top, bottom = m.get("top") or 72, m.get("bottom") or 72

    gutter = L.get("gutter_pt")
    colsep = (gutter[1] - gutter[0]) if (cols == 2 and gutter) else (14 if cols == 2 else 0)
    # 页眉在版心上方的位置靠 headsep 调；页脚距版心底靠 footskip 调
    headsep = max(8, top * 0.36)
    footskip = max(10, bottom * 0.22)
    title_pt = L.get("title_font_pt") or size * 1.8

    latin_chain = pick_latin(L.get("body_font_name"), args.latin_font)
    cjk = args.cjk_font or CJK_CHAIN[0]

    head_l = args.header_left if args.header_left is not None else (L.get("header_left") or "")
    head_r = args.header_right if args.header_right is not None else (L.get("header_right") or "")
    # 默认照抄原文（多为英文 running head）。--header-zh 换成中文，--keep-en-header 保持原样
    if getattr(args, "header_zh", False) and not getattr(args, "keep_en_header", False):
        zh = localize_header(head_l, head_r)
        if zh:
            head_l = zh
            m = re.search(r"\(\d{4}\)", head_r or "")
            head_r = (V or {}).get("journal") or ""
            if head_r and m:
                head_r = f"{head_r}（{m.group(1)}）"
            elif head_r:
                head_r = head_r
    jorn = (V or {}).get("journal") or ""
    src = f"{jorn}，" if jorn else ""
    src += "实测参数复刻" if not V else f"来源 {V.get('publisher','未知')}，按实测参数复刻"

    lines = [
        "% !TeX program = xelatex",
        "% ============================================================================",
        f"% 由 make_template.py 生成（replica 模式）：{src}",
        f"% 页面 {pw}x{ph} pt、{cols} 栏、正文 {size} pt / 行距 {lead} pt、"
        f"边距 {left}/{right}/{top}/{bottom} pt",
        f"% 正文字体：PDF 探测为 {L.get('body_font_name') or '未知'} → {latin_chain[0]}（带存在性回退）",
        "% ============================================================================",
        f"\\documentclass[{size}pt{',twocolumn' if cols == 2 else ''}]{{extarticle}}",
        "",
        "% ---------- 中文支持（ctex 包 + extarticle，可任意字号）----------",
        "\\usepackage[UTF8,scheme=plain,heading=true]{ctex}",
        "",
        "% ---------- 版式：全部取自 probe_layout.py 实测 ----------",
        "\\usepackage[",
        f"  paperwidth={pw}bp, paperheight={ph}bp,",
        f"  left={left}bp, right={right}bp, top={top}bp, bottom={bottom}bp,",
        f"  headheight=12bp, headsep={headsep:.1f}bp, footskip={footskip:.1f}bp"
        + (f",\n  columnsep={colsep:.1f}bp" if cols == 2 else ""),
        "]{geometry}",
        "",
        "% ---------- 字体：fontspec 存在性回退，换机器不会直接报错 ----------",
        "\\usepackage{fontspec}",
        font_cmd("setmainfont", latin_chain),
        "\\IfFontExistsTF{" + cjk + "}{\\setCJKmainfont{" + cjk + "}}{\\setCJKmainfont{Songti SC}}",
        font_cmd("setCJKsansfont", CJK_SANS_CHAIN),
        font_cmd("setCJKfamilyfont{hei}", CJK_SANS_CHAIN).replace(
            "\\setCJKfamilyfont{hei}{", "\\setCJKfamilyfont{hei}{"),
        "\\newcommand{\\hei}{\\CJKfamily{hei}}",
        "\\usepackage{unicode-math}",
        "\\IfFontExistsTF{STIX Two Math}{\\setmathfont{STIX Two Math}}{}",
        "",
        f"% 行距：实测 {lead} pt ÷ (字号 {size} × 1.2) ≈ {spread:.2f}",
        f"\\linespread{{{spread:.2f}}}",
        "",
        "% ---------- 页眉页脚：照抄原文 ----------",
        "\\usepackage{fancyhdr}",
        "\\pagestyle{fancy}",
        "\\fancyhf{}",
    ]
    if head_l or head_r:
        lines += [
            "\\renewcommand{\\headrulewidth}{0pt}",
            f"\\fancyhead[L]{{\\footnotesize\\itshape {head_l}}}" if head_l else "",
            f"\\fancyhead[R]{{\\footnotesize\\itshape {head_r}}}" if head_r else "",
        ]
    else:
        lines.append("\\renewcommand{\\headrulewidth}{0pt}")
    lines += [
        "\\fancyfoot[C]{\\small\\thepage}",
        "% 首页通常不排页眉",
        "\\fancypagestyle{plain}{\\fancyhf{}\\fancyfoot[C]{\\small\\thepage}}",
        "",
        "% ---------- 常用宏包 ----------",
        "\\usepackage{graphicx}",
        "\\usepackage{amsmath}",
        "\\usepackage{booktabs}",
        "\\usepackage{longtable}",
        "\\usepackage{calc}",
        "\\usepackage{array}",
        "\\usepackage{multirow}",
        "\\usepackage{caption}",
        "\\usepackage{float}",
        "\\usepackage{enumitem}",
        "\\usepackage{pifont}",
        "\\usepackage[hidelinks]{hyperref}",
        "\\usepackage{xurl}",
        "\\usepackage{algorithm}",
        "\\usepackage{algpseudocode}",
        "\\captionsetup{font=small, labelsep=quad}",
        "\\setlist{nosep, leftmargin=1.6em}",
        "\\providecommand{\\tightlist}{\\setlength{\\itemsep}{0pt}\\setlength{\\parskip}{0pt}}",
        "\\providecommand{\\pandocbounded}[1]{#1}",
        "\\providecommand{\\cbox}{\\fbox{\\phantom{X}}}",
        "\\providecommand{\\cboxc}{\\fbox{\\ding{51}}}",
        "\\providecommand{\\filledbox}{\\rule{0.62em}{0.62em}}",
        "",
        f"% 标题字号取自首页最大字号（实测 {title_pt:.1f} pt）",
        "\\makeatletter",
        f"\\renewcommand\\@maketitle{{\\begin{{center}}%",
        f"  {{\\hei\\fontsize{{{title_pt:.0f}pt}}{{{title_pt*1.25:.0f}pt}}\\selectfont\\@title\\par}}%",
        "  \\vspace{6pt}{\\normalsize\\@author\\par}%",
        "\\end{center}}",
        "\\makeatother",
        f"\\title{{{args.title}}}",
        f"\\author{{{args.author}}}",
        "\\date{}",
        "",
        "\\begin{document}",
    ] + (["\\maketitle", ""] if (args.title or args.author) else []) + [
        "% >>> BODY",
        "",
        "\\end{document}",
    ]
    return "\n".join(x for x in lines if x is not None) + "\n"


def build_official(L: dict, V: dict, args) -> tuple[str, str | None]:
    cands = [c for c in V.get("candidates", []) if c.get("available")]
    if not cands:
        return "", "来源文档类本机不可用，已回退到 replica 模式"
    c = cands[0]
    cls, opts = c["class"], c.get("options", "")
    doc = []
    doc.append("% !TeX program = xelatex")
    doc.append("% ============================================================================")
    doc.append(f"% 由 make_template.py 生成（official 模式）：{V.get('journal') or ''} / {V.get('publisher') or ''}")
    doc.append(f"% 使用出版方文档类 {cls}（{opts}）。版式由该文档类决定，与已发表 PDF 不完全一致。")
    doc.append("% 需要中文译文时已在下面加载 ctex；若该文档类与 ctex 冲突，请改用 --mode replica。")
    doc.append("% ============================================================================")
    doc.append(f"\\documentclass[{opts}]{{{cls}}}" if opts else f"\\documentclass{{{cls}}}")
    doc.append("\\usepackage[UTF8,scheme=plain]{ctex}")
    doc.append("\\usepackage{graphicx}")
    doc.append("\\usepackage{amsmath}")
    doc.append("\\usepackage{booktabs}")
    doc.append("\\usepackage{longtable}")
    doc.append("\\usepackage{calc}")
    doc.append("\\usepackage{array}")
    doc.append("\\usepackage{multirow}")
    doc.append("\\usepackage{enumitem}")
    doc.append("\\usepackage[hidelinks]{hyperref}")
    doc.append("\\usepackage{xurl}")
    doc.append("\\usepackage{algorithm}")
    doc.append("\\usepackage{algpseudocode}")
    doc.append("\\floatname{algorithm}{算法}")
    doc.append("\\renewcommand{\\algorithmicrequire}{\\textbf{输入：}}")
    doc.append("\\renewcommand{\\algorithmicensure}{\\textbf{输出：}}")
    doc.append("\\providecommand{\\tightlist}{\\setlength{\\itemsep}{0pt}}")
    doc.append("\\providecommand{\\pandocbounded}[1]{#1}")
    doc.append("\\providecommand{\\cbox}{\\fbox{\\phantom{X}}}")
    doc.append("\\providecommand{\\cboxc}{\\fbox{\\ding{51}}}")
    doc.append("")
    if args.title:
        doc.append(f"\\title{{{args.title}}}")
    if args.author:
        doc.append(f"\\author{{{args.author}}}")
    doc += ["\\begin{document}"]
    if args.title or args.author:
        doc += ["\\maketitle", ""]
    doc += ["% >>> BODY", "", "\\end{document}"]
    return "\n".join(doc) + "\n", None


def main():
    ap = argparse.ArgumentParser(description="按实测版式生成 main.tex")
    ap.add_argument("layout")
    ap.add_argument("--venue", default=None, help="detect_venue.py 的输出 JSON")
    ap.add_argument("--mode", default="replica", choices=["replica", "official"])
    ap.add_argument("--out", default="main.tex")
    ap.add_argument("--body", default="body", help="正文文件名（不含 .tex），写到 BODY 标记处")
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--size", type=float, default=None, help="覆盖正文字号（pt）")
    ap.add_argument("--columns", type=int, default=None, choices=[1, 2])
    ap.add_argument("--latin-font", default=None)
    ap.add_argument("--cjk-font", default=None)
    ap.add_argument("--header-zh", action="store_true",
                    help="把页眉改成中文默认值（作者等（年份））；默认照抄原文英文 running head")
    ap.add_argument("--keep-en-header", action="store_true",
                    help="保留英文页眉（与 --header-zh 同给时以此为准）")
    ap.add_argument("--header-left", default=None)
    ap.add_argument("--header-right", default=None)
    args = ap.parse_args()

    L = json.loads(Path(args.layout).read_text(encoding="utf-8"))
    V = None
    if args.venue and Path(args.venue).is_file():
        V = json.loads(Path(args.venue).read_text(encoding="utf-8"))

    warn = None
    if args.mode == "official":
        if not V:
            warn = "official 模式需要 --venue，已回退到 replica"
            text = build_replica(L, None, args)
        else:
            text, warn = build_official(L, V, args)
            if not text:
                text = build_replica(L, V, args)
    else:
        text = build_replica(L, V, args)

    text = text.replace("% >>> BODY", f"\\input{{{args.body}}}")
    out = Path(args.out)
    out.write_text(text, encoding="utf-8")

    size = nearest_size(args.size or L.get("body_font_pt"))
    print(f"已写入 {out}")
    print(f"  模式      : {args.mode}")
    print(f"  版式      : {L.get('page_size_pt')} pt, {args.columns or L.get('columns')} 栏, "
          f"正文 {size} pt / 行距 {L.get('leading_pt')} pt")
    if V:
        print(f"  来源      : {V.get('journal') or '—'} / {V.get('publisher') or '—'}")
    print(f"  正文字体  : {L.get('body_font_name') or '未知'} → {pick_latin(L.get('body_font_name'), args.latin_font)[0]}")
    if warn:
        print(f"  注意      : {warn}")


if __name__ == "__main__":
    main()
