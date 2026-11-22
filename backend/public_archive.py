from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .amazon import CollectionError
from .amazon_content import document
from .marketplaces import product_target, product_url
from .models import ListingInput

ARCHIVE_BASE = "https://img.team/products/"
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def parse_archive(content: bytes, asin: str) -> dict:
    root = document(content)
    reference = ARCHIVE_BASE + asin
    amazon_url = product_url(asin, "US")
    info = root.xpath("//main/section[h2[normalize-space()='商品信息']]")
    if (root.xpath("//head/link[@rel='canonical']/@href") != [reference]
            or len(info) != 1 or amazon_url not in info[0].xpath(".//a/@href")):
        raise ValueError("公开归档里的商品地址与目标美国站 ASIN 不一致。")

    title = root.xpath("normalize-space(//main/h1)")
    source_stamp = root.xpath(
        "normalize-space(//main/p[starts-with(normalize-space(), 'Amazon数据来源：' )"
        " and contains(., '原始采集时间：')])"
    )
    observed = source_stamp.partition("原始采集时间：")[2]
    observed_at = datetime.fromisoformat(observed) if observed else None
    if not title or observed_at is None or observed_at.utcoffset() is None:
        raise ValueError("公开归档缺少商品标题或带时区的原始采集时间。")

    bullets = root.xpath("//main/section/h2[normalize-space()='源页面卖点']/following-sibling::*[1][self::ul]/li")
    specs = root.xpath("//main/section/h2[normalize-space()='商品参数']/following-sibling::*[1][self::table]//tr")
    pairs = []
    for row in specs:
        key = "".join(char for char in row.xpath("normalize-space(th[1])") if unicodedata.category(char) != "Cf")
        key, value = key.strip(" :："), row.xpath("normalize-space(td[1])")
        if key.casefold() == "asin":
            if value.upper() != asin:
                raise ValueError("公开归档的参数表与目标 ASIN 不一致。")
        elif key and value:
            pairs.append(f"{key}: {value}")

    values = {
        "title": title,
        "bullets": "\n".join(text for bullet in bullets if (text := bullet.xpath("normalize-space(.)"))),
        "public_specs": "\n".join(pairs),
    }
    source = f"img.team 公开归档 · Amazon US · {reference}"
    fields = ListingInput(fields={
        key: {"text": value, "state": "pending", "source": source, "collected_at": observed_at}
        for key, value in values.items() if value
    }).normalized()
    return {
        "asin": asin, "market": "US", "origin": "public_archive",
        "brand": info[0].xpath("normalize-space(.//dt[normalize-space()='品牌']/following-sibling::dd[1])")[:120],
        "label": "img.team 公开商品归档", "url": amazon_url, "reference_url": reference,
        "source_observed_at": observed_at.isoformat(),
        "fields": {key: row for key, row in fields.items() if row["text"]},
        "message": "这是公开保存的美国站历史资料。可核对后评分，缺少的字段可通过文件补录。",
    }


def collect_public_archive(value: str, *, market: str, still_running=lambda: True) -> dict:
    asin, market = product_target(value, market)
    if market != "US":
        raise CollectionError(422, "该公开归档仅收录 Amazon 美国站样本。")
    if not still_running():
        raise CollectionError(409, "归档查询已取消。")

    request = Request(ARCHIVE_BASE + asin, headers={"User-Agent": "ListingWorkbench/1.3.2", "Accept": "text/html"})
    try:
        with build_opener(NoRedirect).open(request, timeout=12) as response:
            if response.headers.get_content_type() != "text/html":
                raise CollectionError(502, "公开归档未返回可读取的 HTML。")
            content = response.read(MAX_ARCHIVE_BYTES + 1)
    except HTTPError as exc:
        if exc.code in {404, 410}:
            raise CollectionError(404, f"img.team 暂未收录 Amazon US {asin}。可上传对应商品资料或使用已授权接口。") from exc
        raise CollectionError(502, f"公开归档暂不可用（HTTP {exc.code}）。可上传已保存的商品资料。") from exc
    except (OSError, HTTPException) as exc:
        raise CollectionError(502, "未能连接 img.team 公开归档。可上传已保存的商品资料。") from exc
    if not still_running():
        raise CollectionError(409, "归档查询已取消。")
    if len(content) > MAX_ARCHIVE_BYTES:
        raise CollectionError(502, "公开归档网页超出读取大小上限。")
    try:
        return parse_archive(content, asin)
    except ValueError as exc:
        raise CollectionError(502, "公开归档缺少可核对的商品原文、日期或身份信息。请查看来源页或上传资料。") from exc
