from __future__ import annotations

import re
import unicodedata
from email import policy
from email.parser import BytesParser
from urllib.parse import urlsplit

from lxml import etree, html

from .marketplaces import market_for_host, product_target, product_url
from .models import ListingInput
from .page_images import PageImageReader, archive_images, meaningful_alt

TITLE = "#productTitle, #ebooksProductTitle, #title_feature_div h1"
CAPTCHA = "#captchacharacters, form[action*='validateCaptcha' i]"
SIGN_IN = "#ap_email, #ap_password"
CHALLENGE = f"{CAPTCHA}, {SIGN_IN}"
BULLET_IDS = ("feature-bullets", "featurebullets_feature_div")
HIGHLIGHT_IDS = {"itemhighlight", "itemhighlights", "itemhighlightfeaturediv", "itemhighlightsfeaturediv",
                 "producttitlehighlights", "titleitemhighlights", "titleitemhighlightsfeaturediv"}
AP_IDS = ("aplus", "aplus_feature_div", "aplus3p_feature_div", "aplusBrandStory_feature_div",
          "aplusBrandStory", "premiumAplus", "aplusEnhancedContent")
SPEC_IDS = ("productOverview_feature_div", "productDetails_techSpec_section_1",
            "productDetails_techSpec_section_2", "productDetails_detailBullets_sections1", "detailBullets_feature_div",
            "productFactsDesktop_feature_div", "productFactsMobile_feature_div", "productDetails_feature_div")
DESCRIPTION_IDS = ("productDescription", "productDescription_feature_div", "bookDescription_feature_div")
HIDDEN_CLASSES = {"aok-hidden", "a-hidden", "aok-display-none"}
NON_COPY_TAGS = {"script", "style", "template", "noscript", "button", "select", "svg"}
BREAK_TAGS = {"h1", "h2", "h3", "h4", "p", "div", "li", "br", "tr"}
MORE_LABELS = {"see more", "show more", "read more", "see less", "mehr anzeigen", "voir plus", "mostra altro",
               "ver más", "もっと見る", "查看更多", "查看详情"}
BULLET_HEADINGS = {"about this item", "关于此商品", "この商品について", "info zu diesem artikel",
                   "à propos de cet article", "acerca de este producto", "informazioni su questo articolo"}
HIGHLIGHT_HEADINGS = {"item highlights", "item highlight", "商品亮点", "商品ハイライト", "artikel-highlights",
                      "points forts de l’article", "points forts de l'article", "aspectos destacados del artículo",
                      "punti salienti dell'articolo", "destaques do item"}
DESCRIPTION_HEADINGS = {"product description", "produktbeschreibung", "description du produit", "商品紹介",
                        "商品の説明", "descrizione prodotto", "descripción del producto", "商品描述"}
OTHER_MODULE_IDS = {*AP_IDS, *SPEC_IDS, "productDescription", "customerReviews", "cm-cr-dp-review-list", "cr-summary-ai"}
SAVED_FROM = re.compile(r"saved from url=\(\d+\)(https?://[^\s<>]+)", re.I)
SINGLEFILE_SOURCE = re.compile(r"<!--\s*Page saved with SingleFile[^\r\n]*\r?\n\s*url:\s*(https?://[^\s<>]+)", re.I)


def _webpage_start(data: bytes) -> str:
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    return data[:16384].decode(encoding, errors="replace")


def detect_webpage_format(data: bytes) -> str | None:
    prefix = _webpage_start(data)
    headers = re.split(r"\r?\n\r?\n", prefix, maxsplit=1)[0]
    if (re.search(r"(?im)^MIME-Version:\s*1\.0\b", headers)
            and re.search(r"(?im)^Content-Type:\s*multipart/related\b", headers)):
        return "mhtml"
    markup = re.sub(r"<!--.*?-->", "", prefix, flags=re.S).lstrip()
    if re.match(r"(?:<!doctype\s+html|<html)(?=\s|>)", markup, re.I):
        return "html"
    return None


def document(content: bytes | str):
    encoding = "utf-8" if isinstance(content, str) else None
    if isinstance(content, bytes) and not re.search(br"(?:charset|encoding)\s*=\s*['\"]?[\w-]+", content[:4096], re.I):
        try:
            content.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            pass
    parser = html.HTMLParser(no_network=True, encoding=encoding)
    try:
        return html.document_fromstring(content, parser=parser)
    except (etree.LxmlError, ValueError) as exc:
        raise ValueError("网页文件没有可读取的 HTML 内容。") from exc


def _fold(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().strip(" \t\r\n:：›").split())


def _is_highlight(node) -> bool:
    return (_fold(node.get("aria-label", "")) in HIGHLIGHT_HEADINGS or
            any(re.sub(r"[-_\s]+", "", node.get(key, "").casefold()) in HIGHLIGHT_IDS
                for key in ("id", "data-feature-name", "data-csa-c-content-id", "data-csa-c-slot-id", "itemprop")))


def _is_aplus(node):
    return (node.get("id") in AP_IDS or node.get("data-feature-name", "").casefold() in {"aplus", "aplus3p", "aplusbrandstory"}
            or _fold(node.get("aria-label", "")) in {"a+ content", "premium a+ content"}
            or bool({"aplus-v2", "aplus-v3", "premium-aplus", "aplus-module-wrapper"}.intersection(node.get("class", "").split())))


def _unrelated(node):
    ident = node.get("id", "").lower()
    return (ident.startswith(("sp_detail", "sp-detail", "sims-", "sims_", "customerreview", "cm-cr-", "cr-summary",
                              "recommendation", "similarities", "rhf", "ape_", "p13n"))
            or node.get("data-component-type") in {"sp-sponsored-result", "s-search-result"})


def _in_unrelated(node):
    return _unrelated(node) or any(_unrelated(parent) for parent in node.iterancestors())


def _hidden(node) -> bool:
    return not isinstance(node.tag, str) or node.tag in NON_COPY_TAGS or (
        node.get("hidden") is not None or
        node.get("aria-hidden", "").casefold() == "true" or
        bool(HIDDEN_CLASSES.intersection(node.get("class", "").split())) or
        bool(re.search(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", node.get("style", ""), re.I))
    )


def _visible(node) -> bool:
    return not _hidden(node) and not any(_hidden(parent) for parent in node.iterancestors())


def _text(node, *, multiline=False, exclude_headings=False, omit=None) -> str:
    parts = []

    def scan(element):
        if _hidden(element) or (omit and omit(element)) or exclude_headings and element.tag in {"h1", "h2", "h3", "h4"}:
            return
        block = element.tag in BREAK_TAGS
        if block:
            parts.append("\n")
        if element.text:
            parts.append(element.text)
        for child in element:
            scan(child)
            if child.tail:
                parts.append(child.tail)
        if block:
            parts.append("\n")

    scan(node)
    text = "".join(parts)
    if multiline:
        return "\n".join(clean for line in text.splitlines() if (clean := " ".join(line.split())))
    return " ".join(text.split())


def _roots(root, *ids):
    return [node for ident in ids for node in root.xpath("//*[@id=$ident]", ident=ident) if _visible(node)]


def _heading_content(root, names, allowed_tags, *, inline=False):
    # Heading fallbacks inspect only the next content block and ignore review/recommendation modules.
    for heading in root.xpath("//h2|//h3|//h4|//dt|//strong|//span|//label" if inline else "//h2|//h3|//h4"):
        if inline and len(heading) > 2:
            continue
        if not _visible(heading) or _fold(_text(heading)) not in names:
            continue
        if _in_unrelated(heading) or any(parent.get("id") in OTHER_MODULE_IDS or _is_aplus(parent)
                                        or _is_highlight(parent) for parent in heading.iterancestors()):
            continue
        following = heading.getnext()
        if following is not None and following.tag in allowed_tags and _visible(following):
            yield following


def title_text(root) -> str:
    titles = _roots(root, "productTitle", "ebooksProductTitle")
    titles += [heading for container in _roots(root, "title_feature_div") for heading in container.iter("h1") if _visible(heading)]
    for node in titles:
        if not _in_unrelated(node) and (text := _text(node, omit=_is_highlight)):
            return text.rstrip(" |｜–—") if any(_is_highlight(child) for child in node.iterdescendants("*")) else text
    return ""


def _bullet_text(root) -> str:
    roots = _roots(root, *BULLET_IDS) + list(_heading_content(root, BULLET_HEADINGS, {"ul", "ol", "div"}))
    for section in roots:
        items = []
        for item in section.iter("li"):
            if not _visible(item) or any(parent.tag == "li" for parent in item.iterancestors()):
                continue
            if _in_unrelated(item) or any(_is_highlight(parent) or _is_aplus(parent) or parent.get("id") in OTHER_MODULE_IDS for parent in item.iterancestors()):
                continue
            text = _text(item, exclude_headings=True)
            if text and _fold(text) not in MORE_LABELS:
                items.append(text)
        if items:
            return "\n".join(dict.fromkeys(items[:100]))
    return ""


def _highlight_text(root) -> str:
    roots = [node for node in root.xpath("//*[@id or @data-feature-name or @data-csa-c-content-id or @data-csa-c-slot-id or @itemprop or @aria-label]")
             if _is_highlight(node) and _visible(node)]
    roots.extend(_heading_content(root, HIGHLIGHT_HEADINGS, {"p", "span", "div", "ul", "ol", "dd"}, inline=True))
    for section in roots:
        if _in_unrelated(section) or any(parent.get("id") in OTHER_MODULE_IDS or _is_aplus(parent) for parent in section.iterancestors()):
            continue
        if section.xpath(".//table|.//*[@id='feature-bullets']"):
            continue
        items = [_text(item) for item in section.iter("li") if _visible(item)]
        value = ", ".join(item for item in items if item) or _text(section, exclude_headings=True)
        # Some templates use a plain span for the field's visible heading.
        value = re.sub(r"^Item Highlights\s*[:：]?\s*", "", value, count=1, flags=re.I)
        if value and _fold(value) not in HIGHLIGHT_HEADINGS:
            return value
    for label in root.xpath("//strong|//b|//label|//span"):
        if len(label) or not (label.tail or "").strip() or _fold(label.text or "") not in HIGHLIGHT_HEADINGS:
            continue
        if _visible(label) and not _in_unrelated(label) and not any(_is_aplus(p) or p.get("id") in OTHER_MODULE_IDS for p in label.iterancestors()):
            return " ".join(label.tail.strip(" \t\n:：").split())
    return ""


def access_barrier(root, *, rendered: dict | None = None) -> tuple[str, str] | None:
    title = _fold(" ".join(root.xpath("//head/title/text()")))
    if re.fullmatch(r"(?:amazon(?:\.[a-z.]+)?[\s:–-]+)?(?:robot check|captcha)", title):
        return "人机验证", "网页标题为 Robot Check 或 CAPTCHA"
    if re.fullmatch(r"amazon(?:\.[a-z.]+)?\s+sign[ -]?in", title):
        return "登录", "网页标题为 Amazon 登录页"
    if rendered is not None:
        if rendered["captcha"]:
            return "人机验证", "浏览器显示了验证码表单"
        if rendered["sign_in"]:
            return "登录", "浏览器显示了登录表单"
        if rendered["product"]:
            return None
    captcha = root.xpath("//input[@id='captchacharacters']|//form[contains(translate(@action, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'validatecaptcha')]")
    if any(_visible(node) for node in captcha):
        return "人机验证", "网页含验证码表单，商品详情尚未确认"
    login = root.xpath("//input[@id='ap_email' or @id='ap_password']")
    if not title_text(root) and any(_visible(node) and node.get("type") != "hidden" for node in login):
        return "登录", "网页含登录表单，商品详情尚未确认"
    return None


def selected_asin(root) -> str:
    # Prefer the chosen purchase variant. Recommendation widgets may have their own ASIN inputs.
    nodes = root.xpath("//*[@id='addToCart' or @id='buybox' or @id='desktop_buybox']//input[@id='ASIN' or @name='ASIN']")
    if not nodes:
        nodes = root.xpath("//input[@id='ASIN']")
    values = {value for node in nodes if re.fullmatch(r"[A-Z0-9]{10}", value := node.get("value", "").strip().upper())}
    if len(values) > 1:
        raise ValueError("页面出现多个选中商品编号。请在浏览器确认规格后重新保存商品页。")
    return next(iter(values), "")


def _specifications(root) -> str:
    specs = []
    excluded = {"customer reviews", "best sellers rank", "asin"}
    for section in _roots(root, *SPEC_IDS):
        if _in_unrelated(section):
            continue
        for row in section.iter("tr"):
            cells = [_text(cell) for cell in row if cell.tag in {"th", "td"} and _visible(cell)]
            if _visible(row) and len(cells) == 2 and all(cells) and _fold(cells[0]) not in excluded:
                specs.append(f"{cells[0]}: {cells[1]}")
        if section.get("id") == "detailBullets_feature_div":
            for item in section.iter("li"):
                if _visible(item) and (text := _text(item)):
                    label = text.split(":", 1)[0].translate(str.maketrans("", "", "\u200e\u200f"))
                    if _fold(label) not in excluded:
                        specs.append(text)
        for term in section.iter("dt"):
            value = term.getnext()
            if value is not None and value.tag == "dd" and _visible(term) and _visible(value):
                label, detail = _text(term), _text(value)
                if label and detail and _fold(label) not in excluded:
                    specs.append(f"{label}: {detail}")
    return "\n".join(dict.fromkeys(filter(None, specs)))


def _aplus_content(root, reader):
    candidates = [node for node in root.xpath("//*[@id or @class or @data-feature-name or @aria-label]") if _is_aplus(node)
                  and _visible(node) and not _in_unrelated(node)]
    roots = [node for node in candidates if not any(_is_aplus(parent) for parent in node.iterancestors())]
    rows, metadata, seen = [], [], set()
    chars = modules = images = 0
    notes = []
    captions = {"from the manufacturer", "product description", "from the brand", "brand story", "a+ content",
                "vom hersteller", "メーカーより", "description du fabricant", "du fabricant"}

    def append(value, kind, scope, location):
        nonlocal chars
        value = " ".join(value.split())
        key = (_fold(value), scope)
        if not value or key[0] in MORE_LABELS | captions or key in seen:
            return
        if len(rows) >= 500 or chars + len(value) + bool(rows) > 12_000:
            if not notes:
                notes.append("A+ 文字超过字段上限，已保留可完整定位的段落。")
            return
        seen.add(key)
        metadata.append({"line": len(rows), "kind": kind, "scope": scope, "location": location})
        rows.append(value)
        chars += len(value) + (len(rows) > 1)

    def walk(node, scope, location, buffer):
        nonlocal images, modules
        if _hidden(node) or _unrelated(node):
            return
        context = (node.get("id", "") + " " + node.get("class", "")).lower()
        next_scope = scope
        if "compar" in context or node.tag == "table" and (len(node.xpath(".//th")) > 2 or node.xpath(".//a[contains(@href,'/dp/')]")):
            next_scope = "comparison"
        elif scope == "product" and ("brandstory" in context or "brand-story" in context or
                                     node.get("data-feature-name", "").lower() == "aplusbrandstory"):
            next_scope = "brand"
        has_module = any(token == "aplus-module" or token.startswith(("apm-module", "premium-module"))
                         for token in node.get("class", "").split())
        block = node.tag in BREAK_TAGS | {"table", "th", "td", "dd", "dt", "section", "img"} or has_module or scope != next_scope
        def flush():
            if buffer:
                append("".join(buffer), "html", scope, location)
                buffer.clear()
        if block:
            flush()
        if has_module:
            modules += 1
            location = f"A+ 模块 {modules}"
        scope = next_scope
        if node.tag == "img":
            images += 1
            where = f"{location} · 图片 {images}"
            if alt := meaningful_alt(node.get("alt", "")):
                append(alt, "alt", scope, where)
            if images <= 24:
                for line in reader.read(node).splitlines():
                    if len(line.strip()) > 1:
                        append(line, "ocr", scope, where)
            else:
                reader.skipped.add("部分图片超出本次识别范围，可另行上传文案或截图。")
            return
        if node.text:
            buffer.append(node.text)
        for child in node:
            walk(child, scope, location, buffer)
            if child.tail:
                buffer.append(child.tail)
        if block:
            flush()

    for index, node in enumerate(roots, start=1):
        buffer = []
        location = f"A+ 区块 {index}"
        walk(node, "product", location, buffer)
        if buffer:
            append("".join(buffer), "html", "product", location)
    if roots and not rows:
        notes.append("已找到 A+ 区块，本次没有读到可核对的文字。")
    notes.extend(sorted(reader.skipped))
    return "\n".join(rows), metadata, notes


def make_preview(root, asin: str, market: str, source: str, *, image_loader=None, ocr_binary=None) -> dict:
    title = title_text(root)
    if not title:
        raise ValueError("已收到商品页内容，但未识别到商品标题。请确认保存的是商品详情页，或粘贴原文。")
    sections = _roots(root, *DESCRIPTION_IDS)
    if not sections:
        sections = list(_heading_content(root, DESCRIPTION_HEADINGS, {"p", "div", "section"}))
    descriptions = []
    for node in sections:
        if _in_unrelated(node) or any(_is_aplus(parent) for parent in node.iterancestors()):
            continue
        body = _text(node, multiline=True, omit=lambda e: _is_aplus(e) or _unrelated(e))
        descriptions.extend(line for line in body.splitlines() if _fold(line) not in DESCRIPTION_HEADINGS)
    reader = PageImageReader(market, root.get("lang", ""), binary=ocr_binary, loader=image_loader)
    aplus, metadata, notes = _aplus_content(root, reader)
    brand_lines = [_text(node) for node in _roots(root, "bylineInfo")]
    brand = re.fullmatch(r"(?:Visit the (.+?) Store|Brand:\s*(.+))", brand_lines[0], re.I) if brand_lines else None
    price_nodes = [node for section in _roots(root, "corePrice_feature_div", "corePriceDisplay_desktop_feature_div")
                   for node in section.xpath(".//*[contains(concat(' ',normalize-space(@class),' '),' priceToPay ')]")]
    price = next((value for node in price_nodes if _visible(node) and re.search(r"\d", value := _text(node))), "")
    summaries = [node for section in _roots(root, "cr-summary-ai") for node in section.iter("p") if _visible(node)]
    data = {
        "title": title,
        "bullets": _bullet_text(root),
        "highlights": _highlight_text(root),
        "aplus": aplus,
        "raw_text": "\n".join(dict.fromkeys(descriptions)),
        "public_specs": _specifications(root),
        "price": price[:120],
        "reviews": next((text for node in summaries if (text := _text(node))), ""),
    }
    rows = {key: {"state": "pending", "text": value[:32_000 if key == "raw_text" else 12_000], "source": source}
            for key, value in data.items() if value.strip()}
    if metadata:
        rows["aplus"]["page_lines"] = metadata
    if notes:
        rows.setdefault("aplus", {"state": "missing", "text": "", "source": source})["notes"] = notes
    fields = ListingInput(fields=rows).normalized()
    return {"asin": asin, "market": market, "brand": (brand.group(1) or brand.group(2))[:120] if brand else "",
            "url": product_url(asin, market), "label": f"Amazon {market} 商品页",
            "fields": {key: fields[key] for key in rows},
            "message": "请核对页面文案和商品型号。未识别到的字段可后续补充。"}


def saved_page_preview(data: bytes, filename: str, target: str, market: str, *, file_format: str, ocr_binary=None) -> dict:
    archive_url = ""
    image_loader = None
    charset = ""
    if file_format == "mhtml":
        message = BytesParser(policy=policy.default).parsebytes(data)
        html_parts = [part for part in message.walk() if part.get_content_type() == "text/html"]
        start = message.get_param("start")
        page = next((part for part in html_parts if start and part.get("Content-ID") == start),
                    html_parts[0] if html_parts else None)
        if page is None or not (body := page.get_payload(decode=True)):
            raise ValueError("单文件网页中没有 HTML 正文。请在浏览器中重新保存为 .html。")
        archive_url = str(page.get("Content-Location") or message.get("Snapshot-Content-Location") or "")
        image_loader = archive_images(message, archive_url)
        charset = page.get_content_charset() or ""
        data = body
    try:
        content = data.decode(charset) if charset else data
    except (LookupError, UnicodeDecodeError):
        content = data
    root = document(content)
    if access_barrier(root):
        raise ValueError("保存的是登录或人机验证页面。请打开正常商品详情页后再保存。")
    prefix = _webpage_start(data)
    saved_from = SAVED_FROM.search(prefix) or SINGLEFILE_SOURCE.search(prefix)
    candidates = [archive_url, saved_from.group(1) if saved_from else "",
                  *root.xpath("//link[@rel='canonical']/@href|//meta[@property='og:url']/@content")]
    source_market = ""
    url_asin = ""
    for url in filter(None, candidates):
        try:
            source_market = market_for_host(urlsplit(url).hostname or "") or ""
            if source_market:
                url_asin, source_market = product_target(url)
                break
        except ValueError:
            if source_market:
                break
    asin = selected_asin(root) or url_asin
    if not asin:
        raise ValueError("没有在网页中找到可核对的 ASIN。请保存对应的 Amazon 商品详情页。")
    page_market = source_market or market
    if target and (asin != target or page_market != market):
        raise ValueError(f"保存页实际对应 Amazon {page_market} {asin}，当前目标为 Amazon {market} {target}。"
                         "可在快速评分清空输入的 ASIN，按保存页实际商品重新导入。")
    source = f"Amazon {page_market} 保存的网页 · {filename} · {product_url(asin, page_market)}"
    preview = make_preview(root, asin, page_market, source[:600], image_loader=image_loader, ocr_binary=ocr_binary)
    preview["label"] = filename
    preview["origin"] = "saved_page"
    if not source_market:
        preview["message"] = f"网页未记录站点，已按所选的 Amazon {market} 处理。请核对原文。"
    if url_asin and asin != url_asin:
        preview["message"] = f"网页地址包含 {url_asin}，页面选中变体为 {asin}，已按选中变体导入。"
    return preview
