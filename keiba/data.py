"""data/keiba/*.csv の読み書きと、レース単位へのまとめ。

- data/keiba/runners.csv … 出走馬 1頭 = 1行（大井の過去レース結果）
- data/keiba/weather.csv … 1日 = 1行（気象庁 東京の日別値）
"""
import csv
import datetime as dt
import pathlib
from dataclasses import dataclass, field

from . import conditions as C

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "keiba"
RUNNERS_CSV = DATA / "runners.csv"
WEATHER_CSV = DATA / "weather.csv"

RUNNER_COLS = ["日付", "R", "発走", "距離", "馬場", "天候", "着順", "枠", "馬番", "馬名",
               "騎手", "調教師", "斤量", "馬体重", "単勝オッズ", "人気", "通過", "タイム"]
WEATHER_COLS = ["日付", "日照時間", "降水量", "平均気温", "天気概況昼", "天気概況夜"]


def _num(v, cast=float):
    try:
        return cast(str(v).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


@dataclass
class Runner:
    horse: str
    gate: int | None
    number: int | None
    jockey: str = ""
    trainer: str = ""
    odds: float | None = None
    popularity: int | None = None
    finish: int | None = None  # 除外・中止・失格は None
    corner: str = ""


@dataclass
class Race:
    date: dt.date
    no: int
    start: str = ""
    distance: int | None = None
    track: str = ""
    weather: str = ""
    sunshine: float | None = None
    runners: list[Runner] = field(default_factory=list)

    @property
    def season(self):
        return C.season(self.date)

    @property
    def light(self):
        return C.light(self.date, self.start)

    @property
    def sky(self):
        return C.sky(self.sunshine, self.weather)

    @property
    def key(self):
        return (self.date, self.no)

    def field_size(self):
        return len(self.runners)


def style_from_corner(corner: str, field_size: int | None) -> str:
    """最初のコーナーの通過順から脚質（逃げ/先行/差し/追込）。"""
    if not corner:
        return ""
    first = _num(str(corner).replace("－", "-").split("-")[0], int)
    if first is None:
        return ""
    n = field_size or 12
    if first == 1:
        return "逃げ"
    r = first / n
    return "先行" if r <= 0.35 else "差し" if r <= 0.7 else "追込"


def load_weather(path=WEATHER_CSV) -> dict:
    out = {}
    if not pathlib.Path(path).exists():
        return out
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            out[row["日付"]] = row
    return out


def load_races(path=RUNNERS_CSV, weather_path=WEATHER_CSV) -> list[Race]:
    weather = load_weather(weather_path)
    races: dict[tuple, Race] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            d = dt.date.fromisoformat(row["日付"])
            no = int(row["R"])
            race = races.get((d, no))
            if race is None:
                w = weather.get(row["日付"], {})
                race = races[(d, no)] = Race(
                    date=d, no=no, start=row.get("発走", ""), distance=_num(row.get("距離"), int),
                    track=row.get("馬場", ""), weather=row.get("天候", ""),
                    sunshine=_num(w.get("日照時間")))
            race.runners.append(Runner(
                horse=row["馬名"], gate=_num(row.get("枠"), int), number=_num(row.get("馬番"), int),
                jockey=row.get("騎手", ""), trainer=row.get("調教師", ""),
                odds=_num(row.get("単勝オッズ")), popularity=_num(row.get("人気"), int),
                finish=_num(row.get("着順"), int), corner=row.get("通過", "")))
    return sorted(races.values(), key=lambda r: (r.date, r.no))


def write_csv(path, cols, rows):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_csv(path):
    path = pathlib.Path(path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))
