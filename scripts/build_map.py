"""data/*.json から 交通事故マップ.html を作る。

都道府県の境界は data/japan_prefectures.json（jpn-atlas / 国土地理院「地球地図日本」由来、
d3.geoAzimuthalEqualArea で 850x680 に投影済みの SVG パス）を使う。事故の緯度経度は同じ投影で
SVG 座標に変換する。

使い方: python3 scripts/build_map.py
"""
import json
import math
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "交通事故マップ.html"
TEMPLATE = ROOT / "scripts" / "map_template.html"

# jpn-atlas と同じ投影:
# d3.geoAzimuthalEqualArea().center([138.5, 35.7]).rotate([-138.5, -35.7]).scale(1700).translate([2500, -1930])
_C = 36 - 18 / 60


def _raw(lam, phi):
    k = math.sqrt(2 / (1 + math.cos(lam) * math.cos(phi)))
    return k * math.cos(phi) * math.sin(lam), k * math.sin(phi)


_S = 1700
_CX, _CY = _raw(math.radians(138.5), math.radians(_C))
_DX, _DY = 2500 - _S * _CX, -1930 + _S * _CY


def project(lon, lat):
    lam, phi = math.radians(lon) + math.radians(-138.5), math.radians(lat)
    dphi = math.radians(-_C)
    x, y, z = math.cos(lam) * math.cos(phi), math.sin(lam) * math.cos(phi), math.sin(phi)
    lam, phi = math.atan2(y, x * math.cos(dphi) - z * math.sin(dphi)), math.asin(z * math.cos(dphi) + x * math.sin(dphi))
    px, py = _raw(lam, phi)
    return round(_DX + _S * px, 2), round(_DY - _S * py, 2)


def load(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


acc = sorted(load("accidents.json"), key=lambda a: (a["発生日"], a["都道府県"]))
meta = load("meta.json")
prefs = load("japan_prefectures.json")

points = []
skipped = 0
for n, a in enumerate(acc, 1):
    if a.get("緯度") is None or a.get("経度") is None:
        skipped += 1
        continue
    x, y = project(a["経度"], a["緯度"])
    points.append({
        "no": n, "date": a["発生日"], "pref": a["都道府県"], "city": a["市区町村"], "place": a["場所・道路"],
        "kind": a["事故の種類"], "dead": a["死者数"], "inj": a["負傷者数"], "summary": a["概要"],
        "conf": a["確度"], "src": a["出典"], "url": a["出典URL"], "note": a.get("備考", ""),
        "precision": a.get("位置の精度", ""), "lat": a["緯度"], "lon": a["経度"], "x": x, "y": y,
    })

payload = {"meta": meta, "points": points, "prefs": prefs}
data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
OUT.write_text(html, encoding="utf-8")
print(f"{OUT.name}: {len(points)} 地点" + (f"（緯度経度なし {skipped} 件は地図に出ない）" if skipped else ""))
