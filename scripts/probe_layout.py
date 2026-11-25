#!/usr/bin/env python3
"""探测学术 PDF 的版式参数与图表位置，输出可复用的 JSON 与人类可读摘要。

用法:
    python probe_layout.py <input.pdf> [--json layout.json] [--dpi 200]

输出:
    - 页面尺寸、估计页边距、单栏/双栏判定
    - 正文字号与行距（取众数）
    - 页眉/页脚候选行
    - 每个 "Fig. N." / "Table N" 的页码与 y 坐标
    - 每个图的 bbox（按图题 y 分段 + 矢量对象聚合，同页多图可正确拆分）

依赖: pymupdf (pip install pymupdf)
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

try:
    import pymupdf
except ImportError:  # 兼容旧包名
    try:
        import fitz as pymupdf  # type: ignore
    except ImportError:
        sys.exit("需要 pymupdf：pip install pymupdf")

FIG_CAP = re.compile(r"^Fig\.?\s*(\d+)\.?\s")
TAB_CAP = re.compile(r"^Table\s*(\d+)")


def page_text_dict(page):
    return page.get_text("dict")


def body_font_and_leading(doc, sample_pages=6):
    """用 span 的 size 与 line 的基线间距估计正文字号与行距。"""
    sizes = Counter()
    fonts = Counter()
    leadings = []
    for pno in range(min(sample_pages, doc.page_count)):
        d = page_text_dict(doc[pno])
        for blk in d.get("blocks", []):
            lines = blk.get("lines", [])
            for ln in lines:
                for sp in ln.get("spans", []):
                    t = (sp.get("text") or "").strip()
                    # 下限 7.5：Elsevier 正文常为 8 pt，卡 8.5 会漏掉正文、误取图题 10.6 pt。
                    # 上限 12：排除标题。
                    if len(t) >= 12 and 7.5 <= sp["size"] <= 12:
                        sizes[round(sp["size"], 1)] += len(t)
                        fonts[sp.get("font", "").split("+")[-1]] += len(t)
                # 同一 block 内相邻行距离
        # 行距单独收集（按 block）
        for blk in d.get("blocks", []):
            ys = [ln["bbox"][1] for ln in blk.get("lines", [])]
            for a, b in zip(ys, ys[1:]):
                d_ = b - a
                if 4 < d_ < 40:
                    leadings.append(round(d_, 1))
    top = sizes.most_common(3)
    size = top[0][0] if top else None
    candidates = [x[0] for x in top]
    font = fonts.most_common(1)[0][0] if fonts else None
    leading = Counter(leadings).most_common(1)[0][0] if leadings else None
    return size, leading, candidates, font


def estimate_columns(doc, sample_pages=6):
    """用正文区的 x 直方图判断单栏/双栏。返回 (n_cols, gutter_x0, gutter_x1)。"""
    xs = []
    for pno in range(min(sample_pages, doc.page_count)):
        pg = doc[pno]
        for w in pg.get_text("words"):
            xs.append((w[0], w[2], w[1]))
    if not xs:
        return 1, None, None
    xmin = min(a for a, _, _ in xs)
    xmax = max(b for _, b, _ in xs)
    width = xmax - xmin
    # 把正文横向分成 40 格，统计每格词数
    bins = [0] * 40
    for a, b, _ in xs:
        i = min(39, max(0, int((a - xmin) / width * 40)))
        bins[i] += 1
    mid = bins[18:22]
    total = sum(bins) or 1
    # 中缝若几乎无词（<1.5%）且两侧均有大量词，判为双栏
    if sum(mid) / total < 0.015 and sum(bins[:18]) > total * 0.2 and sum(bins[22:]) > total * 0.2:
        i = bins.index(min(mid))
        return 2, xmin + width * i / 40, xmin + width * (i + 1) / 40
    return 1, None, None


def header_footer(doc, page_no=2):
    """返回页眉、页脚候选文本（页面上下 8% 区域）。

    默认读第 2 页而非第 1 页：首页常是标题页，页眉与正文页不同；
    只有一页时退回第 1 页。
    """
    page_no = min(page_no, doc.page_count)
    pg = doc[page_no - 1]
    h = pg.rect.height
    head, foot = [], []
    for w in pg.get_text("words"):
        y = w[1]
        if y < h * 0.08:
            head.append((w[1], w[0], w[4]))
        elif y > h * 0.92:
            foot.append((w[1], w[0], w[4]))
    # 按 y 聚成行。左右页眉常因字号/基线微差落在不同 y，只取一行会丢一侧，
    # 故把所有行按 y 排序后用 " || " 拼接。
    def rows_to_line(items):
        rows = {}
        for y, x, t in items:
            rows.setdefault(round(y), []).append((x, t))
        return "  ||  ".join(
            " ".join(t for _, t in sorted(rows[k])) for k in sorted(rows)
        )

    return rows_to_line(head), rows_to_line(foot)


def find_captions(doc, body_left=0.0, body_width=1.0):
    """找图题/表题。

    只认块首匹配 + 居中信号，避免把正文里的 "Fig. 7 shows ..." 当成图题。
    图题通常居中排（起点明显右于版心左边界），正文引用则左对齐。
    """
    figs, tabs = [], []
    indent = body_width * 0.05
    for pno in range(doc.page_count):
        pg = doc[pno]
        d = page_text_dict(pg)
        for blk in d.get("blocks", []):
            lines = blk.get("lines", [])
            if not lines:
                continue
            # 图题可能被拆成两行（"Fig. 3." + 标题），合并整个 block 的前两行再匹配
            first = " ".join(
                "".join(sp.get("text", "") for sp in ln.get("spans", [])).strip()
                for ln in lines[:2]
            ).strip()
            x0 = lines[0]["bbox"][0]
            centered = (x0 - body_left) > indent
            # 图题上方隔着整张图（非文本），与上一文本块的间距必然远大于行距；
            # 正文里 "Fig. 7 shows ..." 这类引用的间距只有 ~10.5pt。
            gap = 999.0
            ordered = sorted(d.get("blocks", []), key=lambda b: b["bbox"][1])
            idx = next((i for i, b in enumerate(ordered) if b is blk), 0)
            if idx > 0:
                gap = lines[0]["bbox"][1] - ordered[idx - 1]["bbox"][3]
            m = FIG_CAP.match(first)
            if m and len(first) > 10 and (centered or gap > 18):
                figs.append({"num": int(m.group(1)), "page": pno + 1,
                             "y": round(lines[0]["bbox"][1], 1), "text": first[:80]})
            # 表题：不强制居中（英文期刊表题常左对齐），用长度 + 无动词过滤正文引用
            first_line = "".join(sp.get("text", "") for sp in lines[0].get("spans", [])).strip()
            m2 = TAB_CAP.match(first_line)
            bad = re.search(r"\b(shows?|summar|report|present|list|compar|gives?|indicat)", first_line, re.I)
            if m2 and 6 < len(first_line) < 60 and not bad:
                tabs.append({"num": int(m2.group(1)), "page": pno + 1,
                             "y": round(lines[0]["bbox"][1], 1), "text": first_line[:80]})
    return figs, tabs


def figure_boxes(doc, figs, pad=2.0):
    """按图题 y 分段聚合矢量对象，得到每张图的 bbox。

    关键点（踩坑记录）：
    - 不能用 search_for("Fig. N.") 找图题，它会命中正文里的引用；
    - 同页多图必须按图题 y 分段，否则会把相邻两张图连成一个 bbox；
    - 图内文字可能是位图，get_text 抓不到，只能靠 get_drawings。
    """
    by_page: dict[int, list[dict]] = {}
    for f in figs:
        by_page.setdefault(f["page"], []).append(f)
    out = []
    for pno, items in by_page.items():
        items.sort(key=lambda x: x["y"])
        pg = doc[pno - 1]
        drawings = [d["rect"] for d in pg.get_drawings()
                    if d["rect"].width > 4 and d["rect"].height > 4]
        prev_y = 40.0
        for it in items:
            cap_y = it["y"]
            sel = [r for r in drawings if r.y1 <= cap_y - 1 and r.y0 >= prev_y - 2]
            prev_y = cap_y
            if len(sel) < 3:      # 矢量对象太少，可能是纯位图，留空待人工确认
                out.append({**it, "bbox": None, "note": "vector objects too few"})
                continue
            x0 = min(r.x0 for r in sel); y0 = min(r.y0 for r in sel)
            x1 = max(r.x1 for r in sel); y1 = cap_y - 1
            out.append({**it, "bbox": [round(x0 - pad, 1), round(y0 - pad, 1),
                                       round(x1 + pad, 1), round(y1, 1)]})
    # 同一编号可能有多个候选（图题 + 正文引用/图内文字）。真图题按它分段得到的
    # bbox 必然覆盖整张图，面积远大于引用处分段的结果，故取面积最大者。
    best = {}
    for it in out:
        bb = it.get("bbox")
        area = (bb[2] - bb[0]) * (bb[3] - bb[1]) if bb else -1
        if it["num"] not in best or area > best[it["num"]][0]:
            best[it["num"]] = (area, it)
    return [v[1] for _, v in sorted(((k, v) for k, v in best.items()))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--json", default=None)
    ap.add_argument("--dpi", type=int, default=200, help="仅用于提示渲染命令")
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    pw, ph = doc[0].rect.width, doc[0].rect.height
    size, leading, size_cands, font = body_font_and_leading(doc)
    ncol, g0, g1 = estimate_columns(doc)
    head, foot = header_footer(doc)

    # 正文区 x 范围（排除页眉页脚）
    bw = doc[0].rect.width
    bh = doc[0].rect.height
    bx = [(w[0], w[2]) for p in doc for w in p.get_text("words")
          if bh * 0.10 < w[1] < bh * 0.90]
    body_left = min(a for a, _ in bx) if bx else 0.0
    body_right = max(b for _, b in bx) if bx else bw
    figs, tabs = find_captions(doc, body_left, body_right - body_left)
    boxes = figure_boxes(doc, figs)

    # 页边距：正文词的最小/最大 x
    left, right = (body_left, pw - body_right) if bx else (None, None)

    data = {
        "file": str(Path(args.pdf).name),
        "pages": doc.page_count,
        "page_size_pt": [round(pw, 2), round(ph, 2)],
        "columns": ncol,
        "gutter_pt": [round(g0, 1), round(g1, 1)] if g0 else None,
        "margins_pt": {"left": round(left, 1), "right": round(right, 1)},
        "body_font_pt": size,
        "body_font_name": font,
        "body_font_candidates": size_cands,
        "leading_pt": leading,
        "header": head,
        "footer": foot,
        "figures": boxes,
        "tables": tabs,
    }
    doc.close()

    if args.json:
        Path(args.json).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"文件      : {data['file']}")
    print(f"页数      : {data['pages']}")
    print(f"页面尺寸  : {pw:.2f} x {ph:.2f} pt  ({pw/72*25.4:.1f} x {ph/72*25.4:.1f} mm)")
    print(f"栏数      : {ncol} 栏" + (f"  中缝 x={g0:.1f}-{g1:.1f} pt" if g0 else ""))
    print(f"左右边距  : {left:.1f} / {right:.1f} pt")
    print(f"字号/行距 : {size} pt / {leading} pt   字体 {font}")
    print(f"           候选字号 {size_cands}（正文通常取占比最大者，需人工复核）")
    print(f"页眉      : {head[:70]}")
    print(f"页脚      : {foot[:70]}")
    print(f"图 {len(figs)} 张、表 {len(tabs)} 个")
    for f in boxes:
        bb = f.get("bbox")
        print(f"  Fig.{f['num']:<2d} p{f['page']:<2d} " +
              (f"bbox={bb}" if bb else "bbox=未定位（纯位图，需人工裁剪）"))
    if args.json:
        print(f"\nJSON 已写入 {args.json}")


if __name__ == "__main__":
    main()
