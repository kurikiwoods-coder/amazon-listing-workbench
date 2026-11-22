from __future__ import annotations

import csv
import io
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from zipfile import ZipFile

from openpyxl import load_workbook
from PIL import Image, ImageOps

from .amazon_content import detect_webpage_format, saved_page_preview
from .marketplaces import DEFAULT_MARKET, market_code, product_target, product_url
from .models import FIELDS, PUBLISHED_COPY, utcnow
from .page_images import ocr_language

MAX_UPLOAD = 8 * 1024 * 1024
MAX_WEBPAGE_UPLOAD = 32 * 1024 * 1024
ALIASES = {
    "asin": ("asin", "asincode", "productid", "商品asin", "亚马逊asin", "amazonasin"),
    "title": ("title", "itemname", "producttitle", "listingtitle", "productname", "标题", "商品标题", "产品名称", "商品名称"),
    "highlights": ("highlights", "itemhighlight", "itemhighlights", "亮点", "商品亮点"),
    "bullets": ("bullets", "bulletpoint", "bulletpoints", "aboutthisitem", "features", "keyfeatures", "五点", "五点描述", "产品卖点", "要点"),
    "aplus": ("aplus", "apluscontent", "acontent", "a文案", "a页面", "高级品牌内容"),
    "raw_text": ("rawtext", "description", "productdescription", "listing", "listingtext", "content", "text",
                 "详情", "未分段原文", "原文", "文案", "文案正文", "商品描述", "商品详情", "产品描述"),
    "public_specs": ("publicspecs", "productinformation", "productdetails", "technicalspecifications", "公开商品规格", "公开规格", "商品规格"),
    "attributes": ("attributes", "backendattributes", "productattributes", "后台属性", "产品属性"),
    "search_terms": ("searchterms", "generickeyword", "generickeywords", "搜索词"),
    "price": ("price", "价格"),
    "reviews": ("reviews", "reviewsummary", "评论摘要", "评论"),
    "qa": ("qa", "questionsandanswers", "问答"),
    "keywords": ("keywords", "targetkeywords", "目标关键词", "关键词"),
    "competitors": ("competitors", "competitorasins", "竞品"),
}
ALIASES["market"] = ("market", "marketplace", "site", "站点", "商城")
IMAGE_SUFFIX = {".png", ".jpg", ".jpeg", ".webp"}
WEBPAGE_SUFFIX = {".html": "html", ".htm": "html", ".mhtml": "mhtml", ".mht": "mhtml"}
NON_COPY_KEYS = {"sku", "sellersku", "url", "link", "linkclean", "asinurl", "producturl", "marketplace", "market",
                 "category", "brand", "id", "品牌", "类目", "站点"}
WRAPPERS = ("fields", "listing", "product", "data", "items", "listings", "products", "records", "rows")


def webpage_format(data, suffix):
    if suffix in IMAGE_SUFFIX | {".csv", ".tsv", ".xlsx", ".json"}:
        return None
    return detect_webpage_format(data) or WEBPAGE_SUFFIX.get(suffix)


def normalize(label: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", label).casefold())


def field_key(label: str) -> str | None:
    name = normalize(label)
    if re.fullmatch(r"a\s*\+(?:\s*(?:content|内容|文案))?", unicodedata.normalize("NFKC", label).strip(), re.I):
        return "aplus"
    return next((key for key, synonyms in ALIASES.items() if name == key or name in synonyms), None)


def cell_text(value) -> str:
    if isinstance(value, list):
        return "\n".join(filter(None, (cell_text(part) for part in value)))
    if isinstance(value, dict):
        for key in ("text", "value", "content"):
            if key in value:
                return cell_text(value[key])
        return "\n".join(f"{key}: {text}" for key, item in value.items() if (text := cell_text(item)))
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def bullet_number(label: str) -> int | None:
    match = re.fullmatch(r"(?:bullet(?:point)?|feature|keyfeature|五点(?:描述)?|卖点|要点)(\d{1,3})", normalize(label))
    return int(match[1]) if match else None


def known_label(label: str) -> bool:
    return field_key(label) is not None or bullet_number(label) is not None


def row_identity(row: dict, market: str) -> tuple[str, str]:
    metadata = {key: cell_text(data) for name, data in row.items()
                if isinstance(name, str) and (key := field_key(name)) in {"asin", "market"} and cell_text(data)}
    explicit_market = metadata.get("market")
    selected_market = market_code(explicit_market) if explicit_market else market
    declared = metadata.get("asin", "")
    actual_asin, actual_market = "", selected_market
    if declared:
        try:
            actual_asin, actual_market = product_target(declared, selected_market)
        except ValueError as exc:
            raise ValueError("文件里的 ASIN 无法识别。请核对编号，或清空文件的 ASIN 后按选定项目导入。") from exc
    if explicit_market and actual_market != selected_market:
        raise ValueError("资料中的站点与商品链接不一致。")
    links = set()
    for name, value in row.items():
        if isinstance(name, str) and normalize(name) in {"url", "link", "producturl", "asinurl", "linkclean"} and cell_text(value):
            try:
                links.add(product_target(cell_text(value), selected_market))
            except ValueError:
                continue
    if links:
        link_asin, link_market = next(iter(links))
        fixed_market = explicit_market or (declared and not re.fullmatch(r"[a-zA-Z0-9]{10}", declared))
        if len(links) != 1 or (actual_asin and actual_asin != link_asin) or (fixed_market and link_market != actual_market):
            raise ValueError("资料中的 ASIN、站点与商品链接不一致。")
        return link_asin, link_market
    return actual_asin, actual_market


def tabular_row(row: dict, source: str) -> dict:
    fields, numbered, unclassified = {}, {}, []
    for name, data in row.items():
        if not isinstance(name, str):
            continue
        value = cell_text(data)
        if not value:
            continue
        key = field_key(name)
        if key in FIELDS:
            fields[key] = " ".join(value.splitlines()) if key in {"title", "highlights"} else value
        if key is None:
            number = bullet_number(name)
            if number is not None:
                numbered[number] = value
            elif normalize(name) not in NON_COPY_KEYS and re.search(r"[\w\u3400-\u9fff]{2,}", value):
                unclassified.append(value)
    if "bullets" not in fields and numbered:
        fields["bullets"] = "\n".join(value for _, value in sorted(numbered.items()))
    if not any(key in fields for key in PUBLISHED_COPY) and unclassified:
        fields["raw_text"] = "\n".join(unclassified)
    overflow = [FIELDS[key] for key, value in fields.items() if len(value) > (32_000 if key == "raw_text" else 12_000)]
    if overflow:
        raise ValueError(f"以下字段超过导入上限：{'、'.join(overflow)}。请拆分后重试。")
    return {key: {"state": "pending", "text": value, "source": source, "collected_at": utcnow()}
            for key, value in fields.items()}


def table_rows(values) -> list[dict]:
    values = [row for row in values if any(cell_text(value) for value in row)]
    if not values:
        return []
    candidates = [(sum(known_label(cell_text(value)) for value in row), index)
                  for index, row in enumerate(values[:30])]
    header_score, header_index = max(candidates, key=lambda item: item[0])
    pairs = [(cell_text(row[0]), row[1]) for row in values if len(row) >= 2
             and known_label(cell_text(row[0])) and cell_text(row[1]) and not any(map(cell_text, row[2:]))]
    labels = {field_key(label) or bullet_number(label) for label, _ in pairs}
    if header_score < 2 and (len(labels) == len(pairs) >= 2 or len(values) == len(pairs) == 1):
        return [dict(pairs)]
    if header_score:
        headers = [cell_text(value) or f"column_{index}" for index, value in enumerate(values[header_index])]
        if field_key(headers[0]) in {"raw_text", "bullets"} and all(not any(map(cell_text, row[1:])) for row in values[header_index:]):
            return [{headers[0]: "\n".join(cell_text(row[0]) for row in values[header_index + 1:])}]
        return [dict(zip(headers, row)) for row in values[header_index + 1:]]
    if all(len(row) == 1 or not any(map(cell_text, row[1:])) for row in values):
        return [{"Description": "\n".join(cell_text(row[0]) for row in values)}]
    if len(values) == 2:
        return [{f"column_{index}": value for index, value in enumerate(values[1])}]
    if len(values) == 1:
        return [{f"column_{index}": value for index, value in enumerate(values[0])}]
    return [{f"column_{index}": value for index, value in enumerate(row)} for row in values]


def matches_target(row: dict, asin: str, market: str) -> bool:
    try:
        return row_identity(row, market) == (asin, market)
    except ValueError:
        return False


def select_row(rows: list[dict], asin: str, market: str) -> dict:
    if not rows:
        return {}
    if len(rows) == 1:
        return rows[0]
    matches = [row for row in rows if asin and matches_target(row, asin, market)]
    if len(matches) != 1:
        raise ValueError("文件包含多条商品记录。请选择目标站点并填写 ASIN，或只保留一条记录。")
    return matches[0]


def split_sections(text: str, *, include_identity=False) -> tuple[dict[str, str], str]:
    parsed: dict[str, list[str]] = {}
    current = None
    unclassified = []
    for line in text.splitlines():
        raw = line.strip()
        heading = re.match(r"^(?:#{1,6}\s*)?([^:：]{1,34})(?:[:：]\s*(.*))?$", raw)
        field = field_key(heading[1]) if heading else None
        if field in {"asin", "market"}:
            if include_identity and (value := heading[2]):
                if field in parsed and parsed[field] != [value]:
                    raise ValueError("文件包含多个 ASIN 或站点，请按一个商品保存后导入。")
                parsed[field] = [value]
            current = None
            continue
        if field in FIELDS:
            current = field
            parsed.setdefault(current, [])
            raw = heading[2] or ""
        if current and raw:
            value = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+|\d+、\s*)", "", raw) if current == "bullets" else raw
            parsed[current].append(value)
        elif raw:
            unclassified.append(raw)
    return ({key: (" " if key in {"title", "highlights"} else "\n").join(value).strip()
             for key, value in parsed.items() if value},
            "\n".join(unclassified))


def sections(text: str) -> dict[str, str]:
    return split_sections(text)[0]


def quick_fields(text: str) -> dict[str, dict]:
    body, unclassified = split_sections(text)
    if not body and not unclassified:
        unclassified = text
    if unclassified:
        body["raw_text"] = "\n".join(part for part in (unclassified, body.get("raw_text")) if part)
    if any(len(value) > 12_000 for key, value in body.items() if key != "raw_text"):
        return {"raw_text": {"state": "confirmed", "text": text, "source": "快速粘贴 · 用户提供"}}
    return {key: {"state": "confirmed", "text": value, "source": "快速粘贴 · 用户提供"}
            for key, value in body.items()}


def text_decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for char_set in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(char_set)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文件编码。请另存为 UTF-8 后重试。")


def image_data(data: bytes, binary: str, lang="eng") -> tuple[bytes, str, str]:
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > 30_000_000:
            raise ValueError("图片像素超出上限。请裁剪至 Listing 内容区域后重试。")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((2300, 2300))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    clean = buffer.getvalue()
    executable = shutil.which(binary)
    if not executable:
        return clean, "", "已保留截图。此设备未安装 Tesseract，可对照图片手动转录。"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "listing.png"
        path.write_bytes(clean)
        try:
            completed = subprocess.run([executable, str(path), "stdout", "-l", lang, "--psm", "6"],
                                       check=True, capture_output=True, timeout=20)
            text = completed.stdout.decode("utf-8", errors="replace").strip()[:30_000]
        except (subprocess.SubprocessError, OSError):
            return clean, "", "已保留截图。请核对对应语言的 Tesseract 语言包，或对照图片手动转录。"
    return clean, text, "截图识别结果需要人工核对。" if text else "已保留截图，未识别出可用文字。"


def read_csv(decoded: str) -> list[dict]:
    sample = "\n".join(decoded.splitlines()[:30])
    choices = (",", "\t", ";")
    try:
        preferred = csv.Sniffer().sniff(sample, delimiters="".join(choices)).delimiter
        choices = (preferred, *(choice for choice in choices if choice != preferred))
    except csv.Error:
        pass

    def header_score(choice: str) -> int:
        return max((sum(known_label(value) for value in row)
                    for row in csv.reader(io.StringIO(sample), delimiter=choice)), default=0)
    try:
        delimiter = max(choices, key=header_score)
        return table_rows(csv.reader(io.StringIO(decoded), delimiter=delimiter))
    except csv.Error as exc:
        raise ValueError("无法读取 CSV。请检查分隔符、引号和单元格长度。") from exc


def json_rows(value) -> list[dict]:
    if isinstance(value, str):
        fields, raw = split_sections(value, include_identity=True)
        if raw:
            fields["raw_text"] = "\n".join(filter(None, (raw, fields.get("raw_text"))))
        return [fields]
    if isinstance(value, list):
        return [row for item in value for row in json_rows(item)]
    if not isinstance(value, dict):
        raise ValueError("JSON 可使用商品对象、对象数组、常见外层包装或文案字符串。")
    for key, inner in value.items():
        if normalize(key) in WRAPPERS and isinstance(inner, (dict, list)):
            outer = {name: item for name, item in value.items() if name != key and known_label(name)}
            return [{**outer, **row} for row in json_rows(inner)]
    return [value]


def nexscope_rows(value) -> list[dict] | None:
    if not isinstance(value, dict) or not {"code", "data"} <= value.keys():
        return None
    if value["code"] == 11209:
        raise ValueError("Nexscope 未接受 API Key。请检查服务端配置，或上传成功返回的商品资料。")
    if value["code"] != 0 and "msg" in value:
        raise ValueError("API 响应显示请求未成功。请导入成功返回的商品详情。")
    if not isinstance(data := value["data"], dict):
        return None
    if not isinstance(products := data.get("products"), list):
        return None
    if value["code"] != 0:
        raise ValueError("Nexscope 响应显示请求未成功。请导入成功返回的商品详情。")
    rows = []
    for product in products:
        if not isinstance(product, dict) or not cell_text(product.get("asin")):
            continue
        groups = (product.get("itemSpecifications"), product.get("productDetails"))
        specs = [f"{key}: {text}" for group in groups if isinstance(group, dict) for key, item in group.items()
                 if normalize(key) not in {"asin", "customerreviews", "bestsellersrank"} and (text := cell_text(item))]
        if dimensions := cell_text(product.get("productDimensions")):
            specs.append(f"Product dimensions: {dimensions}")
        bullet_text = product.get("aboutItemFivePoint")
        bullets = [item for item in bullet_text if isinstance(item, str) and item.strip()] if isinstance(bullet_text, list) else []
        description = cell_text(product.get("description") or product.get("productDescription"))
        if not bullets and (mixed := cell_text(product.get("aboutItem"))):
            description = "\n\n".join(filter(None, (description, mixed)))
        brand = re.sub(r"^brand\s*:\s*", "", cell_text(product.get("brand")), flags=re.I)
        rows.append({
            "asin": product["asin"], "title": product.get("title"), "brand": brand,
            "link": product.get("link"), "linkClean": product.get("linkClean"), "asinUrl": product.get("asinUrl"),
            "market": product.get("amazonDomain") or data.get("amazonDomain"),
            "bullets": bullets, "public_specs": list(dict.fromkeys(specs)), "description": description,
            "reviews": product.get("reviewsSummary"), "price": product.get("price"),
        })
    if not rows:
        raise ValueError("Nexscope 响应中没有带 ASIN 的商品。")
    return rows


def parse_import(data: bytes, filename: str, asin: str, ocr_binary: str, market: str = DEFAULT_MARKET, *, image_field="auto") -> dict:
    suffix = Path(filename).suffix.lower()
    result = {"fields": {}, "raw": "", "message": "", "image": None, "asin": ""}
    source = f"资料导入 · {filename}"[:565]
    if file_format := webpage_format(data, suffix):
        return {**result, **saved_page_preview(data, filename, asin, market, file_format=file_format, ocr_binary=ocr_binary)}
    if suffix in {".url", ".webloc"}:
        raise ValueError("收到的是网页快捷方式。请打开商品详情页，按 Ctrl+S 保存为 HTML 或 MHTML 后上传。")
    if suffix in IMAGE_SUFFIX:
        image, raw, message = image_data(data, ocr_binary, ocr_language(market))
        result.update(image=image, raw=raw, message=message)
        row = (sections(raw) or {"raw_text": raw}) if image_field == "auto" else {image_field: raw}
        source = f"截图 OCR · {filename}"[:565]
    elif suffix in {".csv", ".tsv"}:
        row = select_row(read_csv(text_decode(data)), asin, market)
        if {"asin_code", "image_url", "url", "title"} <= row.keys():
            result["origin"] = "oxylabs_csv"
    elif suffix == ".xlsx":
        with ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 40 * 1024 * 1024:
                raise ValueError("表格解压尺寸过大，请另存少量记录再导入。")
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            def sheet_rows(sheet):
                return table_rows(sheet.iter_rows(max_row=min(sheet.max_row or 2501, 2501),
                                                  max_col=min(sheet.max_column or 100, 100), values_only=True))

            active = workbook.active
            rows = sheet_rows(active)
            if asin and not any(matches_target(item, asin, market) for item in rows):
                for sheet in workbook.worksheets:
                    if sheet is active:
                        continue
                    alternatives = sheet_rows(sheet)
                    if any(matches_target(item, asin, market) for item in alternatives):
                        rows = alternatives
                        break
            row = select_row(rows, asin, market)
        finally:
            workbook.close()
    elif suffix == ".json":
        value = json.loads(text_decode(data))
        products = nexscope_rows(value)
        row = select_row(json_rows(value) if products is None else products, asin, market)
        if products is not None:
            result["origin"] = "nexscope"
    elif suffix in {".txt", ".md"}:
        result["raw"] = text_decode(data)[:30_000]
        row, unclassified = split_sections(result["raw"], include_identity=True)
        if unclassified:
            row["raw_text"] = "\n".join(filter(None, (unclassified, row.get("raw_text"))))
    else:
        extension = f"后缀是 {suffix[:40]}" if suffix else "没有文件后缀"
        raise ValueError(f"无法识别上传文件（{extension}）。请上传浏览器保存的 HTML/MHTML，或使用表格、文本和截图导入。")
    row_asin, row_market = row_identity(row, market)
    if asin and (row_asin not in {"", asin} or row_market != market):
        raise ValueError("资料中的 ASIN 或站点与当前项目不同。")
    if row_asin and (origin := result.get("origin")):
        provider = "Nexscope 商品详情" if origin == "nexscope" else "Oxylabs 商品 CSV"
        url = product_url(row_asin, row_market)
        result.update(url=url, brand=cell_text(row.get("brand"))[:120],
                      message=f"已识别 {provider}。请核对站点、ASIN 和实际返回的字段。")
        source = f"{provider} · {filename} · {url}"[:565]
    result.update(fields=tabular_row(row, source), asin=row_asin, market=row_market)
    if suffix in IMAGE_SUFFIX:
        for field in result["fields"].values():
            count = len([s for s in field["text"].splitlines() if s.strip()])
            if count <= 500:
                field["page_lines"] = [{"line": i, "kind": "ocr", "scope": "product", "location": "上传截图"} for i in range(count)]
    if set(result["fields"]) == {"raw_text"} and not result["message"]:
        result["message"] = "已将文案放入未分段原文，可以直接初筛。"
    return result
