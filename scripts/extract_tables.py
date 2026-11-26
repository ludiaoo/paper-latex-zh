#!/usr/bin/env python3
"""把 PDF 里的表格提取成可直接复制的 CSV / Markdown / LaTeX。

为什么不用 find_tables()：学术论文多为三线表（无竖线），pymupdf 的框线检测
会把 15 列的表识别成 4 列（实测表 8 即如此）。本脚本改用**文本坐标**重建行列：
先按 y 合并同一行，再按词间距切分单元格，最后把单元格挂到列锚点上。

输出的 CSV 是 UTF-8 BOM，Excel 双击即可打开；数字是纯文本（不是图片、不是公式），
可以直接选中、复制、求和。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pymupdf

ROW_TOL = 3.6          # 同一行内词基线允许的偏差（pt）
GAP_RATIO = 0.85       # 词间距 > 0.85 × 字高 就切成两个单元格
COL_GAP = 8.0          # 列锚点聚类阈值（pt）
HEADER_MAX = 3         # 最多识别几行表头


# ---------------------------------------------------------------- 字符规范化
def _build_math_map() -> dict[int, str]:
    """数学斜体字母 → 普通字符，否则复制到 Excel 里无法参与计算。"""
    m: dict[int, str] = {}
    for i in range(26):                       # 数学斜体大写 A-Z
        m[0x1D434 + i] = chr(ord("A") + i)
    for i in range(26):                       # 数学斜体小写 a-z
        m[0x1D44E + i] = chr(ord("a") + i)
    for i in range(25):                       # 数学斜体希腊 α-ω
        m[0x1D6FC + i] = chr(0x3B1 + i)
    for i in range(25):                       # 数学斜体希腊 Α-Ω
        m[0x1D6E2 + i] = chr(0x391 + i)
    for i in range(10):                       # 数学数字
        m[0x1D7CE + i] = str(i)
    return m


MATH_MAP = _build_math_map()
LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
             "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st"}
COMBINING = {"\u0304", "\u0305", "\u0307", "\u20d7"}   # 上划线/箭头等装饰


def normalize(text: str) -> str:
    s = text.translate(MATH_MAP)
    for k, v in LIGATURES.items():
        s = s.replace(k, v)
    for ch in COMBINING:
        s = s.replace(ch, "")
    s = (s.replace("\xa0", " ").replace("\u2212", "-")
          .replace("\u2013", "-").replace("\u2014", "-"))
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- 几何重建
def group_rows(pg, rect) -> list[list[tuple]]:
    """按 y 动态合并成行。固定步长分桶会把基线差 3pt 的上下标拆成两行。"""
    words = []
    for w in pg.get_text("words"):
        txt = w[4]
        if not txt.strip():
            continue
        cy = (w[1] + w[3]) / 2
        if not (rect[1] - 2 <= cy <= rect[3] + 2):
            continue
        if not (rect[0] - 4 <= w[0] <= rect[2] + 4):
            continue
        words.append((cy, w[0], w[2], txt, max(w[3] - w[1], 1.0)))
    words.sort()

    rows, cur = [], []
    for item in words:
        if cur and item[0] - cur[-1][0] > ROW_TOL:
            rows.append(sorted(cur, key=lambda z: z[1]))
            cur = []
        cur.append(item)
    if cur:
        rows.append(sorted(cur, key=lambda z: z[1]))
    return rows


def split_cells(row) -> list[tuple[float, float, str]]:
    """行内按间距切单元格。返回 (x0, x1, 文本)。"""
    cells, cur = [], None
    for _cy, x0, x1, txt, h in row:
        if cur is None:
            cur = [x0, x1, txt, h]
            continue
        if x0 - cur[1] > GAP_RATIO * cur[3]:
            cells.append((cur[0], cur[1], cur[2]))
            cur = [x0, x1, txt, h]
        else:
            # 同一个单元格内的续词：两侧都是拉丁字母时补空格，CJK 直接拼
            need_space = txt[:1].isascii() and cur[2][-1:].isascii() \
                and cur[2][-1:] not in "(["
            cur[1] = max(cur[1], x1)
            cur[2] += (" " if need_space else "") + txt
    if cur:
        cells.append((cur[0], cur[1], cur[2]))
    return cells


def cluster_columns(rows: list[list[tuple]], gap: float = COL_GAP) -> list[float]:
    """用数据行的单元格左边界聚类成列锚点。"""
    xs: list[float] = []
    for cells in rows:
        for x0, _x1, _t in cells:
            xs.append(round(x0, 1))
    if not xs:
        return []
    xs = sorted(set(xs))
    groups = [[xs[0]]]
    for x in xs[1:]:
        if x - groups[-1][-1] > gap:
            groups.append([x])
        else:
            groups[-1].append(x)
    return [round(sum(g) / len(g), 1) for g in groups]


def assign(cells, anchors, tol: float = 14.0) -> tuple[list[str], int]:
    """把单元格挂到最近的列锚点；同列多个值（单元格内换行）用空格连接。"""
    out = [""] * len(anchors)
    far = 0
    for x0, _x1, txt in cells:
        idx = min(range(len(anchors)), key=lambda i: abs(anchors[i] - x0))
        if abs(anchors[idx] - x0) > tol:
            far += 1
        out[idx] = f"{out[idx]} {txt}".strip() if out[idx] else txt
    return out, far


def looks_numeric_row(vals: list[str]) -> bool:
    """数据行判据：3/4 以上的单元格是数字（可带百分号、括号、负号）。

    阈值不能放宽到一半：多层表头的「0.1 0.2 0.3」会让表头行看起来像数据行，
    整块表头就被当成数据了。同时要求至少 3 个数字，避免 2 列表误判。
    """
    filled = [v for v in vals if v]
    if not filled:
        return False
    num = sum(1 for v in filled
              if re.fullmatch(r"[-\d.,%()×*]+", v.replace(" ", "")))
    return num >= 3 and num >= len(filled) * 0.75


def merge_header(header_cells: list[list[tuple]], anchors: list[float]) -> list[str]:
    """把多层表头压成一行。

    跨列标题（CPLEX 覆盖 2 列、PRS-VNS 覆盖 3 列）只占一个单元格，不能按序号对列。
    这里用「子标题归属」判定覆盖范围：父标题覆盖它下方、且起点在它和下一个父标题
    之间的那些子标题，子标题所在的列锚点就是它的跨度。
    最后一行没有子标题可依，按最近锚点一对一，避免末列被前面的窄标题吃掉。
    """
    cols: list[list[str]] = [[] for _ in anchors]
    for li, cells in enumerate(header_cells):
        nxt = header_cells[li + 1] if li + 1 < len(header_cells) else None
        for j, (x0, _x1, txt) in enumerate(cells):
            hi = cells[j + 1][0] - 2 if j + 1 < len(cells) else float("inf")
            span: list[int] = []
            if nxt:
                subs = [c[0] for c in nxt if x0 - 2 <= c[0] < hi]
                span = [i for i, a in enumerate(anchors)
                        if any(abs(a - s) <= 14 for s in subs)]
            if not span:
                span = [min(range(len(anchors)), key=lambda i: abs(anchors[i] - x0))]
            for i in span:
                if txt not in cols[i]:
                    cols[i].append(txt)
    return [" ".join(c) if c else f"列{i + 1}" for i, c in enumerate(cols)]


def merge_continuation(rows: list[list[str]]) -> list[list[str]]:
    """把「单元格内换行」产生的续行并回上一行。

    表格某一列文字排满后，剩下的一两个词会落到下一行，形成只有 1 个非空单元格的行。
    只处理非首列：符号表的分组标题（Sets / Parameters）也在首列，误并会把分组名吃掉。
    """
    out: list[list[str]] = []
    for r in rows:
        filled = [i for i, v in enumerate(r) if v]
        if (len(filled) == 1 and filled[0] > 0 and out
                and len(out[-1][filled[0]]) > 30):
            i = filled[0]
            out[-1][i] = f"{out[-1][i]} {r[i]}".strip()
            continue
        out.append(r)
    return out


def fill_merged(rows: list[list[str]]) -> list[list[str]]:
    """首列留空表示"同上"（rowspan），向下填满，便于直接筛选与复制。

    只有「至少两列有内容」的行才算数据行；单格分组标题（符号表的 Sets / Parameters）
    不能当成 rowspan 的来源，否则整组条目都会被填上那个分组名。
    """
    last = ""
    for r in rows:
        if not r:
            continue
        nonempty = sum(1 for v in r if v)
        if nonempty >= 2 and r[0]:
            last = r[0]
        elif nonempty >= 2 and not r[0] and last:
            r[0] = last
    return rows


# ---------------------------------------------------------------- 表格区域
def region_of(tab: dict, body: dict) -> tuple[float, float, float, float]:
    """优先用 probe 记下的 table_bbox；缺失时用版心 + 表题下方兜底。"""
    bb = tab.get("table_bbox")
    bl = body.get("left") or 40.0
    br = body.get("right") or 500.0
    if bb:
        return (bb[0], max(bb[1], tab["y"] + 2), bb[2], bb[3])
    return (bl, tab["y"] + 4, br, tab["y"] + 400)


def extract_one(pg, tab: dict, body: dict, normalize_chars: bool,
                keep_blanks: bool) -> dict:
    rect = region_of(tab, body)
    rows_raw = group_rows(pg, rect)
    if not rows_raw:
        return {"num": tab["num"], "error": "区域内没有文本"}

    cell_rows = [split_cells(r) for r in rows_raw]
    anchors = cluster_columns(cell_rows)
    if len(anchors) < 2:
        return {"num": tab["num"], "error": f"只识别出 {len(anchors)} 列，放弃"}

    assigned, far_total = [], 0
    for cells in cell_rows:
        vals, far = assign(cells, anchors)
        far_total += far
        assigned.append(vals)

    # 表头：区域顶部连续的非数据行，最多 HEADER_MAX 行
    head_n = 0
    for vals in assigned[:HEADER_MAX]:
        if looks_numeric_row(vals):
            break
        if any(len(v) > 40 for v in vals):     # 长句是释义列，属数据行
            break
        head_n += 1
    # 只有一行表头、且非空单元格比列数少 → 那是符号表的分组标题（Sets / Parameters），
    # 不是表头；把它当数据行保留，否则会输出「Sets N Nc」这种怪表头
    # 整张表几乎没有数字（算法对比表、符号表）时，第二行「看起来像表头」的其实是数据，
    # 只取第一行，否则会把首条数据并进表头（表 7 的 "Algorithm GVNS (...)" 就是这么来的）
    all_cells = [v for vals in assigned for v in vals if v]
    num_cells = sum(1 for v in all_cells
                    if re.fullmatch(r"[-\d.,%()×*]+", v.replace(" ", "")))
    if all_cells and num_cells / len(all_cells) < 0.3:
        head_n = min(head_n, 1)
    if head_n == 1 and sum(1 for v in assigned[0] if v) <= len(anchors) - 1:
        head_n = 0
    header = (merge_header(cell_rows[:head_n], anchors) if head_n
              else [f"列{i + 1}" for i in range(len(anchors))])
    body_rows = merge_continuation([r[:] for r in assigned[head_n:]])

    warnings = []
    if not keep_blanks:
        body_rows = fill_merged(body_rows)
    else:
        body_rows = body_rows

    # 分组标题行（符号表的 Sets / Parameters）本来就只有一格，不计入列数一致性检查
    widths = {len([v for v in r if v]) for r in body_rows
              if sum(1 for v in r if v) >= 2}
    if len(widths) > 1:
        warnings.append(f"数据行非空单元格数不一致 {sorted(widths)}，可能有合并单元格或串列")
    empties = sum(1 for r in body_rows if r and not r[0])
    if body_rows and empties > len(body_rows) * 0.5:
        warnings.append(f"首列有 {empties} 行为空，符号可能没被提取出来")
    if far_total:
        warnings.append(f"{far_total} 个单元格离列锚点超过 14pt，列数可能被判错")
    for r in body_rows:
        if not any(r):
            warnings.append("存在空行（可能是表格中间的空白带）")
            break

    def clean(v):
        return normalize(v) if normalize_chars else v

    header = [clean(v) for v in header]
    body_rows = [[clean(v) for v in r] for r in body_rows]
    return {
        "num": tab["num"], "page": tab["page"],
        "ncols": len(anchors), "nrows": len(body_rows),
        "header": header, "rows": body_rows,
        "region": [round(v, 1) for v in rect],
        "anchor_x": anchors, "warnings": warnings,
    }


# ---------------------------------------------------------------- 输出
def write_csv(path: Path, data: dict):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(data["header"])
        w.writerows(data["rows"])


def write_md(path: Path, tables: list[dict], title: str):
    def esc(s):
        return (s or "").replace("|", "\\|").replace("\n", " ")

    out = [f"# {title}", ""]
    for t in tables:
        if t.get("error"):
            out += [f"## 表 {t['num']}（未提取）", "", f"> {t['error']}", ""]
            continue
        out += [f"## 表 {t['num']}（{t['nrows']} 行 × {t['ncols']} 列，p{t['page']}）", ""]
        out.append("| " + " | ".join(esc(h) for h in t["header"]) + " |")
        out.append("|" + "|".join(["---"] * t["ncols"]) + "|")
        for r in t["rows"]:
            out.append("| " + " | ".join(esc(v) for v in r) + " |")
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")


TEX_ESC = [("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"),
           ("#", r"\#"), ("_", r"\_"), ("$", r"\$"),
           ("{", r"\{"), ("}", r"\}"), ("~", r"\textasciitilde{}"),
           ("^", r"\textasciicircum{}")]


def tex_escape(s: str) -> str:
    """% & _ 等在 LaTeX 里是特殊字符，不转义会把整行变成注释。

    方括号也要包成 {[}：数据行若以 "[1.0,1.5)" 开头，上一行行尾的 \\\\
    会把它当成「行距可选参数」整行吃掉，随后 \\bottomrule 错位报
    "\\@BTrule has an extra }"。
    """
    for a, b in TEX_ESC:
        s = s.replace(a, b)
    return s.replace("[", "{[}")


def write_tex(path: Path, data: dict, caption: str = ""):
    rows, n = data["rows"], data["ncols"]
    lens = [max([len(r[i]) for r in rows] + [len(data["header"][i]), 4])
            for i in range(n)]

    head = ["% 由 extract_tables.py 生成，可直接用 \\input 引入",
            "% 宽表已按版心均分列宽；如仍偏挤，可改为 \\begin{table*} 通栏"]
    prefix, suffix = "", ""
    if n >= 8:
        # 宽表：均分列宽。可用宽度要扣掉列间距（每列 2×\tabcolsep），
        # 否则 15 列的 tabcolsep 就有 180pt，怎么排都溢出
        tabsep_pt = 4.0 if n >= 11 else 6.0
        usable = 0.96 - n * (2 * tabsep_pt) / 420.0
        per = max(usable, 0.5) / n * 0.97   # 再留 1% 余量，消掉 \\tabcolsep 的舍入误差
        spec = "@{}" + "".join(f"p{{{per:.4f}\\linewidth}}" for _ in range(n)) + "@{}"
        size = "\\tiny" if n >= 11 else "\\scriptsize"
        prefix = "{%s\\setlength{\\tabcolsep}{%.1fpt}" % (size, tabsep_pt)
        suffix = "}"
    else:
        # 窄表：长文本列给 p，短列保持 r/l，按内容长度分配
        weights = [min(max(L, 6), 40) for L in lens]
        total = sum(weights)
        cells = []
        for i, (L, w) in enumerate(zip(lens, weights)):
            if L > 14:
                cells.append(f"p{{{0.84 * w / total:.3f}\\linewidth}}")
            else:
                cells.append("l" if i == 0 else "r")
        spec = "@{}" + "".join(cells) + "@{}"
        size = "\\small" if n >= 6 else ""
        if size:
            prefix, suffix = "{%s" % size, "}"

    lines = head + ([prefix] if prefix else []) + [
        "\\begin{tabular}{%s}" % spec, "\\toprule",
        " & ".join(tex_escape(h) for h in data["header"]) + " \\\\",
        "\\midrule"]
    for r in rows:
        lines.append(" & ".join(tex_escape(v) for v in r) + " \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"] + ([suffix] if suffix else [])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把 PDF 表格提取成可复制的 CSV / Markdown / LaTeX（按文本坐标重建行列，不依赖框线）")
    ap.add_argument("pdf")
    ap.add_argument("--layout", default=None, help="probe_layout.py 产出的 layout.json")
    ap.add_argument("--out", default="tables", help="输出目录（默认 tables/）")
    ap.add_argument("--tables", default=None, help="只处理指定表号，如 5,8")
    ap.add_argument("--format", default="csv,md",
                    help="输出格式，逗号分隔：csv,md,tex（默认 csv,md）")
    ap.add_argument("--keep-blanks", action="store_true",
                    help="保留合并单元格的留空（默认向下填满，便于筛选）")
    ap.add_argument("--raw", action="store_true",
                    help="不做字符规范化（默认把数学斜体、连字符转成普通字符）")
    ap.add_argument("--json", default=None, help="把提取结果写成 JSON")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"找不到文件：{pdf}", file=sys.stderr)
        return 2
    lay_path = Path(args.layout) if args.layout else pdf.with_suffix(".layout.json")
    if not lay_path.exists():
        print(f"找不到 {lay_path}，请先跑 probe_layout.py --json", file=sys.stderr)
        return 2
    lay = json.loads(lay_path.read_text(encoding="utf-8"))

    want = None
    if args.tables:
        want = {int(x) for x in re.split(r"[,\s]+", args.tables.strip()) if x}

    mp = lay.get("margins_pt") or {}
    pw = (lay.get("page_size_pt") or [595.0, 842.0])[0]
    body = {"left": mp.get("left") or 40.0,
            "right": pw - (mp.get("right") or 40.0)}

    doc = pymupdf.open(pdf)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    formats = {f.strip() for f in args.format.split(",") if f.strip()}

    results = []
    for tab in lay.get("tables", []):
        if want and tab["num"] not in want:
            continue
        pg = doc[tab["page"] - 1]
        res = extract_one(pg, tab, body, not args.raw, args.keep_blanks)
        results.append(res)
        if res.get("error"):
            print(f"表 {res['num']:<3} p{tab['page']:<3} 跳过：{res['error']}")
            continue
        print(f"表 {res['num']:<3} p{res['page']:<3} {res['nrows']:3d} 行 × {res['ncols']:2d} 列"
              + (f"  警告：{res['warnings'][0]}" if res["warnings"] else ""))
        stem = f"table{res['num']:02d}"
        if "csv" in formats:
            write_csv(outdir / f"{stem}.csv", res)
        if "tex" in formats:
            write_tex(outdir / f"{stem}.tex", res)
    doc.close()

    if "md" in formats and results:
        ok = [r for r in results if not r.get("error")]
        write_md(outdir / "tables.md", results, pdf.stem + " 表格")
        print(f"\nMarkdown 汇总：{outdir / 'tables.md'}（{len(ok)} 张表）")
    if "csv" in formats:
        print(f"CSV 目录：{outdir}（UTF-8 BOM，Excel 可直接打开）")

    if args.json:
        Path(args.json).write_text(
            json.dumps([{k: v for k, v in r.items() if k != "anchor_x"} for r in results],
                       ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if any(not r.get("error") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
