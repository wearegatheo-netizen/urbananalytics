import argparse
import csv
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDxfExport,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsFillSymbol,
    QgsMapSettings,
    QgsGeometry,
    QgsLineSymbol,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsPalLayerSettings,
    QgsTextFormat,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
    Qgis,
)
from PyQt5.QtCore import QCoreApplication, QFile, QIODevice, QVariant
from PyQt5.QtGui import QColor, QFont


APP_DIR = Path(__file__).resolve().parent
DEFAULT_STYLE_PATH = APP_DIR.parent / "styles" / "cadastre-default.qml"
DEFAULT_ZONE_STYLE_PATH = Path(
    r"C:\Users\admin\OneDrive\Desktop\개인(26.04.~)\QGIS 자동화\심볼스타일\260522 용도지역 스타일(RGB).qml"
)
DXF_TEXT_STYLE_NAME = "STANDARD"
DXF_TEXT_FONT_FAMILY = "KoPubWorld돋움체 Medium"
DXF_TEXT_FONT_FILE = r"C:\Users\admin\AppData\Local\Microsoft\Windows\Fonts\KoPubWorld Dotum Medium.ttf"
PARCEL_LIST_LAYER_NAME = "엑셀선택지번"
DEFAULT_OUT_DIR = Path.cwd() / "outputs"
DATASET_ID_5174 = "30564"
DATASET_INDEX_URL = "https://data.edmgr.kr/dataView.do?id=vworld_open_30564"
# 도로명주소 실폭도로(시도별 SHP, Z_KAIS_TL_SPRD_RW 포함)
ROAD_DATASET_ID = "30057"
ROAD_INDEX_URL = "https://data.edmgr.kr/dataView.do?id=vworld_open_30057"
VWORLD_DOWNLOAD_URL = "https://www.vworld.kr/dtmk/downloadResourceFile.do"
VWORLD_GEOCODE_URL = "https://api.vworld.kr/req/address"
VWORLD_WFS_URL = "https://api.vworld.kr/req/wfs"
# 도시계획시설(WFS): 도로 + 교통시설
PLAN_FACILITY_TYPENAMES = [("lt_c_upisuq151", 1000), ("lt_c_upisuq152", 800)]
ZONE_DEFINITIONS = {
    "urban": {"label": "도시지역", "line_layer": "ZONE_URBAN", "color": 1, "rgb": (255, 0, 0)},
    "management": {"label": "관리지역", "line_layer": "ZONE_MANAGEMENT", "color": 3, "rgb": (0, 170, 0)},
    "agriculture": {"label": "농림지역", "line_layer": "ZONE_AGRICULTURE", "color": 2, "rgb": (220, 180, 0)},
    "nature": {"label": "자연환경보전지역", "line_layer": "ZONE_NATURE", "color": 5, "rgb": (0, 80, 255)},
}
ZONE_CODE_TO_UNAME = {
    "UQA110": "제1종전용주거지역",
    "UQA111": "제1종전용주거지역",
    "UQA112": "제2종전용주거지역",
    "UQA113": "제2종(7층)일반주거지역",
    "UQA120": "제1종일반주거지역",
    "UQA121": "제1종일반주거지역",
    "UQA122": "제2종일반주거지역",
    "UQA123": "제3종일반주거지역",
    "UQA130": "준주거지역",
    "UQA200": "일반상업지역",
    "UQA210": "중심상업지역",
    "UQA220": "일반상업지역",
    "UQA230": "근린상업지역",
    "UQA240": "유통상업지역",
    "UQA300": "준공업지역",
    "UQA310": "전용공업지역",
    "UQA320": "일반공업지역",
    "UQA330": "준공업지역",
    "UQA410": "보전녹지지역",
    "UQA420": "생산녹지지역",
    "UQA430": "자연녹지지역",
    "UQB100": "보전관리지역",
    "UQB200": "생산관리지역",
    "UQB300": "계획관리지역",
    "UQC001": "농림지역지역",
    "UQC100": "농림지역지역",
    "UQD001": "자연환경보전지역",
    "UQD100": "자연환경보전지역",
}


SIDO_ALIASES = {
    "서울특별시": "서울",
    "서울시": "서울",
    "서울": "서울",
    "부산광역시": "부산",
    "부산시": "부산",
    "부산": "부산",
    "대구광역시": "대구",
    "대구시": "대구",
    "대구": "대구",
    "인천광역시": "인천",
    "인천시": "인천",
    "인천": "인천",
    "광주광역시": "광주",
    "광주시": "광주",
    "광주": "광주",
    "대전광역시": "대전",
    "대전시": "대전",
    "대전": "대전",
    "울산광역시": "울산",
    "울산시": "울산",
    "울산": "울산",
    "세종특별자치시": "세종시",
    "세종시": "세종시",
    "세종": "세종시",
    "경기도": "경기",
    "경기": "경기",
    "강원특별자치도": "강원",
    "강원도": "강원",
    "강원": "강원",
    "충청북도": "충북",
    "충북": "충북",
    "충청남도": "충남",
    "충남": "충남",
    "전북특별자치도": "전북",
    "전라북도": "전북",
    "전북": "전북",
    "전라남도": "전남",
    "전남": "전남",
    "경상북도": "경북",
    "경북": "경북",
    "경상남도": "경남",
    "경남": "경남",
    "제주특별자치도": "제주",
    "제주도": "제주",
    "제주": "제주",
}


class ResourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            attrs = dict(attrs)
            href = attrs.get("href", "")
            if "downloadResourceFile.do" in href:
                self._href = href
                self._text = []

    def handle_data(self, data):
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href:
            label = " ".join("".join(self._text).split())
            if label:
                self.resources.append({"label": label, "href": self._href})
            self._href = None
            self._text = []


def http_get_text(url, params=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as res:
        return res.read().decode("utf-8", errors="replace")


def http_download(url, target, params=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    target.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=600) as res, target.open("wb") as f:
        while True:
            chunk = res.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    if target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"다운로드 응답이 0 byte입니다. VWorld 사이트 로그인/다운로드 정책을 확인해야 합니다: {url}")


def normalize_region_name(name):
    return SIDO_ALIASES.get(name, name)


def radius_label(radius_m):
    if abs(radius_m % 1000) < 1e-9:
        return f"{int(radius_m / 1000)}km"
    return f"{int(radius_m)}m"


def region_from_structure(structure, refined_text):
    """VWorld 지오코딩 응답의 구조화 정보(level1=시도, level2=시군구)에서 시도·시군구를 추출.
    구조화 정보가 없으면 텍스트 파싱으로 폴백한다. (리소스 라벨은 '_시도_시군구.zip' 형태)"""
    structure = structure or {}
    lv1 = (structure.get("level1") or "").strip()
    lv2 = (structure.get("level2") or "").strip()
    sido = normalize_region_name(lv1) if lv1 else None
    # 일반구(예: '성남시 분당구')는 공백을 '_'로, 자치구(예: '영등포구')는 그대로
    sigungu = lv2.replace(" ", "_") if lv2 else None
    if not sido:
        return parse_region_from_text(refined_text)
    return sido, sigungu


def parse_region_from_text(text):
    tokens = text.split()
    if not tokens:
        raise ValueError("주소에서 시도/시군구를 읽지 못했습니다.")
    # 시도: 토큰 중 알려진 시도명에 매칭되는 첫 토큰을 사용(주소가 시도로 시작하지 않아도 대응)
    sido = None
    sido_idx = 0
    for i, token in enumerate(tokens):
        if token in SIDO_ALIASES:
            sido = SIDO_ALIASES[token]
            sido_idx = i
            break
    if sido is None:
        sido = normalize_region_name(tokens[0])
    sigungu = None
    region_tokens = tokens[sido_idx + 1:sido_idx + 5]
    for idx, token in enumerate(region_tokens):
        if token.endswith("시") and idx + 1 < len(region_tokens) and region_tokens[idx + 1].endswith("구"):
            sigungu = f"{token}_{region_tokens[idx + 1]}"
            break
        if token.endswith(("군", "구")):
            sigungu = token
            break
        if token.endswith("시"):
            sigungu = token
    return sido, sigungu


def geocode_address(address, api_key):
    last_error = None
    for addr_type in ("ROAD", "PARCEL"):
        params = {
            "service": "address",
            "request": "getcoord",
            "version": "2.0",
            "crs": "epsg:5174",
            "refine": "true",
            "simple": "false",
            "format": "json",
            "type": addr_type,
            "address": address,
            "key": api_key,
        }
        data = json.loads(http_get_text(VWORLD_GEOCODE_URL, params))
        response = data.get("response", {})
        if response.get("status") == "OK":
            result = response["result"]
            point = result["point"]
            refined_obj = result.get("refined", {}) or {}
            refined = refined_obj.get("text") or address
            structure = refined_obj.get("structure", {}) or {}
            return float(point["x"]), float(point["y"]), refined, structure
        last_error = response
    raise ValueError(f"VWorld 지오코딩 실패: {last_error}")


def reverse_region(x, y, api_key, crs="epsg:5174"):
    """좌표를 역지오코딩(getAddress)하여 (시도, 시군구)를 반환.
    forward getcoord는 refined.structure가 비어 시도/시군구를 못 주는 경우가 많아,
    좌표 기준 reverse가 가장 신뢰성 높다. structure.level1=시도, level2=시군구."""
    params = {
        "service": "address", "request": "getAddress", "version": "2.0",
        "crs": crs, "point": f"{x},{y}", "format": "json", "type": "PARCEL", "key": api_key,
    }
    try:
        data = json.loads(http_get_text(VWORLD_GEOCODE_URL, params))
    except Exception:
        return None, None
    resp = data.get("response", {})
    if resp.get("status") != "OK":
        return None, None
    result = resp.get("result", [])
    if isinstance(result, dict):
        result = [result]
    for item in result:
        st = item.get("structure", {}) or {}
        lv1 = (st.get("level1") or "").strip()
        lv2 = (st.get("level2") or "").strip()
        if lv1:
            # 일반구(예: '성남시 분당구')는 '_'로, 자치구(예: '영등포구')는 그대로
            return normalize_region_name(lv1), (lv2.replace(" ", "_") if lv2 else None)
    return None, None


def load_resource_index(cache_path):
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    html = http_get_text(DATASET_INDEX_URL)
    parser = ResourceParser()
    parser.feed(html)
    resources = parser.resources
    if not resources:
        raise RuntimeError("연속지적도 5174 다운로드 리소스 목록을 찾지 못했습니다.")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(resources, ensure_ascii=False, indent=2), encoding="utf-8")
    return resources


def match_resource(resources, sido, sigungu):
    preferred = []
    fallback = []
    sido_part = f"_{sido}"
    for item in resources:
        label = item["label"]
        if sido_part not in label:
            continue
        if sigungu and f"_{sigungu}.zip" in label:
            preferred.append(item)
        if label.endswith(f"_{sido}.zip 데이터 SHP") or label.endswith(f"_{sido}.zip"):
            fallback.append(item)
    candidates = preferred or fallback
    if not candidates:
        raise ValueError(f"다운로드 리소스를 찾지 못했습니다: {sido} {sigungu or ''}".strip())
    return candidates[0]


def load_road_resource_index(cache_path):
    """도로명주소 실폭도로(시도별) 다운로드 리소스 목록.
    라벨은 <a href='...downloadResourceFile.do?...fileNo=N'> (도로명주소)실폭도로_{시도}.zip 데이터 SHP </a>
    형태로 <a> 안에 있다(href는 작은따옴표). <a>를 직접 파싱해 라벨↔fileNo를 정확히 매핑."""
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    html = http_get_text(ROAD_INDEX_URL)
    resources = []
    for m in re.finditer(
        r"<a[^>]*href=['\"]([^'\"]*downloadResourceFile\.do[^'\"]*)['\"][^>]*>(.*?)</a>", html, re.S
    ):
        href = m.group(1)
        label = " ".join(re.sub(r"<[^>]+>", "", m.group(2)).split())
        if "실폭도로" in label and ".zip" in label:
            if href.startswith("downloadResourceFile"):
                href = "https://www.vworld.kr/dtmk/" + href
            resources.append({"label": label, "href": href})
    if not resources:
        raise RuntimeError("도로명주소 실폭도로 다운로드 리소스 목록을 찾지 못했습니다.")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(resources, ensure_ascii=False, indent=2), encoding="utf-8")
    return resources


def match_road_resource(resources, sido):
    """'실폭도로_{시도}.zip'에서 시도를 정규화해 매칭(전북특별자치도→전북, 세종→세종시 등)."""
    for item in resources:
        m = re.search(r"실폭도로_([^.]+)\.zip", item["label"])
        if m and normalize_region_name(m.group(1).strip()) == sido:
            return item
    return None


def extract_zip_find(zip_path, extract_dir, name_contains):
    """ZIP을 풀고 이름에 name_contains를 포함하는 .shp를 우선 반환(없으면 첫 .shp)."""
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    shps = list(extract_dir.rglob("*.shp"))
    if not shps:
        raise FileNotFoundError(f"SHP 파일을 찾지 못했습니다: {extract_dir}")
    matched = [s for s in shps if name_contains.lower() in s.name.lower()]
    return (matched or shps)[0]


def fetch_wfs_features(typename, bbox4326, api_key, domain, maxf):
    """VWorld WFS(req/wfs)에서 bbox(EPSG:4326) 내 GeoJSON 피처 목록을 반환. 실패 시 빈 목록."""
    params = {
        "REQUEST": "GetFeature", "TYPENAME": typename, "VERSION": "1.1.0",
        "MAXFEATURES": str(maxf), "SRSNAME": "EPSG:4326", "OUTPUT": "json",
        "BBOX": bbox4326, "KEY": api_key,
    }
    if domain:
        params["DOMAIN"] = domain
    try:
        data = json.loads(http_get_text(VWORLD_WFS_URL, params))
        return data.get("features") or []
    except Exception as exc:
        print(f"도시계획시설 WFS({typename}) 조회 경고: {str(exc)[:140]}")
        return []


def build_plan_facility_lines(x, y, radius_m, api_key, domain, work_dir, result_dir, stem, r_label):
    """도시계획시설(도로 lt_c_upisuq151 + 교통시설 lt_c_upisuq152)을 WFS로 받아
    반경 클립 → 면 경계선 SHP를 만든다(EPSG:5174). (라인 SHP 경로, 피처수) 반환."""
    # 5174 중심 → 4326 lon/lat → 대략 bbox(도분)
    t = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:5174"),
        QgsCoordinateReferenceSystem("EPSG:4326"),
        QgsProject.instance(),
    )
    pt = t.transform(QgsPointXY(x, y))
    lon, lat = pt.x(), pt.y()
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(math.cos(math.radians(lat)), 1e-9))
    bbox = f"{lon - dlon},{lat - dlat},{lon + dlon},{lat + dlat}"
    feats = []
    for typename, maxf in PLAN_FACILITY_TYPENAMES:
        feats.extend(fetch_wfs_features(typename, bbox, api_key, domain, maxf))
    if not feats:
        return None, 0
    work_dir.mkdir(parents=True, exist_ok=True)
    gj_path = work_dir / "plan_facilities.geojson"
    gj_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False),
        encoding="utf-8",
    )
    selected_shp = result_dir / f"{stem}_{r_label}_plan.shp"
    count = export_zone_within_radius(gj_path, x, y, radius_m, selected_shp)
    if count <= 0:
        return None, 0
    line_shp = result_dir / f"{stem}_{r_label}_plan_lines.shp"
    export_polygon_boundaries_to_lines(selected_shp, line_shp)
    return line_shp, count


def download_resource(resource, target_dir):
    label = resource["label"].replace(" 데이터 SHP", "")
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", label)
    zip_path = target_dir / safe_name
    if zip_path.exists() and zip_path.stat().st_size > 0:
        return zip_path
    href = resource["href"]
    if href.startswith("/"):
        href = urllib.parse.urljoin("https://www.vworld.kr", href)
    elif href.startswith("javascript"):
        parsed = urllib.parse.urlparse(href)
        raise ValueError(f"예상하지 못한 다운로드 링크입니다: {parsed}")
    if not href.startswith("http"):
        href = urllib.parse.urljoin("https://www.vworld.kr", href)
    http_download(href, zip_path)
    return zip_path


def extract_zip(zip_path, extract_dir):
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    shp_files = list(extract_dir.rglob("*.shp"))
    if not shp_files:
        raise FileNotFoundError(f"SHP 파일을 찾지 못했습니다: {extract_dir}")
    return shp_files[0]


def write_address_point(x, y, out_path):
    fields = QgsFields()
    fields.append(QgsField("name", QVariant.String))
    writer = QgsVectorFileWriter(
        str(out_path),
        "UTF-8",
        fields,
        QgsWkbTypes.Point,
        QgsCoordinateReferenceSystem("EPSG:5174"),
        "ESRI Shapefile",
    )
    feat = QgsFeature(fields)
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
    feat.setAttribute("name", "address")
    writer.addFeature(feat)
    del writer


def export_within_radius(source_shp, x, y, radius_m, selected_shp):
    source = QgsVectorLayer(str(source_shp), "cadastre_source", "ogr")
    if not source.isValid():
        raise RuntimeError(f"레이어를 불러오지 못했습니다: {source_shp}")
    target_crs = QgsCoordinateReferenceSystem("EPSG:5174")
    if source.crs().authid().upper() != "EPSG:5174":
        raise RuntimeError(f"입력 SHP 좌표계가 EPSG:5174가 아닙니다: {source.crs().authid()}")
    buffer_geom = QgsGeometry.fromPointXY(QgsPointXY(x, y)).buffer(radius_m, 64)
    ids = []
    for feat in source.getFeatures():
        geom = feat.geometry()
        if geom and not geom.isEmpty() and geom.intersects(buffer_geom):
            ids.append(feat.id())
    if not ids:
        raise RuntimeError("반경 안에서 선택된 폴리곤이 없습니다.")
    source.selectByIds(ids)
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "ESRI Shapefile"
    options.fileEncoding = "UTF-8"
    options.onlySelectedFeatures = True
    err, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
        source,
        str(selected_shp),
        QgsProject.instance().transformContext(),
        options,
    )
    if err != QgsVectorFileWriter.NoError:
        raise RuntimeError(f"선택 SHP 저장 실패: {msg}")
    return len(ids)


def parse_zone_zip_args(values):
    result = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"zone zip argument must use key=path format: {value}")
        key, path = value.split("=", 1)
        key = key.strip()
        if key not in ZONE_DEFINITIONS:
            raise ValueError(f"unknown zone key: {key}")
        result[key] = Path(path)
    return result


def first_zone_code(feature):
    for value in feature.attributes():
        if value is None:
            continue
        text = str(value).strip().upper()
        match = re.search(r"\bUQ[A-D][0-9A-Z]{3}\b", text)
        if match:
            return match.group(0)
    return ""


def first_zone_uname(feature, category_values):
    fields = feature.fields()
    for field_name in ("uname", "UNAME", "A6", "A8", "A9"):
        if fields.indexOf(field_name) >= 0:
            value = feature.attribute(field_name)
            if value is not None and str(value).strip() in category_values:
                return str(value).strip()
    for value in feature.attributes():
        if value is not None and str(value).strip() in category_values:
            return str(value).strip()
    code = first_zone_code(feature)
    return ZONE_CODE_TO_UNAME.get(code, "")


def enrich_zone_attributes(zone_shp, zone_style):
    layer = QgsVectorLayer(str(zone_shp), "zone_selected_attributes", "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Could not load saved zone layer: {zone_shp}")
    provider = layer.dataProvider()
    added = []
    if layer.fields().indexOf("uname") < 0:
        added.append(QgsField("uname", QVariant.String))
    if layer.fields().indexOf("zone_code") < 0:
        added.append(QgsField("zone_code", QVariant.String))
    if layer.fields().indexOf("dxf_layer") < 0:
        added.append(QgsField("dxf_layer", QVariant.String))
    if added:
        provider.addAttributes(added)
        layer.updateFields()
    uname_idx = layer.fields().indexOf("uname")
    code_idx = layer.fields().indexOf("zone_code")
    dxf_layer_idx = layer.fields().indexOf("dxf_layer")
    category_values = zone_style.get("category_values", set())
    categories = zone_style.get("categories", {})
    changes = {}
    for feat in layer.getFeatures():
        code = first_zone_code(feat)
        uname = first_zone_uname(feat, category_values) or ZONE_CODE_TO_UNAME.get(code, "")
        row = {}
        if uname_idx >= 0 and uname:
            row[uname_idx] = uname
        if code_idx >= 0 and code:
            row[code_idx] = code
        if dxf_layer_idx >= 0 and uname in categories:
            row[dxf_layer_idx] = categories[uname]["layer"]
        if row:
            changes[feat.id()] = row
    if changes:
        provider.changeAttributeValues(changes)
    layer.updateFields()
    return len(changes)


def export_zone_within_radius(source_shp, x, y, radius_m, selected_shp):
    source = QgsVectorLayer(str(source_shp), "zone_source", "ogr")
    if not source.isValid():
        raise RuntimeError(f"Could not load zone layer: {source_shp}")
    target_crs = QgsCoordinateReferenceSystem("EPSG:5174")
    source_crs = source.crs()
    center = QgsPointXY(x, y)
    write_transform = None
    if source_crs.isValid() and source_crs.authid().upper() != "EPSG:5174":
        transform = QgsCoordinateTransform(target_crs, source_crs, QgsProject.instance())
        center = transform.transform(center)
        write_transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
    buffer_geom = QgsGeometry.fromPointXY(center).buffer(radius_m, 64)
    ids = []
    for feat in source.getFeatures():
        geom = feat.geometry()
        if geom and not geom.isEmpty() and geom.intersects(buffer_geom):
            ids.append(feat.id())
    if not ids:
        return 0
    source.selectByIds(ids)
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "ESRI Shapefile"
    options.fileEncoding = "UTF-8"
    options.onlySelectedFeatures = True
    if write_transform:
        options.ct = write_transform
    err, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
        source,
        str(selected_shp),
        QgsProject.instance().transformContext(),
        options,
    )
    if err != QgsVectorFileWriter.NoError:
        raise RuntimeError(f"Zone SHP save failed: {msg}")
    return len(ids)


def export_polygon_boundaries_to_lines(polygon_shp, line_shp):
    polygon_layer = QgsVectorLayer(str(polygon_shp), "cadastre_selected", "ogr")
    if not polygon_layer.isValid():
        raise RuntimeError(f"선택 레이어를 불러오지 못했습니다: {polygon_shp}")
    writer = QgsVectorFileWriter(
        str(line_shp),
        "UTF-8",
        polygon_layer.fields(),
        QgsWkbTypes.MultiLineString,
        polygon_layer.crs(),
        "ESRI Shapefile",
    )
    written = 0
    for feat in polygon_layer.getFeatures():
        geom = feat.geometry()
        if not geom or geom.isEmpty():
            continue
        rings = []
        polygons = geom.asMultiPolygon()
        if not polygons:
            polygon = geom.asPolygon()
            polygons = [polygon] if polygon else []
        for polygon in polygons:
            for ring in polygon:
                if ring:
                    rings.append(ring)
        if not rings:
            continue
        line_feat = QgsFeature(polygon_layer.fields())
        line_feat.setGeometry(QgsGeometry.fromMultiPolylineXY(rings))
        line_feat.setAttributes(feat.attributes())
        writer.addFeature(line_feat)
        written += 1
    del writer
    if written == 0:
        raise RuntimeError("DXF용 경계 라인을 생성하지 못했습니다.")
    return written


def normalize_parcel_key(value):
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace(",", " ").replace("번지", " ").replace("번", " ")
    text = re.sub(r"[‐‑‒–—―]", "-", text)
    matches = list(re.finditer(r"(?<!\d)(\d{1,5})(?:\s*-\s*(\d{1,5}))?(?!\d)", text))
    if not matches:
        return ""
    match = matches[-1]
    main = str(int(match.group(1)))
    sub = match.group(2)
    if sub is not None and int(sub) != 0:
        return f"{main}-{int(sub)}"
    return main


def read_xlsx_values(path):
    values = []
    with zipfile.ZipFile(path) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for item in root.findall(".//x:si", ns):
                parts = [node.text or "" for node in item.findall(".//x:t", ns)]
                shared.append("".join(parts))
        sheet_names = sorted(name for name in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", name))
        if not sheet_names:
            return values
        root = ET.fromstring(zf.read(sheet_names[0]))
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        for cell in root.findall(".//x:c", ns):
            cell_type = cell.attrib.get("t")
            if cell_type == "s":
                v = cell.find("x:v", ns)
                if v is not None and v.text and v.text.isdigit():
                    idx = int(v.text)
                    if 0 <= idx < len(shared):
                        values.append(shared[idx])
            elif cell_type == "inlineStr":
                text = "".join(node.text or "" for node in cell.findall(".//x:t", ns))
                values.append(text)
            else:
                v = cell.find("x:v", ns)
                if v is not None and v.text:
                    values.append(v.text)
    return values


def read_parcel_keys(parcel_list_path):
    if not parcel_list_path:
        return set()
    path = Path(parcel_list_path)
    if not path.exists():
        raise FileNotFoundError(f"지번 리스트 파일을 찾지 못했습니다: {path}")
    suffix = path.suffix.lower()
    values = []
    if suffix == ".xlsx":
        values = read_xlsx_values(path)
    elif suffix in {".csv", ".txt"}:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            if suffix == ".csv":
                for row in csv.reader(f):
                    values.extend(row)
            else:
                values = list(f)
    else:
        raise ValueError("지번 리스트는 .xlsx, .csv, .txt 파일만 지원합니다.")
    keys = {normalize_parcel_key(value) for value in values}
    return {key for key in keys if key}


def feature_parcel_key(feature):
    fields = feature.fields()
    preferred_fields = ("JIBUN", "jibun", "A5", "A6", "PNU")
    for field_name in preferred_fields:
        if fields.indexOf(field_name) >= 0:
            key = normalize_parcel_key(feature.attribute(field_name))
            if key:
                return key
    for value in feature.attributes():
        key = normalize_parcel_key(value)
        if key:
            return key
    return ""


def export_parcel_list_selection(selected_shp, parcel_keys, selected_out, lines_out):
    if not parcel_keys:
        return 0
    source = QgsVectorLayer(str(selected_shp), "excel_parcel_source", "ogr")
    if not source.isValid():
        raise RuntimeError(f"엑셀 지번 선택 원본 레이어를 불러오지 못했습니다: {selected_shp}")
    ids = []
    for feat in source.getFeatures():
        if feature_parcel_key(feat) in parcel_keys:
            ids.append(feat.id())
    if not ids:
        return 0
    source.selectByIds(ids)
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "ESRI Shapefile"
    options.fileEncoding = "UTF-8"
    options.onlySelectedFeatures = True
    err, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
        source,
        str(selected_out),
        QgsProject.instance().transformContext(),
        options,
    )
    if err != QgsVectorFileWriter.NoError:
        raise RuntimeError(f"엑셀 지번 선택 SHP 저장 실패: {msg}")
    export_polygon_boundaries_to_lines(selected_out, lines_out)
    return len(ids)


def line_style_from_qml(style_path):
    defaults = {
        "line_color": "255,0,255,255",
        "line_width": "0",
        "line_width_unit": "MM",
        "line_style": "solid",
    }
    if not style_path or not Path(style_path).exists():
        return defaults
    root = ET.parse(style_path).getroot()
    renderer = root.find(".//renderer-v2")
    line_layer = None
    if renderer is not None:
        line_layer = renderer.find(".//layer[@class='SimpleLine']")
    if line_layer is None:
        line_layer = root.find(".//layer[@class='SimpleLine']")
    if line_layer is None:
        return defaults
    values = defaults.copy()
    for opt in line_layer.findall(".//Option"):
        name = opt.attrib.get("name")
        if name in values:
            values[name] = opt.attrib.get("value", values[name])
    return values


def label_style_from_qml(style_path):
    defaults = {
        "field_name": "JIBUN",
        "font_size_pt": 2.0,
        "text_color": "255,0,255,255",
    }
    if not style_path or not Path(style_path).exists():
        return defaults
    root = ET.parse(style_path).getroot()
    text_style = root.find(".//labeling//text-style")
    if text_style is None:
        return defaults
    values = defaults.copy()
    values["field_name"] = text_style.attrib.get("fieldName", values["field_name"]) or values["field_name"]
    values["font_size_pt"] = 2.0
    values["text_color"] = text_style.attrib.get("textColor", values["text_color"]) or values["text_color"]
    return values


def rgba_tuple(value, fallback=(200, 200, 200, 255)):
    try:
        parts = [int(float(part.strip())) for part in str(value).split(",") if part.strip() != ""]
    except (TypeError, ValueError):
        return fallback
    while len(parts) < 4:
        parts.append(255)
    return tuple(max(0, min(255, part)) for part in parts[:4])


def truecolor_value(rgb):
    r, g, b = rgb[:3]
    return (r << 16) + (g << 8) + b


def safe_dxf_layer_name(text, fallback):
    cleaned = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", str(text)).strip("_")
    return (cleaned or fallback)[:80]


def zone_style_from_qml(style_path):
    result = {
        "attr": "uname",
        "categories": {},
        "category_values": set(),
    }
    if not style_path or not Path(style_path).exists():
        return result
    root = ET.parse(style_path).getroot()
    renderer = root.find(".//renderer-v2")
    if renderer is None:
        return result
    result["attr"] = renderer.attrib.get("attr", result["attr"]) or result["attr"]
    symbols = renderer.find("symbols")
    categories = renderer.find("categories")
    if symbols is None or categories is None:
        return result
    symbol_colors = {}
    for symbol in symbols.findall("symbol"):
        symbol_name = symbol.attrib.get("name")
        fill = symbol.find(".//layer[@class='SimpleFill']")
        if not symbol_name or fill is None:
            continue
        color = None
        for opt in fill.findall(".//Option"):
            if opt.attrib.get("name") == "color":
                color = opt.attrib.get("value")
                break
        if color:
            symbol_colors[symbol_name] = rgba_tuple(color)
    for category in categories.findall("category"):
        value = category.attrib.get("value", "")
        symbol_name = category.attrib.get("symbol", "")
        if not value or symbol_name not in symbol_colors:
            continue
        label = category.attrib.get("label") or value
        rgba = symbol_colors[symbol_name]
        result["category_values"].add(value)
        result["categories"][value] = {
            "label": label,
            "rgba": rgba,
            "color": dxf_color_index(",".join(str(part) for part in rgba)),
            "truecolor": truecolor_value(rgba),
            "layer": safe_dxf_layer_name(value, label or value),
        }
    return result


def zone_feature_style(feature, zone_style, default_layer, default_color, default_rgb):
    uname = first_zone_uname(feature, zone_style.get("category_values", set()))
    if uname and uname in zone_style.get("categories", {}):
        return zone_style["categories"][uname]
    rgba = (*default_rgb, 255)
    return {
        "label": default_layer,
        "rgba": rgba,
        "color": default_color,
        "truecolor": truecolor_value(rgba),
        "layer": default_layer,
    }


def dxf_pairs_from_text(text):
    lines = text.splitlines()
    pairs = []
    for idx in range(0, len(lines) - 1, 2):
        pairs.append([lines[idx].strip(), lines[idx + 1]])
    return pairs


def dxf_text_from_pairs(pairs):
    return "".join(f"{code}\r\n{value}\r\n" for code, value in pairs)


def upsert_dxf_pair(entity, code, value, insert_at=None, after_codes=None):
    if insert_at is not None:
        for pair in entity:
            if pair[0] == code:
                pair[1] = str(value)
                return
        entity.insert(insert_at, [code, str(value)])
        return
    after_codes = after_codes or []
    for pair in entity:
        if pair[0] == code:
            pair[1] = str(value)
            return
    insert_at = 1
    for idx, pair in enumerate(entity):
        if pair[0] in after_codes:
            insert_at = idx + 1
    entity.insert(insert_at, [code, str(value)])


def remove_dxf_pairs(entity, code):
    return [pair for pair in entity if pair[0] != code]


def entity_common_insert_index(entity):
    idx = 1
    for pos, pair in enumerate(entity):
        if pos > 0 and pair[0] == "100" and pair[1] != "AcDbEntity":
            return pos
        if pair[0] == "8":
            idx = pos + 1
    return idx


def force_zone_dxf_colors(dxf_text, zone_style):
    by_layer = {}
    for style in zone_style.get("categories", {}).values():
        style_values = {
            "color": style["color"],
            "truecolor": style["truecolor"],
        }
        by_layer[style["layer"]] = style_values
        by_layer[f"{style['layer']}_HATCH"] = style_values
    if not by_layer:
        return dxf_text

    pairs = dxf_pairs_from_text(dxf_text)
    output = []
    idx = 0
    while idx < len(pairs):
        code, value = pairs[idx]
        if code == "0" and value in {"LAYER", "HATCH", "LWPOLYLINE", "POLYLINE"}:
            entity = []
            while idx < len(pairs):
                if entity and pairs[idx][0] == "0":
                    break
                entity.append([pairs[idx][0], pairs[idx][1]])
                idx += 1

            layer_name = None
            for pair in entity:
                if pair[0] in {"2", "8"} and pair[1] in by_layer:
                    layer_name = pair[1]
                    break
            if layer_name:
                style = by_layer[layer_name]
                if value == "LAYER":
                    upsert_dxf_pair(entity, "62", style["color"], after_codes=["2", "70"])
                    upsert_dxf_pair(entity, "420", style["truecolor"], after_codes=["62"])
                else:
                    entity = remove_dxf_pairs(entity, "62")
                    entity = remove_dxf_pairs(entity, "420")
                    insert_at = entity_common_insert_index(entity)
                    entity.insert(insert_at, ["62", str(style["color"])])
                    entity.insert(insert_at + 1, ["420", str(style["truecolor"])])
            output.extend(entity)
            continue

        output.append([code, value])
        idx += 1
    return dxf_text_from_pairs(output)


def force_solid_hatch_compatibility(dxf_text):
    pairs = dxf_pairs_from_text(dxf_text)
    output = []
    idx = 0
    while idx < len(pairs):
        code, value = pairs[idx]
        if code == "0" and value == "HATCH":
            entity = [[code, value]]
            idx += 1
            while idx < len(pairs):
                if pairs[idx][0] == "0":
                    break
                entity.append(pairs[idx])
                idx += 1

            try:
                hatch_class_idx = next(
                    pos for pos, pair in enumerate(entity)
                    if pair == ["100", "AcDbHatch"]
                )
            except StopIteration:
                output.extend(entity)
                continue

            pattern_idx = None
            extrusion_y_idx = None
            elevation_y_idx = None
            for pos in range(hatch_class_idx + 1, len(entity)):
                if entity[pos][0] == "2":
                    pattern_idx = pos
                    break
                if entity[pos][0] == "20" and elevation_y_idx is None:
                    elevation_y_idx = pos
                if entity[pos][0] == "220":
                    extrusion_y_idx = pos
            search_end = pattern_idx or len(entity)
            has_elevation_z = any(pair[0] == "30" for pair in entity[hatch_class_idx + 1:search_end])
            has_extrusion_z = any(pair[0] == "230" for pair in entity[hatch_class_idx + 1:search_end])
            if elevation_y_idx is not None and not has_elevation_z:
                entity.insert(elevation_y_idx + 1, ["30", "0.0"])
                if extrusion_y_idx is not None and extrusion_y_idx > elevation_y_idx:
                    extrusion_y_idx += 1
                if pattern_idx is not None and pattern_idx > elevation_y_idx:
                    pattern_idx += 1
            if extrusion_y_idx is not None and not has_extrusion_z:
                entity.insert(extrusion_y_idx + 1, ["230", "1.0"])

            output.extend(entity)
            continue

        output.append([code, value])
        idx += 1
    return dxf_text_from_pairs(output)


def force_layer_text_height(dxf_text, layer_name, height):
    pairs = dxf_pairs_from_text(dxf_text)
    output = []
    idx = 0
    while idx < len(pairs):
        code, value = pairs[idx]
        if code == "0" and value in {"TEXT", "MTEXT"}:
            entity = [[code, value]]
            idx += 1
            while idx < len(pairs):
                if pairs[idx][0] == "0":
                    break
                entity.append([pairs[idx][0], pairs[idx][1]])
                idx += 1

            entity_layer = next((pair[1] for pair in entity if pair[0] == "8"), "")
            if entity_layer == layer_name:
                upsert_dxf_pair(entity, "40", f"{height:g}", after_codes=["30", "11", "21", "31"])
            output.extend(entity)
            continue

        output.append([code, value])
        idx += 1
    return dxf_text_from_pairs(output)


def remove_non_text_entities_from_layer(dxf_text, layer_name):
    pairs = dxf_pairs_from_text(dxf_text)
    output = []
    idx = 0
    text_types = {"TEXT", "MTEXT"}
    table_types = {"LAYER", "STYLE", "LTYPE"}
    while idx < len(pairs):
        code, value = pairs[idx]
        if code == "0":
            entity = [[code, value]]
            idx += 1
            while idx < len(pairs):
                if pairs[idx][0] == "0":
                    break
                entity.append([pairs[idx][0], pairs[idx][1]])
                idx += 1

            if value not in text_types and value not in table_types:
                entity_layer = next((pair[1] for pair in entity if pair[0] == "8"), "")
                if entity_layer == layer_name:
                    continue
            output.extend(entity)
            continue

        output.append([code, value])
        idx += 1
    return dxf_text_from_pairs(output)


def force_dxf_codepage(dxf_text, codepage="ANSI_949"):
    pairs = dxf_pairs_from_text(dxf_text)
    for idx in range(len(pairs) - 1):
        if pairs[idx] == ["9", "$DWGCODEPAGE"]:
            pairs[idx + 1] = ["3", codepage]
            return dxf_text_from_pairs(pairs)

    insert_at = None
    for idx in range(len(pairs) - 1):
        if pairs[idx] == ["0", "SECTION"] and pairs[idx + 1] == ["2", "HEADER"]:
            insert_at = idx + 2
            break
    if insert_at is None:
        return dxf_text
    pairs[insert_at:insert_at] = [["9", "$DWGCODEPAGE"], ["3", codepage]]
    return dxf_text_from_pairs(pairs)


def force_text_style_font(dxf_text, style_name=DXF_TEXT_STYLE_NAME, font_file=DXF_TEXT_FONT_FILE):
    pairs = dxf_pairs_from_text(dxf_text)
    output = []
    idx = 0
    while idx < len(pairs):
        code, value = pairs[idx]
        if code == "0" and value in {"STYLE", "TEXT", "MTEXT"}:
            entity = [[code, value]]
            idx += 1
            while idx < len(pairs):
                if pairs[idx][0] == "0":
                    break
                entity.append([pairs[idx][0], pairs[idx][1]])
                idx += 1

            if value == "STYLE":
                entity_style = next((pair[1] for pair in entity if pair[0] == "2"), "")
                if entity_style == style_name:
                    upsert_dxf_pair(entity, "70", "0", after_codes=["2"])
                    upsert_dxf_pair(entity, "42", "2.0", after_codes=["71"])
                    upsert_dxf_pair(entity, "3", font_file, after_codes=["42"])
                    upsert_dxf_pair(entity, "4", "", after_codes=["3"])
                    entity = [pair for pair in entity if pair[0] not in {"1001", "1000", "1071"}]
                    entity.extend(
                        [
                            ["1001", "ACAD"],
                            ["1000", DXF_TEXT_FONT_FAMILY],
                            ["1071", "34"],
                        ]
                    )
            else:
                entity_layer = next((pair[1] for pair in entity if pair[0] == "8"), "")
                if entity_layer == "JIBUN":
                    upsert_dxf_pair(entity, "7", style_name, after_codes=["1"])
            output.extend(entity)
            continue

        output.append([code, value])
        idx += 1
    return dxf_text_from_pairs(output)


def apply_label_style(layer, style_path):
    label_style = label_style_from_qml(style_path)
    field_name = label_style["field_name"]
    field_index = layer.fields().indexOf(field_name)
    if field_index < 0:
        print(f"라벨 경고: '{field_name}' 필드를 찾지 못해 지번 라벨을 건너뜁니다.")
        return False
    rgba = [int(part) for part in label_style["text_color"].split(",")]
    while len(rgba) < 4:
        rgba.append(255)
    text_format = QgsTextFormat()
    text_format.setFont(QFont(DXF_TEXT_FONT_FAMILY))
    text_format.setSize(label_style["font_size_pt"])
    text_format.setColor(QColor(rgba[0], rgba[1], rgba[2], rgba[3]))
    settings = QgsPalLayerSettings()
    settings.fieldName = field_name
    settings.enabled = True
    settings.placement = QgsPalLayerSettings.OverPoint
    settings.centroidWhole = True
    settings.centroidInside = True
    settings.setFormat(text_format)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()
    return True


def label_feature_count(polygon_shp, style_path):
    layer = QgsVectorLayer(str(polygon_shp), "cadastre_label_source", "ogr")
    if not layer.isValid():
        raise RuntimeError(f"라벨 원본 레이어를 불러오지 못했습니다: {polygon_shp}")
    label_style = label_style_from_qml(style_path)
    field_name = label_style["field_name"]
    field_index = layer.fields().indexOf(field_name)
    if field_index < 0:
        return 0
    count = 0
    for feat in layer.getFeatures():
        text = feat.attribute(field_name)
        if text is not None and str(text).strip():
            count += 1
    return count


def apply_line_style(layer, style_path):
    style = line_style_from_qml(style_path)
    rgba = [int(part) for part in style["line_color"].split(",")]
    while len(rgba) < 4:
        rgba.append(255)
    width = float(style["line_width"] or 0)
    symbol = QgsLineSymbol.createSimple(
        {
            "line_color": f"{rgba[0]},{rgba[1]},{rgba[2]},{rgba[3]}",
            "line_style": style["line_style"],
            "line_width": str(width),
            "line_width_unit": style["line_width_unit"],
        }
    )
    symbol.setColor(QColor(rgba[0], rgba[1], rgba[2], rgba[3]))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()


def dxf_color_index(rgba_text):
    try:
        r, g, b, *_ = [int(part) for part in rgba_text.split(",")]
    except ValueError:
        return 7
    candidates = {
        1: (255, 0, 0),
        2: (255, 255, 0),
        3: (0, 255, 0),
        4: (0, 255, 255),
        5: (0, 0, 255),
        6: (255, 0, 255),
        7: (255, 255, 255),
        8: (128, 128, 128),
        9: (192, 192, 192),
    }
    return min(candidates, key=lambda idx: sum((a - b) ** 2 for a, b in zip((r, g, b), candidates[idx])))


def dxf_pair(code, value):
    return f"{code}\r\n{value}\r\n"


def clean_dxf_text(value):
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def write_polyline_entity(parts, layer_name, color_index):
    if len(parts) < 2:
        return ""
    closed = parts[0] == parts[-1]
    vertices = parts[:-1] if closed else parts
    entity = [
        dxf_pair(0, "POLYLINE"),
        dxf_pair(8, layer_name),
        dxf_pair(62, color_index),
        dxf_pair(70, 1 if closed else 0),
        dxf_pair(66, 1),
    ]
    for point in vertices:
        entity.append(dxf_pair(0, "VERTEX"))
        entity.append(dxf_pair(8, layer_name))
        entity.append(dxf_pair(10, f"{point.x():.6f}"))
        entity.append(dxf_pair(20, f"{point.y():.6f}"))
        entity.append(dxf_pair(30, "0.0"))
    entity.append(dxf_pair(0, "SEQEND"))
    entity.append(dxf_pair(8, layer_name))
    return "".join(entity)


def write_text_entity(layer_name, x, y, height, text, color_index):
    text = clean_dxf_text(text)
    if not text:
        return ""
    return "".join(
        [
            dxf_pair(0, "TEXT"),
            dxf_pair(8, layer_name),
            dxf_pair(62, color_index),
            dxf_pair(10, f"{x:.6f}"),
            dxf_pair(20, f"{y:.6f}"),
            dxf_pair(30, "0.0"),
            dxf_pair(40, f"{height:.6f}"),
            dxf_pair(1, text),
            dxf_pair(7, "Standard"),
            dxf_pair(72, 1),
            dxf_pair(11, f"{x:.6f}"),
            dxf_pair(21, f"{y:.6f}"),
            dxf_pair(31, "0.0"),
            dxf_pair(73, 2),
        ]
    )


def hatch_segments_for_ring(ring, spacing=10.0):
    points = list(ring)
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        return []
    min_y = min(point.y() for point in points)
    max_y = max(point.y() for point in points)
    y = (int(min_y // spacing) - 1) * spacing
    segments = []
    while y <= max_y + spacing:
        intersections = []
        for idx, point in enumerate(points):
            nxt = points[(idx + 1) % len(points)]
            y1, y2 = point.y(), nxt.y()
            if abs(y2 - y1) < 1e-9:
                continue
            if y >= min(y1, y2) and y < max(y1, y2):
                x = point.x() + (y - y1) * (nxt.x() - point.x()) / (y2 - y1)
                intersections.append(x)
        intersections.sort()
        for idx in range(0, len(intersections) - 1, 2):
            x1, x2 = intersections[idx], intersections[idx + 1]
            if x2 - x1 > 0.5:
                segments.append(((x1, y), (x2, y)))
        y += spacing
    return segments


def create_zone_hatch_line_layers(zone_layers, zone_style, spacing=10.0):
    hatch_layers = {}
    for zone in zone_layers or []:
        config = ZONE_DEFINITIONS[zone["key"]]
        layer = QgsVectorLayer(str(zone["selected_shp"]), config["line_layer"], "ogr")
        if not layer.isValid():
            continue
        for feat in layer.getFeatures():
            geom = feat.geometry()
            if not geom or geom.isEmpty():
                continue
            style = zone_feature_style(feat, zone_style, config["line_layer"], config["color"], config["rgb"])
            hatch_layer = f"{style['layer']}_HATCH"
            if hatch_layer not in hatch_layers:
                r, g, b, a = style["rgba"]
                memory_layer = QgsVectorLayer("LineString?crs=EPSG:5174", hatch_layer, "memory")
                provider = memory_layer.dataProvider()
                provider.addAttributes([QgsField("dxf_layer", QVariant.String)])
                memory_layer.updateFields()
                symbol = QgsLineSymbol.createSimple(
                    {
                        "line_color": f"{r},{g},{b},{a}",
                        "line_width": "0.12",
                        "line_width_unit": "MM",
                    }
                )
                symbol.setColor(QColor(r, g, b, a))
                memory_layer.setRenderer(QgsSingleSymbolRenderer(symbol))
                hatch_layers[hatch_layer] = memory_layer
            memory_layer = hatch_layers[hatch_layer]
            provider = memory_layer.dataProvider()
            polygons = geom.asMultiPolygon()
            if not polygons:
                polygon = geom.asPolygon()
                polygons = [polygon] if polygon else []
            for polygon in polygons:
                if not polygon or not polygon[0]:
                    continue
                for a, b in hatch_segments_for_ring(polygon[0], spacing=spacing):
                    hatch_feature = QgsFeature(memory_layer.fields())
                    hatch_feature.setGeometry(
                        QgsGeometry.fromPolylineXY(
                            [QgsPointXY(a[0], a[1]), QgsPointXY(b[0], b[1])]
                        )
                    )
                    hatch_feature.setAttributes([hatch_layer])
                    provider.addFeature(hatch_feature)
    valid_layers = []
    for layer in hatch_layers.values():
        layer.updateExtents()
        if layer.isValid() and layer.featureCount() > 0:
            valid_layers.append(layer)
    return valid_layers


def apply_style_and_export_dxf(line_shp, polygon_shp, style_path, dxf_path, zone_layers=None, zone_style_path=None, parcel_list_line_shp=None, road_line_shp=None, plan_line_shp=None):
    line_layer = QgsVectorLayer(str(line_shp), "cadastre_boundary_lines", "ogr")
    if not line_layer.isValid():
        raise RuntimeError(f"DXF용 라인 레이어를 불러오지 못했습니다: {line_shp}")
    polygon_layer = QgsVectorLayer(str(polygon_shp), "cadastre_label_source", "ogr")
    if not polygon_layer.isValid():
        raise RuntimeError(f"DXF 라벨 원본 레이어를 불러오지 못했습니다: {polygon_shp}")

    zone_layers = zone_layers or []
    line_layer.setName("CADASTRE_LINE")
    polygon_layer.setName("JIBUN")
    apply_line_style(line_layer, style_path)
    apply_label_style(polygon_layer, style_path)
    invisible_fill = QgsFillSymbol.createSimple(
        {
            "color": "255,255,255,0",
            "outline_color": "255,255,255,0",
            "outline_width": "0",
            "style": "no",
        }
    )
    polygon_layer.setRenderer(QgsSingleSymbolRenderer(invisible_fill))

    zone_style = zone_style_from_qml(zone_style_path)
    export_layers = [line_layer, polygon_layer]
    if parcel_list_line_shp:
        parcel_list_layer = QgsVectorLayer(str(parcel_list_line_shp), PARCEL_LIST_LAYER_NAME, "ogr")
        if parcel_list_layer.isValid():
            parcel_list_layer.setName(PARCEL_LIST_LAYER_NAME)
            symbol = QgsLineSymbol.createSimple(
                {
                    "line_color": "255,0,0,255",
                    "line_width": "0.45",
                    "line_width_unit": "MM",
                    "line_style": "solid",
                }
            )
            symbol.setColor(QColor(255, 0, 0, 255))
            parcel_list_layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            parcel_list_layer.triggerRepaint()
            export_layers.append(parcel_list_layer)
    if road_line_shp:
        road_layer = QgsVectorLayer(str(road_line_shp), "SILPOK_ROAD", "ogr")
        if road_layer.isValid():
            road_layer.setName("SILPOK_ROAD")
            road_symbol = QgsLineSymbol.createSimple(
                {
                    "line_color": "120,120,120,255",
                    "line_width": "0.25",
                    "line_width_unit": "MM",
                    "line_style": "solid",
                }
            )
            road_symbol.setColor(QColor(120, 120, 120, 255))
            road_layer.setRenderer(QgsSingleSymbolRenderer(road_symbol))
            road_layer.setLabelsEnabled(False)
            road_layer.triggerRepaint()
            export_layers.append(road_layer)
    if plan_line_shp:
        plan_layer = QgsVectorLayer(str(plan_line_shp), "URBAN_PLAN_FACILITY", "ogr")
        if plan_layer.isValid():
            plan_layer.setName("URBAN_PLAN_FACILITY")
            plan_symbol = QgsLineSymbol.createSimple(
                {
                    "line_color": "0,160,160,255",
                    "line_width": "0.3",
                    "line_width_unit": "MM",
                    "line_style": "dash",
                }
            )
            plan_symbol.setColor(QColor(0, 160, 160, 255))
            plan_layer.setRenderer(QgsSingleSymbolRenderer(plan_symbol))
            plan_layer.setLabelsEnabled(False)
            plan_layer.triggerRepaint()
            export_layers.append(plan_layer)
    for zone in zone_layers:
        config = ZONE_DEFINITIONS[zone["key"]]
        zone_polygon_layer = QgsVectorLayer(str(zone["selected_shp"]), config["line_layer"], "ogr")
        if not zone_polygon_layer.isValid():
            continue
        zone_polygon_layer.setName(config["line_layer"])
        if zone_style_path and Path(zone_style_path).exists():
            zone_polygon_layer.loadNamedStyle(str(zone_style_path))
        else:
            r, g, b = config["rgb"]
            symbol = QgsFillSymbol.createSimple(
                {
                    "color": f"{r},{g},{b},255",
                    "outline_color": f"{r},{g},{b},0",
                    "outline_width": "0",
                    "style": "solid",
                }
            )
            symbol.setColor(QColor(r, g, b, 255))
            zone_polygon_layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        zone_polygon_layer.setLabelsEnabled(False)
        export_layers.insert(0, zone_polygon_layer)
    extent = QgsRectangle(line_layer.extent())
    for layer in export_layers:
        if layer.isValid() and not layer.extent().isEmpty():
            extent.combineExtentWith(layer.extent())

    map_settings = QgsMapSettings()
    map_settings.setLayers(export_layers)
    map_settings.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:5174"))
    map_settings.setExtent(extent)

    dxf = QgsDxfExport()
    dxf.setMapSettings(map_settings)
    dxf.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:5174"))
    dxf.setForce2d(True)
    dxf.setLayerTitleAsName(True)
    try:
        dxf.setSymbologyExport(Qgis.FeatureSymbologyExport.SymbolLayerSymbology)
    except AttributeError:
        pass
    try:
        dxf.setFlags(QgsDxfExport.FlagNoMText)
    except AttributeError:
        pass
    dxf_layers = []
    for layer in export_layers:
        dxf_layer_idx = layer.fields().indexOf("dxf_layer")
        if dxf_layer_idx >= 0:
            dxf_layers.append(QgsDxfExport.DxfLayer(layer, dxf_layer_idx))
        else:
            dxf_layers.append(QgsDxfExport.DxfLayer(layer))
    dxf.addLayers(dxf_layers)

    dxf_path = Path(dxf_path)
    dxf_path.parent.mkdir(parents=True, exist_ok=True)
    device = QFile(str(dxf_path))
    if not device.open(QIODevice.WriteOnly | QIODevice.Truncate):
        dxf_path = dxf_path.with_name(f"{dxf_path.stem}_new_{time.strftime('%Y%m%d_%H%M%S')}{dxf_path.suffix}")
        device = QFile(str(dxf_path))
        if not device.open(QIODevice.WriteOnly | QIODevice.Truncate):
            raise RuntimeError(f"DXF 파일을 열지 못했습니다: {dxf_path}")
    try:
        result = dxf.writeToFile(device, "CP949")
    finally:
        device.close()
    if result != QgsDxfExport.ExportResult.Success:
        raise RuntimeError(f"QGIS DXF 내보내기 실패: {result}")

    # QGIS writes standards-compliant DXF; normalizing layer names improves older CAD compatibility.
    dxf_text = Path(dxf_path).read_text(encoding="cp949", errors="replace")
    replacements = {
        "cadastre_boundary_lines": "CADASTRE_LINE",
        "cadastre_label_source": "JIBUN",
        "zone_source": "ZONE",
        "SILPOK_ROAD": "실폭도로",
        "URBAN_PLAN_FACILITY": "도시계획시설",
    }
    for old, new in replacements.items():
        dxf_text = dxf_text.replace(old, new)
    dxf_text = force_zone_dxf_colors(dxf_text, zone_style)
    dxf_text = force_solid_hatch_compatibility(dxf_text)
    dxf_text = remove_non_text_entities_from_layer(dxf_text, "JIBUN")
    dxf_text = force_layer_text_height(dxf_text, "JIBUN", 2.0)
    dxf_text = force_text_style_font(dxf_text)
    dxf_text = force_dxf_codepage(dxf_text)
    with Path(dxf_path).open("w", encoding="cp949", errors="replace", newline="") as f:
        f.write(dxf_text)
    return dxf_path


def create_qgis_project(source_shp, selected_shp, line_shp, point_shp, style_path, project_path, zone_layers=None, zone_style_path=None, parcel_list_line_shp=None):
    project = QgsProject.instance()
    project.clear()
    zone_layers = zone_layers or []
    source = QgsVectorLayer(str(source_shp), "VWorld cadastre source", "ogr")
    selected = QgsVectorLayer(str(selected_shp), "Selected cadastre 5km", "ogr")
    lines = QgsVectorLayer(str(line_shp), "DXF boundary lines", "ogr")
    point = QgsVectorLayer(str(point_shp), "Address point", "ogr")
    for layer in (source, selected, lines, point):
        if layer.isValid():
            project.addMapLayer(layer)
    for zone in zone_layers:
        config = ZONE_DEFINITIONS[zone["key"]]
        zone_selected = QgsVectorLayer(str(zone["selected_shp"]), config["label"], "ogr")
        if zone_selected.isValid():
            project.addMapLayer(zone_selected)
            if zone_style_path and Path(zone_style_path).exists():
                zone_selected.loadNamedStyle(str(zone_style_path))
            else:
                r, g, b = config["rgb"]
                symbol = QgsFillSymbol.createSimple(
                    {
                        "color": f"{r},{g},{b},90",
                        "outline_color": f"{r},{g},{b},0",
                        "outline_width": "0",
                        "style": "solid",
                    }
                )
                symbol.setColor(QColor(r, g, b, 255))
                zone_selected.setRenderer(QgsSingleSymbolRenderer(symbol))
            zone_selected.triggerRepaint()
    if selected.isValid() and style_path and Path(style_path).exists():
        selected.loadNamedStyle(str(style_path))
        selected.triggerRepaint()
    if lines.isValid():
        apply_line_style(lines, style_path)
    if parcel_list_line_shp:
        parcel_lines = QgsVectorLayer(str(parcel_list_line_shp), PARCEL_LIST_LAYER_NAME, "ogr")
        if parcel_lines.isValid():
            project.addMapLayer(parcel_lines)
            symbol = QgsLineSymbol.createSimple(
                {
                    "line_color": "255,0,0,255",
                    "line_width": "0.45",
                    "line_width_unit": "MM",
                    "line_style": "solid",
                }
            )
            symbol.setColor(QColor(255, 0, 0, 255))
            parcel_lines.setRenderer(QgsSingleSymbolRenderer(symbol))
    project_path.parent.mkdir(parents=True, exist_ok=True)
    if not project.write(str(project_path)):
        raise RuntimeError(f"QGIS 프로젝트 저장 실패: {project_path}")


def main():
    parser = argparse.ArgumentParser(description="VWorld 연속지적도 5174 자동 다운로드/선택/DXF 변환")
    parser.add_argument("--address", required=True, help="검색할 주소")
    parser.add_argument("--vworld-key", default=os.environ.get("VWORLD_API_KEY"), help="VWorld API 인증키")
    parser.add_argument("--x", type=float, help="EPSG:5174 X 좌표. API 키가 없을 때 사용")
    parser.add_argument("--y", type=float, help="EPSG:5174 Y 좌표. API 키가 없을 때 사용")
    parser.add_argument("--sido", help="예: 서울, 경기, 전북")
    parser.add_argument("--sigungu", help="예: 강남구, 성남시_분당구")
    parser.add_argument("--radius", type=float, default=5000, help="선택 반경(m)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--style", type=Path, default=DEFAULT_STYLE_PATH)
    parser.add_argument("--zone-style", type=Path, default=DEFAULT_ZONE_STYLE_PATH)
    parser.add_argument("--dry-run", action="store_true", help="다운로드/변환 없이 주소와 다운로드 리소스 매칭만 확인")
    parser.add_argument("--input-zip", type=Path, help="VWorld에서 이미 내려받은 연속지적도 ZIP을 직접 사용")
    parser.add_argument("--zone-zip", action="append", default=[], help="Optional land-use zone ZIP in key=path format")
    parser.add_argument("--road-zip", type=Path, help="도로명주소 실폭도로(시도별) SHP ZIP 경로(Z_KAIS_TL_SPRD_RW)")
    parser.add_argument("--vworld-domain", default=os.environ.get("VWORLD_DOMAIN", ""), help="VWorld WFS 인증 도메인(키 등록 도메인)")
    parser.add_argument("--parcel-list", type=Path, action="append", default=[], help="DXF에 별도 레이어로 추가할 지번 목록 엑셀/CSV/TXT 파일")
    args = parser.parse_args()

    qgs = QgsApplication([], False)
    qgs.initQgis()
    QCoreApplication.setOrganizationName("Codex")
    QCoreApplication.setApplicationName("CadastreAutomation")

    try:
        if args.vworld_key:
            x, y, refined_address, geo_structure = geocode_address(args.address, args.vworld_key)
            # 시도·시군구: 좌표 역지오코딩(getAddress)이 가장 신뢰성 높다.
            # 실패 시 forward structure → 텍스트 파싱 순으로 폴백.
            sido, sigungu = reverse_region(x, y, args.vworld_key)
            if not sido:
                sido, sigungu = region_from_structure(geo_structure, refined_address)
        else:
            if args.x is None or args.y is None or not args.sido:
                raise ValueError("VWorld API 키가 없으면 --x, --y, --sido가 필요합니다.")
            x, y = args.x, args.y
            refined_address = args.address
            sido = normalize_region_name(args.sido)
            sigungu = args.sigungu

        out_dir = args.out_dir.resolve()
        downloads = out_dir / "downloads"
        extracted = out_dir / "extracted"
        base_result_dir = out_dir / "result"
        base_result_dir.mkdir(parents=True, exist_ok=True)

        cache_path = out_dir / "vworld_5174_resources.json"
        resources = load_resource_index(cache_path)
        resource = match_resource(resources, sido, sigungu)
        # 도로명주소 실폭도로(시도별) 리소스도 함께 해결(실패해도 전체 진행에 영향 없음)
        road_resource = None
        try:
            road_cache = out_dir / "vworld_30057_road_resources_v2.json"
            road_resource = match_road_resource(load_road_resource_index(road_cache), sido)
        except Exception as road_exc:
            print(f"실폭도로 리소스 조회 경고: {road_exc}")
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "address": args.address,
                        "refined_address": refined_address,
                        "sido": sido,
                        "sigungu": sigungu,
                        "x": x,
                        "y": y,
                        "resource": resource["label"],
                        "download_href": resource["href"],
                        "road_resource": road_resource["label"] if road_resource else None,
                        "road_download_href": road_resource["href"] if road_resource else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        zip_path = args.input_zip.resolve() if args.input_zip else download_resource(resource, downloads)
        if not zip_path.exists():
            raise FileNotFoundError(f"입력 ZIP을 찾지 못했습니다: {zip_path}")
        print("다운로드한 ZIP 압축을 푸는 중입니다.")
        source_shp = extract_zip(zip_path, extracted / zip_path.stem)

        stem = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", refined_address).strip("_")[:80]
        result_dir = base_result_dir / stem
        result_dir.mkdir(parents=True, exist_ok=True)
        r_label = radius_label(args.radius)
        selected_shp = result_dir / f"{stem}_{r_label}.shp"
        line_shp = result_dir / f"{stem}_{r_label}_lines.shp"
        point_shp = result_dir / f"{stem}_address_point.shp"
        dxf_path = result_dir / f"{stem}_{r_label}.dxf"
        parcel_list_selected_shp = result_dir / f"{stem}_{r_label}_excel_parcels.shp"
        parcel_list_line_shp = result_dir / f"{stem}_{r_label}_excel_parcels_lines.shp"

        print("주소 반경 안의 연속지적 필지를 선택하는 중입니다.")
        count = export_within_radius(source_shp, x, y, args.radius, selected_shp)
        print(f"선택된 필지 수: {count}")
        print("주소 기준점 레이어를 만드는 중입니다.")
        write_address_point(x, y, point_shp)
        print("선택 필지를 CAD용 경계선으로 변환하는 중입니다.")
        line_count = export_polygon_boundaries_to_lines(selected_shp, line_shp)
        print(f"생성된 경계선 수: {line_count}")
        parcel_keys = set()
        for parcel_list_path in args.parcel_list:
            parcel_keys.update(read_parcel_keys(parcel_list_path))
        parcel_list_count = 0
        parcel_list_line_for_dxf = None
        if parcel_keys:
            print(f"엑셀 지번 리스트를 별도 레이어로 선택하는 중입니다. 입력 지번 수: {len(parcel_keys)}")
            parcel_list_count = export_parcel_list_selection(
                selected_shp,
                parcel_keys,
                parcel_list_selected_shp,
                parcel_list_line_shp,
            )
            print(f"엑셀 지번 리스트와 일치한 필지 수: {parcel_list_count}")
            if parcel_list_count > 0:
                parcel_list_line_for_dxf = parcel_list_line_shp
        zone_layers = []
        zone_zips = parse_zone_zip_args(args.zone_zip)
        zone_style = zone_style_from_qml(args.zone_style)
        if zone_zips:
            print("용도지역 레이어를 반경 안에서 선택하는 중입니다.")
        for zone_key, zone_zip in zone_zips.items():
            if not zone_zip.exists():
                raise FileNotFoundError(f"용도지역 ZIP을 찾지 못했습니다: {zone_zip}")
            config = ZONE_DEFINITIONS[zone_key]
            zone_source_shp = extract_zip(zone_zip, extracted / zone_zip.stem)
            zone_selected_shp = result_dir / f"{stem}_{r_label}_{zone_key}.shp"
            zone_count = export_zone_within_radius(zone_source_shp, x, y, args.radius, zone_selected_shp)
            if zone_count > 0:
                enriched_count = enrich_zone_attributes(zone_selected_shp, zone_style)
                print(f"{config['label']} 스타일 속성 보강 수: {enriched_count}")
            print(f"{config['label']} 선택 개수: {zone_count}")
            if zone_count > 0:
                zone_layers.append(
                    {
                        "key": zone_key,
                        "count": zone_count,
                        "source_shp": str(zone_source_shp),
                        "selected_shp": str(zone_selected_shp),
                    }
                )
        # 실폭도로(도로명주소 Z_KAIS_TL_SPRD_RW): 반경 클립 → 면 경계선 → '실폭도로' 레이어
        road_line_for_dxf = None
        road_count = 0
        if args.road_zip and Path(args.road_zip).exists():
            print("실폭도로(도로명주소) SHP를 반경 안에서 선택하는 중입니다.")
            road_source_shp = extract_zip_find(Path(args.road_zip), extracted / Path(args.road_zip).stem, "SPRD_RW")
            road_selected_shp = result_dir / f"{stem}_{r_label}_silpok.shp"
            road_count = export_zone_within_radius(road_source_shp, x, y, args.radius, road_selected_shp)
            print(f"실폭도로 선택 개수: {road_count}")
            if road_count > 0:
                road_line_shp = result_dir / f"{stem}_{r_label}_silpok_lines.shp"
                export_polygon_boundaries_to_lines(road_selected_shp, road_line_shp)
                road_line_for_dxf = road_line_shp
        elif args.road_zip:
            print(f"실폭도로 ZIP 경로를 찾지 못해 건너뜁니다: {args.road_zip}")

        # 도시계획시설(도로·교통시설): VWorld WFS로 받아 반경 클립 → 면 경계선 → '도시계획시설' 레이어
        plan_line_for_dxf = None
        plan_count = 0
        if args.vworld_key:
            try:
                print("도시계획시설(도로·교통시설)을 WFS로 받아 반경 안에서 선택하는 중입니다.")
                plan_line_for_dxf, plan_count = build_plan_facility_lines(
                    x, y, args.radius, args.vworld_key, args.vworld_domain,
                    extracted / "plan_facilities", result_dir, stem, r_label)
                print(f"도시계획시설 선택 개수: {plan_count}")
            except Exception as plan_exc:
                print(f"도시계획시설 처리를 건너뜁니다(계속 진행): {plan_exc}")

        print("연속지적 라인과 용도지역 해치 DXF를 만드는 중입니다.")
        dxf_path = apply_style_and_export_dxf(line_shp, selected_shp, args.style, dxf_path, zone_layers, args.zone_style, parcel_list_line_for_dxf, road_line_for_dxf, plan_line_for_dxf)
        label_count = label_feature_count(selected_shp, args.style)
        print(f"지번 라벨 수: {label_count}")
        print("QGIS 프로젝트를 저장하는 중입니다.")
        project_path = result_dir / f"{stem}_{r_label}.qgz"
        create_qgis_project(source_shp, selected_shp, line_shp, point_shp, args.style, project_path, zone_layers, args.zone_style, parcel_list_line_for_dxf)

        summary = {
            "address": args.address,
            "refined_address": refined_address,
            "sido": sido,
            "sigungu": sigungu,
            "x": x,
            "y": y,
            "resource": resource["label"],
            "selected_count": count,
            "line_count": line_count,
            "label_count": label_count,
            "parcel_list_count": parcel_list_count,
            "road_count": road_count,
            "plan_count": plan_count,
            "zone_layers": zone_layers,
            "source_shp": str(source_shp),
            "selected_shp": str(selected_shp),
            "line_shp": str(line_shp),
            "address_point_shp": str(point_shp),
            "parcel_list_shp": str(parcel_list_selected_shp) if parcel_list_count else None,
            "parcel_list_line_shp": str(parcel_list_line_shp) if parcel_list_count else None,
            "style": str(args.style),
            "zone_style": str(args.zone_style),
            "dxf": str(dxf_path),
            "qgis_project": str(project_path),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    main()

