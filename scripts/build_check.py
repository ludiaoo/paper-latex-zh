#!/usr/bin/env python3
"""编译 LaTeX 工程并做结构校验（图号/表号/公式号连续性、溢出、错误）。

用法:
    python build_check.py main.tex [--preview 1,5,12] [--dpi 110]

检查项:
    - xelatex 退出码与 log 中的 "! " 错误
    - Overfull \\hbox / \\vbox（排版溢出）
    - 未定义引用 / 乘号
    - PDF 页数
    - 正文中 "图 N" / "表 N" / 公式 "(N)" 编号是否连续
    - 可选：渲染指定页为 PNG 便于肉眼验收

退出码: 0 全部通过；1 有错误或溢出；2 环境问题（找不到 xelatex）。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

CANDIDATES = [
    "xelatex",
    r"D:/texlive/2026/bin/windows/xelatex.exe",
    r"C:/texlive/2026/bin/windows/xelatex.exe",
    "/usr/local/texlive/2026/bin/x86_64-linux/xelatex",
    "/Library/TeX/texbin/xelatex",
]


def find_xelatex(explicit: str | None) -> str:
    if explicit:
        return explicit
    for c in CANDIDATES:
        p = shutil.which(c) if "/" not in c and "\\" not in c else (c if Path(c).exists() else None)
        if p:
            return p
    sys.exit("找不到 xelatex；用 --xelatex 指定路径")


def run(tex: Path, xelatex: str) -> tuple[int, str]:
    """编译两遍（交叉引用），返回 (最后一次返回码, 最后一次 stdout)。"""
    last = (0, "")
    for _ in range(2):
        p = subprocess.run(
            [xelatex, "-interaction=nonstopmode", "-halt-on-error", tex.name],
            cwd=tex.parent, capture_output=True, text=True, errors="replace",
        )
        last = (p.returncode, p.stdout + p.stderr)
        if p.returncode != 0:
            break
    return last


def numbers(pattern: str, text: str) -> list[int]:
    return sorted({int(m) for m in re.findall(pattern, text, re.M)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tex")
    ap.add_argument("--xelatex", default=None)
    ap.add_argument("--preview", default=None, help="要渲染的页码，如 1,5,12")
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--overfull-tol", type=float, default=3.0,
                    help="允许的 Overfull pt 上限（默认 3，与 SKILL.md 验收标准一致）")
    args = ap.parse_args()

    tex = Path(args.tex).resolve()
    if not tex.exists():
        sys.exit(f"找不到 {tex}")
    xelatex = find_xelatex(args.xelatex)

    print(f"编译 {tex.name} …")
    code, out = run(tex, xelatex)
    log = tex.with_suffix(".log")
    logtext = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""

    errors = [l for l in logtext.splitlines() if l.startswith("! ")]
    # 与 SKILL.md 验收标准对齐：< 3pt 且不影响阅读的溢出不算失败。
    # 原文是字符串，转成 float 才能按阈值过滤。
    oh_all = [float(x) for x in re.findall(r"Overfull \\hbox \(([\d.]+)pt too wide\)", logtext)]
    ov_all = [float(x) for x in re.findall(r"Overfull \\vbox \(([\d.]+)pt too high\)", logtext)]
    over_h = [x for x in oh_all if x > args.overfull_tol]
    over_v = [x for x in ov_all if x > args.overfull_tol]
    undef = "undefined" in logtext.lower() and "LaTeX Warning: Reference" in logtext

    pdf = tex.with_suffix(".pdf")
    pages = None
    if pdf.exists():
        try:
            import pymupdf
            d = pymupdf.open(pdf); pages = d.page_count; d.close()
        except Exception:
            info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True)
            m = re.search(r"Pages:\s+(\d+)", info.stdout)
            pages = int(m.group(1)) if m else None

    # ---------- 结构编号 ----------
    # 早期做法是从 PDF 全文正则抓「图 N」，正文里的「如图 8 所示」也会被算作编号，
    # 于是少插一张图仍可能报「缺 无」——这是假阳性通过。
    # 改为优先读 .aux 的 \newlabel（只统计真正打过 \label 的浮动体）。
    fig_ids = tab_ids = eq_ids = []
    label_src = None
    aux = tex.with_suffix(".aux")
    if aux.exists():
        atxt = aux.read_text(encoding="utf-8", errors="replace")
        fig_ids = sorted({int(m) for m in re.findall(r"\\newlabel\{fig:[^}]*\}\{\{(\d+)\}", atxt)})
        tab_ids = sorted({int(m) for m in re.findall(r"\\newlabel\{tab:[^}]*\}\{\{(\d+)\}", atxt)})
        eq_ids = sorted({int(m) for m in re.findall(r"\\newlabel\{eq:[^}]*\}\{\{(\d+)\}", atxt)})
        if fig_ids or tab_ids:
            label_src = "aux"

    # 源码里的环境计数：没有 .aux 时至少能核对 float 与 caption 是否成对
    src_tex = ""
    for f in sorted(tex.parent.glob("*.tex")):
        src_tex += f.read_text(encoding="utf-8", errors="replace") + "\n"
    src_fig = len(re.findall(r"\\begin\{figure\*?\}", src_tex))
    src_tab = len(re.findall(r"\\begin\{table\*?\}", src_tex))
    src_cap = len(re.findall(r"\\caption\{", src_tex))

    if label_src is None and pdf.exists():
        try:
            import pymupdf
            d = pymupdf.open(pdf)
            txt = "\n".join(p.get_text() for p in d)
            pages_info = []
            repl = 0
            for i, pg in enumerate(d):
                t = pg.get_text()
                pages_info.append((i + 1, len(t.strip()), len(pg.get_images(full=True))))
                repl += t.count("\ufffd")
            d.close()
        except Exception:
            txt = ""
        eq_ids = numbers(r"\((\d{1,2})\)\s*$", txt)

    # ---------- 页面体检：近空白页与替换字符 ----------
    near_blank, repl_total, pages_audited = [], 0, 0
    if pdf.exists():
        try:
            import pymupdf
            d = pymupdf.open(pdf)
            for i, pg in enumerate(d):
                t = pg.get_text()
                chars = len(t.strip())
                repl_total += t.count("\ufffd")
                if chars < 120 and not pg.get_images(full=True):
                    near_blank.append((i + 1, chars))
            pages_audited = d.page_count
            d.close()
        except Exception:
            pass

    ok = code == 0 and not errors and not over_h and not over_v
    print(f"\n退出码      : {code}")
    print(f"PDF 页数    : {pages}")
    print(f"错误        : {len(errors)}")
    for e in errors[:8]:
        print("   ", e)
    fmt = lambda xs: ", ".join(f"{x:.1f}" for x in xs[:6])
    print(f"Overfull h  : {len(over_h)} pt>阈值"
          + (f"  ({fmt(over_h)})" if over_h else "")
          + (f"  [忽略 {len(oh_all) - len(over_h)} 处 ≤{args.overfull_tol:g}pt]" if len(oh_all) > len(over_h) else ""))
    print(f"Overfull v  : {len(over_v)} pt>阈值"
          + (f"  ({fmt(over_v)})" if over_v else "")
          + (f"  [忽略 {len(ov_all) - len(over_v)} 处 ≤{args.overfull_tol:g}pt]" if len(ov_all) > len(over_v) else ""))
    print(f"未定义引用  : {'是' if undef else '否'}")

    print(f"页面体检    : 近空白 {len(near_blank)} 页"
          + (f"（{', '.join(str(p) for p, _ in near_blank[:8])}）" if near_blank else "")
          + f"，替换字符 {repl_total} 个")
    if repl_total:
        print("              替换字符说明有字形缺字（U+FFFD），检查字体或缺字字符")

    def check_seq(name, ids):
        if not ids:
            print(f"{name}编号     : 未检出（若文中确实没有可忽略）")
            return True
        miss = [i for i in range(1, max(ids) + 1) if i not in ids]
        print(f"{name}编号     : {ids[0]}–{ids[-1]}，缺 {miss if miss else '无'}")
        return not miss

    if label_src == "aux":
        print(f"编号来源    : .aux 的 \\newlabel（源码里 figure {src_fig} 个、table {src_tab} 个、caption {src_cap} 个）")
        ok = check_seq("图", fig_ids) and ok
        ok = check_seq("表", tab_ids) and ok
        ok = check_seq("公式", eq_ids) and ok
    else:
        print(f"编号来源    : 无 .aux，改查源码计数（figure {src_fig} 个、table {src_tab} 个、caption {src_cap} 个）")
        if src_fig != src_cap - src_tab:
            print(f"              figure+caption 不成对，可能有图被漏插或 caption 写漏"
                  f"（应为 figure {src_fig} = caption {src_cap} − table {src_tab}）")
            ok = False
        ok = check_seq("公式", eq_ids) and ok
    if repl_total:
        ok = False

    if args.preview and pdf.exists():
        pages_wanted = [p.strip() for p in args.preview.split(",") if p.strip()]
        outdir = tex.parent / "build"
        outdir.mkdir(exist_ok=True)
        pdftoppm = shutil.which("pdftoppm")
        if pdftoppm:
            for p in pages_wanted:
                subprocess.run([pdftoppm, "-r", str(args.dpi), "-png",
                                "-f", p, "-l", p, str(pdf), str(outdir / f"preview-{p}")],
                               check=False)
            print(f"预览        : 已渲染 {', '.join(pages_wanted)} 页到 build/")

    print("\n" + ("全部通过。" if ok else "存在问题，见上。"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
