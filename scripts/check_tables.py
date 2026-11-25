#!/usr/bin/env python3
"""核对译文表格的列数与源表是否一致，附源表表头供人工核对译文表头。

用法:
    python check_tables.py <layout.json> <tex_dir> [--tol 0] [--json out.json]

做法：
  1. 扫 tex_dir 下所有 .tex，按文档顺序抓顶层 tabular / longtable，解析列声明得列数；
  2. 与 layout.json 的 tables[].ncols 按出现顺序配对（跳号就报"无法对齐"，不猜）；
  3. tex 比源表少列 → 判 FAIL（有列被丢了，这是危险方向）；
     tex 比源表多列 → 判 WARN（多半是 pymupdf 把无框线表识别少了，不冤枉作者）。

退出码: 全部一致/仅 WARN 为 0，有 FAIL 为 1，参数或文件问题为 2。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BEGIN_RE = re.compile(r"\\begin\{(tabular|longtable|tabularx|tabulary)\}")


def read_balanced(text: str, i: int) -> tuple[str, int]:
    """从 text[i] 开始（应为 '{'）读一个配平的组，返回 (内容, 结束位置)。"""
    depth, start = 0, i
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
        i += 1
    return text[start + 1:], len(text)


def strip_inline_args(spec: str) -> str:
    """去掉 @{...} !{...} >{...} <{...}，它们不占列。"""
    out, i = [], 0
    while i < len(spec):
        if spec[i] in "@!><" and i + 1 < len(spec) and spec[i + 1] == "{":
            _, i = read_balanced(spec, i + 1)
            continue
        out.append(spec[i])
        i += 1
    return "".join(out)


def expand_star(spec: str) -> str:
    """展开 *{n}{spec}。"""
    out, i = [], 0
    while i < len(spec):
        if spec[i] == "*" and i + 1 < len(spec) and spec[i + 1] == "{":
            num, i2 = read_balanced(spec, i + 1)
            if i2 < len(spec) and spec[i2] == "{":
                body, i3 = read_balanced(spec, i2)
                try:
                    out.append(body * int(num.strip()))
                    i = i3
                    continue
                except ValueError:
                    pass
        out.append(spec[i])
        i += 1
    return "".join(out)


def count_columns(spec: str) -> int:
    spec = expand_star(strip_inline_args(spec))
    n = 0
    i = 0
    while i < len(spec):
        c = spec[i]
        if c in "lcrXpmbLCRJS":          # 常见列类型
            n += 1
        elif c in "{":
            _, i = read_balanced(spec, i)
            continue
        i += 1
    return n


def scan_tex(tex_dir: Path):
    """按文件名排序、文件内顺序，收集顶层表格的列数与位置。"""
    found = []
    for path in sorted(tex_dir.rglob("*.tex")):
        text = path.read_text(encoding="utf-8", errors="replace")
        # 去掉注释行，避免注释里的示例表格被算进去
        lines = [l if not l.lstrip().startswith("%") else "" for l in text.splitlines()]
        text = "\n".join(lines)
        pos = 0
        while True:
            m = BEGIN_RE.search(text, pos)
            if not m:
                break
            i = m.end()
            # 跳过可选参数 [..]
            while i < len(text) and text[i] in " \t\r\n":
                i += 1
            if i < len(text) and text[i] == "[":
                j = text.find("]", i)
                i = j + 1 if j > 0 else i
                while i < len(text) and text[i] in " \t\r\n":
                    i += 1
            if i >= len(text) or text[i] != "{":
                pos = m.end()
                continue
            spec, end = read_balanced(text, i)
            line_no = text.count("\n", 0, m.start()) + 1
            found.append({"file": path.name, "line": line_no,
                          "env": m.group(1), "ncols": count_columns(spec)})
            pos = end
    return found


def main():
    ap = argparse.ArgumentParser(description="核对译文表格列数与源表")
    ap.add_argument("layout")
    ap.add_argument("tex_dir")
    ap.add_argument("--tol", type=int, default=0, help="允许的列数偏差")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    lay_p, tex_d = Path(args.layout), Path(args.tex_dir)
    if not lay_p.is_file():
        sys.exit(f"找不到 {lay_p}")
    if not tex_d.is_dir():
        sys.exit(f"找不到目录 {tex_d}")

    layout = json.loads(lay_p.read_text(encoding="utf-8"))
    sources = layout.get("tables", [])
    tex = scan_tex(tex_d)

    # 按表号排序配对；跳号说明源码里少了表，直接报无法对齐
    src = [{**t} for t in sources]
    try:
        src.sort(key=lambda t: int(t["num"]))
    except (KeyError, ValueError):
        sys.exit("layout.json 的 tables[].num 不是整数，无法对齐")

    if len(tex) != len(src):
        print(f"注意：源码里有 {len(tex)} 个表格环境，源文有 {len(src)} 个表题，按顺序前缀对齐")
    pairs = min(len(tex), len(src))
    nums = [int(t["num"]) for t in src[:pairs]]
    if nums != list(range(1, pairs + 1)):
        print(f"无法对齐：表号不是从 1 连续递增（{[t['num'] for t in src[:pairs]]}）")
        sys.exit(1)

    rows, fails, warns = [], 0, 0
    print(f"{'表':<6}{'源':<5}{'tex':<5}{'位置':<22}判定")
    for t, x in zip(src[:pairs], tex[:pairs]):
        sn, tn = t.get("ncols"), x["ncols"]
        loc = f"{x['file']}:{x['line']}"
        if sn is None:
            status = "跳过（源表未识别）"
            warns += 1
        elif tn < sn - args.tol:
            status = f"列数不符（少 {sn - tn} 列）"
            fails += 1
        elif tn > sn + args.tol:
            status = "WARN（源表可能被识别少了，请核对原表）"
            warns += 1
        else:
            status = "OK"
        print(f"表{t['num']:<5}{str(sn):<5}{str(tn):<5}{loc:<22}{status}")
        rows.append({"num": t["num"], "source_cols": sn, "tex_cols": tn,
                     "file": x["file"], "line": x["line"], "status": status,
                     "header": t.get("header")})

    print("\n源表表头（前 6 格，供核对译文表头是否逐项对应）：")
    for t in src[:pairs]:
        h = t.get("header")
        if h:
            print(f"  表{t['num']}: " + " | ".join(str(c).replace("\n", " ") for c in h))

    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 已写入 {args.json}")
    print(f"\n{len(rows) - fails - warns} 一致 / {fails} 少列 / {warns} 待人工确认")
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
