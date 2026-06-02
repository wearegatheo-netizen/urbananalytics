import json
import math
import base64
import csv
import io
import re
import mimetypes
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "dist" / "app"
CADASTRE_SCRIPT = APP_DIR / "run_cadastre_browser_full.ps1"
DEFAULT_HTML = ROOT / "map_bisan_1056_6_satellite.html"
DEFAULT_OUT_DIR = Path.home() / "OneDrive" / "Desktop" / "연속지적"
VWORLD_GEOCODE_URL = "https://api.vworld.kr/req/address"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
ESRI_TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
# OSM 공식 타일서버는 서버측 대량 요청을 차단(Access blocked 타일)하므로
# 일반 지도는 OSM 데이터 기반의 Carto Voyager 타일을 사용한다.
CARTO_VOYAGER_TILE_URL = "https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"
CARTO_LIGHT_NOLABEL_TILE_URL = "https://a.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png"

JOBS = {}


def cors_headers(handler, status=200, content_type="application/json"):
    handler.send_response(status)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    if content_type.startswith("text/") or content_type == "application/json":
        content_type = f"{content_type}; charset=utf-8"
    handler.send_header("Content-Type", content_type)


def json_response(handler, payload, status=200):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    cors_headers(handler, status=status)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def http_get_json(url, params):
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8", errors="replace"))


def geocode(address, api_key):
    normalized = address.replace(" ", "")
    if "비산동1056" in normalized or "관악대로254" in normalized:
        return 37.401514, 126.9513167, "경기 안양시 동안구 관악대로 254 일원"
    if "서울송파구중대로113" in normalized or "송파구중대로113" in normalized:
        return 37.4939, 127.1198, "서울특별시 송파구 중대로 113"

    if api_key:
        last_error = None
        for addr_type in ("ROAD", "PARCEL"):
            params = {
                "service": "address",
                "request": "getcoord",
                "version": "2.0",
                "crs": "epsg:4326",
                "refine": "true",
                "simple": "false",
                "format": "json",
                "type": addr_type,
                "address": address,
                "key": api_key,
            }
            data = http_get_json(VWORLD_GEOCODE_URL, params)
            response = data.get("response", {})
            if response.get("status") == "OK":
                result = response["result"]
                point = result["point"]
                refined = result.get("refined", {}).get("text") or address
                return float(point["y"]), float(point["x"]), refined
            last_error = response
        raise ValueError(f"VWorld 주소 변환 실패: {last_error}")

    params = {"q": address, "format": "jsonv2", "limit": "1", "countrycodes": "kr"}
    req = urllib.request.Request(
        f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "CadastreMapBridge/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        data = json.loads(res.read().decode("utf-8", errors="replace"))
    if data:
        result = data[0]
        return float(result["lat"]), float(result["lon"]), result.get("display_name") or address
    raise ValueError("주소를 좌표로 변환하지 못했습니다. VWorld API 키를 입력하거나 도로명 주소로 다시 시도해 주세요.")


def reverse_geocode_parcel(lat, lon, api_key):
    if not api_key:
        raise ValueError("지도에서 지번을 선택하려면 VWorld API 키가 필요합니다.")
    params = {
        "service": "address",
        "request": "getAddress",
        "version": "2.0",
        "crs": "epsg:4326",
        "point": f"{lon},{lat}",
        "format": "json",
        "type": "PARCEL",
        "key": api_key,
    }
    data = http_get_json(VWORLD_GEOCODE_URL, params)
    response = data.get("response", {})
    if response.get("status") != "OK":
        raise ValueError(f"VWorld 지번 조회 실패: {response}")
    result = response.get("result", [])
    if isinstance(result, dict):
        result = [result]
    for item in result:
        text = item.get("text") or item.get("address", {}).get("parcel") or item.get("address", {}).get("text")
        if text:
            return text
    raise ValueError("선택한 위치의 지번을 찾지 못했습니다.")


def point_in_ring(lon, lat, ring):
    inside = False
    count = len(ring)
    if count < 4:
        return False
    j = count - 1
    for i in range(count):
        xi, yi = float(ring[i][0]), float(ring[i][1])
        xj, yj = float(ring[j][0]), float(ring[j][1])
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def feature_contains_point(feature, lon, lat):
    # 외곽링/홀을 even-odd 규칙으로 함께 판정(홀 안의 점은 제외)
    inside = False
    for ring in geometry_rings(feature.get("geometry") or {}):
        if point_in_ring(lon, lat, ring):
            inside = not inside
    return inside


def feature_max_ring_area(feature):
    areas = [
        abs(projected_polygon_area(project_ring(ring)))
        for ring in geometry_rings(feature.get("geometry") or {})
        if len(ring) >= 4
    ]
    return max(areas) if areas else 0.0


def feature_centroid_distance(feature, lon, lat):
    points = [pt for ring in geometry_rings(feature.get("geometry") or {}) for pt in ring]
    if not points:
        return float("inf")
    cx = sum(float(pt[0]) for pt in points) / len(points)
    cy = sum(float(pt[1]) for pt in points) / len(points)
    return (cx - lon) ** 2 + (cy - lat) ** 2


def get_parcel_feature(lat, lon, api_key):
    if not api_key:
        raise ValueError("지적도 도형 조회에는 VWorld API 키가 필요합니다.")
    last_error = None
    candidates = []
    # 본번/부번 두 레이어에서 클릭 지점 주변 필지들을 모은 뒤, 점을 '포함'하는 필지를 고른다.
    # (예전 방식은 MAXFEATURES=1 + 넓은 BBOX의 첫 피처를 써서 인접한 큰 도형(가구)을 잡았다.)
    for typename in ("lp_pa_cbnd_bubun", "lp_pa_cbnd_bonbun"):
        for delta in (0.00012, 0.0004):
            params = {
                "REQUEST": "GetFeature",
                "TYPENAME": typename,
                "VERSION": "1.1.0",
                "MAXFEATURES": "100",
                "SRSNAME": "EPSG:4326",
                "OUTPUT": "json",
                "BBOX": f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}",
                "KEY": api_key,
            }
            try:
                data = http_get_json("https://api.vworld.kr/req/wfs", params)
            except Exception as exc:
                last_error = exc
                continue
            features = data.get("features") or []
            if not features:
                last_error = data
                continue
            containing = [f for f in features if feature_contains_point(f, lon, lat)]
            if containing:
                # 점을 포함하는 필지 중 가장 작은(=가장 구체적인) 필지를 선택
                best = min(containing, key=feature_max_ring_area)
                return {"type": "FeatureCollection", "features": [best]}
            candidates.extend(features)
            break  # 이 레이어에서 피처는 받았으므로(포함 필지는 없음) 다음 레이어로
    if candidates:
        # 포함 필지를 못 찾으면 클릭점에서 중심이 가장 가까운 필지로 대체
        best = min(candidates, key=lambda f: feature_centroid_distance(f, lon, lat))
        return {"type": "FeatureCollection", "features": [best]}
    raise ValueError(f"선택한 위치의 지적도 도형을 찾지 못했습니다: {last_error}")


def geometry_rings(geometry):
    coords = (geometry or {}).get("coordinates") or []
    geom_type = (geometry or {}).get("type")
    if geom_type == "Polygon":
        return coords
    if geom_type == "MultiPolygon":
        return [ring for polygon in coords for ring in polygon]
    return []


def project_point(lon, lat, origin_lat):
    meters_per_lat = 111320.0
    meters_per_lon = 111320.0 * math.cos(math.radians(origin_lat))
    return lon * meters_per_lon, lat * meters_per_lat


def project_ring(ring):
    origin_lat = sum(float(pt[1]) for pt in ring) / len(ring)
    return [project_point(float(pt[0]), float(pt[1]), origin_lat) for pt in ring]


def projected_polygon_area(points):
    area = 0.0
    for idx, point in enumerate(points):
        nxt = points[(idx + 1) % len(points)]
        area += point[0] * nxt[1] - nxt[0] * point[1]
    return area / 2.0


def primary_ring(feature):
    rings = geometry_rings(feature.get("geometry") or {})
    rings = [ring for ring in rings if len(ring) >= 4]
    if not rings:
        raise ValueError("필지 도형 좌표를 읽지 못했습니다.")
    return max(rings, key=lambda ring: abs(projected_polygon_area(project_ring(ring))))


def lonlat_centroid(ring):
    return sum(float(pt[1]) for pt in ring) / len(ring), sum(float(pt[0]) for pt in ring) / len(ring)


def polygon_segments(points):
    return [(points[idx], points[(idx + 1) % len(points)]) for idx in range(len(points) - 1)]


def segment_length(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def point_segment_distance(point, a, b):
    px, py = point
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def segment_distance(a1, a2, b1, b2):
    return min(
        point_segment_distance(a1, b1, b2),
        point_segment_distance(a2, b1, b2),
        point_segment_distance(b1, a1, a2),
        point_segment_distance(b2, a1, a2),
    )


def parse_width_tag(value):
    if value is None:
        return None
    match = re.search(r"\d+(?:[\.,]\d+)?", str(value))
    return float(match.group(0).replace(",", ".")) if match else None


def estimate_road_width(tags):
    width = parse_width_tag(tags.get("width"))
    if width:
        return width
    lanes = parse_width_tag(tags.get("lanes"))
    if lanes:
        return max(4.0, lanes * 3.2)
    defaults = {
        "motorway": 28.0,
        "trunk": 24.0,
        "primary": 20.0,
        "secondary": 15.0,
        "tertiary": 12.0,
        "unclassified": 8.0,
        "residential": 8.0,
        "service": 6.0,
        "living_street": 6.0,
        "footway": 4.0,
    }
    return defaults.get(tags.get("highway"), 8.0)


def osm_roads_near(lat, lon, radius=120):
    query = f"""
    [out:json][timeout:15];
    way(around:{int(radius)},{lat},{lon})["highway"];
    (._;>;);
    out body;
    """
    req = urllib.request.Request(
        OVERPASS_URL,
        data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
        headers={"User-Agent": "CadastreMapBridge/1.0"},
    )
    with urllib.request.urlopen(req, timeout=25) as res:
        data = json.loads(res.read().decode("utf-8", errors="replace"))
    nodes = {item["id"]: (float(item["lat"]), float(item["lon"])) for item in data.get("elements", []) if item.get("type") == "node"}
    roads = []
    for item in data.get("elements", []):
        tags = item.get("tags", {})
        if item.get("type") != "way" or "highway" not in tags:
            continue
        coords = [nodes[node_id] for node_id in item.get("nodes", []) if node_id in nodes]
        if len(coords) < 2:
            continue
        roads.append(
            {
                "name": tags.get("name") or tags.get("name:ko") or tags.get("highway") or "road",
                "highway": tags.get("highway"),
                "width": estimate_road_width(tags),
                "coords": coords,
            }
        )
    return roads


def analyze_roads_for_ring(ring, projected, area):
    lat, lon = lonlat_centroid(ring)
    parcel_segments = polygon_segments(projected)
    touching = []
    try:
        roads = osm_roads_near(lat, lon)
    except Exception:
        roads = []
    for road in roads:
        road_points = [project_point(coord[1], coord[0], lat) for coord in road["coords"]]
        min_dist = None
        frontage = 0.0
        for idx in range(len(road_points) - 1):
            r1, r2 = road_points[idx], road_points[idx + 1]
            for p1, p2 in parcel_segments:
                dist = segment_distance(p1, p2, r1, r2)
                min_dist = dist if min_dist is None else min(min_dist, dist)
                if dist <= 8.0:
                    frontage += segment_length(p1, p2)
        if min_dist is not None and min_dist <= 12.0:
            item = dict(road)
            item["distance"] = min_dist
            item["frontage"] = frontage
            touching.append(item)
    touching.sort(key=lambda road: (road["distance"], -road["frontage"]))
    if touching:
        main = touching[0]
        return {
            "roadName": main["name"],
            "roadWidth": main["width"],
            "frontage": max(main["frontage"], math.sqrt(area)),
            "touchingRoads": touching[:3],
        }
    return {
        "roadName": "접도 도로 자동판정 필요",
        "roadWidth": None,
        "frontage": math.sqrt(area),
        "touchingRoads": [],
    }


def coverage_source(payload):
    district = parse_float(payload.get("districtPlanCoverage"), None)
    ordinance = parse_float(payload.get("ordinanceCoverage"), None)
    default = parse_float(payload.get("coverageRatio"), 60.0)
    if district:
        return district, "지구단위계획 확인값"
    if ordinance:
        return ordinance, "조례 확인값"
    return default, "기본값"


# 국토계획법 시행령 제84조 용도지역별 건폐율 상한(%) — 조례 조회 실패 시 폴백
COVERAGE_CAP = [
    ("제1종전용주거", 50), ("제2종전용주거", 50),
    ("제1종일반주거", 60), ("제2종일반주거", 60), ("제3종일반주거", 50),
    ("준주거", 70),
    ("중심상업", 90), ("일반상업", 80), ("근린상업", 70), ("유통상업", 80),
    ("전용공업", 70), ("일반공업", 70), ("준공업", 70),
    ("보전녹지", 20), ("생산녹지", 20), ("자연녹지", 20),
    ("보전관리", 20), ("생산관리", 20), ("계획관리", 40),
    ("농림", 20), ("자연환경보전", 20),
]


def coverage_cap_for(zone_name):
    name = str(zone_name or "").replace(" ", "")
    for key, pct in COVERAGE_CAP:
        if key in name:
            return float(pct)
    return None


def get_landuse(lat, lon, api_key):
    """VWorld 용도지역(lt_c_uq111)에서 점이 포함된 용도지역과 지자체를 반환."""
    if not api_key:
        return None
    delta = 0.00015
    params = {
        "REQUEST": "GetFeature", "TYPENAME": "lt_c_uq111", "VERSION": "1.1.0",
        "MAXFEATURES": "30", "SRSNAME": "EPSG:4326", "OUTPUT": "json",
        "BBOX": f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}", "KEY": api_key,
    }
    try:
        data = http_get_json("https://api.vworld.kr/req/wfs", params)
    except Exception:
        return None
    feats = data.get("features") or []
    if not feats:
        return None
    chosen = next((f for f in feats if feature_contains_point(f, lon, lat)), feats[0])
    props = chosen.get("properties") or {}
    return {
        "zone": props.get("uname"),
        "sido": props.get("sido_name"),
        "sigungu": props.get("sigg_name"),
        "sggcd": props.get("std_sggcd"),
    }


_ORDINANCE_CACHE = {}


def fetch_ordinance_coverage(sigungu, zone_name, oc):
    """법제처 자치법규 API로 해당 지자체 도시계획조례의 용도지역별 건폐율(%)을 조회."""
    if not oc or not sigungu or not zone_name:
        return None
    cache_key = (sigungu, zone_name)
    if cache_key in _ORDINANCE_CACHE:
        return _ORDINANCE_CACHE[cache_key]
    result = None
    try:
        # 1) 도시계획조례 검색 → 자치법규 일련번호(MST)
        search = http_get_json("https://www.law.go.kr/DRF/lawSearch.do", {
            "OC": oc, "target": "ordin", "type": "JSON",
            "query": f"{sigungu} 도시계획 조례", "display": "20",
        })
        block = search.get("OrdinSearch") or search.get("LawSearch") or {}
        laws = block.get("law") or block.get("ordin") or []
        if isinstance(laws, dict):
            laws = [laws]
        mst = None
        for law in laws:
            name = str(law.get("자치법규명") or law.get("법령명한글") or law.get("법령명") or "")
            if "도시계획" in name and "조례" in name and "시행" not in name:
                mst = law.get("자치법규일련번호") or law.get("MST") or law.get("법령일련번호") or law.get("ID")
                break
        if mst:
            # 2) 조문 본문 → 건폐율 조문에서 용도지역별 퍼센트 파싱
            body = http_get_json("https://www.law.go.kr/DRF/lawService.do", {
                "OC": oc, "target": "ordin", "type": "JSON", "MST": str(mst),
            })
            text = json.dumps(body, ensure_ascii=False)
            zone_key = str(zone_name).replace(" ", "")
            # "제2종일반주거지역 ... 60퍼센트/60%" 형태에서 가장 가까운 퍼센트 추출
            for m in re.finditer(re.escape(zone_key[:7]), text):
                seg = text[m.start(): m.start() + 80]
                pm = re.search(r"(\d{1,3})\s*(?:퍼센트|%)", seg)
                if pm:
                    result = float(pm.group(1))
                    break
    except Exception:
        result = None
    _ORDINANCE_CACHE[cache_key] = result
    return result


def ring_perimeter(points):
    total = 0.0
    for idx in range(len(points)):
        total += segment_length(points[idx], points[(idx + 1) % len(points)])
    return total


def estimate_polygon_width(ring):
    """긴 띠 형태 도로 필지의 대략 폭 ≈ 2*면적/둘레."""
    projected = project_ring(ring)
    area = abs(projected_polygon_area(projected))
    per = ring_perimeter(projected)
    if per <= 0:
        return None
    return max(1.0, 2.0 * area / per)


def jimok_road_width(subject_ring, lat, lon, api_key):
    """인접한 지목='도로'(지번 끝 글자 '도') 필지의 폭을 측정."""
    if not api_key:
        return None
    delta = 0.0004
    best = None
    subject_proj = project_ring(subject_ring)
    subject_segments = polygon_segments(subject_proj)
    for typename in ("lp_pa_cbnd_bubun", "lp_pa_cbnd_bonbun"):
        params = {
            "REQUEST": "GetFeature", "TYPENAME": typename, "VERSION": "1.1.0",
            "MAXFEATURES": "60", "SRSNAME": "EPSG:4326", "OUTPUT": "json",
            "BBOX": f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}", "KEY": api_key,
        }
        try:
            data = http_get_json("https://api.vworld.kr/req/wfs", params)
        except Exception:
            continue
        for feature in data.get("features") or []:
            jibun = str((feature.get("properties") or {}).get("jibun") or "")
            if not jibun.endswith("도"):
                continue
            rings = [r for r in geometry_rings(feature.get("geometry") or {}) if len(r) >= 4]
            if not rings:
                continue
            road_ring = max(rings, key=lambda r: abs(projected_polygon_area(project_ring(r))))
            road_proj = [project_point(float(p[0]), float(p[1]), lat) for p in road_ring]
            road_segments = polygon_segments(road_proj)
            min_dist = min(
                (segment_distance(a1, a2, b1, b2) for a1, a2 in subject_segments for b1, b2 in road_segments),
                default=None,
            )
            if min_dist is None or min_dist > 6.0:
                continue
            width = estimate_polygon_width(road_ring)
            if width and (best is None or min_dist < best[1]):
                best = (width, min_dist, jibun)
    if best:
        return {"width": round(best[0], 2), "name": f"지목도로({best[2]})", "source": "지목 도로"}
    return None


def upis_road_width(lat, lon, api_key):
    """도시계획시설(lt_c_upisuq153)에서 인접 계획도로의 폭을 산정."""
    if not api_key:
        return None
    delta = 0.0004
    params = {
        "REQUEST": "GetFeature", "TYPENAME": "lt_c_upisuq153", "VERSION": "1.1.0",
        "MAXFEATURES": "40", "SRSNAME": "EPSG:4326", "OUTPUT": "json",
        "BBOX": f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}", "KEY": api_key,
    }
    try:
        data = http_get_json("https://api.vworld.kr/req/wfs", params)
    except Exception:
        return None
    best = None
    for feature in data.get("features") or []:
        props = feature.get("properties") or {}
        names = f"{props.get('lcl_nam','')}{props.get('mls_nam','')}{props.get('dgm_nm','')}"
        if "도로" not in names:
            continue
        area = parse_float(props.get("dgm_ar"))
        length = parse_float(props.get("dgm_lt"))
        width = None
        if area and length and length > 0:
            width = area / length
        else:
            rings = [r for r in geometry_rings(feature.get("geometry") or {}) if len(r) >= 4]
            if rings:
                width = estimate_polygon_width(max(rings, key=lambda r: abs(projected_polygon_area(project_ring(r)))))
        grade = props.get("mls_nam") or props.get("dgm_nm") or "계획도로"
        if width and (best is None or width < best[0]):
            best = (width, grade)
    if best:
        return {"width": round(best[0], 2), "name": f"도시계획도로({best[1]})", "source": "도시계획시설"}
    return None


def resolve_road_width(ring, projected, area, lat, lon, api_key, osm_info, fallback):
    """지목도로·도시계획도로·OSM 세 방법을 모두 산정해 최적값을 채택."""
    candidates = []
    try:
        jimok = jimok_road_width(ring, lat, lon, api_key)
        if jimok:
            candidates.append(jimok)
    except Exception:
        pass
    try:
        upis = upis_road_width(lat, lon, api_key)
        if upis:
            candidates.append(upis)
    except Exception:
        pass
    if osm_info.get("roadWidth"):
        candidates.append({"width": round(osm_info["roadWidth"], 2),
                           "name": osm_info.get("roadName") or "OSM 도로", "source": "OSM"})
    # 채택 우선순위: 지목 도로(실측) > 도시계획시설(계획) > OSM
    priority = {"지목 도로": 0, "도시계획시설": 1, "OSM": 2}
    candidates.sort(key=lambda c: priority.get(c["source"], 9))
    if candidates:
        chosen = candidates[0]
        detail = ", ".join(f"{c['source']}={c['width']}m" for c in candidates)
        return chosen["width"], chosen["name"], chosen["source"], detail
    return fallback, "접도 도로 자동판정 필요", "기본값", f"기본값={fallback}m"


def analyze_selected_parcels(payload):
    api_key = str(payload.get("apiKey") or "").strip()
    if not api_key:
        raise ValueError("선택필지 자동분석에는 VWorld API 키가 필요합니다.")
    parcel_items = payload.get("parcelListItems") or []
    if not parcel_items:
        raise ValueError("지도에서 필지를 먼저 선택해 주세요.")
    slope_multiplier = parse_float(payload.get("slopeMultiplier"), 1.5)
    fallback_road_width = 8.0
    review_note = str(payload.get("reviewNote") or "").strip()
    law_oc = str(payload.get("lawApiKey") or "").strip()
    # 수동 입력값(있으면 우선)
    manual_district = parse_float(payload.get("districtPlanCoverage"), None)
    manual_ordinance = parse_float(payload.get("ordinanceCoverage"), None)
    rows = []
    total_floor_area = 0.0
    total_volume = 0.0
    for item in parcel_items:
        lat = float(item.get("lat"))
        lon = float(item.get("lon"))
        parcel = str(item.get("parcel") or "").strip() or reverse_geocode_parcel(lat, lon, api_key)
        collection = get_parcel_feature(lat, lon, api_key)
        feature = collection["features"][0]
        ring = primary_ring(feature)
        projected = project_ring(ring)
        area = abs(projected_polygon_area(projected))

        # 용도지역 자동판별
        landuse = get_landuse(lat, lon, api_key) or {}
        zone = landuse.get("zone")
        sigungu = landuse.get("sigungu")

        # 건폐율: 지구단위계획(입력) > 조례(입력 또는 법제처 자동) > 시행령 상한 > 없음
        coverage = None
        cov_source = "확인 필요"
        if manual_district:
            coverage, cov_source = manual_district, "지구단위계획(입력)"
        elif manual_ordinance:
            coverage, cov_source = manual_ordinance, "조례(입력)"
        else:
            ord_auto = fetch_ordinance_coverage(sigungu, zone, law_oc) if law_oc else None
            if ord_auto:
                coverage, cov_source = ord_auto, f"조례(법제처·{zone})"
            else:
                cap = coverage_cap_for(zone)
                if cap:
                    coverage, cov_source = cap, f"시행령 상한({zone})"
        if not coverage:
            coverage, cov_source = 60.0, "확인 필요(기본 60% 가정)"

        # 도로폭: 지목도로 + 도시계획시설 + OSM 모두 산정 후 채택
        osm_info = analyze_roads_for_ring(ring, projected, area)
        road_width, road_name, road_source, road_detail = resolve_road_width(
            ring, projected, area, lat, lon, api_key, osm_info, fallback_road_width
        )
        frontage = osm_info["frontage"] or math.sqrt(area)
        depth = area / frontage if frontage else math.sqrt(area)
        coverage_ratio = coverage / 100.0 if coverage > 1 else coverage
        floor_area = area * coverage_ratio
        min_height = slope_multiplier * road_width
        max_height = slope_multiplier * (road_width + depth)
        avg_height = (min_height + max_height) / 2.0
        volume = floor_area * avg_height
        total_floor_area += floor_area
        total_volume += volume
        cov_ok = cov_source.startswith(("지구단위", "조례", "시행령"))
        confidence = "상" if (road_source in ("지목 도로", "도시계획시설") and cov_ok) else "중" if cov_ok else "하"
        rows.append(
            {
                "parcel": parcel,
                "zone": zone or "-",
                "area": round(area, 3),
                "coverageRatio": round(coverage_ratio * 100, 3),
                "coverageSource": cov_source,
                "floorArea": round(floor_area, 3),
                "roadName": road_name,
                "roadWidth": round(road_width, 3),
                "roadSource": road_source,
                "roadDetail": road_detail,
                "frontage": round(frontage, 3),
                "depth": round(depth, 3),
                "minHeight": round(min_height, 3),
                "maxHeight": round(max_height, 3),
                "avgHeight": round(avg_height, 3),
                "volume": round(volume, 3),
                "method": "선택필지 자동분석",
                "confidence": confidence,
                "note": review_note,
            }
        )
    if total_floor_area <= 0:
        raise ValueError("건폐율 적용 바닥면적 합계가 0입니다.")
    return {
        "rows": rows,
        "totals": {
            "parcelCount": len(rows),
            "floorArea": round(total_floor_area, 3),
            "volume": round(total_volume, 3),
            "blockAverageHeight": round(total_volume / total_floor_area, 3),
        },
    }


def facility_category(tags):
    railway = tags.get("railway")
    station = tags.get("station")
    amenity = tags.get("amenity")
    highway = tags.get("highway")
    leisure = tags.get("leisure")
    tourism = tags.get("tourism")
    office = tags.get("office")
    building = tags.get("building")
    landuse = tags.get("landuse")
    name = tags.get("name") or tags.get("name:ko") or ""

    if railway in {"station", "halt", "subway_entrance"} or station == "subway":
        return "교통"
    if highway == "bus_station" or amenity == "bus_station":
        return "교통"
    if amenity in {"townhall", "courthouse", "police", "fire_station", "post_office"} or office == "government":
        return "공공"
    if amenity == "kindergarten":
        return None
    if amenity in {"school", "university", "college"}:
        return "교육"
    if amenity == "hospital":
        return "의료"
    if building in {"apartments", "residential"} or landuse == "residential":
        return "주거"
    if "아파트" in name or "주공" in name or "래미안" in name or "자이" in name:
        return "주거"
    if leisure in {"stadium", "sports_centre", "park"} or tourism in {"attraction", "museum"}:
        return "생활"
    if "시청" in name or "구청" in name or "경찰" in name or "소방" in name:
        return "공공"
    if "역" in name or "정류장" in name:
        return "교통"
    return None


def allowed_facility(name, category):
    if category == "교육" and "유치원" in name:
        return False
    return True


def get_context_facilities(lat, lon, radius=2500):
    curated = curated_facilities(lat, lon, radius)
    query = f"""
    [out:json][timeout:20];
    (
      node(around:{int(radius)},{lat},{lon})["railway"~"station|halt|subway_entrance"];
      node(around:{int(radius)},{lat},{lon})["station"="subway"];
      node(around:{int(radius)},{lat},{lon})["highway"="bus_station"];
      node(around:{int(radius)},{lat},{lon})["amenity"~"bus_station|townhall|courthouse|police|fire_station|post_office|school|university|college|hospital"];
      node(around:{int(radius)},{lat},{lon})["leisure"~"stadium|sports_centre|park"];
      way(around:{int(radius)},{lat},{lon})["amenity"~"townhall|courthouse|police|fire_station|school|university|hospital"];
      way(around:{int(radius)},{lat},{lon})["leisure"~"stadium|sports_centre|park"];
      way(around:{int(radius)},{lat},{lon})["building"~"apartments|residential"];
      way(around:{int(radius)},{lat},{lon})["landuse"="residential"];
      relation(around:{int(radius)},{lat},{lon})["amenity"~"townhall|courthouse|police|fire_station|school|university|hospital"];
      relation(around:{int(radius)},{lat},{lon})["leisure"~"stadium|sports_centre|park"];
      relation(around:{int(radius)},{lat},{lon})["building"~"apartments|residential"];
      relation(around:{int(radius)},{lat},{lon})["landuse"="residential"];
    );
    out center tags 160;
    """
    req = urllib.request.Request(
        OVERPASS_URL,
        data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
        headers={"User-Agent": "CadastreMapBridge/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            data = json.loads(res.read().decode("utf-8", errors="replace"))
    except Exception:
        return curated

    facilities = list(curated)
    seen = set()
    for item in facilities:
        seen.add((item["name"], round(float(item["lat"]), 5), round(float(item["lon"]), 5)))
    for element in data.get("elements", []):
        tags = element.get("tags", {})
        name = tags.get("name:ko") or tags.get("name")
        if not name:
            continue
        item_lat = element.get("lat") or element.get("center", {}).get("lat")
        item_lon = element.get("lon") or element.get("center", {}).get("lon")
        if item_lat is None or item_lon is None:
            continue
        category = facility_category(tags)
        if not category:
            continue
        if not allowed_facility(name, category):
            continue
        key = (name, round(float(item_lat), 5), round(float(item_lon), 5))
        if key in seen:
            continue
        seen.add(key)
        distance = haversine_m(lat, lon, float(item_lat), float(item_lon))
        facilities.append(
            {
                "name": name,
                "category": category,
                "lat": float(item_lat),
                "lon": float(item_lon),
                "distance": round(distance),
            }
        )

    facilities.sort(key=lambda item: (category_rank(item["category"]), item["distance"]))
    facilities = dedupe_facilities(facilities)
    caps = {"교통": 10, "공공": 8, "생활": 8, "교육": 8, "의료": 6, "주거": 18}
    counts = {}
    selected = []
    for item in facilities:
        category = item["category"]
        counts[category] = counts.get(category, 0)
        if counts[category] >= caps.get(category, 3):
            continue
        selected.append(item)
        counts[category] += 1
        if len(selected) >= 58:
            break
    return selected


def canonical_facility_name(name, category):
    cleaned = clean_facility_name(name, category)
    if category == "교통":
        if "정류장" not in cleaned and "터미널" not in cleaned and not cleaned.endswith("역"):
            cleaned = f"{cleaned}역"
        cleaned = re.sub(r"(역|驛)$", "역", cleaned)
        cleaned = re.sub(r"(역)(역)$", "역", cleaned)
    return cleaned


def dedupe_facilities(facilities):
    result = []
    seen = set()
    for item in facilities:
        canonical = canonical_facility_name(item["name"], item["category"])
        if not canonical:
            continue
        key = (item["category"], canonical)
        if key in seen:
            continue
        item = dict(item)
        item["name"] = canonical
        seen.add(key)
        result.append(item)
    return result


def curated_facilities(lat, lon, radius):
    candidates = [
        ("안양종합운동장", "생활", 37.4050, 126.9509),
        ("안양시청", "공공", 37.3944, 126.9568),
        ("동안구청", "공공", 37.3926, 126.9518),
        ("범계역", "교통", 37.3898, 126.9508),
        ("평촌역", "교통", 37.3943, 126.9638),
        ("인덕원역", "교통", 37.4019, 126.9767),
        ("안양역", "교통", 37.4019, 126.9227),
        ("평촌중앙공원", "생활", 37.3917, 126.9575),
        ("한림대학교성심병원", "의료", 37.3913, 126.9620),
        ("안양동안경찰서", "공공", 37.3948, 126.9589),
        ("삼호아파트", "주거", 37.4008, 126.9488),
        ("비산삼성래미안", "주거", 37.4073, 126.9468),
        ("관악성원아파트", "주거", 37.3991, 126.9568),
        ("평촌래미안푸르지오", "주거", 37.3916, 126.9487),
        ("샛별한양아파트", "주거", 37.3884, 126.9555),
    ]
    facilities = []
    for name, category, item_lat, item_lon in candidates:
        distance = haversine_m(lat, lon, item_lat, item_lon)
        if distance <= max(radius, 3500):
            facilities.append(
                {
                    "name": name,
                    "category": category,
                    "lat": item_lat,
                    "lon": item_lon,
                    "distance": round(distance),
                }
            )
    return facilities


def haversine_m(lat1, lon1, lat2, lon2):
    earth = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return earth * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def category_rank(category):
    ranks = {"교통": 0, "공공": 1, "생활": 2, "주거": 3, "교육": 4, "의료": 5}
    return ranks.get(category, 9)


def latlon_to_pixel(lat, lon, zoom):
    sin_lat = math.sin(math.radians(lat))
    world = 256 * (2**zoom)
    x = (lon + 180.0) / 360.0 * world
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * world
    return x, y


def tile_image(z, x, y, map_type="satellite"):
    templates = {
        "satellite": ESRI_TILE_URL,
        "street": CARTO_VOYAGER_TILE_URL,
        "blank": CARTO_LIGHT_NOLABEL_TILE_URL,
    }
    template = templates.get(map_type, ESRI_TILE_URL)
    url = template.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return Image.open(res).convert("RGB")


def load_font(size, bold=False):
    candidates = [
        Path("C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def draw_centered_text(draw, box, text, font, fill):
    left, top, right, bottom = box
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.text((left + (right - left - width) / 2, top + (bottom - top - height) / 2), text, font=font, fill=fill)


def choose_zoom(lat, radius, span):
    for zoom in range(18, 10, -1):
        meters_per_pixel = 156543.03392 * math.cos(math.radians(lat)) / (2**zoom)
        if radius / meters_per_pixel <= span * 0.47:
            return zoom
    return 11


def draw_text_with_outline(draw, xy, text, font, fill, outline=(20, 20, 20, 255), width=2):
    x, y = xy
    for dx in range(-width, width + 1):
        for dy in range(-width, width + 1):
            if dx or dy:
                draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def clean_facility_name(name, category):
    name = " ".join(str(name or "").replace("\n", " ").split())
    name = re.sub(r"\s+\d{1,4}$", "", name)
    name = re.sub(r"\s+\d{1,4}(동|호)$", "", name)
    if category == "주거":
        name = re.sub(r"\s+[가-힣A-Za-z0-9]{1,4}동$", "", name)
        name = re.sub(r"\s+[가-힣A-Za-z0-9]{1,4}호$", "", name)
        name = re.sub(r"\s*\d{1,4}$", "", name)
    return name.strip()


def facility_label_box(draw, x, y, facility, font):
    text = clean_facility_name(facility["name"], facility["category"])
    if not text or text.isdigit() or len(text) < 2:
        return None
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    return (x - text_w / 2 - 4, y - text_h / 2 - 3, x + text_w / 2 + 4, y + text_h / 2 + 3, text)


def boxes_overlap(a, b, padding=3):
    return not (a[2] + padding < b[0] or b[2] + padding < a[0] or a[3] + padding < b[1] or b[3] + padding < a[1])


def box_center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def box_distance(a, b):
    ax, ay = box_center(a)
    bx, by = box_center(b)
    return math.hypot(ax - bx, ay - by)


def crowded_label_count(box, placed_boxes, distance):
    return sum(1 for placed in placed_boxes if box_distance(box, placed) < distance)


def draw_facility_label(draw, box, text, facility, font):
    colors = {
        "교통": (69, 163, 255, 255),
        "공공": (216, 117, 255, 255),
        "생활": (53, 232, 111, 255),
        "교육": (255, 212, 0, 255),
        "의료": (255, 87, 87, 255),
        "주거": (255, 184, 74, 255),
    }
    color = colors.get(facility["category"], (255, 255, 255, 255))
    draw_text_with_outline(draw, (box[0] + 4, box[1] + 3), text, font, color, outline=(0, 0, 0, 255), width=2)


def draw_dashed_ellipse(draw, box, fill, width=2, dash_degrees=1.2, gap_degrees=1.2):
    start = 0
    while start < 360:
        end = min(start + dash_degrees, 360)
        draw.arc(box, start=start, end=end, fill=fill, width=width)
        start += dash_degrees + gap_degrees


def format_radius_label(radius_m):
    if radius_m >= 1000:
        km = radius_m / 1000
        if abs(km - round(km)) < 0.05:
            return f"{int(round(km))}km"
        return f"{km:.1f}km"
    return f"{int(round(radius_m))}m"


def radius_ring_values(radius_m):
    if radius_m <= 1000:
        values = [value for value in (200, 500, 1000) if value <= radius_m]
    else:
        values = [value for value in (500, 1000, 3000, 5000, 10000, 25000) if value <= radius_m]
    return sorted(values)


def draw_radius_ring(draw, cx, cy, pixel_radius, label, font, canvas_size, scale=1.0, primary=False):
    line_width = max(2, round((2 if primary else 1) * scale))
    outline_width = max(line_width + 2, round((4 if primary else 2) * scale))
    dash = 1.2 if primary else 1.0
    gap = 1.2 if primary else 1.0
    box = (cx - pixel_radius, cy - pixel_radius, cx + pixel_radius, cy + pixel_radius)
    draw_dashed_ellipse(draw, box, (20, 20, 20, 230), width=outline_width, dash_degrees=dash, gap_degrees=gap)
    draw_dashed_ellipse(draw, box, (255, 255, 255, 245), width=line_width, dash_degrees=dash, gap_degrees=gap)

    bbox = draw.textbbox((0, 0), label, font=font)
    label_w = bbox[2] - bbox[0]
    label_h = bbox[3] - bbox[1]
    angle = math.radians(-28)
    label_x = cx + math.cos(angle) * pixel_radius - label_w / 2
    label_y = cy + math.sin(angle) * pixel_radius - label_h / 2
    canvas_w, canvas_h = canvas_size
    label_x = max(12, min(canvas_w - label_w - 12, label_x))
    label_y = max(12, min(canvas_h - label_h - 12, label_y))
    draw_text_with_outline(draw, (label_x, label_y), label, font, (255, 255, 255, 255), outline=(20, 20, 20, 255), width=2)


def make_satellite_map(address, api_key, width=3200, height=2000, radius=3000, show_facilities=True, categories=None, map_type="satellite", show_radius=True):
    lat, lon, refined = geocode(address, api_key)
    zoom = choose_zoom(lat, radius, min(width, height))
    center_px, center_py = latlon_to_pixel(lat, lon, zoom)
    left_px = center_px - width / 2
    top_px = center_py - height / 2
    first_tile_x = math.floor(left_px / 256)
    first_tile_y = math.floor(top_px / 256)
    last_tile_x = math.floor((left_px + width) / 256)
    last_tile_y = math.floor((top_px + height) / 256)

    blank_mode = map_type == "blank"
    background = (248, 248, 246) if blank_mode else (20, 22, 20)
    canvas = Image.new("RGB", ((last_tile_x - first_tile_x + 1) * 256, (last_tile_y - first_tile_y + 1) * 256), background)
    max_tile = 2**zoom
    for ty in range(first_tile_y, last_tile_y + 1):
        for tx in range(first_tile_x, last_tile_x + 1):
            try:
                tile = tile_image(zoom, tx % max_tile, ty, map_type=map_type)
                canvas.paste(tile, ((tx - first_tile_x) * 256, (ty - first_tile_y) * 256))
            except Exception:
                pass

    crop_left = int(round(left_px - first_tile_x * 256))
    crop_top = int(round(top_px - first_tile_y * 256))
    img = canvas.crop((crop_left, crop_top, crop_left + width, crop_top + height)).convert("RGBA")
    draw = ImageDraw.Draw(img)

    scale = min(width / 1600, height / 1000)
    label_font = load_font(max(18, round(17 * scale)), bold=True)
    small_font = load_font(16)
    facility_font = load_font(max(13, round(10 * scale)), bold=True)

    cx, cy = width // 2, height // 2
    meters_per_pixel = 156543.03392 * math.cos(math.radians(lat)) / (2**zoom)
    pixel_radius = max(32, min(min(width, height) * 0.47, radius / meters_per_pixel))

    if show_radius:
        ring_values = radius_ring_values(radius)
        for ring_radius_m in ring_values:
            ring_pixel_radius = max(24, ring_radius_m / meters_per_pixel)
            ring_label = format_radius_label(ring_radius_m)
            draw_radius_ring(
                draw,
                cx,
                cy,
                ring_pixel_radius,
                ring_label,
                label_font,
                (width, height),
                scale=scale,
                primary=bool(ring_values and ring_radius_m == ring_values[-1]),
            )
    dot_r = max(18, round(18 * scale))
    dot_w = max(5, round(5 * scale))
    draw.ellipse((cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r), fill=(230, 0, 18, 235), outline=(255, 255, 255, 255), width=dot_w)

    if show_facilities:
        try:
            facilities = get_context_facilities(lat, lon, radius)
        except Exception:
            facilities = curated_facilities(lat, lon, radius)
        if categories:
            facilities = [facility for facility in facilities if facility["category"] in categories]
        placed_boxes = []
        placed_names = set()
        facilities = sorted(facilities, key=lambda item: (category_rank(item["category"]), item["distance"]))
        offset_scale = max(0.85, scale * 0.72)
        offsets = [
            (0, 0), (0, -36), (0, 36), (52, 0), (-52, 0),
            (58, -36), (-58, -36), (58, 36), (-58, 36),
            (0, -72), (0, 72), (104, 0), (-104, 0),
            (104, -58), (-104, -58), (104, 58), (-104, 58),
            (0, -110), (0, 110), (150, 0), (-150, 0),
        ]
        offsets = [(dx * offset_scale, dy * offset_scale) for dx, dy in offsets]
        for facility in facilities:
            fx, fy = latlon_to_pixel(facility["lat"], facility["lon"], zoom)
            sx = cx + (fx - center_px)
            sy = cy + (fy - center_py)
            if not (40 < sx < width - 40 and 40 < sy < height - 40):
                continue
            for dx, dy in offsets:
                box = facility_label_box(draw, sx + dx, sy + dy, facility, facility_font)
                if not box:
                    break
                if box[4] in placed_names:
                    break
                if box[0] < 8 or box[1] < 8 or box[2] > width - 8 or box[3] > height - 32:
                    continue
                if any(boxes_overlap(box, placed, padding=max(4, round(5 * scale))) for placed in placed_boxes):
                    continue
                if crowded_label_count(box, placed_boxes, distance=max(78, round(54 * scale))) >= 2:
                    continue
                draw_facility_label(draw, box, box[4], facility, facility_font)
                placed_boxes.append(box)
                placed_names.add(box[4])
                break

    footer = {
        "satellite": "Satellite imagery: Esri | Geocoding: VWorld",
        "street": "Map: CARTO (OpenStreetMap data) | Geocoding: VWorld",
        "blank": "Blank base map | Geocoding: VWorld",
    }.get(map_type, "Geocoding: VWorld")
    bbox = draw.textbbox((0, 0), footer, font=small_font)
    draw.rectangle((width - (bbox[2] - bbox[0]) - 22, height - 30, width, height), fill=(255, 255, 255, 210))
    draw.text((width - (bbox[2] - bbox[0]) - 12, height - 26), footer, font=small_font, fill=(40, 40, 40, 255))

    out = ROOT / f"location_map_{time.strftime('%Y%m%d_%H%M%S')}.png"
    img.convert("RGB").save(out, "PNG")
    return out, lat, lon, refined


def select_folder(initial_dir=""):
    initial = str(initial_dir or DEFAULT_OUT_DIR)
    script = r"""
& {
  param([string]$InitialPath)
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $owner = New-Object System.Windows.Forms.Form
  $owner.StartPosition = "Manual"
  $owner.Size = New-Object System.Drawing.Size(1, 1)
  $owner.Location = New-Object System.Drawing.Point(-32000, -32000)
  $owner.ShowInTaskbar = $false
  $owner.TopMost = $true
  $owner.Show()
  $owner.Activate()
  $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
  $dialog.Description = "연속지적 저장 폴더 선택"
  $dialog.ShowNewFolderButton = $true
  if ($InitialPath -and (Test-Path -LiteralPath $InitialPath)) {
    $dialog.SelectedPath = $InitialPath
  }
  try {
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
      Write-Output $dialog.SelectedPath
    }
  } finally {
    $owner.Close()
    $owner.Dispose()
  }
}
"""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
            initial,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "폴더 선택창을 열지 못했습니다.").strip())
    return result.stdout.strip()


def save_parcel_list_upload(payload, out_dir):
    paths = []
    upload = payload.get("parcelListFile") or {}
    name = str(upload.get("name") or "").strip()
    data_url = str(upload.get("data") or "")
    upload_dir = out_dir / "parcel_list_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    if name and data_url:
        if "," in data_url:
            data_url = data_url.split(",", 1)[1]
        suffix = Path(name).suffix.lower()
        if suffix not in {".xlsx", ".csv", ".txt"}:
            raise ValueError("지번 리스트는 .xlsx, .csv, .txt 파일만 첨부할 수 있습니다.")
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", Path(name).name)
        target = upload_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{safe_name}"
        target.write_bytes(base64.b64decode(data_url))
        paths.append(target)

    parcel_items = payload.get("parcelListItems") or []
    if parcel_items:
        target = upload_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_map_selected_parcels.csv"
        lines = ["parcel"]
        for item in parcel_items:
            text = str(item.get("parcel") if isinstance(item, dict) else item).strip()
            if text:
                lines.append('"' + text.replace('"', '""') + '"')
        if len(lines) > 1:
            target.write_text("\n".join(lines), encoding="utf-8-sig")
            paths.append(target)
    return paths


def normalize_header(value):
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "", str(value or "")).lower()


def parse_float(value, default=None):
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    text = text.replace(",", "").replace("㎡", "").replace("m2", "").replace("m", "").replace("%", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return default
    return float(match.group(0))


def row_value(row, aliases, default=None):
    for alias in aliases:
        key = normalize_header(alias)
        if key in row and str(row[key]).strip():
            return row[key]
    return default


def parse_delimited_table(raw_bytes, suffix):
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    delimiter = "\t" if suffix == ".txt" and "\t" in sample else ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        delimiter = dialect.delimiter
    except Exception:
        pass
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for item in reader:
        normalized = {normalize_header(k): (v or "").strip() for k, v in item.items() if k is not None}
        if any(normalized.values()):
            rows.append(normalized)
    return rows


def parse_xlsx_table(raw_bytes):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.findall(".//m:t", ns)))
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in zf.namelist():
            sheet_name = next((name for name in zf.namelist() if name.startswith("xl/worksheets/sheet")), None)
        if not sheet_name:
            raise ValueError("엑셀 시트가 비어 있습니다.")
        root = ET.fromstring(zf.read(sheet_name))
        matrix = []
        for row in root.findall(".//m:sheetData/m:row", ns):
            values = {}
            max_col = -1
            for cell in row.findall("m:c", ns):
                ref = cell.attrib.get("r", "")
                letters = re.sub(r"[^A-Z]", "", ref.upper())
                col = 0
                for ch in letters:
                    col = col * 26 + ord(ch) - 64
                col -= 1
                max_col = max(max_col, col)
                value_node = cell.find("m:v", ns)
                inline_node = cell.find("m:is/m:t", ns)
                value = ""
                if inline_node is not None:
                    value = inline_node.text or ""
                elif value_node is not None:
                    value = value_node.text or ""
                    if cell.attrib.get("t") == "s":
                        idx = int(float(value))
                        value = shared[idx] if 0 <= idx < len(shared) else ""
                values[col] = str(value).strip()
            if max_col >= 0:
                matrix.append([values.get(i, "") for i in range(max_col + 1)])
    header_index = next((i for i, row in enumerate(matrix) if any(str(cell).strip() for cell in row)), None)
    if header_index is None:
        return []
    headers = [normalize_header(cell) for cell in matrix[header_index]]
    rows = []
    for raw in matrix[header_index + 1:]:
        row = {headers[i]: raw[i].strip() if i < len(raw) else "" for i in range(len(headers)) if headers[i]}
        if any(row.values()):
            rows.append(row)
    return rows


def parse_block_height_rows(upload):
    name = str(upload.get("name") or "").strip()
    data_url = str(upload.get("data") or "")
    if not name or not data_url:
        raise ValueError("필지 지적 리스트 파일을 첨부해 주세요.")
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    raw_bytes = base64.b64decode(data_url)
    suffix = Path(name).suffix.lower()
    if suffix == ".xlsx":
        return parse_xlsx_table(raw_bytes)
    if suffix in {".csv", ".txt"}:
        return parse_delimited_table(raw_bytes, suffix)
    raise ValueError("산정 파일은 .xlsx, .csv, .txt 형식만 지원합니다.")


def calculate_block_height(payload):
    rows = parse_block_height_rows(payload.get("heightListFile") or {})
    default_coverage = (
        parse_float(payload.get("districtPlanCoverage"), None)
        or parse_float(payload.get("ordinanceCoverage"), None)
        or parse_float(payload.get("coverageRatio"), 60.0)
    )
    default_road_width = parse_float(payload.get("roadWidth"), 8.0)
    slope_multiplier = parse_float(payload.get("slopeMultiplier"), 1.5)
    default_frontage = parse_float(payload.get("frontage"), None)
    default_depth = parse_float(payload.get("lotDepth"), None)
    if default_coverage <= 0:
        raise ValueError("건폐율은 0보다 커야 합니다.")
    if slope_multiplier <= 0:
        raise ValueError("도로사선 계수는 0보다 커야 합니다.")

    aliases = {
        "parcel": ["지번", "필지", "필지명", "parcel", "lot", "pnu"],
        "area": ["대지면적", "필지면적", "면적", "area", "lotarea", "sitearea"],
        "coverage": ["건폐율", "coverage", "buildingcoverage", "coverageratio"],
        "road_width": ["도로폭", "도로너비", "접도폭", "도로폭원", "roadwidth", "road"],
        "frontage": ["접도길이", "전면폭", "필지폭", "frontage", "width"],
        "depth": ["필지깊이", "대지깊이", "깊이", "depth", "lotdepth"],
        "avg_height": ["평균높이", "사선평균높이", "높이", "avgheight", "averageheight", "height"],
        "min_height": ["최저높이", "minheight", "lowheight"],
        "max_height": ["최고높이", "maxheight", "highheight"],
    }

    result_rows = []
    total_floor_area = 0.0
    total_volume = 0.0
    for index, row in enumerate(rows, 1):
        parcel = str(row_value(row, aliases["parcel"], f"{index}")).strip()
        area = parse_float(row_value(row, aliases["area"]))
        if not area or area <= 0:
            raise ValueError(f"{index}행의 대지면적/필지면적을 확인해 주세요.")
        coverage = parse_float(row_value(row, aliases["coverage"]), default_coverage)
        if coverage > 1:
            coverage_ratio = coverage / 100.0
        else:
            coverage_ratio = coverage
        if coverage_ratio <= 0:
            raise ValueError(f"{index}행의 건폐율을 확인해 주세요.")
        floor_area = area * coverage_ratio

        explicit_avg = parse_float(row_value(row, aliases["avg_height"]))
        min_height = parse_float(row_value(row, aliases["min_height"]))
        max_height = parse_float(row_value(row, aliases["max_height"]))
        road_width = parse_float(row_value(row, aliases["road_width"]), default_road_width)
        frontage = parse_float(row_value(row, aliases["frontage"]), default_frontage)
        depth = parse_float(row_value(row, aliases["depth"]), default_depth)

        method = "입력 평균높이"
        if explicit_avg is not None:
            avg_height = explicit_avg
            if min_height is None:
                min_height = avg_height
            if max_height is None:
                max_height = avg_height
        elif min_height is not None and max_height is not None:
            avg_height = (min_height + max_height) / 2.0
            method = "최저/최고 평균"
        else:
            if road_width <= 0:
                raise ValueError(f"{index}행은 평균높이 또는 도로폭을 입력해야 합니다.")
            if depth is None:
                if frontage and frontage > 0:
                    depth = floor_area / frontage
                    method = "도로사선 1.5D(접도길이 추정)"
                else:
                    depth = math.sqrt(floor_area)
                    method = "도로사선 1.5D(정방형 추정)"
            else:
                method = "도로사선 1.5D(필지깊이)"
            min_height = slope_multiplier * road_width
            max_height = slope_multiplier * (road_width + depth)
            avg_height = (min_height + max_height) / 2.0

        volume = floor_area * avg_height
        total_floor_area += floor_area
        total_volume += volume
        result_rows.append(
            {
                "parcel": parcel,
                "area": round(area, 3),
                "coverageRatio": round(coverage_ratio * 100, 3),
                "floorArea": round(floor_area, 3),
                "roadWidth": round(road_width, 3) if road_width else None,
                "frontage": round(frontage, 3) if frontage else None,
                "depth": round(depth, 3) if depth else None,
                "minHeight": round(min_height, 3) if min_height is not None else None,
                "maxHeight": round(max_height, 3) if max_height is not None else None,
                "avgHeight": round(avg_height, 3),
                "volume": round(volume, 3),
                "method": method,
            }
        )

    if total_floor_area <= 0:
        raise ValueError("건폐율 적용 바닥면적 합계가 0입니다.")
    block_average = total_volume / total_floor_area
    return {
        "rows": result_rows,
        "totals": {
            "parcelCount": len(result_rows),
            "floorArea": round(total_floor_area, 3),
            "volume": round(total_volume, 3),
            "blockAverageHeight": round(block_average, 3),
        },
    }


def _fmt_num(value, digits=2):
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def build_height_report_xlsx(payload):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    rows = payload.get("rows") or []
    if not rows:
        raise ValueError("보고서로 만들 산정 결과가 없습니다. 먼저 평균높이를 산정해 주세요.")
    slope = parse_float(payload.get("slopeMultiplier"), 1.5)
    totals = payload.get("totals") or {}

    wb = Workbook()
    ws = wb.active
    ws.title = "평균높이산정"

    bold = Font(bold=True)
    title_font = Font(bold=True, size=14, color="14301F")
    legend_font = Font(size=9, color="555555")
    head_font = Font(bold=True, color="FFFFFF", size=10)
    head_fill = PatternFill("solid", fgColor="1B4F9C")
    input_fill = PatternFill("solid", fgColor="FFF4E0")   # 입력/원천값
    calc_fill = PatternFill("solid", fgColor="E7EFFB")    # 자동계산(수식)
    total_fill = PatternFill("solid", fgColor="EAF3EC")
    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right = Alignment(horizontal="right", vertical="center")
    num_fmt = "#,##0.00"

    ws["A1"] = "블록별 평균높이 산정 보고서"
    ws["A1"].font = title_font
    ws["A2"] = f"생성: {time.strftime('%Y-%m-%d %H:%M')}   ·   사선계수(도로사선) = {slope}   ·   필지 수 = {len(rows)}"
    ws["A2"].font = legend_font
    legends = [
        "[산식] 바닥면적 = 대지면적 × 건폐율(%) ÷ 100",
        "[산식] (도로사선) 최저높이 = 사선계수 × 도로폭,  최고높이 = 사선계수 × (도로폭 + 필지깊이)",
        "[산식] 평균높이 = (최저높이 + 최고높이) ÷ 2",
        "[산식] 체적 = 바닥면적 × 평균높이",
        "[산식] 블록 평균높이 = Σ체적 ÷ Σ바닥면적",
        "※ 주황색 셀 = 입력/원천값,  파란색 셀 = Excel 수식(셀 클릭 시 산식 표시, 입력값을 바꾸면 자동 재계산).  '검산' 열은 숫자를 대입한 식입니다.",
    ]
    r = 4
    for text in legends:
        ws.cell(row=r, column=1, value=text).font = legend_font
        r += 1

    header_row = r + 1
    headers = [
        "필지", "대지면적(㎡)", "건폐율(%)", "바닥면적(㎡)", "도로폭(m)", "접도길이(m)",
        "필지깊이(m)", "사선계수", "최저높이(m)", "최고높이(m)", "평균높이(m)", "체적(㎥)",
        "산정방식", "건폐율출처", "신뢰도", "검산(산식·숫자 대입)", "비고",
    ]
    for c, text in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=c, value=text)
        cell.font = head_font
        cell.fill = head_fill
        cell.alignment = center
        cell.border = border

    first_data = header_row + 1
    input_cols = {2, 3, 5, 6, 7, 8, 9, 10}
    calc_cols = {4, 11, 12}

    for i, row in enumerate(rows):
        rr = first_data + i
        area = parse_float(row.get("area"))
        cov = parse_float(row.get("coverageRatio"))
        rw = parse_float(row.get("roadWidth"))
        fr = parse_float(row.get("frontage"))
        dp = parse_float(row.get("depth"))
        mn = parse_float(row.get("minHeight"))
        mx = parse_float(row.get("maxHeight"))
        av = parse_float(row.get("avgHeight"))
        fl = parse_float(row.get("floorArea"))
        vol = parse_float(row.get("volume"))
        method = str(row.get("method") or "")
        roadsine = bool(
            rw and dp is not None and mn is not None and mx is not None
            and abs(mn - slope * rw) < 0.06 and abs(mx - slope * (rw + dp)) < 0.06
        )

        ws.cell(row=rr, column=1, value=row.get("parcel") or f"{i + 1}")
        ws.cell(row=rr, column=2, value=area)
        ws.cell(row=rr, column=3, value=cov)
        ws.cell(row=rr, column=4, value=f"=B{rr}*C{rr}/100")
        ws.cell(row=rr, column=5, value=rw)
        ws.cell(row=rr, column=6, value=fr)
        ws.cell(row=rr, column=7, value=dp)
        ws.cell(row=rr, column=8, value=(slope if roadsine else None))
        if roadsine:
            ws.cell(row=rr, column=9, value=f"=H{rr}*E{rr}")
            ws.cell(row=rr, column=10, value=f"=H{rr}*(E{rr}+G{rr})")
        else:
            ws.cell(row=rr, column=9, value=mn)
            ws.cell(row=rr, column=10, value=mx)
        ws.cell(row=rr, column=11, value=f"=(I{rr}+J{rr})/2")
        ws.cell(row=rr, column=12, value=f"=D{rr}*K{rr}")
        ws.cell(row=rr, column=13, value=method)
        ws.cell(row=rr, column=14, value=row.get("coverageSource") or "")
        ws.cell(row=rr, column=15, value=row.get("confidence") or "")

        checks = []
        if area is not None and cov is not None:
            checks.append(f"바닥면적={_fmt_num(area)}×{_fmt_num(cov)}%={_fmt_num(fl)}")
        if roadsine:
            checks.append(f"최저={_fmt_num(slope)}×{_fmt_num(rw)}={_fmt_num(mn)}")
            checks.append(f"최고={_fmt_num(slope)}×({_fmt_num(rw)}+{_fmt_num(dp)})={_fmt_num(mx)}")
        if mn is not None and mx is not None:
            checks.append(f"평균=({_fmt_num(mn)}+{_fmt_num(mx)})/2={_fmt_num(av)}")
        if fl is not None and av is not None:
            checks.append(f"체적={_fmt_num(fl)}×{_fmt_num(av)}={_fmt_num(vol)}")
        ws.cell(row=rr, column=16, value=" · ".join(checks))
        ws.cell(row=rr, column=17, value=row.get("note") or "")

        for c in range(1, 18):
            cell = ws.cell(row=rr, column=c)
            cell.border = border
            if 2 <= c <= 12:
                cell.number_format = num_fmt
                cell.alignment = right
            else:
                cell.alignment = left
            if c in input_cols and not (c in (9, 10) and roadsine):
                cell.fill = input_fill
            if c in calc_cols or (c in (9, 10) and roadsine):
                cell.fill = calc_fill

    last_data = first_data + len(rows) - 1
    tr = last_data + 1
    ws.cell(row=tr, column=1, value="합계 / 블록 평균").font = bold
    ws.cell(row=tr, column=4, value=f"=SUM(D{first_data}:D{last_data})")
    ws.cell(row=tr, column=11, value=f"=L{tr}/D{tr}")
    ws.cell(row=tr, column=12, value=f"=SUM(L{first_data}:L{last_data})")
    ws.cell(
        row=tr, column=16,
        value=(
            f"블록평균높이=Σ체적÷Σ바닥면적={_fmt_num(totals.get('volume'))}"
            f"÷{_fmt_num(totals.get('floorArea'))}={_fmt_num(totals.get('blockAverageHeight'))}"
        ),
    )
    for c in range(1, 18):
        cell = ws.cell(row=tr, column=c)
        cell.border = border
        cell.fill = total_fill
        if c in (4, 11, 12):
            cell.number_format = num_fmt
            cell.font = bold
            cell.alignment = right

    widths = [16, 12, 9, 12, 9, 11, 11, 9, 11, 11, 11, 12, 20, 12, 8, 46, 18]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=first_data, column=1)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def start_cadastre_job(payload):
    address = payload.get("address", "").strip()
    api_key = payload.get("apiKey", "").strip()
    radius = str(payload.get("radius") or "1000").strip()
    vworld_id = payload.get("vworldId", "").strip()
    vworld_pw = payload.get("vworldPassword", "")
    out_dir = Path(payload.get("outDir") or DEFAULT_OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    parcel_list_paths = save_parcel_list_upload(payload, out_dir)
    log_dir = out_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"html_run_{time.strftime('%Y%m%d_%H%M%S')}.log"

    if not CADASTRE_SCRIPT.exists():
        raise FileNotFoundError(f"연속지적 스크립트를 찾지 못했습니다: {CADASTRE_SCRIPT}")

    args = [
        "powershell.exe",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(CADASTRE_SCRIPT),
        "-Address",
        address,
        "-VWorldKey",
        api_key,
        "-Radius",
        radius,
        "-OutDir",
        str(out_dir),
    ]
    for parcel_list_path in parcel_list_paths:
        args += ["-ParcelListPath", str(parcel_list_path)]
    if vworld_id and vworld_pw:
        args += ["-VWorldId", vworld_id, "-VWorldPassword", vworld_pw]

    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(args, cwd=str(APP_DIR), stdout=log_file, stderr=subprocess.STDOUT, text=True)
    job_id = str(int(time.time() * 1000))
    JOBS[job_id] = {"process": process, "log": log_path, "outDir": out_dir}
    return job_id, log_path, out_dir


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_OPTIONS(self):
        cors_headers(self)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/":
                self.serve_file(DEFAULT_HTML)
                return
            if path == "/api/health":
                json_response(self, {"ok": True})
                return
            if path == "/api/geocode":
                address = qs.get("address", [""])[0].strip()
                api_key = qs.get("apiKey", [""])[0].strip()
                lat, lon, refined = geocode(address, api_key)
                json_response(self, {"ok": True, "lat": lat, "lon": lon, "refined": refined})
                return
            if path == "/api/reverse-parcel":
                lat = float(qs.get("lat", ["0"])[0])
                lon = float(qs.get("lon", ["0"])[0])
                api_key = qs.get("apiKey", [""])[0].strip()
                parcel = reverse_geocode_parcel(lat, lon, api_key)
                json_response(self, {"ok": True, "parcel": parcel})
                return
            if path == "/api/parcel-feature":
                lat = float(qs.get("lat", ["0"])[0])
                lon = float(qs.get("lon", ["0"])[0])
                api_key = qs.get("apiKey", [""])[0].strip()
                feature = get_parcel_feature(lat, lon, api_key)
                json_response(self, {"ok": True, "feature": feature})
                return
            if path == "/api/context-facilities":
                lat = float(qs.get("lat", ["0"])[0])
                lon = float(qs.get("lon", ["0"])[0])
                radius = float(qs.get("radius", ["2500"])[0])
                facilities = get_context_facilities(lat, lon, radius)
                json_response(self, {"ok": True, "facilities": facilities})
                return
            if path == "/api/satellite-map":
                address = qs.get("address", [""])[0].strip()
                api_key = qs.get("apiKey", [""])[0].strip()
                radius = float(qs.get("radius", ["3000"])[0])
                show_radius = qs.get("showRadius", ["1"])[0] != "0"
                show_facilities = qs.get("facilities", ["1"])[0] != "0"
                map_type = qs.get("mapType", ["satellite"])[0]
                if map_type not in {"satellite", "street", "blank"}:
                    map_type = "satellite"
                size = qs.get("size", ["high"])[0]
                dimensions = {
                    "standard": (1600, 1000),
                    "high": (3200, 2000),
                    "ultra": (4800, 3000),
                }.get(size, (3200, 2000))
                category_text = qs.get("categories", [""])[0]
                categories = [item for item in category_text.split(",") if item] if category_text else None
                out, lat, lon, refined = make_satellite_map(address, api_key, width=dimensions[0], height=dimensions[1], radius=radius, show_facilities=show_facilities, categories=categories, map_type=map_type, show_radius=show_radius)
                data = out.read_bytes()
                cors_headers(self, content_type="image/png")
                filename = urllib.parse.quote(out.name)
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{filename}")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/status":
                job_id = qs.get("jobId", [""])[0]
                job = JOBS.get(job_id)
                if not job:
                    json_response(self, {"ok": False, "error": "작업을 찾지 못했습니다."}, status=404)
                    return
                process = job["process"]
                log_path = job["log"]
                log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
                json_response(
                    self,
                    {
                        "ok": True,
                        "running": process.poll() is None,
                        "exitCode": process.poll(),
                        "log": log[-6000:],
                        "logPath": str(log_path),
                        "outDir": str(job["outDir"]),
                    },
                )
                return
            self.serve_file(ROOT / path.lstrip("/"))
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, status=500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if parsed.path == "/api/run-cadastre":
                job_id, log_path, out_dir = start_cadastre_job(payload)
                json_response(self, {"ok": True, "jobId": job_id, "logPath": str(log_path), "outDir": str(out_dir)})
                return
            if parsed.path == "/api/calculate-block-height":
                result = calculate_block_height(payload)
                json_response(self, {"ok": True, **result})
                return
            if parsed.path == "/api/height-report":
                data = build_height_report_xlsx(payload)
                cors_headers(self, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                filename = urllib.parse.quote(f"block_height_report_{time.strftime('%Y%m%d_%H%M%S')}.xlsx")
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{filename}")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if parsed.path == "/api/analyze-selected-parcels":
                result = analyze_selected_parcels(payload)
                json_response(self, {"ok": True, **result})
                return
            if parsed.path == "/api/select-folder":
                path = select_folder(payload.get("initialDir", ""))
                json_response(self, {"ok": True, "path": path})
                return
            json_response(self, {"ok": False, "error": "알 수 없는 요청입니다."}, status=404)
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, status=500)

    def serve_file(self, path):
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            json_response(self, {"ok": False, "error": "파일을 찾지 못했습니다."}, status=404)
            return
        if not resolved.is_relative_to(ROOT):
            json_response(self, {"ok": False, "error": "파일을 찾지 못했습니다."}, status=404)
            return
        path = resolved
        if not path.exists() or not path.is_file():
            json_response(self, {"ok": False, "error": "파일을 찾지 못했습니다."}, status=404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/"):
            content_type = f"{content_type}; charset=utf-8"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        return


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8788
    server = ThreadingHTTPServer(("127.0.0.1", port), BridgeHandler)
    if sys.stdout:
        print(f"Cadastre bridge running: http://127.0.0.1:{port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
