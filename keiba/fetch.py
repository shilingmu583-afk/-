"""大井競馬の過去レース結果（地方競馬情報サイト keiba.go.jp）と、東京の日別気象（気象庁）を集める。

使い方:
  python3 -m keiba.fetch                      # 今日までの過去365日
  python3 -m keiba.fetch --from 2025-10-09 --to 2026-10-08
  python3 -m keiba.fetch --weather-only

取得した HTML は data/keiba/raw/ にキャッシュする（git 管理外）。2回目以降はキャッシュを使うので、
途中で止まっても続きから再開できる。サーバーに負担をかけないよう、1リクエストごとに --wait 秒待つ。

注意: 必要な接続先は www.keiba.go.jp と www.data.jma.go.jp。ネットワーク制限のある環境では
許可リストにこの2つを追加すること。
"""
import argparse
import datetime as dt
import hashlib
import html
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from . import data as D

KEIBA = "https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/"
OHI_BABA_CODE = 20  # 大井
JMA_DAILY = ("https://www.data.jma.go.jp/stats/etrn/view/daily_s1.php"
             "?prec_no=44&block_no=47662&year={y}&month={m}&day=&view=")  # 東京
RAW = D.DATA / "raw"
UA = "Mozilla/5.0 (ohi-keiba-analysis; personal research)"


# ---------------------------------------------------------------- HTML
class _Tables(HTMLParser):
    """ページ内の <table> を [[セル文字列, ...], ...] のリストで、<a href> を一覧で集める。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.links, self.text = [], [], []
        self._stack, self._cell, self._href = [], None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack:
            self._cell = []
            if not self._stack[-1]:
                self._stack[-1].append([])
        elif tag == "a" and a.get("href"):
            self._href = [a["href"], []]
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._stack:
            self._stack[-1][-1].append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "table" and self._stack:
            self.tables.append([r for r in self._stack.pop() if r])
        elif tag == "a" and self._href:
            self.links.append((self._href[0], "".join(self._href[1]).strip()))
            self._href = None

    def handle_data(self, s):
        if self._cell is not None:
            self._cell.append(s)
        if self._href:
            self._href[1].append(s)
        self.text.append(s)


def parse(page: str) -> _Tables:
    p = _Tables()
    p.feed(page)
    p.page_text = re.sub(r"\s+", " ", html.unescape("".join(p.text)))
    return p


def get(url: str, wait: float, refresh=False) -> str:
    RAW.mkdir(parents=True, exist_ok=True)
    cache = RAW / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    if cache.exists() and not refresh:
        return cache.read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                body = res.read()
                charset = res.headers.get_content_charset() or "utf-8"
            break
        except Exception as e:  # noqa: BLE001
            if i == 3:
                raise
            print(f"  再試行 {url}: {e}", file=sys.stderr)
            time.sleep(2 ** (i + 1))
    page = body.decode(charset, errors="replace")
    cache.write_text(page, encoding="utf-8")
    time.sleep(wait)
    return page


# ---------------------------------------------------------------- 大井のレース結果
def race_list_url(d: dt.date) -> str:
    return KEIBA + "RaceList?" + urllib.parse.urlencode(
        {"k_raceDate": d.strftime("%Y/%m/%d"), "k_babaCode": OHI_BABA_CODE})


def result_urls(d: dt.date, wait: float) -> dict[int, list[str]]:
    """その日の大井のレース番号 → 結果ページ候補 URL。開催が無い日は空。"""
    p = parse(get(race_list_url(d), wait))
    out: dict[int, list[str]] = {}
    for href, _text in p.links:
        m = re.search(r"k_raceNo=(\d+)", href)
        if not m or f"k_babaCode={OHI_BABA_CODE}" not in href:
            continue
        url = urllib.parse.urljoin(KEIBA, html.unescape(href))
        urls = out.setdefault(int(m.group(1)), [])
        if url not in urls:
            # 「結果」らしいページを先に試す
            (urls.insert(0, url) if "Result" in href or "Dividend" in href else urls.append(url))
    return out


# 結果表の見出し → runners.csv の列（見出しに含まれる文字で判定、先に当たったもの優先）
HEADER_MAP = [
    ("着順", ("着順", "着")), ("枠", ("枠",)), ("馬番", ("馬番",)), ("馬名", ("馬名",)),
    ("騎手", ("騎手",)), ("調教師", ("調教師",)), ("斤量", ("重量", "斤量")),
    ("馬体重", ("体重",)), ("タイム", ("タイム",)), ("人気", ("人気",)),
    ("通過", ("通過", "コーナー")), ("単勝オッズ", ("オッズ", "単勝")),
]


def _map_header(header: list[str]) -> dict[str, int]:
    cols: dict[str, int] = {}
    for i, h in enumerate(header):
        h = h.replace(" ", "")
        for col, keys in HEADER_MAP:
            if col in cols:
                continue
            if any(k in h for k in keys) and not (col == "着順" and "差" in h):
                cols[col] = i
                break
    return cols


def parse_result(page: str, d: dt.date, no: int) -> list[dict]:
    """結果ページから runners.csv の行を作る。結果表が見つからなければ空リスト。"""
    p = parse(page)
    text = p.page_text
    meta = {
        "日付": d.isoformat(), "R": no,
        "発走": (m.group(1) if (m := re.search(r"発走[時刻]*\s*[:：]?\s*(\d{1,2}[:：]\d{2})", text)) else "").replace("：", ":"),
        "距離": m.group(1) if (m := re.search(r"(\d{3,4})\s*[mｍM]", text)) else "",
        "馬場": m.group(1) if (m := re.search(r"馬場[状態]*\s*[:：]?\s*(稍重|不良|良|重)", text)) else "",
        "天候": m.group(1) if (m := re.search(r"天候\s*[:：]?\s*(晴|曇|小雨|雨|小雪|雪)", text)) else "",
    }
    for table in p.tables:
        hi = next((i for i, r in enumerate(table[:3]) if any("馬名" in c for c in r)
                   and any("着" in c for c in r)), None)
        if hi is None:
            continue
        cols = _map_header(table[hi])
        if "馬名" not in cols:
            continue
        rows = []
        for r in table[hi + 1:]:
            if len(r) < len(table[hi]) - 2:
                continue
            row = dict(meta)
            for col, i in cols.items():
                row[col] = r[i] if i < len(r) else ""
            row["馬名"] = re.sub(r"\s*\(.*?\)\s*", "", row["馬名"]).strip()
            row["騎手"] = re.sub(r"[▲△☆◇★\s]|\(.*?\)", "", row.get("騎手", ""))
            row["着順"] = row["着順"] if row.get("着順", "").isdigit() else ""
            if row["馬名"]:
                rows.append(row)
        if rows:
            return rows
    return []


def fetch_results(start: dt.date, end: dt.date, wait: float) -> list[dict]:
    existing = D.read_csv(D.RUNNERS_CSV)
    done = {(r["日付"], r["R"]) for r in existing}
    rows = list(existing)
    d = start
    while d <= end:
        try:
            urls = result_urls(d, wait)
        except Exception as e:  # noqa: BLE001
            print(f"{d}: レース一覧を取得できない ({e})", file=sys.stderr)
            urls = {}
        got = 0
        for no in sorted(urls):
            if (d.isoformat(), str(no)) in done:
                continue
            for url in urls[no]:
                part = parse_result(get(url, wait), d, no)
                if part:
                    rows += part
                    got += 1
                    break
        if urls:
            print(f"{d}: 大井 {len(urls)}R 中 {got}R の結果を追加")
        d += dt.timedelta(days=1)
    rows.sort(key=lambda r: (r["日付"], int(r["R"]), int(r["着順"]) if r.get("着順") else 99))
    D.write_csv(D.RUNNERS_CSV, D.RUNNER_COLS, rows)
    return rows


# ---------------------------------------------------------------- 気象庁 東京の日別値
def _val(s: str):
    s = re.sub(r"[)\]#×/ ]", "", s or "")
    return s if re.fullmatch(r"-?\d+(\.\d+)?", s) else ""


def parse_jma_month(page: str, y: int, m: int) -> list[dict]:
    """daily_s1（東京）の表: 0日 3降水量合計 6平均気温 16日照時間 19天気概況昼 20天気概況夜。"""
    rows = []
    for table in parse(page).tables:
        for r in table:
            if len(r) < 21 or not r[0].isdigit():
                continue
            rows.append({"日付": dt.date(y, m, int(r[0])).isoformat(), "日照時間": _val(r[16]),
                         "降水量": _val(r[3]) or ("0" if r[3].strip() == "--" else ""),
                         "平均気温": _val(r[6]), "天気概況昼": r[19], "天気概況夜": r[20]})
    return rows


def fetch_weather(start: dt.date, end: dt.date, wait: float) -> list[dict]:
    by_day = {r["日付"]: r for r in D.read_csv(D.WEATHER_CSV)}
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        this_month = (y, m) == (dt.date.today().year, dt.date.today().month)
        page = get(JMA_DAILY.format(y=y, m=m), wait, refresh=this_month)
        for r in parse_jma_month(page, y, m):
            if start.isoformat() <= r["日付"] <= end.isoformat():
                by_day[r["日付"]] = r
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    rows = [by_day[k] for k in sorted(by_day)]
    D.write_csv(D.WEATHER_CSV, D.WEATHER_COLS, rows)
    print(f"気象データ {len(rows)} 日分")
    return rows


def main():
    today = dt.date.today()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", type=dt.date.fromisoformat, default=today - dt.timedelta(days=365))
    ap.add_argument("--to", dest="end", type=dt.date.fromisoformat, default=today - dt.timedelta(days=1))
    ap.add_argument("--wait", type=float, default=1.0, help="1リクエストごとの待ち秒数")
    ap.add_argument("--weather-only", action="store_true")
    a = ap.parse_args()
    fetch_weather(a.start, a.end, a.wait)
    if not a.weather_only:
        rows = fetch_results(a.start, a.end, a.wait)
        print(f"レース結果 {len(rows)} 行（{len({(r['日付'], r['R']) for r in rows})} レース）")


if __name__ == "__main__":
    main()
