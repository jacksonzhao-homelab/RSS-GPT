#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QC sitemap → 标准 RSS 2.0，在 main.py 之前跑。
QC 的 sitemap 是 urlset 协议，feedparser 不解析；这里转成 RSS 供 RSS-GPT 消费。
数据源：QC research/announcements/lean sitemap（实测直连即可）。
移植自 QuantumTrading/scripts/qc_rss.py（实测版本），简化为 RSS-GPT 内嵌版。
"""
import html
import re
import subprocess
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

BASE = "https://www.quantconnect.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"

# (feed 名, sitemap URL, 类别)
FEEDS = [
    ("research", f"{BASE}/research.posts.sitemap.xml", "研究"),
    ("announcements", f"{BASE}/announcements.posts.sitemap.xml", "公告"),
]

CONTENT_PAT = re.compile(r"quantconnect\.com/(research|announcements|lean|forum/discussion)/")


def fetch(url, timeout=30):
    r = subprocess.run(["curl", "-sS", "-m", str(timeout), "-A", UA, "-L", url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"curl {r.returncode}: {r.stderr[:120]}")
    return r.stdout


def parse_sitemap(xml):
    """从 urlset 抽 (loc, lastmod) 列表，只留真实内容页"""
    items = []
    for m in re.finditer(r"<url>(.*?)</url>", xml, re.S):
        block = m.group(1)
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", block)
        lastmod = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block)
        if not loc:
            continue
        url = html.unescape(loc.group(1))
        if not CONTENT_PAT.search(url):
            continue
        dt = None
        if lastmod:
            try:
                dt = datetime.fromisoformat(lastmod.group(1).replace("Z", "+00:00"))
            except ValueError:
                pass
        items.append((url, dt))
    return items


def esc(s):
    return html.escape(s, quote=True)


def build_rss(feed_name, category):
    xml = fetch(f"{BASE}/{feed_name}.posts.sitemap.xml")
    items = parse_sitemap(xml)
    items.sort(key=lambda x: x[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0"><channel>',
           f"<title>QuantConnect {category}</title>",
           f"<link>{BASE}</link>",
           f"<description>QC {category} via sitemap→RSS</description>"]
    for url, dt in items[:30]:
        slug = url.rstrip("/").split("/")[-1].replace("-", " ").strip()
        title = slug[:80] if slug else url
        pub = format_datetime(dt) if dt else format_datetime(datetime.now(timezone.utc))
        out.append("<item>")
        out.append(f"<title>{esc(title)}</title>")
        out.append(f"<link>{esc(url)}</link>")
        out.append(f"<guid>{esc(url)}</guid>")
        out.append(f"<pubDate>{pub}</pubDate>")
        out.append(f"<category>{esc(category)}</category>")
        out.append(f"<description>{esc(title)}</description>")
        out.append("</item>")
    out.append("</channel></rss>")
    return "\n".join(out)


if __name__ == "__main__":
    out_dir = Path("docs")
    out_dir.mkdir(exist_ok=True)
    for name, _, cat in FEEDS:
        rss = build_rss(name, cat)
        p = out_dir / f"qc-{name}.xml"
        p.write_text(rss, encoding="utf-8")
        print(f"wrote {p} ({len(rss)} bytes)", file=sys.stderr)
