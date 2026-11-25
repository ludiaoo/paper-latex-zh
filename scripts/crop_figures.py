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


def edge_ink(pix, band: int = 3, white: int = 240) -> dict[str, float]:
    """统计四边各 band 像素宽的非白占比，判断裁剪是否切到了内容。

    直接用内存里的 Pixmap.samples，不落盘再读回；RGB 三通道都 >240 才算白。
    返回 {"上": x, "下": x, "左": x, "右": x}。
    """
    w, h, n = pix.width, pix.height, pix.n
    s = pix.samples
    step = 1

    def nonwhite(x: int, y: int) -> bool:
        i = (y * w + x) * n
        return not (s[i] > white and s[i + 1] > white and s[i + 2] > white)

    def ratio(points) -> float:
        total = 0
        hit = 0
        for x, y in points:
            total += 1
            if nonwhite(x, y):
                hit += 1
        return hit / total if total else 0.0

    band = max(1, min(band, h // 4 or 1, w // 4 or 1))
    top = ((x, y) for y in range(band) for x in range(0, w, step))
    bottom = ((x, y) for y in range(h - band, h) for x in range(0, w, step))
    left = ((x, y) for x in range(band) for y in range(0, h, step))
    right = ((x, y) for x in range(w - band, w) for y in range(0, h, step))
    return {"上": ratio(top), "下": ratio(bottom), "左": ratio(left), "右": ratio(right)}


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
        # 边缘墨迹检查：bbox 少算几 pt 时，图的轴标题会被切掉一截，
        # 裁剪结果本身看着"有图"，但边上还留着内容。四边各取 3px 带统计非白占比。
        for side, frac in edge_ink(pix).items():
            if frac > 0.02:
                print(f"[warn] fig{n} {side}边缘 {frac:.1%} 非白，可能切到内容，核对 {path}")

    for n, pno, w, h, path in done:
        print(f"Fig.{n:<2d} p{pno:<2d} {w}x{h}  -> {path}")
    if skipped:
        print(f"\n未定位（纯位图或无图题）：{skipped}")
        print("用 --rect N=页码:x0,y0,x1,y1 手动指定后重跑。")
    doc.close()


if __name__ == "__main__":
    main()
