"""RSS を取得し、フィルタ・重複まとめをして site/data/news.json を出力する。

標準ライブラリのみで動作する（Python 3.11 以上）。
    python scripts/build_news.py
"""
import concurrent.futures
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import pathlib
import re
import sys
import time
import tomllib
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.toml"
OUTPUT = ROOT / "site" / "data" / "news.json"
CACHE = ROOT / "cache" / "enrich.json"
INDEX = ROOT / "site" / "index.html"
USER_AGENT = "Mozilla/5.0 (personal news reader)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
JST = dt.timezone(dt.timedelta(hours=9))


# ---------------------------------------------------------------- 取得・解析

def fetch(url, headers=None):
    last_error = None
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
            with urllib.request.urlopen(req, timeout=20) as res:
                return res.read()
        except Exception as e:  # noqa: BLE001 - 失敗は呼び出し元で記録する
            last_error = e
    raise last_error


def local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_date(text):
    if not text:
        return None
    text = text.strip()
    try:
        d = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=JST)
    return d


def clean_text(text):
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(data, feed):
    """RSS 2.0 / RSS 1.0(RDF) / Atom の記事を共通形式の dict にする。"""
    root = ET.fromstring(data)
    articles = []
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        a = {"title": "", "url": "", "date": None, "summary": "",
             "source": feed["source"], "source_url": ""}
        for c in el:
            name = local(c.tag)
            if name == "title":
                a["title"] = clean_text(c.text)
            elif name == "link":
                a["url"] = (c.text or c.get("href") or "").strip()
            elif name in ("pubDate", "date", "published", "updated") and not a["date"]:
                a["date"] = parse_date(c.text)
            elif name in ("description", "summary"):
                a["summary"] = clean_text(c.text)
            elif name == "source":  # Googleニュース: 記事ごとの媒体名
                a["source"] = (c.text or "").strip() or feed["source"]
                a["source_url"] = c.get("url", "")
        if a["source"] != feed["source"]:
            # Googleニュースは「見出し - 媒体名」形式なので媒体名を取り除く
            suffix = " - " + a["source"]
            if a["title"].endswith(suffix):
                a["title"] = a["title"][: -len(suffix)]
            a["summary"] = ""  # 中身は関連記事のリンク一覧なので使わない
        if a["title"] and a["url"].startswith(("http://", "https://")):
            articles.append(a)
    return articles


# ---------------------------------------------------------------- フィルタ

def norm(text):
    return unicodedata.normalize("NFKC", text or "").lower()


def compile_words(words):
    rules = []
    for w in words:
        if w.startswith("re:"):
            rules.append(("re", re.compile(norm(w[3:]))))
        else:
            rules.append(("str", norm(w)))
    return rules


def match_word(title, rules):
    t = norm(title)
    for kind, rule in rules:
        if (kind == "str" and rule in t) or (kind == "re" and rule.search(t)):
            return True
    return False


def source_matches(article, names):
    src = norm(article["source"])
    host = urllib.parse.urlparse(article["source_url"] or article["url"]).hostname or ""
    for n in names:
        n = norm(n)
        if ("." in n and host.endswith(n)) or ("." not in n and n in src):
            return True
    return False


# ---------------------------------------------------------------- 重複まとめ

def title_key(title):
    t = norm(title)
    t = re.sub(r"【[^】]*】|\[[^\]]*\]|（[^）]*）$|\([^)]*\)$", "", t)
    return re.sub(r"[\s\W_]+", "", t)


def bigrams(title):
    k = title_key(title)
    return {k[i:i + 2] for i in range(len(k) - 1)}


def similar(a, b, threshold):
    if not a or not b:
        return False
    shared = len(a & b)
    return shared >= 4 and shared / min(len(a), len(b)) >= threshold


def article_id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- 概要・リンク先の補完
# 結果は CACHE に保存し、次回以降は同じ記事へアクセスしない。

class Cache:
    KEEP = dt.timedelta(days=4)

    def __init__(self, path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def get(self, key):
        v = self.data.get(key)
        return v["v"] if v else None

    def set(self, key, value):
        self.data[key] = {"v": value, "t": dt.datetime.now(dt.timezone.utc).timestamp()}

    def save(self):
        limit = (dt.datetime.now(dt.timezone.utc) - self.KEEP).timestamp()
        self.data = {k: v for k, v in self.data.items() if v["t"] >= limit}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")


def is_google(url):
    return urllib.parse.urlparse(url).hostname == "news.google.com"


def resolve_google(url):
    """Googleニュースの転送用URLから元記事のURLを割り出す（非公式の方法。失敗時は None）。"""
    aid = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
    page = fetch_text(f"https://news.google.com/articles/{aid}", limit=None)
    sg = re.search(r'data-n-a-sg="([^"]+)"', page)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page)
    if not (sg and ts):
        return None
    inner = (f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
             f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{aid}",{ts.group(1)},"{sg.group(1)}"]')
    body = urllib.parse.urlencode({"f.req": json.dumps([[["Fbv4je", inner, None, "generic"]]])}).encode()
    req = urllib.request.Request(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body,
        headers={"User-Agent": BROWSER_UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=10) as res:
        text = res.read().decode("utf-8")
    real = json.loads(json.loads(text.split("\n\n")[1])[0][2])[1]
    return real if real.startswith(("http://", "https://")) else None


def fetch_text(url, limit=400_000):
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=10) as res:
        raw = res.read(limit)
        charset = res.headers.get_content_charset()
    if not charset:
        m = re.search(rb'charset=["\']?([\w-]+)', raw[:3000])
        charset = m.group(1).decode() if m else "utf-8"
    try:
        return raw.decode(charset, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


META_TAG = re.compile(r"<meta\s[^>]*>", re.I)


def page_meta(url):
    """記事ページの紹介文（og:description）と画像（og:image）を取り出す。"""
    meta = {}
    for tag in META_TAG.findall(fetch_text(url)):
        name = re.search(r'(?:property|name)=["\']([^"\']+)', tag)
        content = re.search(r'content=["\']([^"\']*)', tag)
        if name and content:
            meta.setdefault(name.group(1).lower(), html.unescape(content.group(1)).strip())
    desc = clean_text(meta.get("og:description") or meta.get("description") or "")
    image = meta.get("og:image") or meta.get("twitter:image") or ""
    image = urllib.parse.urljoin(url, image) if image else ""
    return {"d": desc, "i": image if image.startswith("https://") else ""}


def shorten(text, n=120):
    text = re.sub(r"^【[^】]{1,15}】", "", text.strip("　 ")).strip("　 ")  # 先頭の【読売新聞】等
    return text if len(text) <= n else text[: n - 1] + "…"


GOOGLE_BUDGET = 40  # 1回の更新で新たに調べる件数（多いとGoogleに制限される）


def enrich(items, cache, ng_words):
    # 1) Googleニュースの記事は元記事のURLに置き換える。
    #    制限されないよう1件ずつ間をあけ、断られたらその回は打ち切る（調べた分は次回に引き継ぐ）
    budget = GOOGLE_BUDGET
    leads = [i["_members"][0] for i in items]
    others = [m for i in items for m in i["_members"][1:]]
    for m in leads + others:
        if not is_google(m["url"]):
            continue
        key = "url:" + m["url"]
        if cache.get(key) is None and budget > 0:
            budget -= 1
            try:
                real = resolve_google(m["url"])
                if real:
                    cache.set(key, real)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    budget = 0
            except Exception:  # noqa: BLE001 - 次回また試す
                pass
            time.sleep(0.7)
        m["url"] = cache.get(key) or m["url"]

    # 2) 記事ページから紹介文と画像を取る（概要がない記事の補完と、サムネイル用）
    def fetch_meta(m):
        m["image"] = ""
        if is_google(m["url"]):
            return
        key = "meta:" + m["url"]
        if cache.get(key) is None:
            try:
                cache.set(key, page_meta(m["url"]))
            except Exception:  # noqa: BLE001
                return
        meta = cache.get(key)
        m["summary"] = m["summary"] or meta["d"]
        m["image"] = meta["i"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch_meta, leads + others))

    # サイト共通の決まり文句・ロゴ画像（別々の話題で同じもの）は使わない
    seen = {}
    for n, i in enumerate(items):
        for m in i["_members"]:
            for v in (m["summary"], m["image"]):
                if v:
                    seen.setdefault(v, set()).add(n)
    unique = lambda v: v and len(seen[v]) == 1  # noqa: E731

    for i in items:
        lead, *related = i.pop("_members")
        i["url"] = lead["url"]
        for r, m in zip(i["related"], related):
            r["url"] = m["url"]
        summaries = [m["summary"] for m in (lead, *related) if unique(m["summary"])]
        i["summary"] = shorten(next((s for s in summaries if not match_word(s, ng_words)), ""))
        i["image"] = next((m["image"] for m in (lead, *related) if unique(m["image"])), "")


# ---------------------------------------------------------------- ランキング

RAKUTEN_BOOKS = "https://openapi.rakuten.co.jp/services/api/BooksBook/Search/20170404"


def fetch_json(url, headers=None):
    return json.loads(fetch(url, headers))


def apple_ranking(r):
    results = fetch_json(r["url"])["feed"]["results"]
    # 30秒の試聴用音源（iTunes Search API）。取れなくてもランキングは出す
    previews = {}
    try:
        ids = ",".join(x["id"] for x in results)
        lookup = fetch_json(f"https://itunes.apple.com/lookup?country=jp&id={ids}")
        previews = {str(x.get("trackId")): x.get("previewUrl", "") for x in lookup.get("results", [])}
    except Exception:  # noqa: BLE001
        pass
    return [{"title": x["name"], "sub": x.get("artistName", ""), "url": x["url"],
             "image": x.get("artworkUrl100", ""), "preview": previews.get(x["id"], "")} for x in results]


def rakuten_get(url, params, referer):
    app_id, key = os.environ.get("RAKUTEN_APP_ID"), os.environ.get("RAKUTEN_ACCESS_KEY")
    if not (app_id and key):
        raise RuntimeError("RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY が未設定")
    query = urllib.parse.urlencode({"applicationId": app_id, "accessKey": key, "formatVersion": 2, **params})
    # 楽天は「許可されたWebサイト」からの呼び出しかを Referer と Origin の両方で確かめる
    site = urllib.parse.urlparse(referer)
    req = urllib.request.Request(f"{url}?{query}", headers={
        "User-Agent": USER_AGENT, "Referer": referer, "Origin": f"{site.scheme}://{site.netloc}"})
    # 楽天は1秒に1回までの制限があるため、呼び出しごと・再試行の前に間をあける
    for attempt in range(2):
        time.sleep(1.2)
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                return json.loads(res.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:150]
            if attempt == 1 or e.code not in (429, 500, 502, 503):
                raise RuntimeError(f"HTTP {e.code} {detail}") from None
            time.sleep(3)


def rakuten_group(r, group):
    """楽天ブックスの1ジャンルの売上順。"""
    data = rakuten_get(RAKUTEN_BOOKS, {"booksGenreId": group["genre"], "sort": "sales",
                                       "hits": r.get("count", 10)}, r["referer"])
    return [{
        "title": re.sub(r"^(【[^】]*】\s*)+", "", x["title"]).strip() or x["title"],  # 【限定特典】等を外す
        "sub": x.get("author", ""), "url": x["itemUrl"], "image": x.get("mediumImageUrl", ""),
    } for x in data["Items"]]


def load_rankings(cfg, cache):
    """ランキングを取得する。各ランキングは groups（見出し付きの一覧）のリストとして出力する。
    取れなかったときは前回の結果を使う。"""
    out = []
    for r in cfg.get("rankings", []):
        entry = {"id": r["id"], "name": r["name"], "source": r["source"], "show": r.get("show", 5),
                 "groups": [], "links": r.get("links", []), "errors": []}
        if r["type"] == "apple":
            jobs = [({"name": ""}, lambda: apple_ranking(r))]
        else:
            jobs = [(g, lambda g=g: rakuten_group(r, g)) for g in r.get("groups", [])]
        for group, fn in jobs:
            key = f"ranking:{r['id']}:{group['name']}"
            try:
                items = fn()[:r.get("count", 10)]
                cache.set(key, items)
            except Exception as e:  # noqa: BLE001
                entry["errors"].append(f"{group['name']} {type(e).__name__}: {e}"[:200])
                items = cache.get(key) or []
            entry["groups"].append({"name": group["name"], "link": group.get("link", ""), "items": items})
        out.append(entry)
    return out


# ---------------------------------------------------------------- メイン

def main():
    cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    settings = cfg.get("settings", {})
    max_age = dt.timedelta(hours=settings.get("max_age_hours", 36))
    threshold = settings.get("similarity", 0.55)
    per_section = settings.get("max_items_per_section", 30)
    ng_sources = cfg.get("filter", {}).get("ng_sources", [])
    ng_words = compile_words(cfg.get("filter", {}).get("ng_words", []))
    now = dt.datetime.now(dt.timezone.utc)

    sections = cfg["sections"]
    jobs = [(si, s, f) for si, s in enumerate(sections) for f in s.get("feeds", [])]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda j: _load(j[2]), jobs))

    feed_status = []
    stats = {"old": 0, "ng_source": 0, "ng_word": 0, "not_selected": 0}
    articles = []
    seen_urls = set()
    for (si, section, feed), (items, error) in zip(jobs, results):
        newest = max((a["date"] for a in items if a["date"]), default=None)
        feed_status.append({
            "section": section["name"], "source": feed["source"], "url": feed["url"],
            "ok": error is None, "count": len(items), "error": error,
            "newest": newest.isoformat() if newest else None,
        })
        only = feed.get("only_sources")
        feed_max_age = dt.timedelta(hours=feed["max_age_hours"]) if "max_age_hours" in feed else max_age
        for a in items:
            if a["date"] is None:
                a["date"] = now
            if now - a["date"] > feed_max_age:
                stats["old"] += 1
            elif only and not source_matches(a, only):
                stats["not_selected"] += 1
            elif source_matches(a, ng_sources):
                stats["ng_source"] += 1
            elif match_word(a["title"], ng_words):
                stats["ng_word"] += 1
            elif a["url"] not in seen_urls:
                seen_urls.add(a["url"])
                a["section"] = si
                a["priority"] = feed.get("priority", 5)
                a["boost"] = feed.get("boost", 0)
                a["short_title"] = feed.get("short_title", False)
                a["always_show"] = feed.get("always_show", False)
                a["grams"] = bigrams(a["title"])
                articles.append(a)

    # 同じ出来事の記事をまとめる（上のジャンル・優先度の高い媒体から順に処理）
    articles.sort(key=lambda a: (a["section"], a["priority"], -a["date"].timestamp()))
    clusters = []
    for a in articles:
        for c in clusters:
            if any(similar(a["grams"], m["grams"], threshold) for m in c):
                c.append(a)
                break
        else:
            clusters.append([a])

    out_sections = [{"id": s["id"], "name": s["name"], "items": []} for s in sections]
    for c in clusters:
        lead = c[0]
        # 見出しが同じもの（NHK と Google経由の NHK など）は関連記事に出さない
        related, keys = [], {title_key(lead["title"])}
        for m in c[1:]:
            k = title_key(m["title"])
            if k not in keys:
                keys.add(k)
                related.append(m)
        coverage = len({lead["source"]} | {m["source"] for m in related})  # 報じた媒体の数
        latest = max(m["date"] for m in c)
        hours = (now - latest).total_seconds() / 3600
        boost = max(m["boost"] for m in c)
        # 多くの媒体が報じた・編集部が選んだ・新しい話題ほど上に
        score = coverage * 4 + boost - hours
        # 「いまの話題」用：媒体数をより重視し、主要ニュースに選ばれた話題を加点
        hot = coverage * 3 + boost + (3 if c[0]["section"] == 0 else 0) - hours * 0.5
        short = next((m["title"] for m in c if m["short_title"]), lead["title"])
        short = re.sub(r"^【[^】]{1,10}】\s*", "", short) or short  # 一覧では【速報】等を外す
        out_sections[lead["section"]]["items"].append({
            "score": score,
            "_hot": hot if hours <= 18 else None,
            "_always": any(m["always_show"] for m in c),
            "headline": short,
            "coverage": coverage,
            "title": lead["title"],
            "url": lead["url"],
            "id": article_id(lead["url"]),
            "source": lead["source"],
            "time": lead["date"].isoformat(),
            "summary": "",
            "_members": [lead, *related],
            "related": [{
                "title": m["title"], "url": m["url"], "id": article_id(m["url"]),
                "source": m["source"], "time": m["date"].isoformat(),
            } for m in related],
        })

    for s in out_sections:
        s["items"].sort(key=lambda i: -i["score"])
        # 上限件数まで＋ always_show の情報源の記事は必ず残す
        s["items"] = s["items"][:per_section] + [i for i in s["items"][per_section:] if i["_always"]]
        for i in s["items"]:
            del i["score"], i["_always"]

    cache = Cache(CACHE)
    enrich([i for s in out_sections for i in s["items"]], cache, ng_words)
    rankings = load_rankings(cfg, cache)
    cache.save()

    # 主要トピックス：全ジャンルから話題の大きいものを選ぶ（各ジャンルにも残す。ページ側で重複を調整）
    candidates = [i for s in out_sections for i in s["items"] if i["_hot"] is not None]
    hot = [i["id"] for i in sorted(candidates, key=lambda i: -i["_hot"])[:settings.get("hot_count", 8)]]
    for s in out_sections:
        for i in s["items"]:
            del i["_hot"]

    total = sum(len(s["items"]) for s in out_sections)
    if total == 0:
        print("記事を1件も取得できませんでした。出力を更新しません。", file=sys.stderr)
        sys.exit(1)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # 画面の版番号（index.html の app.js?v=N）。開いている画面が古ければ、ページ側が自動で入れ替える
    version = re.search(r"app\.js\?v=(\d+)", INDEX.read_text(encoding="utf-8"))

    OUTPUT.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "app_version": version.group(1) if version else "",
        "show_per_section": settings.get("show_per_section", 8),
        "hot": hot,  # 主要トピックスに出す記事の id（上から順に）
        "sections": out_sections,
        "rankings": rankings,
        "feeds": feed_status,
        "filtered": stats,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    items = [i for s in out_sections for i in s["items"]]
    print(f"{total}件を出力しました  除外: {stats}  "
          f"概要あり {sum(1 for i in items if i['summary'])}件  "
          f"Google経由のまま {sum(1 for i in items if is_google(i['url']))}件")
    for f in feed_status:
        mark = "OK" if f["ok"] else "NG"
        print(f"  [{mark}] {f['section']:<6} {f['source']:<12} {f['count']:>4}件  最新 {f['newest'] or '-'}"
              + (f"  {f['error']}" if f["error"] else ""))
    for r in rankings:
        counts = " ".join(f"{g['name'] or r['name']}{len(g['items'])}件" for g in r["groups"])
        print(f"  [{'NG' if r['errors'] else 'OK'}] ランキング {r['name']}: {counts}"
              + (f"  {r['errors']}" if r["errors"] else ""))


def _load(feed):
    try:
        return parse_feed(fetch(feed["url"]), feed), None
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"[:200]


if __name__ == "__main__":
    main()
