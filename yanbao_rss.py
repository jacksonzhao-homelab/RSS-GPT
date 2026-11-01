#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""券商金工/量化研报 → 标准 RSS 2.0（供 RSS-GPT 摘要）。

源：东方财富 reportapi（免登录、国内直连）。
  https://reportapi.eastmoney.com/report/list?qType=2&columnCode=002001002
过滤：标题词表（量化/因子/金工/轮动/择时/Alpha/量价/情绪/资金流/高频/机器学习…）
产出：docs/yanbao.xml —— main.py 通过 file:// 读取（fetch_feed 已支持）。
移植自 Hermes cron job a87a47741ab7 的「券商金工研报动态」节。
"""
import html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

API = "https://reportapi.eastmoney.com/report/list"

# 标题词表：宁漏勿滥，噪音交给下游 GLM 摘要自然淘汰
KEYWORDS = (
    "量化", "因子", "金工", "多因子", "选股", "指数增强", "Barra", "barra",
    "量价", "择时", "Alpha", "alpha", "ALPHA", "高频", "smart", "Smart",
    "轮动", "机器学习", "深度学习", "NLP", "情绪", "资金流", "北向",
    "拥挤度", "微盘", "小市值", "风格", "配对交易", "事件驱动", "异动",
)

PAGE_SIZE = 100
DAYS = 8          # 周报窗口：拉最近 8 天（覆盖周一运行时上一周）
MAX_PAGES = 4


def fetch_json(url, timeout=20):
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    r = subprocess.run(["curl", "-sS", "-m", str(timeout), url],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"curl {r.returncode}: {r.stderr[:120]}")
    return json.loads(r.stdout)


REPORT_PAGE = "https://data.eastmoney.com/report/info/{code}.html"
_TAG = re.compile(r"<[^>]+>")


def fetch_abstract(code, timeout=20, limit=1500):
    """研报正文页的「摘要」块（div.ctx-content）→ 纯文本。

    reportapi 的列表接口没有摘要字段，只给标题；不补摘要的话 GLM 只能靠标题
    猜内容。这里抓正文页把摘要塞进 description，喂给 main.py 的 clean_html。
    """
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    r = subprocess.run(["curl", "-sSL", "-m", str(timeout), "-A", "Mozilla/5.0",
                        REPORT_PAGE.format(code=code)],
                       capture_output=True, text=True, errors="replace", env=env)
    if r.returncode != 0 or not r.stdout:
        return ""
    body = r.stdout
    i = body.find('class="ctx-content"')
    if i < 0:
        return ""
    start = body.find(">", i)          # 从开标签的 '>' 之后开始，别把 class 属性当正文
    if start < 0:
        return ""
    seg = body[start + 1:start + 20000]
    j = seg.find("</div>")
    text = _TAG.sub(" ", seg[:j] if j > 0 else seg)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]



def collect(days=DAYS):
    end = datetime.now(timezone(timedelta(hours=8)))
    begin = end - timedelta(days=days)
    rows = []
    for page in range(1, MAX_PAGES + 1):
        url = (f"{API}?pageSize={PAGE_SIZE}&pageNo={page}&qType=2"
               f"&columnCode=002001002"
               f"&beginTime={begin:%Y-%m-%d}&endTime={end:%Y-%m-%d}")
        d = fetch_json(url)
        batch = d.get("data") or []
        rows += batch
        if len(batch) < PAGE_SIZE:
            break
        time.sleep(0.3)
    return rows


def filter_hits(rows):
    seen, out = set(), []
    for x in rows:
        t = (x.get("title") or "").strip()
        code = x.get("infoCode")
        if not t or not code or code in seen:
            continue
        if not any(k in t for k in KEYWORDS):
            continue
        seen.add(code)
        out.append(x)
    return out


def build_rss(hits, abstracts=None):
    abstracts = abstracts or {}
    items = sorted(hits, key=lambda x: x.get("publishDate") or "", reverse=True)
    now = format_datetime(datetime.now(timezone.utc))
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" '
           'xmlns:content="http://purl.org/rss/1.0/modules/content/">',
           "  <channel>",
           "    <title>券商金工研报线索（东方财富）</title>",
           "    <link>https://data.eastmoney.com/report/strategyreport.jshtml</link>",
           "    <description>东财策略研报栏量化词过滤 —— 由 yanbao_rss.py 生成</description>",
           "    <language>zh-cn</language>",
           f"    <lastBuildDate>{now}</lastBuildDate>"]
    for x in items:
        t = x["title"].strip()
        code = x["infoCode"]
        link = f"https://data.eastmoney.com/report/zw_strategy.jshtml?infocode={code}"
        dt_raw = (x.get("publishDate") or "")[:19]
        try:
            dt = datetime.strptime(dt_raw, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone(timedelta(hours=8)))
            pub = format_datetime(dt)
        except ValueError:
            pub = now
        org = html.escape(x.get("orgSName") or "")
        body = abstracts.get(code) or ""
        desc = f"[{org}] {html.escape(t)}"
        content = f"[{org}] {t}" + (f"\n\n摘要：{body}" if body else "")
        out += ["    <item>",
                f"      <title>{html.escape(t)}</title>",
                f"      <link>{link}</link>",
                f'      <guid isPermaLink="true">{link}</guid>',
                f"      <author>{org}</author>",
                f"      <pubDate>{pub}</pubDate>",
                f"      <description>{desc}</description>",
                f"      <content:encoded><![CDATA[{content}]]></content:encoded>",
                "    </item>"]
    out += ["  </channel>", "</rss>", ""]
    return "\n".join(out)


if __name__ == "__main__":
    try:
        rows = collect()
        hits = filter_hits(rows)
        print(f"[yanbao] fetched={len(rows)} hits={len(hits)}", file=sys.stderr)
    except Exception as e:
        print(f"[yanbao] fetch failed: {e}", file=sys.stderr)
        hits = []
    # 只给最近会被摘要的那几条抓正文（main.py max_items=8），控制请求数
    abstracts = {}
    for x in sorted(hits, key=lambda x: x.get("publishDate") or "", reverse=True)[:12]:
        code = x["infoCode"]
        try:
            abstracts[code] = fetch_abstract(code)
        except Exception as e:            # noqa: BLE001
            print(f"[yanbao] abstract failed {code}: {e}", file=sys.stderr)
        time.sleep(0.5)
    print(f"[yanbao] abstracts={sum(1 for v in abstracts.values() if v)}/{len(abstracts)}",
          file=sys.stderr)
    rss = build_rss(hits, abstracts)
    out_dir = Path("docs")
    out_dir.mkdir(exist_ok=True)
    # 注意：必须和 main.py 的输出 docs/yanbao.xml 分开写。同名会让 main.py 把
    # 刚生成的原始条目当成 existing_entries 直接跳过，因此永远不会调 LLM。
    (out_dir / "yanbao_raw.xml").write_text(rss, encoding="utf-8")
    print(f"[yanbao] wrote docs/yanbao_raw.xml ({len(rss)} bytes)", file=sys.stderr)
