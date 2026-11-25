#!/usr/bin/env python3
"""识别论文 PDF 的来源期刊/出版方，给出可用的 LaTeX 模板候选。

用法:
    python detect_venue.py paper.pdf [--json venue.json] [--layout layout.json]

判据优先级：DOI 前缀 > 元数据 creator/producer > 首页与页眉文字。
每命中一条都会记录证据，便于人工复核；识别不出时回退到「按实测参数生成模板」。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf  # type: ignore
    except ImportError:
        sys.exit("需要 pymupdf：pip install pymupdf")

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>{}]+")

# DOI 前缀 → (出版方, 首选文档类, 备选文档类)
PREFIX_MAP = {
    "10.1016": ("Elsevier", "elsarticle", ""),
    "10.1109": ("IEEE", "IEEEtran", ""),
    "10.1145": ("ACM", "acmart", ""),
    "10.1007": ("Springer", "sn-jnl", "svjour3"),
    "10.1287": ("INFORMS", "informs4", ""),
    "10.1111": ("Wiley", "article", ""),
    "10.1002": ("Wiley", "article", ""),
    "10.1080": ("Taylor & Francis", "interact", ""),
    "10.1177": ("SAGE", "sagej", ""),
    "10.3390": ("MDPI", "mdpi", ""),
    "10.1088": ("IOP Publishing", "iopart", ""),
    "10.1061": ("ASCE", "ascelike", ""),
    "10.1093": ("Oxford University Press", "oup-authoring-template", ""),
    "10.1137": ("SIAM", "siamart190516", ""),
    "10.1051": ("EDP Sciences", "edpsciences", ""),
    "10.1038": ("Nature Portfolio", "sn-jnl", ""),
}

# 元数据 creator / producer 关键字 → 出版方
CREATOR_MAP = {
    "elsevier": "Elsevier",
    "sciencedirect": "Elsevier",
    "springer": "Springer",
    "wiley": "Wiley",
    "ieee": "IEEE",
    "acm": "ACM",
    "taylor": "Taylor & Francis",
    "sage": "SAGE",
    "mdpi": "MDPI",
}

# 文档类 → 单栏/双栏时的推荐选项
CLASS_OPTIONS = {
    "elsarticle": {1: "preprint,12pt", 2: "3p,times,twocolumn"},
    "IEEEtran": {1: "conference", 2: "journal"},
    "acmart": {1: "manuscript,review,anonymous=false", 2: "sigconf"},
    "sn-jnl": {1: "sn-mathphys-num", 2: "sn-mathphys-num"},
    "svjour3": {1: "smallextended", 2: "smallextended"},
    "revtex4-2": {1: "aps,pra,reprint", 2: "aps,pra,twocolumn"},
}

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def class_available(cls: str) -> bool:
    if not cls:
        return False
    p = subprocess.run(["kpsewhich", f"{cls}.cls"], capture_output=True, text=True)
    return bool(p.stdout.strip())


def cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    n = len(CJK_RE.findall(text))
    letters = len(re.findall(r"[^\s]", text)) or 1
    return n / letters


def guess_journal(subject: str, first_page: str, header_right: str) -> str | None:
    """从 subject（Elsevier 常写成 "Journal, Vol (Year) Page. doi:..."）或页眉猜刊名。"""
    if subject:
        head = re.split(r"[,.]\s*\d|\sdoi:", subject)[0].strip()
        if 3 < len(head) < 120:
            return head
    for line in first_page.splitlines():
        s = line.strip()
        if re.search(r"(Journal|Transactions|Letters|Proceedings|Review|Science|学报|期刊)", s, re.I) \
                and 4 < len(s) < 90:
            return s
    if header_right:
        m = re.match(r"^(.+?)\s+\d+\s+\(", header_right)
        if m:
            return m.group(1).strip()
    return None


def main():
    ap = argparse.ArgumentParser(description="识别论文来源与可用 LaTeX 模板")
    ap.add_argument("pdf")
    ap.add_argument("--json", default=None)
    ap.add_argument("--layout", default=None, help="probe_layout.py 的 JSON，用其中的栏数选文档类选项")
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    meta = doc.metadata or {}
    first = doc[0].get_text()
    pages_text = "\n".join(doc[i].get_text() for i in range(min(3, doc.page_count)))
    # 页眉（第 2 页顶部 6%）
    head_words = []
    if doc.page_count > 1:
        pg = doc[1]
        head_words = [w[4] for w in pg.get_text("words") if w[1] < pg.rect.height * 0.06]
    header = " ".join(head_words)
    doc.close()

    text_all = f"{meta.get('title','')} {meta.get('subject','')} {first} {header}"
    dois = DOI_RE.findall(text_all)
    doi = dois[0].rstrip(".,;") if dois else None

    evidence: list[str] = []
    publisher = None
    cls = ""
    alt = ""

    if doi:
        prefix = ".".join(doi.split("/")[0].split(".")[:2])
        if prefix in PREFIX_MAP:
            publisher, cls, alt = PREFIX_MAP[prefix]
            evidence.append(f"DOI {doi} → 前缀 {prefix} 属 {publisher}")
        else:
            evidence.append(f"DOI {doi} 的前缀 {prefix} 不在已知表内")

    if not publisher:
        hay = f"{meta.get('creator','')} {meta.get('producer','')}".lower()
        for key, pub in CREATOR_MAP.items():
            if key in hay:
                publisher = pub
                evidence.append(f"元数据 creator/producer 含 “{key}” → {pub}")
                break

    if not publisher:
        if "sciencedirect" in first.lower():
            publisher, evidence = "Elsevier", evidence + ["首页出现 ScienceDirect 横幅"]
        elif "springer" in first.lower():
            publisher, evidence = "Springer", evidence + ["首页出现 Springer 标识"]
        elif "ieee" in first.lower():
            publisher, evidence = "IEEE", evidence + ["首页出现 IEEE 标识"]

    is_cjk = cjk_ratio(pages_text) > 0.15
    if is_cjk:
        evidence.append(f"正文汉字占比 {cjk_ratio(pages_text):.0%} → 中文文献")
        cls = "ctexart"
        alt = ""
        publisher = publisher or "中文期刊 / 学位论文"

    cols = 1
    if args.layout and Path(args.layout).is_file():
        try:
            cols = int(json.loads(Path(args.layout).read_text(encoding="utf-8")).get("columns") or 1)
        except Exception:
            pass

    candidates = []
    for c, a in ((cls, alt), (alt, "")):
        if not c:
            continue
        avail = class_available(c)
        opts = CLASS_OPTIONS.get(c, {}).get(cols, "")
        candidates.append({
            "class": c,
            "options": opts,
            "available": avail,
            "note": "本机 TeX Live 已安装" if avail
                    else "本机未安装该文档类，需 tlmgr install 或改用生成模板",
        })

    journal = guess_journal(meta.get("subject", ""), first, header)
    confidence = "high" if doi and publisher else ("medium" if publisher else "low")

    data = {
        "pdf": str(Path(args.pdf).name),
        "doi": doi,
        "journal": journal,
        "publisher": publisher,
        "language": "zh" if is_cjk else "en",
        "cjk_ratio": round(cjk_ratio(pages_text), 3),
        "columns": cols,
        "confidence": confidence,
        "evidence": evidence,
        "candidates": candidates,
    }
    if args.json:
        Path(args.json).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"文件      : {data['pdf']}")
    print(f"DOI       : {doi or '—'}")
    print(f"期刊      : {journal or '—'}")
    print(f"出版方    : {publisher or '未识别'}")
    print(f"语言/栏数 : {'中文' if is_cjk else '英文'} / {cols} 栏")
    print(f"置信度    : {confidence}")
    for e in evidence:
        print(f"  证据 · {e}")
    if candidates:
        print("模板候选  :")
        for c in candidates:
            print(f"  {c['class']:<14} 选项 {c['options'] or '—':<28} {'可用' if c['available'] else '未安装'}  {c['note']}")
    else:
        print("模板候选  : 无匹配文档类，请用 make_template.py 按实测参数生成")
    if args.json:
        print(f"\nJSON 已写入 {args.json}")


if __name__ == "__main__":
    main()
