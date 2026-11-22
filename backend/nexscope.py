from __future__ import annotations

import os

import httpx

from .amazon import CollectionError
from .importers import MAX_UPLOAD, parse_import
from .marketplaces import MARKETPLACES, product_target

PRODUCT_DETAIL = "https://api.nexscope.ai/amazon/product/detail"


def enabled() -> bool:
    return bool(os.getenv("LISTING_NEXSCOPE_API_KEY", "").strip())


def collect_nexscope(value: str, *, market: str, still_running=lambda: True) -> dict:
    asin, market = product_target(value, market)
    key = os.getenv("LISTING_NEXSCOPE_API_KEY", "").strip()
    if not key:
        raise CollectionError(409, "尚未配置 Nexscope 商品接口。可先导入已保存的商品资料。")
    if not still_running():
        raise CollectionError(409, "采集已取消。")
    try:
        with httpx.stream(
            "POST", PRODUCT_DETAIL, timeout=35.0, follow_redirects=False,
            headers={"Authorization": f"Bearer {key}"},
            json={"asins": asin, "amazonDomain": MARKETPLACES[market][0]},
        ) as response:
            if response.status_code in {401, 403}:
                raise CollectionError(502, "Nexscope 拒绝授权。请检查服务端 API Key 和接口权限。")
            if response.status_code != 200:
                raise CollectionError(502, f"Nexscope 商品接口返回 HTTP {response.status_code}。请检查供应商状态或额度。")
            content = bytearray()
            for chunk in response.iter_bytes(chunk_size=65_536):
                content.extend(chunk)
                if len(content) > MAX_UPLOAD:
                    raise CollectionError(502, "Nexscope 商品响应超出 8 MB。可在供应商端导出单件商品后上传。")
    except httpx.RequestError as exc:
        raise CollectionError(502, "未能连接 Nexscope 商品接口。请检查工作台网络。") from exc
    if not still_running():
        raise CollectionError(409, "采集已取消。")
    try:
        preview = parse_import(bytes(content), "Nexscope API.json", asin, "", market)
    except ValueError as exc:
        raise CollectionError(502, str(exc) if not isinstance(exc, UnicodeError) else "Nexscope 商品响应无法读取。") from exc
    if preview.get("origin") != "nexscope" or not preview["fields"].get("title", {}).get("text"):
        raise CollectionError(502, "Nexscope 尚未返回可核对的商品原文。可在供应商端查看结果或上传返回的资料。")
    return {**preview, "label": "Nexscope 商品详情"}
