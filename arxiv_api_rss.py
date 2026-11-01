#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""arXiv 分类 → 标准 RSS 2.0 原始条目（近 N 天），供 RSS-GPT 摘要。

为什么不用官方 rss.arxiv.org：
  官方 feed 只保留「最新一批公告」且 <skipDays> 里有 Saturday/Sunday，
  每周跑一次只能拿到周五那一批，周一至周四的论文随 feed 滚动永久丢失。
这里改为 arXiv API 按提交日期窗口拉取（窗口内重复由 main.py 按 link 去重，
不会重复摘要），输出 docs/arxiv-<slug>-raw.xml，main.py 通过 file:// 读取后
生成 docs/arxiv-<slug>.xml。

用法：python arxiv_api_rss.py    （在仓库根目录执行）
"""
import html
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import feedparser

API = "http://export.arxiv.org/api/query"

# (arXiv 分类, 输出 slug)；slug 与 config.ini 的 name 一致
CATEGORIES = [
    ("q-fin.ST", "arxiv-qfin-st"),
    ("q-fin.PM", "arxiv-qfin-pm"),
    ("q-fin.TR", "arxiv-qfin-tr"),
]

WINDOW_DAYS = 10   # 提交日期窗口（比周更长，漏跑的调整窗口也能补齐；重复靠去重消化）
MAX_RESULTS = 100
UA = "RSS-GPT-arxiv/1.0 (+https://github.com/jacksonzhao-homelab/RSS-GPT)"


def fetch(query, max_results=MAX_RESULTS, retries=3):
    url = API + "?" + urllib.parse.urlencode({
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=60).read()
        except Exception as e:            # noqa: BLE001 - 网络层的任何异常都重试
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"fetch failed: {last}")


def norm_link(entry_id):
    """arXiv API 的 id 形如 http://arxiv.org/abs/2610.29578v1 → 统一成
    https://arxiv.org/abs/2610.29578，与旧 rss.arxiv.org 条目的 link 完全一致，
    否则 main.py 的去重会认不出来、把老论文重新摘要一遍。"""
    m = re.search(r"/abs/([^/]+)$", entry_id or "")
    ident = re.sub(r"v\d+$", "", m.group(1)) if m else (entry_id or "")
    return f"https://arxiv.org/abs/{ident}"


def entry_pub(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return format_datetime(datetime(*t[:6], tzinfo=timezone.utc))
    return format_datetime(datetime.now(timezone.utc))


def clean_text(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def build_rss(category, entries):
    now = format_datetime(datetime.now(timezone.utc))
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" '
           'xmlns:content="http://purl.org/rss/1.0/modules/content/">',
           "  <channel>",
           f"    <title>arXiv {category}（近 {WINDOW_DAYS} 天提交）</title>",
           f"    <link>https://arxiv.org/list/{category}/recent</link>",
           f"    <description>arXiv API 按提交日期窗口生成 —— 由 arxiv_api_rss.py 生成</description>",
           "    <language>en-us</language>",
           f"    <lastBuildDate>{now}</lastBuildDate>"]
    for e in entries:
        title = html.escape(clean_text(e.title))
        link = html.escape(norm_link(e.get("id", "")))
        abstract = clean_text(e.get("summary", ""))
        cats = ", ".join(c.get("term", "") for c in e.get("tags", []) or [])
        desc = html.escape(abstract)
        out += ["    <item>",
                f"      <title>{title}</title>",
                f"      <link>{link}</link>",
                f'      <guid isPermaLink="true">{link}</guid>',
                f"      <pubDate>{entry_pub(e)}</pubDate>",
                f"      <description>{desc}</description>",
                f"      <content:encoded><![CDATA[{abstract}"
                f"\n\narXiv categories: {html.escape(cats)}]]></content:encoded>",
                "    </item>"]
    out += ["  </channel>", "</rss>", ""]
    return "\n".join(out)


def main():
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=WINDOW_DAYS)
    window = (f"submittedDate:[{start:%Y%m%d%H%M} TO {end:%Y%m%d%H%M}]")
    out_dir = Path("docs")
    out_dir.mkdir(exist_ok=True)
    failed = []
    for cat, slug in CATEGORIES:
        target = out_dir / f"{slug}-raw.xml"
        try:
            raw = fetch(f"cat:{cat} AND {window}")
            feed = feedparser.parse(raw)
            entries = feed.entries
            if not entries:
                # 窗口内确实可能没有新稿（假期），但也要把总数打出来便于判断
                print(f"[arxiv] {cat}: 0 entries in window", file=sys.stderr)
            target.write_text(build_rss(cat, entries), encoding="utf-8")
            print(f"[arxiv] {cat}: {len(entries)} entries -> {target}", file=sys.stderr)
        except Exception as e:            # noqa: BLE001
            failed.append(cat)
            print(f"[arxiv] {cat} FAILED: {e}（保留旧文件不动）", file=sys.stderr)
        time.sleep(3)                     # arXiv API 礼貌间隔：<= 1 req / 3s
    if failed:
        print(f"[arxiv] failed categories: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
