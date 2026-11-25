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


def title_font(doc):
    """首页最大字号的 span 视为标题字号（用于生成模板时复刻标题层级）。"""
    sizes = Counter()
    for blk in page_text_dict(doc[0]).get("blocks", []):
        for ln in blk.get("lines", []):
            for sp in ln.get("spans", []):
                t = (sp.get("text") or "").strip()
                if len(t) >= 4:
                    sizes[round(sp["size"], 1)] += len(t)
    return max(sizes) if sizes else None


def estimate_columns(doc, sample_pages=6):
    """判断单栏/双栏，并给出中缝的实际 x 范围。

    踩过的两个坑：
    1) 假设「中缝在页面正中 10%」——窄页双栏（Elsevier 544pt、栏距 14pt）会漏判；
    2) 用「词起点」做直方图——中文每行只有一个起点，整栏几乎只有一列计数，
       缺口会虚胖成上百 pt。改为统计**覆盖度**：每个词的 x0..x1 都算覆盖，
       栏间没有字，缺口才是真中缝。
    """
    boxes = []
    for pno in range(min(sample_pages, doc.page_count)):
        for w in doc[pno].get_text("words"):
            boxes.append((w[0], w[2]))
    if not boxes:
        return 1, None, None
    xmin = min(a for a, _ in boxes)
    xmax = max(b for _, b in boxes)
    width = xmax - xmin
    if width <= 0:
        return 1, None, None

    step = 1.0
    nbins = int(width / step) + 2
    cover = [0] * nbins
    for a, b in boxes:
        i0 = max(0, int((a - xmin) / step))
        i1 = min(nbins - 1, int((b - xmin) / step))
        for i in range(i0, i1 + 1):
            cover[i] += 1

    # 只在版心 35%–65% 找覆盖为零的连续带
    lo, hi = int(nbins * 0.35), int(nbins * 0.65)
    best = (0, None, None)
    i = lo
    while i < hi:
        if cover[i] == 0:
            j = i
            while j < hi and cover[j] == 0:
                j += 1
            if j - i > best[0]:
                best = (j - i, i, j)
            i = j
        else:
            i += 1

    run, i0, i1 = best
    if run * step < 4:                       # 栏距小于 4pt 视为单栏（CJK 两端对齐会吃到 4pt）
        return 1, None, None
    g0, g1 = xmin + i0 * step, xmin + i1 * step
    mid = (g0 + g1) / 2
    left = sum(1 for a, b in boxes if b <= mid)
    right = sum(1 for a, b in boxes if a >= mid)
    if left > len(boxes) * 0.2 and right > len(boxes) * 0.2:
        return 2, round(g0, 1), round(g1, 1)
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
        if y < h * 0.06:          # 6%：8% 会把正文首行也算进来
            head.append((w[1], w[0], w[4]))
        elif y > h * 0.90:
            # 页脚：底部 10% 内且像页码（纯数字/罗马数字），或已经贴到页面最底
            txt = w[4].strip()
            if re.fullmatch(r"[0-9ivxlcIVXLC\-–—]+", txt) or y > h * 0.96:
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

    # 左/右页眉分开给（生成模板时要分别填 \fancyhead[L]/[R]）
    mid = pg.rect.width / 2
    h_left = " ".join(t for _, x, t in sorted(head, key=lambda z: (z[1], z[0])) if x < mid)
    h_right = " ".join(t for _, x, t in sorted(head, key=lambda z: (z[1], z[0])) if x >= mid)
    head_y = min(y for y, _, _ in head) if head else None
    foot_y = min(y for y, _, _ in foot) if foot else None
    return (rows_to_line(head), rows_to_line(foot), h_left.strip(), h_right.strip(),
            head_y, foot_y)


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


def add_table_structure(doc, tabs):
    """给每个表题配上表结构（列数/行数/表头），供 check_tables.py 核对列数。

    find_tables() 比较慢，按页缓存；老版本 pymupdf 没有该接口时写 null 并提示，
    不抛异常——没有它只是少了列数校验，不影响其他功能。
    """
    has_api = hasattr(pymupdf.Page, "find_tables")
    if not has_api:
        print("提示：当前 pymupdf 无 find_tables()，表格列数记为 null（升级 pymupdf 可启用列数校验）")
        return [{**t, "ncols": None, "nrows": None} for t in tabs]

    by_page: dict[int, list] = {}
    for t in tabs:
        by_page.setdefault(t["page"], []).append(t)
    out = []
    for pno, items in by_page.items():
        pg = doc[pno - 1]
        try:
            found = pg.find_tables().tables
        except Exception:
            found = []
        items.sort(key=lambda x: x["y"])
        used: set[tuple] = set()          # 同一张表不能配给两个表题（表 6/表 7 曾重复命中）
        for it in items:
            cap_y = it["y"]
            # 题注可能在表上方（多数期刊）也可能在下方，取距题注最近的那个表；
            # 早期按「表底在题注之上」筛，正好把题注在上、表在下的情况全排除了
            def dist(tb):
                if tb.bbox[1] >= cap_y - 2:
                    return tb.bbox[1] - cap_y
                if tb.bbox[3] <= cap_y + 2:
                    return cap_y - tb.bbox[3]
                return 0.0
            cands = [t for t in found
                     if dist(t) <= 140 and tuple(round(v, 1) for v in t.bbox) not in used]
            if not cands:
                out.append({**it, "ncols": None, "nrows": None})
                continue
            t = min(cands, key=dist)
            used.add(tuple(round(v, 1) for v in t.bbox))
            try:
                rows = t.extract()
            except Exception:
                rows = []
            header = [str(c) for c in (rows[0][:6] if rows else [])]
            # 表头几乎全空、或行数过少时，find_tables 的列数不可信（无框线表常被切错），
            # 宁可给 null 让 check_tables 跳过，也不要报一个假的不符
            filled = sum(1 for c in header if str(c).strip())
            if filled < 2 or int(t.row_count) < 3:
                out.append({**it, "ncols": None, "nrows": int(t.row_count),
                            "table_bbox": [round(v, 1) for v in t.bbox],
                            "header": header, "note": "表头不清，列数不可信"})
                continue
            out.append({**it, "ncols": int(t.col_count), "nrows": int(t.row_count),
                        "table_bbox": [round(v, 1) for v in t.bbox], "header": header})
    return out


def figure_boxes(doc, figs, pad=5.0):
    """按图题 y 分段聚合「矢量对象 + 文本行」，得到每张图的 bbox。

    关键点（踩坑记录）：
    - 不能用 search_for("Fig. N.") 找图题，它会命中正文里的引用；
    - 同页多图必须按图题 y 分段，否则会把相邻两张图连成一个 bbox；
    - 不能按尺寸过滤矢量对象：Elsevier 图的刻度字形高约 2.8pt、宽约 4.8pt，
      用 width>4 会把它们筛掉，bbox 只剩坐标轴框，左边界少 20pt 以上；
    - 图内文字可能是可选中文本而不是路径，只聚合 get_drawings 会漏掉轴标题；
    - pad 至少 5pt：2pt 在 300dpi 下只有 8px，切边很常见。
    """
    by_page: dict[int, list[dict]] = {}
    for f in figs:
        by_page.setdefault(f["page"], []).append(f)
    out = []
    for pno, items in by_page.items():
        items.sort(key=lambda x: x["y"])
        pg = doc[pno - 1]
        h = pg.rect.height
        page_area = pg.rect.width * h
        rects = []
        for d in pg.get_drawings():
            r = d["rect"]
            if r.width <= 0.5 and r.height <= 0.5:
                continue
            if r.width * r.height > page_area * 0.7:      # 整页背景框
                continue
            if r.y1 < h * 0.06 or r.y0 > h * 0.94:        # 页眉页脚区
                continue
            rects.append(r)
        lines = []
        for blk in page_text_dict(pg).get("blocks", []):
            if blk.get("type") == 1:          # 图片块：合成图里的小图标常是位图，不聚合就会切边
                b = blk.get("bbox")
                if not b:
                    continue
                bw, bh_ = b[2] - b[0], b[3] - b[1]
                if bw * bh_ > page_area * 0.7:
                    continue
                rects.append(pymupdf.Rect(*b))
                continue
            for ln in blk.get("lines", []):
                lines.append(ln["bbox"])

        prev_y = 40.0
        for it in items:
            cap_y = it["y"]
            band_top = prev_y
            sel = [r for r in rects if r.y1 <= cap_y - 1 and r.y0 >= band_top - 2]
            prev_y = cap_y
            if len(sel) < 3:      # 矢量对象太少，可能是纯位图，留空待人工确认
                out.append({**it, "bbox": None, "note": "vector objects too few"})
                continue
            # 先用"大对象"（坐标轴框这类）做锚点，再向外吸收小对象与文本行。
            # 不能直接拿全部对象求并集：同页上方的表格会把 bbox 拉高上百 pt；
            # 也不能按垂直空隙切簇：多子图之间本身就有 25pt 以上留白。
            anchor = [r for r in sel if r.width > 4 and r.height > 4]
            if len(anchor) < 3:
                anchor = sel
            x0 = min(r.x0 for r in anchor); y0 = min(r.y0 for r in anchor)
            x1 = max(r.x1 for r in anchor); y1 = max(r.y1 for r in anchor)
            ax0, ax1 = x0, x1                   # 锚点的横向范围，用于判定"在图内"
            # 只扩一圈、不迭代：迭代会让表格线把 bbox 一路往上带（Fig.8 上方是 Table 9）
            for _ in range(1):
                for r in sel:
                    # 横向在图内的对象（色标标题、子图注）允许向上吸 25pt；
                    # 横向超出图范围的（同页上方整宽表格线）只给 12pt，避免吞表
                    inside = r.x0 >= ax0 - 5 and r.x1 <= ax1 + 5
                    vpad = 25 if inside else 12
                    if (r.x1 < x0 - 28 or r.x0 > x1 + 28
                            or r.y1 < y0 - vpad or r.y0 > y1 + vpad):
                        continue
                    x0 = min(x0, r.x0); y0 = min(y0, r.y0)
                    x1 = max(x1, r.x1); y1 = max(y1, r.y1)
                for b in lines:
                    if b[1] < band_top - 2 or b[3] > cap_y - 1:
                        continue
                    # 用行框中点判定，口径与 verify_figures.py 一致；
                    # 用边缘判定会漏掉"中心在带内、但顶端略高"的行
                    cy = (b[1] + b[3]) / 2
                    if (b[2] < x0 - 28 or b[0] > x1 + 28
                            or not (y0 - 12 <= cy <= y1 + 12)):
                        continue
                    x0 = min(x0, b[0]); y0 = min(y0, b[1])
                    x1 = max(x1, b[2]); y1 = max(y1, b[3])
            out.append({**it, "bbox": [round(x0 - pad, 1), round(y0 - pad, 1),
                                       round(x1 + pad, 1), round(cap_y - 3, 1)]})
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
    head, foot, head_l, head_r, head_y, foot_y = header_footer(doc)
    title_pt = title_font(doc)

    # 正文区 x 范围（排除页眉页脚）
    bw = doc[0].rect.width
    bh = doc[0].rect.height
    bx = [(w[0], w[2]) for p in doc for w in p.get_text("words")
          if bh * 0.10 < w[1] < bh * 0.90]
    body_left = min(a for a, _ in bx) if bx else 0.0
    body_right = max(b for _, b in bx) if bx else bw
    # 版心上下边界（排除页眉页脚区域），生成模板时直接用
    # 用页眉/页脚的实际 y 界定正文区：否则页脚页码会被算进正文，底边距偏小十几 pt
    y_top = (head_y + 8) if head_y else bh * 0.09
    y_bot = (foot_y - 6) if foot_y else bh * 0.94
    bys = [(w[1], w[3]) for p in doc for w in p.get_text("words")
           if y_top < w[1] < y_bot]
    body_top = min(a for a, _ in bys) if bys else None
    body_bottom = max(b for _, b in bys) if bys else None

    figs, tabs = find_captions(doc, body_left, body_right - body_left)
    boxes = figure_boxes(doc, figs)
    tabs = add_table_structure(doc, tabs)

    # 页边距：正文词的最小/最大 x
    left, right = (body_left, pw - body_right) if bx else (None, None)

    data = {
        "file": str(Path(args.pdf).name),
        "pages": doc.page_count,
        "page_size_pt": [round(pw, 2), round(ph, 2)],
        "columns": ncol,
        "gutter_pt": [round(g0, 1), round(g1, 1)] if g0 else None,
        "margins_pt": {
            "left": round(left, 1), "right": round(right, 1),
            "top": round(body_top, 1) if body_top else None,
            "bottom": round(ph - body_bottom, 1) if body_bottom else None,
        },
        "body_bbox_y": [round(body_top, 1), round(body_bottom, 1)] if body_top else None,
        "title_font_pt": title_pt,
        "body_font_pt": size,
        "body_font_name": font,
        "body_font_candidates": size_cands,
        "leading_pt": leading,
        "header": head,
        "header_left": head_l,
        "header_right": head_r,
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
