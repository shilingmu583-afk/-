"""過去1年の大井のレース結果を、四季・明るさ（昼/薄暮/ナイター）・天候（日照時間）・馬場で分析する。

使い方: python3 -m keiba.analyze
  → data/keiba/model.json（予想に使う重みと検証結果）
  → 大井競馬_条件別分析.html（条件別の傾向レポート）
  → 大井競馬_予想.html（日付と気温を入れると、その日の有利な枠・脚質・騎手が出るページ）
  → 大井競馬_分析データ.xlsx（条件別の傾向・好成績騎手・全レース結果を Excel で）
"""
import datetime as dt
import html
import json
import statistics
from collections import defaultdict

from . import conditions as C
from . import data as D
from . import model as M
from . import outlook as O
from . import xlsx as X

MODEL_JSON = D.DATA / "model.json"
REPORT = D.ROOT / "大井競馬_条件別分析.html"
XLSX = D.ROOT / "大井競馬_分析データ.xlsx"
PREDICTOR = D.ROOT / "大井競馬_予想.html"
PREDICTOR_TEMPLATE = D.ROOT / "keiba" / "predictor_template.html"
STYLES = ("逃げ", "先行", "差し", "追込")
GATES = ("内", "中", "外")


def bucket_table(races, dim_name, key_fn, values):
    """条件の値ごとに、荒れ具合・枠・脚質の傾向をまとめる。"""
    groups = defaultdict(list)
    for r in races:
        groups[key_fn(r)].append(r)
    rows = []
    for v in values:
        rs = groups.get(v, [])
        if not rs:
            continue
        s = M.Stats()
        fav_n = fav_win = fav_p3 = 0
        win_pops, win_odds = [], []
        for race in rs:
            n = race.field_size()
            for r in race.runners:
                s.add(("gate", M.gate_group(r.gate)), n, r.finish)
                s.add(("style", D.style_from_corner(r.corner, n)), n, r.finish)
                if r.popularity == 1:
                    fav_n += 1
                    fav_win += r.finish == 1
                    fav_p3 += r.finish is not None and r.finish <= 3
                if r.finish == 1:
                    if r.popularity:
                        win_pops.append(r.popularity)
                    if r.odds:
                        win_odds.append(r.odds)

        def ae(key, place):
            a, e = (2, 3) if place else (0, 1)
            c = s.raw(key)
            return c[a] / c[e] if c[e] else None

        rows.append({
            dim_name: v, "レース数": len(rs),
            "1番人気勝率": fav_win / fav_n if fav_n else None,
            "1番人気複勝率": fav_p3 / fav_n if fav_n else None,
            "勝ち馬の平均人気": statistics.mean(win_pops) if win_pops else None,
            "勝ち馬単勝の中央値": statistics.median(win_odds) if win_odds else None,
            **{f"{g}枠 複勝A/E": ae(("gate", g), True) for g in GATES},
            **{f"{st} 勝利A/E": ae(("style", st), False) for st in STYLES},
        })
    return rows


def jockey_table(races, dim_name, key_fn, values, min_rides=40, top=5):
    s = M.Stats()
    rides = defaultdict(int)
    for race in races:
        n = race.field_size()
        v = key_fn(race)
        for r in race.runners:
            if r.jockey:
                s.add((r.jockey, v), n, r.finish)
                rides[(r.jockey, v)] += 1
    out = []
    for v in values:
        cand = [(j, s.raw((j, vv))) for (j, vv), c in rides.items() if vv == v and c >= min_rides]
        cand.sort(key=lambda t: -(t[1][2] / t[1][3]))
        for j, c in cand[:top]:
            out.append({dim_name: v, "騎手": j, "騎乗数": rides[(j, v)], "勝利": int(c[0]),
                        "3着内": int(c[2]), "複勝A/E": c[2] / c[3]})
    return out


def analyze(races):
    dims = [
        ("四季", lambda r: r.season, C.SEASONS),
        ("明るさ", lambda r: r.light, C.LIGHTS),
        ("天候", lambda r: r.sky, C.SKIES),
        ("馬場", lambda r: r.track, C.TRACKS),
        ("四季×明るさ", lambda r: f"{r.season}・{r.light}",
         [f"{s}・{l}" for s in C.SEASONS for l in C.LIGHTS]),
    ]
    return {
        "条件別の傾向": {name: bucket_table(races, name, fn, vals) for name, fn, vals in dims},
        "条件別の好成績騎手": {name: jockey_table(races, name, fn, vals) for name, fn, vals in dims[:3]},
    }


# ---------------------------------------------------------------- レポート
def _cell(col, v):
    if v is None:
        return "<td class=n>–</td>"
    if "率" in col:
        return f"<td class=n>{v:.1%}</td>"
    if "A/E" in col:
        cls = "hi" if v >= 1.15 else "lo" if v <= 0.85 else ""
        return f"<td class='n {cls}'>{v:.2f}</td>"
    if isinstance(v, float):
        return f"<td class=n>{v:.1f}</td>"
    if isinstance(v, int):
        return f"<td class=n>{v}</td>"
    return f"<td>{html.escape(str(v))}</td>"


def _table(rows):
    if not rows:
        return "<p class=muted>データなし</p>"
    cols = list(rows[0])
    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(_cell(c, r.get(c)) for c in cols) + "</tr>" for r in rows)
    return f"<div class=scroll><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def render(result, bt, races):
    period = f"{races[0].date} 〜 {races[-1].date}" if races else "–"
    v = bt.get("検証", {})
    base = bt.get("オッズと馬の地力だけで検証", {})
    kpis = [("分析レース数", f"{len(races):,}"), ("◎の勝率（検証）", f"{v.get('◎勝率', 0):.1%}"),
            ("◎の複勝率（検証）", f"{v.get('◎複勝率', 0):.1%}")]
    if "1番人気の勝率" in v:
        kpis.append(("1番人気の勝率（同期間）", f"{v['1番人気の勝率']:.1%}"))
    if "◎単勝回収率" in v:
        kpis.append(("◎単勝回収率（検証）", f"{v['◎単勝回収率']:.0%}"))
    kpi_html = "".join(f"<div class=kpi><div class=kv>{b}</div><div class=kl>{a}</div></div>" for a, b in kpis)
    sec = []
    for name, rows in result["条件別の傾向"].items():
        sec.append(f"<h3>{html.escape(name)}</h3>{_table(rows)}")
    for name, rows in result["条件別の好成績騎手"].items():
        sec.append(f"<h3>{html.escape(name)}別 好成績騎手（40騎乗以上・複勝A/E順）</h3>{_table(rows)}")
    weights = "".join(f"<tr><td>{html.escape(k)}</td><td class=n>{w:+.2f}</td></tr>"
                      for k, w in bt.get("学習期間の重み", {}).items())
    compare = ""
    if base:
        compare = (f"<p>条件の特徴量を使わない場合（オッズと馬の地力だけ）: ◎勝率 {base.get('◎勝率', 0):.1%}、"
                   f"対数損失 {base.get('対数損失', 0):.3f} → 条件込み: ◎勝率 {v.get('◎勝率', 0):.1%}、"
                   f"対数損失 {v.get('対数損失', 0):.3f}（小さいほど良い）</p>")
    return f"""<!doctype html><html lang=ja><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>大井競馬 条件別分析</title>
<style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e4e1d8;--card:#fff;--hi:#1f7a4d;--lo:#b3412e;--acc:#2b5aa8}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#16171a;--fg:#ecebe6;--muted:#9a998f;--line:#2e2f33;--card:#1e1f23;--hi:#5fcf95;--lo:#f08a74;--acc:#86a8ec}}}}
:root[data-theme="dark"]{{--bg:#16171a;--fg:#ecebe6;--muted:#9a998f;--line:#2e2f33;--card:#1e1f23;--hi:#5fcf95;--lo:#f08a74;--acc:#86a8ec}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Hiragino Sans","Noto Sans JP",sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px 64px}}
h1{{font-size:1.6rem;margin:0 0 4px}} h2{{margin-top:40px;font-size:1.2rem;border-bottom:1px solid var(--line);padding-bottom:6px}}
h3{{font-size:1rem;margin:24px 0 8px}} .muted{{color:var(--muted)}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:20px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
.kv{{font-size:1.5rem;font-weight:650;font-variant-numeric:tabular-nums}} .kl{{color:var(--muted);font-size:.85rem}}
.scroll{{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}}
table{{border-collapse:collapse;width:100%;font-size:.88rem}}
th,td{{padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:left}}
th{{color:var(--muted);font-weight:600}} td.n{{text-align:right;font-variant-numeric:tabular-nums}}
td.hi{{color:var(--hi);font-weight:650}} td.lo{{color:var(--lo)}}
</style></head><body><main>
<h1>大井競馬 条件別分析</h1>
<p class=muted>対象期間 {period}／作成 {dt.date.today()}／四季: 春3–5月・夏6–8月・秋9–11月・冬12–2月／
明るさ: 発走が日の入りの60分前より早い=昼、日の入り±60分=薄暮、60分後より遅い=ナイター／
天候: 東京の日照時間 6時間以上=晴天、2–6時間=薄日、2時間未満=曇雨</p>
<div class=kpis>{kpi_html}</div>
<h2>予想モデルの検証</h2>
<p class=muted>古い75%のレースで学習し、新しい25%（{' 〜 '.join(bt.get('検証期間', ['–']))}）で検証。
特徴量はすべて前日までのデータだけで計算しているので、検証期間の成績は「当日に予想していたら」の成績に相当する。</p>
{compare}
<div class=scroll><table><thead><tr><th>特徴量</th><th>重み</th></tr></thead><tbody>{weights}</tbody></table></div>
<h2>条件別の傾向</h2>
<p class=muted>A/E = 実際の数 ÷ 頭数から見た期待値。1.00 が平均、<span style="color:var(--hi)">1.15以上</span>は有利、
<span style="color:var(--lo)">0.85以下</span>は不利。枠は 内=1–2枠・中=3–6枠・外=7–8枠。脚質は1コーナーの通過順から判定。</p>
{''.join(sec)}
</main></body></html>"""


def write_xlsx(result, bt, races, path=XLSX):
    """分析結果と全レース結果を Excel にまとめる。"""
    wb = X.new_book()
    v = bt.get("検証", {})
    base = bt.get("オッズと馬の地力だけで検証", {})
    lines = [
        "大井競馬 条件別分析データ",
        ("対象期間", f"{races[0].date} 〜 {races[-1].date}（{len(races):,} レース）"),
        ("作成日", dt.date.today().isoformat()),
        ("四季", "春=3〜5月、夏=6〜8月、秋=9〜11月、冬=12〜2月"),
        ("明るさ", "発走が日の入りの60分前より早い=昼、日の入り±60分=薄暮、60分後より遅い=ナイター"),
        ("天候", "東京（気象庁）の日照時間 6時間以上=晴天、2〜6時間=薄日、2時間未満=曇雨"),
        ("A/E", "実際の数 ÷ 頭数から見た期待値。1.00が平均、1.15以上（緑）は有利、0.85以下（赤）は不利"),
        ("枠", "内=1〜2枠、中=3〜6枠、外=7〜8枠"),
        ("脚質", "1コーナーの通過順から 逃げ／先行／差し／追込"),
        "",
        ("予想モデルの検証", f"古い75%で学習 → 新しい25%（{' 〜 '.join(bt.get('検証期間', ['–']))}）で検証"),
    ]
    for k in ("レース数", "◎勝率", "◎複勝率", "◎単勝回収率", "1番人気の勝率"):
        if k in v:
            lines.append((k, f"{v[k]:.1%}" if isinstance(v[k], float) else str(v[k])))
    if base:
        lines.append(("条件を使わない場合の◎勝率", f"{base.get('◎勝率', 0):.1%}（オッズと馬の地力だけ）"))
    X.text_sheet(wb.create_sheet("説明"), lines)

    ws = wb.create_sheet("条件別の傾向")
    row = 1
    for name, rows in result["条件別の傾向"].items():
        row = X.table_at(ws, row, rows, title=name)
    ws = wb.create_sheet("好成績騎手")
    row = 1
    for name, rows in result["条件別の好成績騎手"].items():
        row = X.table_at(ws, row, rows, title=f"{name}別（40騎乗以上・複勝A/E順）")

    rows = []
    for race in races:
        n = race.field_size()
        for r in sorted(race.runners, key=lambda r: r.finish or 99):
            rows.append({"日付": race.date.isoformat(), "R": race.no, "発走": race.start, "四季": race.season,
                         "明るさ": race.light, "天候": race.sky, "日照時間": race.sunshine, "馬場": race.track,
                         "距離": race.distance, "着順": r.finish, "枠": r.gate, "馬番": r.number,
                         "馬名": r.horse, "騎手": r.jockey, "人気": r.popularity, "単勝オッズ": r.odds,
                         "脚質": D.style_from_corner(r.corner, n), "通過": r.corner})
    ws = wb.create_sheet("レース結果")
    X.table_at(ws, 1, rows, name="RaceResults")
    ws.freeze_panes = "A2"
    wb.save(path)


def write_predictor(stats: M.Stats, races, path=PREDICTOR):
    """日付と気温を入れて使う予想ページ。条件別の集計値を埋め込み、計算はブラウザで行う。"""
    jockeys = sorted(k[1] for k, c in stats.c.items()
                     if k[0] == "jockey" and len(k) == 2 and c[4] >= O.MIN_RIDES)
    keep = set(jockeys)
    data = {
        "period": f"{races[0].date} 〜 {races[-1].date}" if races else "",
        "races": len(races),
        "jockeys": jockeys,
        "stats": {"|".join(map(str, k)): [round(v, 3) for v in c] for k, c in stats.c.items()
                  if (k[0] in ("gate", "style") and k[1]) or (k[0] == "pop" and k[1] == "1")
                  or (k[0] == "jockey" and k[1] in keep)},
    }
    page = PREDICTOR_TEMPLATE.read_text(encoding="utf-8")
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    path.write_text(page.replace("__DATA__", js), encoding="utf-8")


def main():
    races = D.load_races()
    if not races:
        raise SystemExit(f"{D.RUNNERS_CSV} にデータがない。先に python3 -m keiba.fetch を実行する。")
    bt, w_all, history = M.backtest(races)
    result = analyze(races)
    MODEL_JSON.write_text(json.dumps({
        "作成日": dt.date.today().isoformat(),
        "対象期間": [races[0].date.isoformat(), races[-1].date.isoformat()],
        "特徴量": M.FEATURES, "重み": w_all, "バックテスト": bt, **result,
    }, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    REPORT.write_text(render(result, bt, races), encoding="utf-8")
    write_xlsx(result, bt, races)
    write_predictor(history.s, races)
    v = bt.get("検証", {})
    print(f"{len(races)} レースを分析 → {MODEL_JSON.relative_to(D.ROOT)}, {REPORT.name}, {XLSX.name}, {PREDICTOR.name}")
    for k, val in v.items():
        print(f"  {k}: {val:.3f}" if isinstance(val, float) else f"  {k}: {val}")


if __name__ == "__main__":
    main()
