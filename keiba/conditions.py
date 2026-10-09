"""レースの「四季」「日照（明るさ）」「天候（日照時間）」の区分を決める。

- 四季: 春(3-5月) 夏(6-8月) 秋(9-11月) 冬(12-2月)
- 明るさ: 発走時刻と大井競馬場の日の入りの差で 昼 / 薄暮 / ナイター
  （大井は通年でトゥインクル（ナイター）開催が多く、日没前後で馬場の見え方・気温が大きく変わる）
- 天候: その日の東京の日照時間（気象庁）で 晴天 / 薄日 / 曇雨。日照時間が無ければ公式の天候（晴・曇・雨…）で決める
"""
import datetime as dt
import math

# 大井競馬場（東京都品川区勝島）
LAT = 35.5925
LON = 139.7425

SEASONS = ("春", "夏", "秋", "冬")
LIGHTS = ("昼", "薄暮", "ナイター")
SKIES = ("晴天", "薄日", "曇雨")
TRACKS = ("良", "稍重", "重", "不良")

# 日の入りの前後この分数の間に発走するレースを「薄暮」とする
TWILIGHT_MIN = 60


def season(d: dt.date) -> str:
    return {12: "冬", 1: "冬", 2: "冬", 3: "春", 4: "春", 5: "春",
            6: "夏", 7: "夏", 8: "夏"}.get(d.month, "秋")


def sunset_minutes(d: dt.date, lat: float = LAT, lon: float = LON) -> float:
    """日の入り時刻（日本時間、0時からの分）。NOAA の近似式で誤差は数分以内。"""
    g = 2 * math.pi / 365 * (d.timetuple().tm_yday - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    phi = math.radians(lat)
    ha = math.degrees(math.acos(math.cos(math.radians(90.833)) / (math.cos(phi) * math.cos(decl))
                                - math.tan(phi) * math.tan(decl)))
    return 720 - 4 * (lon - ha) - eqtime + 9 * 60


def light(d: dt.date, start: str) -> str:
    """発走時刻 'HH:MM' から 昼 / 薄暮 / ナイター を返す。時刻が無ければ空文字。"""
    if not start or ":" not in start:
        return ""
    h, m = start.split(":")[:2]
    diff = int(h) * 60 + int(m) - sunset_minutes(d)
    if diff < -TWILIGHT_MIN:
        return "昼"
    if diff <= TWILIGHT_MIN:
        return "薄暮"
    return "ナイター"


def sky(sunshine_hours, weather: str = "") -> str:
    """日照時間（時間）から 晴天(6h以上) / 薄日(2〜6h) / 曇雨(2h未満)。無ければ公式の天候から推定。"""
    if sunshine_hours not in (None, ""):
        h = float(sunshine_hours)
        return "晴天" if h >= 6 else "薄日" if h >= 2 else "曇雨"
    w = weather or ""
    if "晴" in w:
        return "晴天"
    if "曇" in w:
        return "薄日"
    if any(c in w for c in "雨雪"):
        return "曇雨"
    return ""


def fmt_minutes(m: float) -> str:
    m = round(m)
    return f"{m // 60}:{m % 60:02d}"
