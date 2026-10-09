"""日付と気温を入れて、その日の大井のレースを過去1年の条件別成績から予想する。

使い方:
  python3 -m keiba.predict                                  # 日付・気温などを順に聞かれる
  python3 -m keiba.predict --date 2026-10-10 --temp 22       # 日付と最高気温（予報）
  python3 -m keiba.predict --date 2026-10-10 --temp 22 --sky 雨 --track 重
  python3 -m keiba.predict --date 2026-10-10 --temp 22 --entries 出馬表.csv

--temp はその日の最高気温（天気予報の値でよい）。天候は --sky（晴/曇/雨）か --sunshine（日照時間）、
馬場は --track（良/稍重/重/不良）。省略すると 晴・良 とみなす。

出走馬ごとの予想には出馬表が必要。--entries が無ければ data/keiba/entries/YYYY-MM-DD.csv を探し、
それも無ければ地方競馬情報サイトから取得を試みる。出馬表が無いときは、条件だけで分かる
「その日の傾向」（有利な枠・脚質、1番人気の信頼度、好調騎手）を出す。

出馬表 CSV（1頭 = 1行、UTF-8）の列:
  R, 発走(HH:MM), 距離, 枠, 馬番, 馬名, 騎手  （必須）
  単勝オッズ, 人気                            （任意。あれば精度が上がる。締切前のオッズでよい）

先に python3 -m keiba.analyze を実行して data/keiba/model.json を作っておくこと。
"""
import argparse
import datetime as dt
import json
import pathlib
import re

from . import conditions as C
from . import data as D
from . import model as M
from . import outlook as O
from . import word as W
from . import xlsx as X

MARKS = "◎○▲△△"
OUT_DIR = D.DATA / "predictions"
MODEL_JSON = D.DATA / "model.json"


def load_entries(rows, date: dt.date, sunshine, sky_word, track, max_temp=None) -> list[D.Race]:
    races: dict[int, D.Race] = {}
    for row in rows:
        no = int(row["R"])
        race = races.setdefault(no, D.Race(
            date=date, no=no, start=row.get("発走", ""), distance=D._num(row.get("距離"), int),
            track=track, weather=sky_word, sunshine=sunshine, max_temp=max_temp))
        race.runners.append(D.Runner(
            horse=row["馬名"].strip(), gate=D._num(row.get("枠"), int), number=D._num(row.get("馬番"), int),
            jockey=re.sub(r"[▲△☆◇★\s]", "", row.get("騎手", "")),
            odds=D._num(row.get("単勝オッズ")), popularity=D._num(row.get("人気"), int)))
    return [races[k] for k in sorted(races)]


def top3_probs(p):
    """勝つ確率から3着以内の確率を Harville の式で近似する。"""
    n = len(p)
    out = [0.0] * n
    for i in range(n):
        for j in range(n):
            if j == i:
                continue
            pj = p[j] / (1 - p[i]) if p[i] < 1 else 0
            out[j] += p[i] * pj  # j が2着
            for k in range(n):
                if k in (i, j):
                    continue
                rest = 1 - p[i] - p[j]
                out[k] += p[i] * pj * (p[k] / rest if rest > 0 else 0)  # k が3着
        out[i] += p[i]
    return out


def predict_race(race: D.Race, history: M.History, w):
    x = history.features(race)
    p = M.softmax(x, w)
    p3 = top3_probs(p)
    order = sorted(range(len(p)), key=lambda i: -p[i])
    rows = []
    for rank, i in enumerate(order):
        r = race.runners[i]
        contrib = [(M.FEATURES[j], w[j] * x[i][j]) for j in range(2, len(w))]
        reasons = [f"{name}{'＋' if c > 0 else '－'}" for name, c in sorted(contrib, key=lambda t: -abs(t[1]))
                   if abs(c) >= 0.08][:3]
        rows.append({
            "印": MARKS[rank] if rank < len(MARKS) else "", "馬番": r.number, "馬名": r.horse,
            "騎手": r.jockey, "脚質": history.horse_style(r.horse) or "?",
            "勝率": p[i], "3着内率": min(p3[i], 1.0), "根拠": "・".join(reasons),
        })
    return rows


def ask(prompt, default=""):
    v = input(f"{prompt}" + (f" [{default}]" if default != "" else "") + ": ").strip()
    return v or default


def find_entries(path, date):
    """出馬表の行。--entries → data/keiba/entries/日付.csv → 地方競馬情報サイト の順に探す。"""
    if path:
        return D.read_csv(path), str(path)
    local = D.DATA / "entries" / f"{date}.csv"
    if local.exists():
        return D.read_csv(local), str(local.relative_to(D.ROOT))
    try:
        from .fetch import fetch_entries
        rows = fetch_entries(date)
    except Exception as e:  # noqa: BLE001
        print(f"（出馬表をサイトから取得できなかった: {e}）")
        return [], ""
    if rows:
        D.write_csv(local, ["R", "発走", "距離", "枠", "馬番", "馬名", "騎手", "単勝オッズ", "人気"], rows)
        return rows, f"地方競馬情報サイト（{local.relative_to(D.ROOT)} に保存）"
    return [], ""


def run(date, max_temp, sky_word="晴", sunshine=None, track="良", entries=None):
    if not MODEL_JSON.exists():
        raise SystemExit("data/keiba/model.json がない。先に python3 -m keiba.analyze を実行する。")
    w = json.loads(MODEL_JSON.read_text(encoding="utf-8"))["重み"]
    w += [0.0] * (len(M.FEATURES) - len(w))
    past = [r for r in D.load_races() if r.date < date]
    _, history = M.build_dataset(past)

    sky = C.sky(sunshine, sky_word)
    rows, source = find_entries(entries, date)
    races = load_entries(rows, date, sunshine, sky_word, track, max_temp)
    lights = sorted({r.light for r in races if r.light}, key=C.LIGHTS.index) or None
    o = O.outlook(history.s, date, max_temp, sky, track, lights)

    head = (f"# 大井競馬 予想 {date}（{'月火水木金土日'[date.weekday()]}）\n\n"
            f"- 条件: {C.season(date)}／最高気温 {max_temp:g}℃（{C.temp_band(max_temp)}）／天候 {sky}"
            f"{f'（日照 {sunshine:g} 時間）' if sunshine is not None else ''}／馬場 {track}／"
            f"日の入り {o['日の入り']}\n"
            f"- 学習データ: {past[0].date if past else '–'} 〜 {past[-1].date if past else '–'}"
            f"（{len(past)} レース）\n"
            f"- 出馬表: {source or 'なし（条件から分かる傾向だけを表示）'}\n")
    out = [head, "\n## この条件の傾向\n\n"] + [f"- {line}\n" for line in O.summary_lines(o)]

    wb = X.new_book()
    ws = wb.create_sheet("予想")
    xrow = 1
    ws.cell(row=xrow, column=1, value=head.splitlines()[0].lstrip("# ")).font = X.title_font
    for line in head.splitlines()[1:]:
        if line:
            xrow += 1
            ws.cell(row=xrow, column=1, value=line.lstrip("- ")).font = X.base
    xrow += 2
    trend = []
    for L, v in o["明るさ別"].items():
        trend.append({"明るさ": L, "1番人気の勝率": v["1番人気の勝率"], "1番人気の信頼度 A/E": v["1番人気の信頼度"],
                      **{f"{g}枠 複勝A/E": a for g, a in v["枠 複勝A/E"].items()},
                      **{f"{st} 勝利A/E": a for st, a in v["脚質 勝利A/E"].items()},
                      "好調騎手": "・".join(j["騎手"] for j in v["好調騎手"])})
    doc = W.new_document()
    W.title(doc, head.splitlines()[0].lstrip("# "))
    for line in head.splitlines()[1:]:
        if line:
            W.bullet(doc, line.lstrip("- "))
    W.heading(doc, "この条件の傾向")
    for line in O.summary_lines(o):
        L, text = line.split(": ", 1)
        W.bullet(doc, text, L)
    W.table(doc, trend)
    W.para(doc, "A/E: 1.00が平均（1番人気の信頼度は、いつもの1番人気と比べた値）。1.15以上（緑）は有利、0.85以下（赤）は不利。",
           muted=True)
    xrow = X.table_at(ws, xrow, trend, title="この条件の傾向",
                      note="A/E: 1.00が平均（1番人気の信頼度は、いつもの1番人気と比べた値）。1.15以上（緑）は有利、0.85以下（赤）は不利")

    for race in races:
        rows = predict_race(race, history, w)
        title = f"{race.no}R {race.start} {race.distance or ''}m（{race.light or '時刻不明'}）"
        xrow = X.table_at(ws, xrow, rows, title=title)
        W.heading(doc, title)
        W.table(doc, rows, font_size=9.5)
        out.append(f"\n## {title}\n\n"
                   "| 印 | 馬番 | 馬名 | 騎手 | 脚質 | 勝率 | 3着内率 | 根拠 |\n|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            out.append(f"| {r['印']} | {r['馬番'] or ''} | {r['馬名']} | {r['騎手']} | {r['脚質']} | "
                       f"{r['勝率']:.1%} | {r['3着内率']:.0%} | {r['根拠']} |\n")
    text = "".join(out)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md, xlsx_path, docx_path = (OUT_DIR / f"{date}{ext}" for ext in (".md", ".xlsx", ".docx"))
    md.write_text(text, encoding="utf-8")
    wb.save(xlsx_path)
    doc.save(docx_path)
    return text, md, xlsx_path, docx_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=dt.date.fromisoformat, help="YYYY-MM-DD（省略すると聞く）")
    ap.add_argument("--temp", type=float, help="最高気温 ℃（省略すると聞く）")
    ap.add_argument("--sky", default="", help="晴 / 曇 / 雨")
    ap.add_argument("--sunshine", type=float, help="予報の日照時間（時間）。--sky の代わり")
    ap.add_argument("--track", default="", help="良 / 稍重 / 重 / 不良")
    ap.add_argument("--entries", type=pathlib.Path, help="出馬表 CSV")
    a = ap.parse_args()

    interactive = a.date is None or a.temp is None
    date = a.date or dt.date.fromisoformat(ask("日付 (YYYY-MM-DD)", dt.date.today().isoformat()))
    temp = a.temp if a.temp is not None else float(ask("最高気温 ℃"))
    sky_word = a.sky or ("" if a.sunshine is not None else ask("天気 (晴/曇/雨)", "晴") if interactive else "晴")
    track = a.track or (ask("馬場 (良/稍重/重/不良)", "良") if interactive else "良")
    if track not in C.TRACKS:
        raise SystemExit(f"馬場は {'/'.join(C.TRACKS)} のどれか")
    text, *paths = run(date, temp, sky_word, a.sunshine, track, a.entries)
    print(text)
    print("→ " + ", ".join(str(x.relative_to(D.ROOT)) for x in paths))


if __name__ == "__main__":
    main()
