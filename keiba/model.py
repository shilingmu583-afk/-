"""条件（四季・明るさ・天候・馬場）ごとの成績を集計し、各馬の勝つ確率を出すモデル。

考え方:
- 枠・脚質・騎手・馬について「その条件での成績が平均よりどれだけ良いか」を A/E（実際÷期待）で測る。
  期待値は頭数で決まる（12頭立てなら勝つ期待は 1/12、3着内は 3/12）。
- 件数が少ない条件は上位の区分（例: 騎手×ナイター×秋 → 騎手全体 → 全体平均）に寄せる（縮小推定）。
- 各 A/E の対数を特徴量にして、レース内で softmax をとる条件付きロジットで重みを学習する。
- 学習用の特徴量は、そのレースの「前日まで」のデータだけで作る（未来の結果を使わない）。
"""
import math
from collections import Counter, defaultdict

from .data import Race, Runner, style_from_corner

FEATURES = ["オッズ", "人気×条件", "枠×条件", "脚質×条件", "騎手×条件", "馬の地力", "馬×条件", "馬×気温"]
K = 3.0  # 縮小推定の強さ（期待値の単位）


class Stats:
    """キー → [勝ち数, 勝ちの期待値, 3着内数, 3着内の期待値, 出走数]"""

    def __init__(self):
        self.c = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0])

    def add(self, key, n, finish):
        s = self.c[key]
        s[0] += finish == 1
        s[1] += 1 / n
        s[2] += finish is not None and finish <= 3
        s[3] += min(3, n) / n
        s[4] += 1

    def ae(self, keys, place=True, k=K):
        """keys を粗い順に並べて渡すと、上位から順に縮小推定した A/E を返す。"""
        a, e = (2, 3) if place else (0, 1)
        prior = 1.0
        for key in keys:
            s = self.c.get(key)
            if s and s[e] > 0:
                prior = (s[a] + k * prior) / (s[e] + k)
        return max(prior, 0.05)

    def raw(self, key):
        return self.c.get(key, [0, 0, 0, 0, 0])


def cond_keys(L, S, Tb, Y, T, gate="", style="", pop="", jockey=""):
    """条件（明るさ L・四季 S・気温 Tb・天候 Y・馬場 T）ごとの集計キー。粗い順に並べる。"""
    return {
        "人気": [("pop", pop), ("pop", pop, L), ("pop", pop, L, S), ("pop", pop, L, S, Tb)],
        "枠": [("gate", gate), ("gate", gate, L), ("gate", gate, L, S), ("gate", gate, L, S, Tb),
               ("gate", gate, L, S, Tb, T)],
        "脚質": [("style", style), ("style", style, L), ("style", style, L, Tb), ("style", style, L, Tb, Y),
                ("style", style, L, Tb, Y, T)],
        "騎手": [("jockey", jockey), ("jockey", jockey, L), ("jockey", jockey, L, S),
                ("jockey", jockey, L, S, Tb)],
    }


def gate_group(g):
    if g is None:
        return ""
    return "内" if g <= 2 else "外" if g >= 7 else "中"


def pop_group(p):
    if p is None:
        return ""
    return str(p) if p <= 5 else "6-8" if p <= 8 else "9-"


class History:
    """過去レースを日付順に積み上げる。features() は積み上げた分だけを使う。"""

    def __init__(self):
        self.s = Stats()
        self.styles = defaultdict(list)  # 馬 → 直近の脚質

    def horse_style(self, horse):
        st = self.styles.get(horse)
        if not st:
            return ""
        return Counter(st[-5:]).most_common(1)[0][0]

    def keys(self, race: Race, r: Runner, style: str):
        L, S = race.light, race.season
        g, p = gate_group(r.gate), pop_group(r.popularity)
        return dict(cond_keys(L, S, race.temp, race.sky, race.track, g, style, p, r.jockey),
                    馬=[("horse", r.horse)],
                    馬条件=[("horse", r.horse), ("horse", r.horse, L), ("horse", r.horse, L, S)],
                    馬気温=[("horse", r.horse), ("horse", r.horse, "気温", race.temp)])

    def features(self, race: Race) -> list[list[float]]:
        n = race.field_size()
        odds = [r.odds for r in race.runners]
        if all(o and o > 1 for o in odds):
            inv = [1 / o for o in odds]
            z = sum(inv)
            mk = [math.log(v / z * n) for v in inv]  # 1/n を基準にした対数
        else:
            mk = [0.0] * n
        rows = []
        for r, m in zip(race.runners, mk):
            ks = self.keys(race, r, self.horse_style(r.horse))
            horse = self.s.ae(ks["馬"])
            rows.append([
                m,
                math.log(self.s.ae(ks["人気"], place=False)) if r.popularity else 0.0,
                math.log(self.s.ae(ks["枠"])) if r.gate else 0.0,
                math.log(self.s.ae(ks["脚質"])) if ks["脚質"][0][1] else 0.0,
                math.log(self.s.ae(ks["騎手"])) if r.jockey else 0.0,
                math.log(horse),
                math.log(self.s.ae(ks["馬条件"]) / horse),
                math.log(self.s.ae(ks["馬気温"]) / horse) if race.temp else 0.0,
            ])
        return rows

    def add(self, race: Race):
        n = race.field_size()
        for r in race.runners:
            style = style_from_corner(r.corner, n)
            keys = {key for group in self.keys(race, r, style).values() for key in group}
            for key in keys:
                if key[1] not in ("", None):
                    self.s.add(key, n, r.finish)
            if style:
                self.styles[r.horse].append(style)


def build_dataset(races: list[Race]):
    """日付順に、前日までのデータで特徴量を作る。[(race, 特徴量, 勝ち馬の index), ...] と History を返す。"""
    h = History()
    out, day, pending = [], None, []
    for race in races:
        if race.date != day:
            for p in pending:
                h.add(p)
            pending, day = [], race.date
        winner = next((i for i, r in enumerate(race.runners) if r.finish == 1), None)
        if winner is not None and race.field_size() >= 2:
            out.append((race, h.features(race), winner))
        pending.append(race)
    for p in pending:
        h.add(p)
    return out, h


def softmax(x, w):
    s = [sum(a * b for a, b in zip(row, w)) for row in x]
    m = max(s)
    e = [math.exp(v - m) for v in s]
    z = sum(e)
    return [v / z for v in e]


def _solve(A, b):
    """連立一次方程式 A x = b（ガウスの消去法、小さな行列用）。"""
    n = len(b)
    m = [row[:] + [v] for row, v in zip(A, b)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        for r in range(n):
            if r != c and m[c][c]:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] if m[i][i] else 0.0 for i in range(n)]


def fit(dataset, iters=25, l2=1.0, use=None):
    """条件付きロジットの重みをニュートン法で学習する。use で使う特徴量の index を絞れる。"""
    nf = len(FEATURES)
    use = sorted(range(nf) if use is None else use)
    w = [0.0] * nf
    if not dataset:
        return w
    for _ in range(iters):
        g = [-l2 * w[j] for j in use]
        H = [[l2 * (a == b) for b in use] for a in use]  # 負のヘッセ行列
        for _race, x, win in dataset:
            p = softmax(x, w)
            mean = [sum(pi * xi[j] for pi, xi in zip(p, x)) for j in use]
            for a, j in enumerate(use):
                g[a] += x[win][j] - mean[a]
            for pi, xi in zip(p, x):
                d = [xi[j] - mu for j, mu in zip(use, mean)]
                for a in range(len(use)):
                    if d[a]:
                        row, da = H[a], pi * d[a]
                        for b in range(len(use)):
                            row[b] += da * d[b]
        step = _solve(H, g)
        for a, j in enumerate(use):
            w[j] += step[a]
        if max(abs(v) for v in step) < 1e-6:
            break
    return w


def evaluate(dataset, w):
    """◎（確率1位）の勝率・複勝率・単勝回収率、1番人気との比較、対数損失。"""
    n = len(dataset)
    if not n:
        return {}
    m = Counter()
    for race, x, win in dataset:
        p = softmax(x, w)
        top = max(range(len(p)), key=p.__getitem__)
        r = race.runners[top]
        m["◎勝ち"] += top == win
        m["◎3着内"] += r.finish is not None and r.finish <= 3
        if r.odds:
            m["◎単勝払戻"] += r.odds * 100 if top == win else 0
            m["◎単勝購入"] += 100
        fav = [i for i, rr in enumerate(race.runners) if rr.popularity == 1]
        if fav:
            m["1番人気レース"] += 1
            m["1番人気勝ち"] += fav[0] == win
        m["logloss"] -= math.log(max(p[win], 1e-9))
        m["均等logloss"] += math.log(len(p))
    out = {
        "レース数": n,
        "◎勝率": m["◎勝ち"] / n,
        "◎複勝率": m["◎3着内"] / n,
        "対数損失": m["logloss"] / n,
        "均等予想の対数損失": m["均等logloss"] / n,
    }
    if m["◎単勝購入"]:
        out["◎単勝回収率"] = m["◎単勝払戻"] / m["◎単勝購入"]
    if m["1番人気レース"]:
        out["1番人気の勝率"] = m["1番人気勝ち"] / m["1番人気レース"]
    return out


def backtest(races: list[Race], test_ratio=0.25):
    """古い 75% で学習し、新しい 25% で検証する。最後に全期間で学習し直した重みも返す。"""
    dataset, history = build_dataset(races)
    cut = int(len(dataset) * (1 - test_ratio))
    train, test = dataset[:cut], dataset[cut:]
    w = fit(train)
    result = {"検証": evaluate(test, w), "学習期間の重み": dict(zip(FEATURES, w))}
    if test:
        result["検証期間"] = [test[0][0].date.isoformat(), test[-1][0].date.isoformat()]
        # 条件（四季・明るさ・天候）を使う特徴量を抜いて「オッズと馬の地力だけ」と比べる
        result["オッズと馬の地力だけで検証"] = evaluate(test, fit(train, use=[0, 5]))
    w_all = fit(dataset)
    return result, w_all, history
