import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsDxfExport,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsMapSettings,
    QgsGeometry,
    QgsLineSymbol,
    QgsPointXY,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsPalLayerSettings,
    QgsTextFormat,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from PyQt5.QtCore import QCoreApplication, QFile, QIODevice, QVariant
from PyQt5.QtGui import QColor, QFont


APP_DIR = Path(__file__).resolve().parent
DEFAULT_STYLE_PATH = APP_DIR.parent / "styles" / "cadastre-default.qml"
DEFAULT_OUT_DIR = Path.cwd() / "outputs"
DATASET_ID_5174 = "30564"
DATASET_INDEX_URL = "https://data.edmgr.kr/dataView.do?id=vworld_open_30564"
VWORLD_DOWNLOAD_URL = "https://www.vworld.kr/dtmk/downloadResourceFile.do"
VWORLD_GEOCODE_URL = "https://api.vworld.kr/req/address"
ZONE_DEFINITIONS = {
    "urban": {"label": "도시지역", "line_layer": "ZONE_URBAN", "text_layer": "ZONE_URBAN_LABEL", "color": 1, "rgb": (255, 0, 0)},
    "management": {"label": "관리지역", "line_layer": "ZONE_MANAGEMENT", "text_layer": "ZONE_MANAGEMENT_LABEL", "color": 3, "rgb": (0, 170, 0)},
    "agriculture": {"label": "농림지역", "line_layer": "ZONE_AGRICULTURE", "text_layer": "ZONE_AGRICULTURE_LABEL", "color": 2, "rgb": (220, 180, 0)},
    "nature": {"label": "자연환경보전지역", "line_layer": "ZONE_NATURE", "text_layer": "ZONE_NATURE_LABEL", "color": 5, "rgb": (0, 80, 255)},
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


def parse_region_from_text(text):
    tokens = text.split()
    if not tokens:
        raise ValueError("주소에서 시도/시군구를 읽지 못했습니다.")
    sido = normalize_region_name(tokens[0])
    sigungu = None
    region_tokens = tokens[1:5]
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
            refined = result.get("refined", {}).get("text") or address
            return float(point["x"]), float(point["y"]), refined
        last_error = response
    raise ValueError(f"VWorld 지오코딩 실패: {last_error}")


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


def export_zone_within_radius(source_shp, x, y, radius_m, selected_shp):
    source = QgsVectorLayer(str(source_shp), "zone_source", "ogr")
    if not source.isValid():
        raise RuntimeError(f"Could not load zone layer: {source_shp}")
    if source.crs().authid().upper() != "EPSG:5174":
        raise RuntimeError(f"Zone SHP CRS is not EPSG:5174: {source.crs().authid()}")
    buffer_geom = QgsGeometry.fromPointXY(QgsPointXY(x, y)).buffer(radius_m, 64)
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
        "font_size_pt": 5.0,
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
    try:
        values["font_size_pt"] = float(text_style.attrib.get("fontSize", values["font_size_pt"]))
    except (TypeError, ValueError):
        pass
    values["text_color"] = text_style.attrib.get("textColor", values["text_color"]) or values["text_color"]
    return values


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
    text_format.setFont(QFont("Malgun Gothic"))
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


def apply_style_and_export_dxf(line_shp, polygon_shp, style_path, dxf_path, zone_layers=None):
    line_layer = QgsVectorLayer(str(line_shp), "cadastre_boundary_lines", "ogr")
    if not line_layer.isValid():
        raise RuntimeError(f"DXF용 라인 레이어를 불러오지 못했습니다: {line_shp}")
    polygon_layer = QgsVectorLayer(str(polygon_shp), "cadastre_label_source", "ogr")
    if not polygon_layer.isValid():
        raise RuntimeError(f"DXF 라벨 원본 레이어를 불러오지 못했습니다: {polygon_shp}")

    line_style = line_style_from_qml(style_path)
    label_style = label_style_from_qml(style_path)
    line_color = dxf_color_index(line_style["line_color"])
    label_color = dxf_color_index(label_style["text_color"])
    label_height = max(label_style["font_size_pt"] * 0.35, 0.5)
    label_field = label_style["field_name"]
    label_field_index = polygon_layer.fields().indexOf(label_field)
    zone_layers = zone_layers or []

    sections = [
        dxf_pair(0, "SECTION"),
        dxf_pair(2, "HEADER"),
        dxf_pair(9, "$ACADVER"),
        dxf_pair(1, "AC1009"),
        dxf_pair(0, "ENDSEC"),
        dxf_pair(0, "SECTION"),
        dxf_pair(2, "TABLES"),
        dxf_pair(0, "TABLE"),
        dxf_pair(2, "LTYPE"),
        dxf_pair(70, 1),
        dxf_pair(0, "LTYPE"),
        dxf_pair(2, "CONTINUOUS"),
        dxf_pair(70, 0),
        dxf_pair(3, "Solid line"),
        dxf_pair(72, 65),
        dxf_pair(73, 0),
        dxf_pair(40, "0.0"),
        dxf_pair(0, "ENDTAB"),
        dxf_pair(0, "TABLE"),
        dxf_pair(2, "STYLE"),
        dxf_pair(70, 1),
        dxf_pair(0, "STYLE"),
        dxf_pair(2, "STANDARD"),
        dxf_pair(70, 0),
        dxf_pair(40, "0.0"),
        dxf_pair(41, "1.0"),
        dxf_pair(50, "0.0"),
        dxf_pair(71, 0),
        dxf_pair(42, "2.5"),
        dxf_pair(3, "txt"),
        dxf_pair(4, ""),
        dxf_pair(0, "ENDTAB"),
        dxf_pair(0, "TABLE"),
        dxf_pair(2, "LAYER"),
        dxf_pair(70, 2 + (len(zone_layers) * 2)),
        dxf_pair(0, "LAYER"),
        dxf_pair(2, "CADASTRE_LINE"),
        dxf_pair(70, 0),
        dxf_pair(62, line_color),
        dxf_pair(6, "CONTINUOUS"),
        dxf_pair(0, "LAYER"),
        dxf_pair(2, "JIBUN"),
        dxf_pair(70, 0),
        dxf_pair(62, label_color),
        dxf_pair(6, "CONTINUOUS"),
    ]
    for zone in zone_layers:
        config = ZONE_DEFINITIONS[zone["key"]]
        sections.extend(
            [
                dxf_pair(0, "LAYER"),
                dxf_pair(2, config["line_layer"]),
                dxf_pair(70, 0),
                dxf_pair(62, config["color"]),
                dxf_pair(6, "CONTINUOUS"),
                dxf_pair(0, "LAYER"),
                dxf_pair(2, config["text_layer"]),
                dxf_pair(70, 0),
                dxf_pair(62, config["color"]),
                dxf_pair(6, "CONTINUOUS"),
            ]
        )
    sections.extend(
        [
            dxf_pair(0, "ENDTAB"),
            dxf_pair(0, "ENDSEC"),
            dxf_pair(0, "SECTION"),
            dxf_pair(2, "ENTITIES"),
        ]
    )

    for zone in zone_layers:
        config = ZONE_DEFINITIONS[zone["key"]]
        zone_line_layer = QgsVectorLayer(str(zone["line_shp"]), config["line_layer"], "ogr")
        if zone_line_layer.isValid():
            for feat in zone_line_layer.getFeatures():
                geom = feat.geometry()
                if not geom or geom.isEmpty():
                    continue
                polylines = geom.asMultiPolyline()
                if not polylines:
                    polyline = geom.asPolyline()
                    polylines = [polyline] if polyline else []
                for polyline in polylines:
                    sections.append(write_polyline_entity(polyline, config["line_layer"], config["color"]))

    for feat in line_layer.getFeatures():
        geom = feat.geometry()
        if not geom or geom.isEmpty():
            continue
        polylines = geom.asMultiPolyline()
        if not polylines:
            polyline = geom.asPolyline()
            polylines = [polyline] if polyline else []
        for polyline in polylines:
            sections.append(write_polyline_entity(polyline, "CADASTRE_LINE", line_color))

    if label_field_index >= 0:
        for feat in polygon_layer.getFeatures():
            text = feat.attribute(label_field)
            if text is None or clean_dxf_text(text) == "":
                continue
            point_geom = feat.geometry().pointOnSurface()
            if not point_geom or point_geom.isEmpty():
                point_geom = feat.geometry().centroid()
            if not point_geom or point_geom.isEmpty():
                continue
            point = point_geom.asPoint()
            sections.append(write_text_entity("JIBUN", point.x(), point.y(), label_height, text, label_color))

    for zone in zone_layers:
        config = ZONE_DEFINITIONS[zone["key"]]
        zone_polygon_layer = QgsVectorLayer(str(zone["selected_shp"]), config["text_layer"], "ogr")
        if not zone_polygon_layer.isValid():
            continue
        for feat in zone_polygon_layer.getFeatures():
            point_geom = feat.geometry().pointOnSurface()
            if not point_geom or point_geom.isEmpty():
                point_geom = feat.geometry().centroid()
            if not point_geom or point_geom.isEmpty():
                continue
            point = point_geom.asPoint()
            sections.append(write_text_entity(config["text_layer"], point.x(), point.y(), max(label_height * 1.4, 1.0), config["label"], config["color"]))

    sections.extend([dxf_pair(0, "ENDSEC"), dxf_pair(0, "EOF")])
    Path(dxf_path).write_bytes("".join(sections).encode("cp949", errors="replace"))


def create_qgis_project(source_shp, selected_shp, line_shp, point_shp, style_path, project_path, zone_layers=None):
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
        zone_lines = QgsVectorLayer(str(zone["line_shp"]), config["line_layer"], "ogr")
        for layer in (zone_selected, zone_lines):
            if layer.isValid():
                project.addMapLayer(layer)
        if zone_lines.isValid():
            r, g, b = config["rgb"]
            symbol = QgsLineSymbol.createSimple({"line_color": f"{r},{g},{b},255", "line_width": "0.3"})
            symbol.setColor(QColor(r, g, b, 255))
            zone_lines.setRenderer(QgsSingleSymbolRenderer(symbol))
            zone_lines.triggerRepaint()
    if selected.isValid() and style_path and Path(style_path).exists():
        selected.loadNamedStyle(str(style_path))
        selected.triggerRepaint()
    if lines.isValid():
        apply_line_style(lines, style_path)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    if not project.write(str(project_path)):
        raise RuntimeError(f"QGIS 프로젝트 저장 실패: {project_path}")


def open_project_in_qgis(project_path):
    prefix = os.environ.get("QGIS_PREFIX_PATH", "")
    candidates = []
    if prefix:
        candidates.append(Path(prefix.replace("/", "\\")) / "bin" / "qgis-bin.exe")
    candidates.extend(
        [
            Path(r"C:\Program Files\QGIS 3.34.2\bin\qgis.bat"),
            Path(r"C:\Program Files\QGIS 3.34.2\apps\qgis\bin\qgis-bin.exe"),
            Path(r"C:\Program Files\QGIS 3.8\bin\qgis.bat"),
            Path(r"C:\Program Files\QGIS 3.8\apps\qgis\bin\qgis-bin.exe"),
        ]
    )
    for root in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")), Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))):
        if root.exists():
            candidates.extend(sorted(root.glob("QGIS*/bin/qgis.bat"), reverse=True))
            candidates.extend(sorted(root.glob("QGIS*/apps/qgis/bin/qgis-bin.exe"), reverse=True))
    for candidate in candidates:
        if candidate.exists():
            subprocess.Popen([str(candidate), str(project_path)], close_fds=True)
            return
    raise FileNotFoundError("QGIS 실행 파일을 찾지 못했습니다.")


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
    parser.add_argument("--open-qgis", action="store_true", help="처리 후 QGIS 프로젝트를 열기")
    parser.add_argument("--dry-run", action="store_true", help="다운로드/변환 없이 주소와 다운로드 리소스 매칭만 확인")
    parser.add_argument("--input-zip", type=Path, help="VWorld에서 이미 내려받은 연속지적도 ZIP을 직접 사용")
    parser.add_argument("--zone-zip", action="append", default=[], help="Optional land-use zone ZIP in key=path format")
    args = parser.parse_args()

    qgs = QgsApplication([], False)
    qgs.initQgis()
    QCoreApplication.setOrganizationName("Codex")
    QCoreApplication.setApplicationName("CadastreAutomation")

    try:
        if args.vworld_key:
            x, y, refined_address = geocode_address(args.address, args.vworld_key)
            sido, sigungu = parse_region_from_text(refined_address)
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

        print("주소 반경 안의 연속지적 필지를 선택하는 중입니다.")
        count = export_within_radius(source_shp, x, y, args.radius, selected_shp)
        print(f"선택된 필지 수: {count}")
        print("주소 기준점 레이어를 만드는 중입니다.")
        write_address_point(x, y, point_shp)
        print("선택 필지를 CAD용 경계선으로 변환하는 중입니다.")
        line_count = export_polygon_boundaries_to_lines(selected_shp, line_shp)
        print(f"생성된 경계선 수: {line_count}")
        zone_layers = []
        zone_zips = parse_zone_zip_args(args.zone_zip)
        if zone_zips:
            print("용도지역 레이어를 반경 안에서 선택하는 중입니다.")
        for zone_key, zone_zip in zone_zips.items():
            if not zone_zip.exists():
                raise FileNotFoundError(f"용도지역 ZIP을 찾지 못했습니다: {zone_zip}")
            config = ZONE_DEFINITIONS[zone_key]
            zone_source_shp = extract_zip(zone_zip, extracted / zone_zip.stem)
            zone_selected_shp = result_dir / f"{stem}_{r_label}_{zone_key}.shp"
            zone_line_shp = result_dir / f"{stem}_{r_label}_{zone_key}_lines.shp"
            zone_count = export_zone_within_radius(zone_source_shp, x, y, args.radius, zone_selected_shp)
            print(f"{config['label']} 선택 개수: {zone_count}")
            if zone_count > 0:
                export_polygon_boundaries_to_lines(zone_selected_shp, zone_line_shp)
                zone_layers.append(
                    {
                        "key": zone_key,
                        "count": zone_count,
                        "source_shp": str(zone_source_shp),
                        "selected_shp": str(zone_selected_shp),
                        "line_shp": str(zone_line_shp),
                    }
                )
        print("해치 없는 라인 DXF와 지번 라벨을 만드는 중입니다.")
        apply_style_and_export_dxf(line_shp, selected_shp, args.style, dxf_path, zone_layers)
        label_count = label_feature_count(selected_shp, args.style)
        print(f"지번 라벨 수: {label_count}")
        print("QGIS 프로젝트를 저장하는 중입니다.")
        project_path = result_dir / f"{stem}_{r_label}.qgz"
        create_qgis_project(source_shp, selected_shp, line_shp, point_shp, args.style, project_path, zone_layers)
        if args.open_qgis:
            open_project_in_qgis(project_path)

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
            "zone_layers": zone_layers,
            "source_shp": str(source_shp),
            "selected_shp": str(selected_shp),
            "line_shp": str(line_shp),
            "address_point_shp": str(point_shp),
            "style": str(args.style),
            "dxf": str(dxf_path),
            "qgis_project": str(project_path),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    main()

