#!/usr/bin/env python3
"""核对 layout.json 里的图 bbox 是否盖住了全部内容，并给出可复制的 --rect 修正值。

用法:
    python verify_figures.py <source.pdf> <layout.json> [--pad 3] [--tol 2.5] [--json out.json]

判定：以 bbox 上下各 12pt 为观察带，取带内所有矢量对象与文本行（x 限定在版心 ±6pt）的并集，
再看 bbox 四边还剩多少内容在外面。任一边超过 --tol 判 FAIL，并输出建议 rect。

退出码: 0 全部通过 / 1 有 FAIL / 2 环境问题（缺 pymupdf 或文件）。
依赖: 只用 pymupdf。
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


# 图题/表题本身就在图的下方，不能算作"图的内容"，否则每张图的底边都会报缺口
CAPTION_RE = re.compile(r"^\s*(图|表|Fig\.?|Table)\s*\d")


def body_x_range(doc, layout) -> tuple[float, float]:
    m = (layout or {}).get("margins_pt") or {}
    pw = doc[0].rect.width
    left = m.get("left")
    right = m.get("right")
    if left is None or right is None:
        xs = [w[0] for pg in doc for w in pg.get_text("words")]
        xe = [w[2] for pg in doc for w in pg.get_text("words")]
        return (min(xs), max(xe)) if xs else (0.0, pw)
    return float(left), pw - float(right)


def table_rects(pg):
    """页内表格的包围盒。图紧贴表格时，表格的行文字会被误判成"图被切掉"，
    用 find_tables() 把表格区域排除掉。老版本 pymupdf 没有该接口时返回空表。"""
    try:
        tf = pg.find_tables()
    except Exception:
        return []
    return [tuple(t.bbox) for t in getattr(tf, "tables", [])]


def page_objects(pg, h: float, page_area: float, bl: float, br: float):
    """取该页可用于判断图边界的对象：矢量矩形 + 文本行 bbox。"""
    tabs = table_rects(pg)
    rects = []
    for d in pg.get_drawings():
        r = d["rect"]
        if r.width <= 0.5 and r.height <= 0.5:
            continue
        if r.width * r.height > page_area * 0.7:
            continue
        if r.y1 < h * 0.06 or r.y0 > h * 0.94:
            continue
        # 又细又长的横线/竖线是表格分隔线，不是图内容；不排除会把上/下方表格的
        # 最后一条线误判成"图被切掉"（Fig.8 上方紧贴 Table 9 就是这种情况）
        if r.height <= 1.5 and r.width > (br - bl) * 0.7:
            continue
        if r.width <= 1.5 and r.height > (h * 0.5):
            continue
        if any(t[0] - 2 <= (r.x0 + r.x1) / 2 <= t[2] + 2
               and t[1] - 2 <= (r.y0 + r.y1) / 2 <= t[3] + 2 for t in tabs):
            continue
        rects.append((r.x0, r.y0, r.x1, r.y1))
    for blk in pg.get_text("dict").get("blocks", []):
        for ln in blk.get("lines", []):
            b = ln["bbox"]
            if CAPTION_RE.match("".join(sp.get("text", "") for sp in ln.get("spans", [])).strip()):
                continue
            if any(t[0] - 2 <= (b[0] + b[2]) / 2 <= t[2] + 2
                   and t[1] - 2 <= (b[1] + b[3]) / 2 <= t[3] + 2 for t in tabs):
                continue
            rects.append((b[0], b[1], b[2], b[3]))
    return rects


def main():
    ap = argparse.ArgumentParser(description="核对图 bbox 是否盖住全部内容")
    ap.add_argument("pdf")
    ap.add_argument("layout")
    ap.add_argument("--pad", type=float, default=3.0, help="建议 rect 的外扩量（pt）")
    ap.add_argument("--tol", type=float, default=2.5, help="允许的缺口（pt）")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    pdf, lay = Path(args.pdf), Path(args.layout)
    for f in (pdf, lay):
        if not f.is_file():
            sys.exit(f"找不到 {f}")
    layout = json.loads(lay.read_text(encoding="utf-8"))
    doc = pymupdf.open(str(pdf))
    bl, br = body_x_range(doc, layout)
    pw, ph = doc[0].rect.width, doc[0].rect.height
    page_area = pw * ph

    # find_tables() 很慢，按页缓存一次；否则每张图都会把整份 PDF 重扫一遍
    cache: dict[int, list] = {}

    def objects_of(pno: int):
        if pno not in cache:
            pg = doc[pno - 1]
            cache[pno] = page_objects(pg, pg.rect.height, page_area, bl, br)
        return cache[pno]

    rows, fails = [], 0
    for fig in layout.get("figures", []):
        bbox = fig.get("bbox")
        num, pno = fig.get("num"), fig.get("page")
        if not bbox:
            rows.append({"num": num, "page": pno, "status": "SKIP",
                         "note": "无 bbox（纯位图/手工裁）"})
            continue
        x0, y0, x1, y1 = bbox
        band = (y0 - 12, y1 + 12)
        objs = []
        for r in objects_of(pno):        # 只在图所在页找，避免跨页误并
            cy = (r[1] + r[3]) / 2
            if not (band[0] <= cy <= band[1]):
                continue
            if r[2] < bl - 6 or r[0] > br + 6:
                continue
            objs.append(r)
        if not objs:
            rows.append({"num": num, "page": pno, "status": "SKIP",
                         "note": "页内无可测对象"})
            continue
        ux0 = min(o[0] for o in objs); uy0 = min(o[1] for o in objs)
        ux1 = max(o[2] for o in objs); uy1 = max(o[3] for o in objs)
        l = x0 - ux0            # >0 表示左侧有内容露在框外
        r = ux1 - x1            # >0 表示右侧有内容露在框外
        t = y0 - uy0
        b = uy1 - y1
        worst = max(l, r, t, b)
        ok = worst <= args.tol
        if not ok:
            fails += 1
        rect = (round(ux0 - args.pad, 1), round(uy0 - args.pad, 1),
                round(ux1 + args.pad, 1), round(uy1 + args.pad, 1))
        rows.append({
            "num": num, "page": pno, "status": "OK" if ok else "FAIL",
            "gaps": {"l": round(l, 1), "r": round(r, 1), "t": round(t, 1), "b": round(b, 1)},
            "rect": rect,
        })
    doc.close()

    print(f"{'图':<8}{'页':<5}{'判定':<6}缺口 / 建议")
    for row in rows:
        head = f"Fig.{row['num']:<3} p{str(row['page']):<4}{row['status']:<6}"
        if row["status"] == "SKIP":
            print(head + row.get("note", ""))
        elif row["status"] == "OK":
            g = row["gaps"]
            print(head + f"l={g['l']:+.1f} r={g['r']:+.1f} t={g['t']:+.1f} b={g['b']:+.1f}")
        else:
            g = row["gaps"]
            x0, y0, x1, y1 = row["rect"]
            print(head + f"l={g['l']:+.1f} r={g['r']:+.1f} t={g['t']:+.1f} b={g['b']:+.1f}"
                  f"   -> --rect {row['num']}={row['page']}:{x0},{y0},{x1},{y1}")

    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 已写入 {args.json}")
    print(f"\n{sum(1 for r in rows if r['status'] == 'OK')} 通过 / "
          f"{fails} 失败 / {sum(1 for r in rows if r['status'] == 'SKIP')} 跳过")
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
