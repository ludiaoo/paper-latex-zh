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
    over_h = re.findall(r"Overfull \\hbox \(([\d.]+)pt too wide\)", logtext)
    over_v = re.findall(r"Overfull \\vbox \(([\d.]+)pt too high\)", logtext)
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

    # 结构编号：从 PDF 抽取文本核对
    fig_ids = tab_ids = eq_ids = []
    if pdf.exists():
        try:
            import pymupdf
            d = pymupdf.open(pdf)
            txt = "\n".join(p.get_text() for p in d)
            d.close()
        except Exception:
            txt = ""
        fig_ids = numbers(r"图\s*(\d+)", txt)
        tab_ids = numbers(r"表\s*(\d+)", txt)
        eq_ids = numbers(r"\((\d{1,2})\)\s*$", txt)

    ok = code == 0 and not errors and not over_h and not over_v
    print(f"\n退出码      : {code}")
    print(f"PDF 页数    : {pages}")
    print(f"错误        : {len(errors)}")
    for e in errors[:8]:
        print("   ", e)
    print(f"Overfull h  : {len(over_h)}" + (f"  ({', '.join(over_h[:6])} pt)" if over_h else ""))
    print(f"Overfull v  : {len(over_v)}" + (f"  ({', '.join(over_v[:6])} pt)" if over_v else ""))
    print(f"未定义引用  : {'是' if undef else '否'}")

    def check_seq(name, ids):
        if not ids:
            print(f"{name}编号     : 未检出（若文中确实没有可忽略）")
            return True
        miss = [i for i in range(1, max(ids) + 1) if i not in ids]
        print(f"{name}编号     : {ids[0]}–{ids[-1]}，缺 {miss if miss else '无'}")
        return not miss

    if pdf.exists():
        ok = check_seq("图", fig_ids) and ok
        ok = check_seq("表", tab_ids) and ok
        ok = check_seq("公式", eq_ids) and ok

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
