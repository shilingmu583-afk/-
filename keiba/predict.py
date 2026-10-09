"""その日の大井のレースを、過去1年の条件別成績から予想する。

使い方:
  python3 -m keiba.predict data/keiba/entries/2026-10-09.csv --sunshine 7.5 --track 良
  python3 -m keiba.predict data/keiba/entries/2026-10-09.csv --sky 曇 --track 稍重

出馬表 CSV（1頭 = 1行、UTF-8）の列:
  R, 発走(HH:MM), 距離, 枠, 馬番, 馬名, 騎手  （必須）
  単勝オッズ, 人気                            （任意。あれば精度が上がる。締切前のオッズでよい）
日付はファイル名（YYYY-MM-DD.csv）か --date で指定する。

当日の天候は --sunshine（予報の日照時間）か --sky（晴/曇/雨）、馬場は --track（良/稍重/重/不良）。
省略すると 晴天・良 とみなす。

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

MARKS = "◎○▲△△"
OUT_DIR = D.DATA / "predictions"
MODEL_JSON = D.DATA / "model.json"


def load_entries(path, date: dt.date, sunshine, sky_word, track) -> list[D.Race]:
    races: dict[int, D.Race] = {}
    for row in D.read_csv(path):
        no = int(row["R"])
        race = races.setdefault(no, D.Race(
            date=date, no=no, start=row.get("発走", ""), distance=D._num(row.get("距離"), int),
            track=track, weather=sky_word, sunshine=sunshine))
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entries", type=pathlib.Path, help="出馬表 CSV")
    ap.add_argument("--date", type=dt.date.fromisoformat)
    ap.add_argument("--sunshine", type=float, help="予報の日照時間（時間）")
    ap.add_argument("--sky", default="", help="晴 / 曇 / 雨（--sunshine が無いとき）")
    ap.add_argument("--track", default="良", choices=C.TRACKS)
    a = ap.parse_args()

    date = a.date
    if date is None:
        m = re.search(r"\d{4}-\d{2}-\d{2}", a.entries.name)
        if not m:
            raise SystemExit("日付が分からない。ファイル名を YYYY-MM-DD.csv にするか --date を付ける。")
        date = dt.date.fromisoformat(m.group())
    sky_word = a.sky or ("" if a.sunshine is not None else "晴")

    if not MODEL_JSON.exists():
        raise SystemExit("data/keiba/model.json がない。先に python3 -m keiba.analyze を実行する。")
    w = json.loads(MODEL_JSON.read_text(encoding="utf-8"))["重み"]
    past = [r for r in D.load_races() if r.date < date]
    _, history = M.build_dataset(past)

    races = load_entries(a.entries, date, a.sunshine, sky_word, a.track)
    sky = C.sky(a.sunshine, sky_word)
    head = (f"# 大井競馬 予想 {date}（{'月火水木金土日'[date.weekday()]}）\n\n"
            f"- 条件: {C.season(date)}／天候 {sky}"
            f"{f'（日照 {a.sunshine:g} 時間）' if a.sunshine is not None else ''}／馬場 {a.track}／"
            f"日の入り {C.fmt_minutes(C.sunset_minutes(date))}\n"
            f"- 学習データ: {past[0].date if past else '–'} 〜 {past[-1].date if past else '–'}"
            f"（{len(past)} レース）\n")
    out = [head]
    for race in races:
        rows = predict_race(race, history, w)
        out.append(f"\n## {race.no}R {race.start} {race.distance or ''}m（{race.light or '時刻不明'}）\n\n"
                   "| 印 | 馬番 | 馬名 | 騎手 | 脚質 | 勝率 | 3着内率 | 根拠 |\n|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            out.append(f"| {r['印']} | {r['馬番'] or ''} | {r['馬名']} | {r['騎手']} | {r['脚質']} | "
                       f"{r['勝率']:.1%} | {r['3着内率']:.0%} | {r['根拠']} |\n")
    text = "".join(out)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{date}.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"→ {path.relative_to(D.ROOT)}")


if __name__ == "__main__":
    main()
