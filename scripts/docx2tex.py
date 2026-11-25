#!/usr/bin/env python3
"""把 Word（.docx）文档转成 LaTeX 正文 body.tex，供本 skill 的模板编译。

用法:
    python docx2tex.py thesis.docx --out build_tex
    python docx2tex.py thesis.docx --out build_tex --template cn-a4
    python docx2tex.py thesis.docx --out build_tex --numbering auto --no-auto-headings

转换分两步：
  1. pandoc 负责 docx → LaTeX（公式 OMML 会变成 $...$，表格变 longtable）；
  2. 本脚本清理 pandoc 输出里 xelatex 编不过、或中文文档特有的东西：
     媒体路径扁平化、WMF 公式占位、\\pandocbounded 解包、\\ul 换 \\underline、
     引号与复选框、手写标题编号识别。

依赖: pandoc >= 3.0（不装 Python 库）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

MEDIA_EXT = {".png", ".jpg", ".jpeg", ".pdf", ".eps", ".bmp", ".gif", ".tif", ".tiff", ".svg"}
VECTOR_EXT = {".wmf", ".emf"}          # xelatex 不支持，必须转 PNG 或手工替换
END_PUNCT = "。；，、！？;,"

# Word 里的复选框/符号：xelatex 没有对应字形，换成模板里定义的画法
# （unicode-math 不提供 \square，用 \fbox + pifont 的对勾更稳）
SYMBOL_MAP = {
    "⬜": r"\cbox", "□": r"\cbox", "☐": r"\cbox",
    "🗹": r"\cboxc", "☑": r"\cboxc", "✅": r"\cboxc", "✔": r"\cboxc",
    "⬛": r"\filledbox", "■": r"\filledbox",
}

# 手写编号：2 / 2.1 / 2.1.3 / 一、/ 第3章 / 第2节
NUM_PREFIX = re.compile(
    r"^(?:(\d+(?:\.\d+){0,3})[.、]?\s+"
    r"|([一二三四五六七八九十百]+)[、.]\s*"
    r"|第\s*([一二三四五六七八九十百\d]+)\s*([章节])\s*)"
)


def find_pandoc(explicit: str | None) -> str:
    if explicit:
        if not Path(explicit).exists() and not shutil.which(explicit):
            sys.exit(f"找不到 pandoc：{explicit}")
        return explicit
    p = shutil.which("pandoc")
    if not p:
        sys.exit(
            "需要 pandoc（>= 3.0）做 docx → LaTeX 的底层转换：\n"
            "  Windows: winget install --id JohnMacFarlane.Pandoc\n"
            "  macOS:   brew install pandoc\n"
            "  Linux:   sudo apt install pandoc\n"
            "装好后重试，或用 --pandoc 指定可执行文件路径。"
        )
    return p


def run_pandoc(docx: Path, workdir: Path, pandoc: str) -> tuple[str, str]:
    """调用 pandoc 转换，返回 (latex 文本, 版本号)。"""
    raw = workdir / "_pandoc_raw.tex"
    cmd = [pandoc, str(docx), "-f", "docx", "-t", "latex",
           "--wrap=preserve", "--extract-media=media", "-o", str(raw)]
    p = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, errors="replace")
    if p.returncode != 0:
        sys.exit(f"pandoc 失败：\n{p.stderr.strip()[:800]}")
    ver = subprocess.run([pandoc, "--version"], capture_output=True, text=True)
    version = (ver.stdout.splitlines() or ["pandoc ?"])[0].replace("pandoc", "").strip()
    return raw.read_text(encoding="utf-8", errors="replace"), version


def unwrap_braced(text: str, name: str) -> tuple[str, int]:
    """去掉 \\name{...} 外壳、保留内容（按花括号配对，支持嵌套）。"""
    pat = "\\" + name + "{"
    out, i, n = [], 0, 0
    while True:
        j = text.find(pat, i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        k = j + len(pat)
        depth, start = 1, k
        while k < len(text) and depth:
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
            k += 1
        out.append(text[start:k - 1])
        i, n = k, n + 1
    return "".join(out), n


def fix_graphics(text: str, workdir: Path, outdir: Path, warnings: list[str],
                 max_width_in: float = 5.7) -> tuple[str, int, int]:
    """把 media/** 的图片扁平化到 figs/，WMF/EMF 换成占位符，过宽图片改为按版心缩放。

    返回 (新文本, 成功张数, 需人工处理数)。
    """
    figs = outdir / "figs"
    figs.mkdir(parents=True, exist_ok=True)
    ok = bad = 0

    def repl(m: re.Match) -> str:
        nonlocal ok, bad
        opts = m.group(1) or ""
        raw_path = m.group(2).strip()
        p = Path(raw_path)
        src = (workdir / p)
        if not src.is_file():                     # pandoc 路径可能带 media/media/
            cand = list(workdir.rglob(p.name))
            src = cand[0] if cand else src
        ext = p.suffix.lower()
        if ext in VECTOR_EXT or src.suffix.lower() in VECTOR_EXT:
            bad += 1
            warnings.append(
                f"{src.name}: {ext or '矢量图'} 无法嵌入 xelatex，已替换为占位文字；"
                "请用 Word 另存为 PNG/PDF 后替换，或改为 $...$ 公式"
            )
            return r"\texttt{[未转换的矢量图 %s]}" % src.name
        if not src.is_file():
            bad += 1
            warnings.append(f"{raw_path}: 图片文件未找到，已保留原命令")
            return m.group(0)
        dst = figs / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
        ok += 1
        # Word 里的图片常按原始尺寸给绝对宽度（如 6.49in），超过版心就会 Overfull。
        # 超过阈值时改成按版心缩放，并丢掉 height 以保持纵横比。
        mw = re.search(r"width\s*=\s*([\d.]+)\s*(in|cm|mm|pt)", opts)
        if mw:
            val, unit = float(mw.group(1)), mw.group(2)
            inches = {"in": val, "cm": val / 2.54, "mm": val / 25.4, "pt": val / 72.27}[unit]
            if inches > max_width_in:
                keep = ""
                if "keepaspectratio" in opts:
                    keep = ",keepaspectratio"
                return f"\\includegraphics[width=\\linewidth{keep}]{{figs/{src.name}}}"
        return f"\\includegraphics{opts}{{figs/{src.name}}}"

    return re.sub(r"\\includegraphics(\[[^\]]*\])?\{([^}]*)\}", repl, text), ok, bad


def strip_number(text: str) -> tuple[str, int]:
    """去掉标题开头的编号，返回 (标题, 层级)。层级 1/2/3。"""
    m = NUM_PREFIX.match(text)
    if not m:
        return text, 1
    if m.group(1):
        depth = min(3, m.group(1).count(".") + 1)
    elif m.group(4) == "章":
        depth = 1
    elif m.group(4) == "节":
        depth = 2
    else:
        depth = 1
    return text[m.end():].strip(), depth


def has_number(text: str) -> bool:
    return bool(NUM_PREFIX.match(text))


def heading_cmd(depth: int) -> str:
    return {1: "section", 2: "subsection", 3: "subsubsection"}.get(depth, "subsubsection")


def normalize_headings(text: str, numbering: str, auto_headings: bool,
                       warnings: list[str]) -> tuple[str, list[str], list[str]]:
    """统一标题命令与编号。

    - numbering="keep"：保留 Word 里的手写编号，用带星号的命令，避免与 LaTeX 自动编号叠加；
    - numbering="auto"：去掉手写编号，交给 LaTeX 编号。
    另外把"整行加粗且以编号开头"的段落提升为标题（Word 里手写标题很常见）。
    """
    lines = text.splitlines()
    kept, promoted, all_titles = [], [], []
    star = "*" if numbering == "keep" else ""

    for ln in lines:
        stripped = ln.strip()

        # 1) pandoc 已识别的标题（Word 标题样式）
        m = re.match(r"^\\(section|subsection|subsubsection|paragraph)\{(.*)\}$", stripped)
        if m:
            title = m.group(2)
            all_titles.append(title)
            if numbering == "auto":
                title, depth = strip_number(title)
                cmd = heading_cmd(depth)
            else:
                cmd = m.group(1)
                _, depth = strip_number(title)
                cmd = heading_cmd(depth) if m.group(1) in ("paragraph",) else cmd
            kept.append(f"\\{cmd}{star}{{{title}}}")
            continue

        # 2) 手写标题：整行 \textbf{2.1 xxx} 或 短编号行
        if auto_headings and not ln.startswith((" ", "\t")):
            inner = None
            mb = re.match(r"^\\textbf\{(.+)\}$", stripped)
            if mb and has_number(mb.group(1)) and len(mb.group(1)) <= 60 \
                    and not mb.group(1).rstrip().endswith(tuple(END_PUNCT)):
                inner = mb.group(1)
            elif (has_number(stripped) and len(stripped) <= 42
                    and not stripped.rstrip().endswith(tuple(END_PUNCT))
                    and not stripped.startswith(("\\", "$", "\\item"))
                    # 未加粗时只认"2.1 / 第X章 / 一、"这类明确层级，
                    # 单级 "1." 与有序列表长得一样，不提升
                    and (stripped.count(".") >= 1
                         or re.match(r"^第\s*[一二三四五六七八九十百\d]+\s*[章节]", stripped)
                         or re.match(r"^[一二三四五六七八九十百]+[、.]", stripped))):
                inner = stripped
            if inner:
                title, depth = strip_number(inner)
                if title and len(title) >= 2:
                    all_titles.append(inner)
                    kept.append(f"\\{heading_cmd(depth)}{star}{{{title}}}")
                    promoted.append(inner[:70])
                    continue

        kept.append(ln)

    return "\n".join(kept), promoted, all_titles


def strip_wrapping_cmd(text: str, name: str, nargs: int) -> str:
    """把 \\name{a}{b}{内容} 还原成「内容」（去掉表格命令的外壳）。

    flatten 模式去掉表格框架后，\\multicolumn / \\multirow 会留在正文里：前者报
    Misplaced \\omit，后者排版错乱，都需要解包。
    """
    key = "\\" + name          # 命令名本身，参数的花括号由下面逐个读取
    out: list[str] = []
    i = 0
    while True:
        j = text.find(key, i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        k = j + len(key)
        groups: list[str] = []
        while len(groups) < nargs:
            while k < len(text) and text[k] in " \t\r\n":
                k += 1
            if k >= len(text) or text[k] != "{":
                break
            depth, start = 1, k + 1
            k += 1
            while k < len(text) and depth:
                if text[k] == "{":
                    depth += 1
                elif text[k] == "}":
                    depth -= 1
                k += 1
            groups.append(text[start:k - 1])
        out.append(groups[-1] if len(groups) == nargs else text[j:k])
        i = k
    return "".join(out)


def repair_tables(text: str, warnings: list[str], flatten: bool = False) -> tuple[str, int]:
    """修 pandoc 处理 Word 表格时的两类结构问题。

    1. **嵌套表格**：Word 允许单元格里再放表格，pandoc 会把内层 longtable 原样塞进外层
       单元格。longtable 不能嵌套，编译直接报错。这里把内层降级为 tabular（可放在单元格里），
       并去掉 tabular 不认的 \\endhead / \\endlastfoot 等。
    2. **列数错位**：外层声明 1 列、某行却多出 &，报 "Extra alignment tab"。
       多出的用「；」并回同一格，缺少的补空单元格。

    flatten=True 时直接丢掉表格框架，把每行单元格用「；」连成一段，保证可编译。
    返回 (新文本, 修过的行数)。
    """
    lines = text.splitlines()
    out: list[str] = []
    stack: list[dict] = []
    fixed = 0
    drop = 0
    flat_buf: list[str] = []

    def emit_flat(block: list[str]) -> list[str]:
        """flatten 模式下整张表一起处理（跨行的 \\multicolumn 必须整体解包）。"""
        body = strip_wrapping_cmd("\n".join(block), "multicolumn", 3)
        body = strip_wrapping_cmd(body, "multirow", 3)
        res: list[str] = []
        for line in body.splitlines():
            if re.match(r"^\s*\\(toprule|midrule|bottomrule|hline|endhead|endfirsthead"
                        r"|endfoot|endlastfoot|noalign)", line):
                continue
            if "\\def\\LTcaptype" in line:
                continue
            line = line.replace("\\\\", "")
            line = re.sub(r"(?<!\\)&", "；", line).strip()
            # 残余的表格结构：preamble 片段、孤立花括号、minipage 包裹、\toprule 等
            if not line or line in ("}", "{", "%"):
                continue
            if any(k in line for k in ("\\linewidth", "\\tabcolsep", "\\real{", "@{}}")):
                continue
            if re.fullmatch(r"\\(begin|end)\{minipage\}(\[[^\]]*\])?(\{[^}]*\})?", line):
                continue
            res.append(line)
        return res

    def flush(level: dict) -> list[str]:
        nonlocal fixed
        row, ncol = level["buf"], level["ncol"]
        if not row or not ncol:
            return row
        seg = "\n".join(row)
        amps = [m.start() for m in re.finditer(r"(?<!\\)&", seg)]
        spans = sum(int(n) - 1 for n in re.findall(r"\\multicolumn\{(\d+)\}", seg))
        # \multicolumn{2} 只算一个单元格却占 2 列，有效列数要把差额加回来
        eff = len(amps) + 1 + spans
        if eff > ncol:
            chars = list(seg)
            for i in amps[-(eff - ncol):]:
                chars[i] = "；"
            fixed += 1
            warnings.append(f"表格列数不匹配：声明 {ncol} 列、该行 {eff} 格，"
                            "已把多出的单元格用「；」并回同一格")
            return "".join(chars).split("\n")
        if eff < ncol:
            pad = " & " * (ncol - eff)
            last = row[-1]
            if last.rstrip().endswith("\\\\"):
                k = last.rindex("\\\\")
                row = row[:-1] + [last[:k] + pad + last[k:]]
            else:
                row = row[:-1] + [last + pad]
            fixed += 1
        return row

    for ln in lines:
        if flatten and "\\begin{longtable}" in ln:
            drop += 1
            if drop == 1:
                flat_buf = []
                fixed += 1
                warnings.append("按 --tables flatten 处理：已去掉表格框架，单元格文字用「；」连接，"
                                "内容保留但版式不再与 Word 一致")
            continue
        if flatten and drop and "\\end{longtable}" in ln:
            drop -= 1
            if drop == 0:
                out.extend(emit_flat(flat_buf))
            continue
        if flatten and drop:
            flat_buf.append(ln)
            continue

        if "\\begin{longtable}" in ln:
            nested = bool(stack)
            level = {"ncol": 0, "pre": True, "preamble": [ln], "buf": []}
            if nested:
                ln = ln.replace("\\begin{longtable}[]", "\\begin{tabular}", 1)
                if "\\begin{tabular}" not in ln:
                    ln = ln.replace("\\begin{longtable}", "\\begin{tabular}", 1)
                level["preamble"] = [ln]
                fixed += 1
                warnings.append("检测到嵌套表格（单元格里套表格），已把内层 longtable 改为 tabular；"
                                "表格线可能需手工调整")
            stack.append(level)
            out.append(ln)
            continue

        if stack and stack[-1]["pre"]:
            level = stack[-1]
            level["preamble"].append(ln)
            level["ncol"] += ln.count("p{(")
            out.append(ln)
            if ln.rstrip().endswith("@{}}"):
                level["pre"] = False
                if level["ncol"] == 0:      # 全是 l/c/r 的简表
                    spec = "\n".join(level["preamble"])
                    level["ncol"] = len(re.findall(r"(?<![a-z\\])[lcr](?![a-z])", spec))
                level["ncol"] = max(level["ncol"], 1)
            continue

        if stack and "\\end{longtable}" in ln:
            level = stack.pop()
            out.extend(flush(level))
            out.append("\\end{tabular}" if stack else ln)
            continue

        if stack:
            level = stack[-1]
            stripped = ln.strip()
            if len(stack) > 1 and stripped in (
                    "\\endhead", "\\endfirsthead", "\\endfoot", "\\endlastfoot"):
                continue
            level["buf"].append(ln)
            if ln.rstrip().endswith("\\\\"):
                out.extend(flush(level))
                level["buf"] = []
            continue

        out.append(ln)
    return "\n".join(out), fixed


def postprocess(text: str, workdir: Path, outdir: Path, numbering: str,
                auto_headings: bool, warnings: list[str],
                max_width_in: float = 5.7, flatten_tables: bool = False) -> dict:
    stats = {}

    # pandoc 2.10+ 的图片包装；模板里也有 providecommand，这里直接解包更干净
    text, n_bound = unwrap_braced(text, "pandocbounded")
    stats["pandocbounded"] = n_bound

    text, stats["images"], stats["vector_left"] = fix_graphics(
        text, workdir, outdir, warnings, max_width_in)

    # pandoc 把上标引用写成 \textsuperscript{{[}29{]}}，多出的花括号会排成 "[}29{]" 之类
    text = text.replace("{{[}", "{[}").replace("{]}}", "{]}")

    # pandoc 的合并单元格写 \multirow{2}{=}；"= 宽度"只有部分 multirow 版本支持，
    # 不支持时报 Missing number，换成 *（自然宽度）
    text = re.sub(r"\\multirow\{(\d+)\}\{=\}", r"\\multirow{\1}{*}", text)

    # pandoc 对无标题表格写 \def\LTcaptype{none}，longtable 会去引用不存在的
    # 计数器 "none" 而报错（No counter 'none' defined），改成真实计数器
    text = text.replace(r"\def\LTcaptype{none}", r"\def\LTcaptype{table}")

    # Word 下划线 -> \underline。但 \underline 内部不换行：长句（超链接文字、整句强调）
    # 会撑出一个不可断的盒子，直接 Overfull 几百 pt。超过 12 字的就去掉下划线，保留文字。
    text = re.sub(r"\\ul\{(.*?)\}", r"\\underline{\1}", text)
    text = re.sub(r"\\underline\{([^{}]{13,}?)\}", r"\1", text)

    # pandoc 的双引号是 ``…''，中文用弯引号
    text = re.sub(r"``([^`']{1,120})''", r"“\1”", text)

    # 紧凑列表标记；模板已 providecommand，段落里单独出现时删掉更干净
    text = re.sub(r"^\s*\\tightlist\s*$", "", text, flags=re.M)

    # 复选框等符号
    n_sym = 0
    for k, v in SYMBOL_MAP.items():
        if k in text:
            n_sym += text.count(k)
            text = text.replace(k, v)
    stats["symbols"] = n_sym

    text, stats["tables_fixed"] = repair_tables(text, warnings, flatten_tables)

    text, promoted, titles = normalize_headings(text, numbering, auto_headings, warnings)
    stats["headings"] = len(titles)
    stats["promoted"] = promoted
    stats["tables"] = text.count("\\begin{longtable}") + text.count("\\begin{tabular}")
    stats["equations"] = len(re.findall(r"\\\[|\$[^$]+\$", text))

    # 折叠 3 个以上连续空行
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    return {"text": text, **stats}


def write_main(template: Path, outdir: Path, warnings: list[str]) -> Path | None:
    """按模板生成 main.tex，把 \\input{body} 插到 BODY 标记处。"""
    if not template.is_file():
        warnings.append(f"模板不存在：{template}")
        return None
    s = template.read_text(encoding="utf-8")
    m = re.search(r"^.*% >>> BODY.*$", s, flags=re.M)
    if m:
        s = s[:m.start()] + r"\input{body}" + s[m.end():]
    elif "\\end{document}" in s:
        s = s.replace("\\end{document}", "\\input{body}\n\n\\end{document}")
        warnings.append("模板无 BODY 标记，已把 \\input{body} 插到 \\end{document} 前（模板自带的示例内容需要手工删除）")
    else:
        warnings.append("模板既无 BODY 标记也无 \\end{document}，未生成 main.tex")
        return None
    out = outdir / "main.tex"
    out.write_text(s, encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description="Word(.docx) → LaTeX 正文")
    ap.add_argument("docx")
    ap.add_argument("--out", default="build_tex", help="输出目录")
    ap.add_argument("--template", default="cn-a4",
                    choices=["cn-a4", "single", "twocol", "none"],
                    help="生成 main.tex 用的模板；none 只输出 body.tex")
    ap.add_argument("--numbering", default=None, choices=["keep", "auto"],
                    help="keep=保留 Word 手写编号（默认，检测得到编号时）auto=交给 LaTeX 编号")
    ap.add_argument("--no-auto-headings", action="store_true",
                    help="不把'加粗+编号'的段落提升为标题")
    ap.add_argument("--pandoc", default=None, help="pandoc 可执行文件路径")
    ap.add_argument("--tables", default="keep", choices=["keep", "flatten"],
                    help="keep=保留表格结构（默认，复杂表单可能编不过）；"
                         "flatten=去掉表格框架只留文字，保证可编译")
    ap.add_argument("--textwidth-in", type=float, default=None,
                    help="版心宽度（英寸），超过此宽度的图片按 \\linewidth 缩放；默认按模板推断")
    args = ap.parse_args()

    docx = Path(args.docx).expanduser().resolve()
    if not docx.is_file():
        sys.exit(f"找不到 {docx}")
    outdir = Path(args.out).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    workdir = outdir / "_work"
    workdir.mkdir(exist_ok=True)

    pandoc = find_pandoc(args.pandoc)
    print(f"pandoc {pandoc}")
    raw, version = run_pandoc(docx, workdir, pandoc)
    print(f"  docx → LaTeX：{len(raw.splitlines())} 行")

    warnings: list[str] = []
    # 先扫一遍标题编号，决定默认 numbering 模式
    if args.numbering:
        numbering = args.numbering
    else:
        probe_titles = re.findall(r"^\\(?:sub)*section\{(.*)\}$", raw, flags=re.M)
        ratio = (sum(1 for t in probe_titles if has_number(t)) / len(probe_titles)) if probe_titles else 0
        numbering = "keep" if ratio >= 0.5 else "auto"
        if probe_titles and ratio < 0.5:
            warnings.append(
                f"Word 标题样式里只有 {ratio:.0%} 带编号，按 auto 处理（交给 LaTeX 编号）；"
                "若原稿用自动编号列表，这是正确的"
            )

    # 版心宽：A4 单栏约 5.7in，Elsevier 窄页 6.3in，双栏单栏宽 3.1in
    tw = args.textwidth_in or {"cn-a4": 5.7, "single": 6.3, "twocol": 3.1}.get(args.template, 5.7)
    res = postprocess(raw, workdir, outdir, numbering, not args.no_auto_headings,
                      warnings, tw, args.tables == "flatten")
    body = outdir / "body.tex"
    body.write_text(res["text"], encoding="utf-8")

    main_tex = None
    if args.template != "none":
        tpl = Path(__file__).resolve().parent.parent / "assets" / f"main-{args.template}.tex"
        main_tex = write_main(tpl, outdir, warnings)

    # 同一张 WMF 可能被引用几十次，合并成一条并计数
    seen: dict[str, int] = {}
    for w in warnings:
        seen[w] = seen.get(w, 0) + 1
    warnings = [f"{w}（×{n}）" if n > 1 else w for w, n in seen.items()]

    report = {
        "docx": str(docx),
        "pandoc": version,
        "numbering": numbering,
        "body": str(body),
        "main": str(main_tex) if main_tex else None,
        "headings": res["headings"],
        "promoted_headings": res["promoted"],
        "tables": res["tables"],
        "images": res["images"],
        "vector_left": res["vector_left"],
        "equations": res["equations"],
        "symbols": res["symbols"],
        "warnings": warnings,
    }
    (outdir / "docx2tex_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 清理 pandoc 中间产物（媒体文件已复制到 figs/）
    shutil.rmtree(workdir, ignore_errors=True)

    print(f"  body.tex      : {res['headings']} 个标题 / {res['tables']} 个表 / "
          f"{res['images']} 张图 / {res['equations']} 处公式")
    if res["promoted"]:
        print(f"  识别出的手写标题（前 5 条，可用 --no-auto-headings 关掉）：")
        for t in res["promoted"][:5]:
            print(f"    · {t}")
    print(f"  编号方式      : {numbering}")
    if res["promoted"] and numbering == "auto":
        print("    提示：有手写编号的标题被重新编号了；若需保留原编号，重跑时加 --numbering keep")
    if main_tex:
        print(f"  main.tex      : {main_tex}")
    if warnings:
        print(f"\n  需要人工处理 {len(warnings)} 项：")
        for w in warnings[:12]:
            print(f"    ! {w}")
    print(f"\n下一步：python {Path(__file__).parent.name}/build_check.py {main_tex or body}")


if __name__ == "__main__":
    main()
