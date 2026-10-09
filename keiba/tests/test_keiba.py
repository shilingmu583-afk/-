"""python3 -m unittest discover -s keiba/tests -t .

実データは使わず、条件で有利・不利が決まる合成レースを作って、集計とモデルがそれを拾えるかを確かめる。
"""
import csv
import datetime as dt
import math
import random
import tempfile
import unittest
from pathlib import Path

from keiba import conditions as C
from keiba import data as D
from keiba import fetch as F
from keiba import model as M
from keiba.predict import top3_probs


def synthetic_max_temp(d):
    """東京の最高気温らしい値（1月下旬が最低 約9℃、8月上旬が最高 約32℃）。"""
    return round(20.5 - 11.5 * math.cos(2 * math.pi * (d.timetuple().tm_yday - 25) / 365), 1)


def synthetic_runners(days=200, seed=1):
    """ナイターでは内枠・逃げ馬が、冬は騎手「冬男」が、暑い日は「夏馬」が強い、という傾向を仕込んだ合成データ。"""
    rnd = random.Random(seed)
    horses = [(f"馬{i}", rnd.gauss(0, 0.6), rnd.choice("逃先差追")) for i in range(300)]
    jockeys = [f"騎手{i}" for i in range(20)] + ["冬男"]
    rows, d = [], dt.date(2025, 10, 1)
    for _ in range(days):
        d += dt.timedelta(days=rnd.choice([1, 1, 2, 5]))
        for no in range(1, 9):
            start = f"{14 + no + (no > 4)}:{rnd.choice(['00', '30'])}"
            race = D.Race(date=d, no=no, start=start, max_temp=synthetic_max_temp(d) + rnd.gauss(0, 2))
            field = rnd.sample(horses, 12)
            ent = []
            for k, (name, ability, st) in enumerate(field):
                gate = min(8, 1 + k * 8 // 12)
                j = rnd.choice(jockeys)
                s = ability + rnd.gauss(0, 1)
                if race.light == "ナイター":
                    s += 0.6 * (gate <= 2) + 0.8 * (st == "逃")
                if j == "冬男" and race.season == "冬":
                    s += 1.2
                if int(name[1:]) % 4 == 0:  # 夏馬
                    s += {"暑い": 1.0, "寒い": -0.6}.get(race.temp, 0)
                ent.append((s, name, gate, k + 1, j, st, ability + rnd.gauss(0, 0.5)))
            pop = {e[1]: i for i, e in enumerate(sorted(ent, key=lambda e: -e[6]), 1)}
            ent.sort(reverse=True)
            for pos, (s, name, gate, num, j, st, _) in enumerate(ent, 1):
                first = {"逃": 1, "先": rnd.randint(2, 4), "差": rnd.randint(5, 8), "追": rnd.randint(9, 12)}[st]
                rows.append({"_最高気温": race.max_temp, "日付": d.isoformat(), "R": no, "発走": start, "距離": 1200, "馬場": "良",
                             "天候": "晴", "着順": pos, "枠": gate, "馬番": num, "馬名": name, "騎手": j, "人気": pop[name],
                             "通過": f"{first}-{first}-{pos}-{pos}"})
    return rows


class ConditionsTest(unittest.TestCase):
    def test_sunset_tokyo(self):
        # 東京の日の入り: 夏至ごろ 19:00 前後、冬至ごろ 16:30 前後
        self.assertAlmostEqual(C.sunset_minutes(dt.date(2026, 6, 21)), 19 * 60 + 1, delta=6)
        self.assertAlmostEqual(C.sunset_minutes(dt.date(2025, 12, 22)), 16 * 60 + 32, delta=6)

    def test_light_and_season(self):
        self.assertEqual(C.light(dt.date(2025, 12, 22), "15:00"), "昼")
        self.assertEqual(C.light(dt.date(2025, 12, 22), "16:40"), "薄暮")
        self.assertEqual(C.light(dt.date(2025, 12, 22), "20:00"), "ナイター")
        self.assertEqual(C.light(dt.date(2026, 6, 21), "18:30"), "薄暮")
        self.assertEqual([C.season(dt.date(2026, m, 1)) for m in (2, 3, 6, 9, 12)],
                         ["冬", "春", "夏", "秋", "冬"])

    def test_sky(self):
        self.assertEqual(C.sky(8.1), "晴天")
        self.assertEqual(C.sky("3.0"), "薄日")
        self.assertEqual(C.sky(0.4, "晴"), "曇雨")
        self.assertEqual(C.sky(None, "小雨"), "曇雨")

    def test_temp_band(self):
        self.assertEqual([C.temp_band(t) for t in (5, 12, 19.9, 20, 28, 35)],
                         ["寒い", "涼しい", "涼しい", "暖かい", "暑い", "暑い"])
        self.assertEqual(C.temp_band(None), "")


class ModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path, wpath = Path(cls.tmp.name) / "runners.csv", Path(cls.tmp.name) / "weather.csv"
        rows = synthetic_runners()
        D.write_csv(path, D.RUNNER_COLS, rows)
        D.write_csv(wpath, D.WEATHER_COLS, [{"日付": r["日付"], "最高気温": f"{r['_最高気温']:.1f}"}
                                            for r in {r["日付"]: r for r in rows}.values()])
        cls.races = D.load_races(path, wpath)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_backtest_learns_condition_effects(self):
        bt, w, history = M.backtest(self.races)
        v = bt["検証"]
        self.assertLess(v["対数損失"], v["均等予想の対数損失"] - 0.05)
        self.assertGreater(v["◎勝率"], 1 / 12 * 2)
        weights = dict(zip(M.FEATURES, w))
        self.assertGreater(weights["枠×条件"], 0.2)
        self.assertGreater(weights["脚質×条件"], 0.2)
        self.assertGreater(weights["騎手×条件"], 0.1)
        self.assertGreater(weights["馬×気温"], 0.2)
        # 条件を使うと、オッズと馬の地力だけより当たる
        self.assertLess(v["対数損失"], bt["オッズと馬の地力だけで検証"]["対数損失"])
        # 仕込んだ傾向: ナイターの逃げは有利、昼の逃げは平均並み
        s = history.s
        self.assertGreater(s.ae([("style", "逃げ", "ナイター")], place=False), 1.5)
        self.assertLess(abs(s.ae([("style", "逃げ", "昼")], place=False) - 1), 0.35)
        self.assertGreater(s.ae([("jockey", "冬男", "ナイター", "冬")]),
                           s.ae([("jockey", "冬男", "ナイター", "秋")]) * 1.3)

    def test_outlook_from_date_and_temperature(self):
        from keiba.outlook import outlook, summary_lines
        _, history = M.build_dataset(self.races)
        o = outlook(history.s, dt.date(2026, 1, 15), 9.0, "晴天", "良")
        self.assertEqual((o["四季"], o["気温"]), ("冬", "寒い"))
        night = o["明るさ別"]["ナイター"]
        self.assertEqual(max(night["脚質 勝利A/E"], key=night["脚質 勝利A/E"].get), "逃げ")
        self.assertEqual(max(night["枠 複勝A/E"], key=night["枠 複勝A/E"].get), "内")
        self.assertEqual(night["好調騎手"][0]["騎手"], "冬男")
        self.assertTrue(0 < night["1番人気の勝率"] < 1)
        self.assertTrue(any(line.startswith("ナイター: ") for line in summary_lines(o)))

    def test_features_use_only_past_days(self):
        dataset, _ = M.build_dataset(self.races)
        first_day = dataset[0][0].date
        for race, x, _w in dataset:
            if race.date != first_day:
                break
            self.assertTrue(all(v == 0 for row in x for v in row))

    def test_analyze_tables(self):
        from keiba.analyze import analyze, render
        res = analyze(self.races)
        light = {r["明るさ"]: r for r in res["条件別の傾向"]["明るさ"]}
        self.assertGreater(light["ナイター"]["逃げ 勝利A/E"], light["昼"]["逃げ 勝利A/E"])
        bt, _, _ = M.backtest(self.races)
        page = render(res, bt, self.races)
        self.assertIn("<title>大井競馬 条件別分析</title>", page)
        from openpyxl import load_workbook
        from keiba.analyze import write_xlsx
        path = Path(self.tmp.name) / "a.xlsx"
        write_xlsx(res, bt, self.races, path)
        wb = load_workbook(path)
        self.assertEqual(wb.sheetnames, ["説明", "条件別の傾向", "好成績騎手", "レース結果"])
        self.assertEqual(wb["レース結果"].max_row, 1 + sum(r.field_size() for r in self.races))

        import docx
        from keiba.analyze import write_docx
        path = Path(self.tmp.name) / "a.docx"
        write_docx(res, bt, self.races, path)
        d = docx.Document(path)
        self.assertEqual(d.paragraphs[0].text, "大井競馬 条件別分析")
        self.assertIn("ナイター", [t.rows[3].cells[0].text for t in d.tables if len(t.rows) > 3])


class PredictTest(unittest.TestCase):
    def test_top3_probs(self):
        p = [0.4, 0.3, 0.2, 0.1]
        p3 = top3_probs(p)
        self.assertAlmostEqual(sum(p3), 3, places=6)
        self.assertEqual(sorted(p3, reverse=True), p3)


class ParseTest(unittest.TestCase):
    def test_parse_result(self):
        page = """<html><body><p>1R 発走時刻 14:50 ダート 1200m 天候：晴 馬場：稍重</p>
        <table><tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>負担重量</th><th>騎手</th>
        <th>調教師</th><th>馬体重</th><th>タイム</th><th>着差</th><th>コーナー通過順</th><th>人気</th></tr>
        <tr><td>1</td><td>3</td><td>4</td><td><a href=#>サンプルホース</a> (牡3)</td><td>56.0</td><td>△山田太郎</td>
        <td>鈴木</td><td>480(+2)</td><td>1:13.2</td><td></td><td>2-2-1-1</td><td>1</td></tr>
        <tr><td>中止</td><td>8</td><td>12</td><td>ゴールデンテスト</td><td>54.0</td><td>佐藤</td>
        <td>田中</td><td>450(0)</td><td></td><td></td><td></td><td>9</td></tr></table></body></html>"""
        rows = F.parse_result(page, dt.date(2026, 1, 5), 1)
        self.assertEqual(len(rows), 2)
        r = rows[0]
        self.assertEqual((r["着順"], r["枠"], r["馬番"], r["馬名"], r["騎手"], r["通過"], r["人気"]),
                         ("1", "3", "4", "サンプルホース", "山田太郎", "2-2-1-1", "1"))
        self.assertEqual((r["発走"], r["距離"], r["馬場"], r["天候"]), ("14:50", "1200", "稍重", "晴"))
        self.assertEqual(rows[1]["着順"], "")

    def test_parse_entries(self):
        page = """<p>3R 発走時刻 15:55 ダート1600m</p><table>
        <tr><th>枠番</th><th>馬番</th><th>馬名</th><th>性齢</th><th>騎手</th><th>人気</th></tr>
        <tr><td>1</td><td>1</td><td>テストワン (牝4)</td><td>牝4</td><td>☆高橋</td><td>2</td></tr>
        <tr><td>2</td><td>2</td><td>テストツー</td><td>牡5</td><td>伊藤</td><td>1</td></tr></table>"""
        rows = F.parse_entries(page, 3)
        self.assertEqual([(r["R"], r["発走"], r["距離"], r["枠"], r["馬番"], r["馬名"], r["騎手"], r["人気"])
                          for r in rows],
                         [(3, "15:55", "1600", "1", "1", "テストワン", "高橋", "2"),
                          (3, "15:55", "1600", "2", "2", "テストツー", "伊藤", "1")])

    def test_parse_jma(self):
        cells = ["5", "1010.1", "1013.2", "0.5", "0.5", "0.5", "12.3", "16.0", "8.1", "60", "40",
                 "2.0", "5.0", "北", "9.0", "北北西", "7.4)", "--", "--", "晴後曇", "曇"]
        page = "<table><tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr></table>"
        rows = F.parse_jma_month(page, 2026, 1)
        self.assertEqual(rows, [{"日付": "2026-01-05", "日照時間": "7.4", "降水量": "0.5",
                                 "平均気温": "12.3", "最高気温": "16.0", "天気概況昼": "晴後曇", "天気概況夜": "曇"}])


if __name__ == "__main__":
    unittest.main()
