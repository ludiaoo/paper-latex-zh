#!/usr/bin/env python3
"""按 probe_layout.py 输出的 JSON 批量裁剪论文插图。

用法:
    python crop_figures.py <input.pdf> <layout.json> [--out figs] [--dpi 300]
    python crop_figures.py <input.pdf> <layout.json> --rect 10=24:58,56,498,380

--rect 格式: 编号=页码:x0,y0,x1,y1（PDF pt，可重复），用于修正自动定位失败的图。

设计要点（踩坑记录）:
- 裁剪范围只取"图题上方、上一张图题下方"，避免把相邻图或正文段落裁进来；
- 纯位图（矢量对象不足）会在 probe 阶段标记 bbox=null，此处跳过并提示用 --rect；
- 输出 300 dpi PNG，保证 xelatex 嵌入后仍有足够清晰度。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf  # type: ignore
    except ImportError:
        sys.exit("需要 pymupdf：pip install pymupdf")


def parse_rects(items):
    out = {}
    for it in items or []:
        m = re.match(r"^(\d+)=(\d+):([\d.]+),([\d.]+),([\d.]+),([\d.]+)$", it.strip())
        if not m:
            sys.exit(f"--rect 格式错误：{it}（应为 编号=页码:x0,y0,x1,y1）")
        n, p, x0, y0, x1, y1 = m.groups()
        out[int(n)] = (int(p), float(x0), float(y0), float(x1), float(y1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("layout")
    ap.add_argument("--out", default="figs")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--rect", action="append", help="手动修正：编号=页码:x0,y0,x1,y1")
    args = ap.parse_args()

    data = json.loads(Path(args.layout).read_text(encoding="utf-8"))
    doc = pymupdf.open(args.pdf)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    manual = parse_rects(args.rect)

    done, skipped = [], []
    for fig in data.get("figures", []):
        n = fig["num"]
        if n in manual:
            pno, x0, y0, x1, y1 = manual[n]
        elif fig.get("bbox"):
            x0, y0, x1, y1 = fig["bbox"]
            pno = fig["page"]
        else:
            skipped.append(n)
            continue
        clip = pymupdf.Rect(x0, y0, x1, y1)
        pix = doc[pno - 1].get_pixmap(clip=clip, dpi=args.dpi)
        path = outdir / f"fig{n}.png"
        pix.save(path)
        done.append((n, pno, pix.width, pix.height, path))

    for n, pno, w, h, path in done:
        print(f"Fig.{n:<2d} p{pno:<2d} {w}x{h}  -> {path}")
    if skipped:
        print(f"\n未定位（纯位图或无图题）：{skipped}")
        print("用 --rect N=页码:x0,y0,x1,y1 手动指定后重跑。")
    doc.close()


if __name__ == "__main__":
    main()
