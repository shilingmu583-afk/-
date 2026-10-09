"""日付と気温（＋天候・馬場）だけで分かる「その日の傾向」。

出走馬が分からなくても、過去1年の同じ条件（四季・明るさ・気温・天候・馬場）の成績から
どの枠・脚質が有利か、1番人気はどれくらい信頼できるか、どの騎手が好調かを出す。
analyze が作る HTML の予想ページ（大井競馬_予想.html）も、同じ計算を JavaScript で行う。
"""
import datetime as dt

from . import conditions as C
from . import model as M

STYLES = ("逃げ", "先行", "差し", "追込")
GATES = ("内", "中", "外")
MIN_RIDES = 30  # 騎手ランキングに載せる最低騎乗数


def lights_of_season(stats: M.Stats, season: str):
    """その季節に過去レースがあった明るさの区分（無ければ全部）。"""
    found = [L for L in C.LIGHTS if any(stats.c.get(("gate", g, L, season)) for g in GATES)]
    return found or list(C.LIGHTS)


def outlook(stats: M.Stats, date: dt.date, max_temp, sky: str, track: str, lights=None):
    S, Tb = C.season(date), C.temp_band(max_temp)
    out = {"日付": date.isoformat(), "四季": S, "気温": Tb, "天候": sky, "馬場": track,
           "日の入り": C.fmt_minutes(C.sunset_minutes(date)), "明るさ別": {}}
    for L in lights or lights_of_season(stats, S):
        keys = lambda **kw: M.cond_keys(L, S, Tb, sky, track, **kw)  # noqa: E731
        fav = keys(pop="1")["人気"]
        fav_ae = stats.ae(fav, place=False)
        base = stats.raw(("pop", "1"))
        fav_rate = fav_ae * base[1] / base[4] if base[4] else None
        usual = base[0] / base[1] if base[1] else 1.0  # 全体での1番人気の勝利A/E
        jockeys = []
        for key, c in stats.c.items():
            if key[0] == "jockey" and len(key) == 2 and c[4] >= MIN_RIDES:
                jockeys.append((key[1], stats.ae(keys(jockey=key[1])["騎手"]),
                                stats.raw(("jockey", key[1], L, S))[4]))
        jockeys.sort(key=lambda t: -t[1])
        out["明るさ別"][L] = {
            "1番人気の勝率": fav_rate,
            "1番人気の信頼度": fav_ae / usual if usual else 1.0,  # 1.00 = いつも通り
            "枠 複勝A/E": {g: stats.ae(keys(gate=g)["枠"]) for g in GATES},
            "脚質 勝利A/E": {st: stats.ae(keys(style=st)["脚質"], place=False) for st in STYLES},
            "好調騎手": [{"騎手": j, "複勝A/E": ae, "同条件の騎乗数": n} for j, ae, n in jockeys[:5]],
        }
    return out


def summary_lines(o) -> list[str]:
    """outlook() の結果を、人が読む短い文にする。"""
    lines = []
    for L, v in o["明るさ別"].items():
        gate = max(v["枠 複勝A/E"].items(), key=lambda t: t[1])
        style = max(v["脚質 勝利A/E"].items(), key=lambda t: t[1])
        fav = v["1番人気の信頼度"]
        trust = "堅い" if fav >= 1.1 else "荒れやすい" if fav <= 0.9 else "平均的"
        jk = "・".join(j["騎手"] for j in v["好調騎手"][:3]) or "–"
        rate = f"（勝率 {v['1番人気の勝率']:.0%}）" if v["1番人気の勝率"] is not None else ""
        lines.append(f"{L}: 1番人気は{trust}{rate}／{gate[0]}枠が有利（{gate[1]:.2f}）／"
                     f"{style[0]}が有利（{style[1]:.2f}）／好調騎手 {jk}")
    return lines
