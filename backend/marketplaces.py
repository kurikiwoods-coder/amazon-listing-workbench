from __future__ import annotations

import re
from urllib.parse import urlsplit

DEFAULT_MARKET = "US"
# Domain, browser locale, and the display name used by the workspace.
MARKETPLACES = {
    "US": ("amazon.com", "en-US", "美国"),
    "CA": ("amazon.ca", "en-CA", "加拿大"),
    "MX": ("amazon.com.mx", "es-MX", "墨西哥"),
    "BR": ("amazon.com.br", "pt-BR", "巴西"),
    "UK": ("amazon.co.uk", "en-GB", "英国"),
    "IE": ("amazon.ie", "en-IE", "爱尔兰"),
    "DE": ("amazon.de", "de-DE", "德国"),
    "FR": ("amazon.fr", "fr-FR", "法国"),
    "IT": ("amazon.it", "it-IT", "意大利"),
    "ES": ("amazon.es", "es-ES", "西班牙"),
    "NL": ("amazon.nl", "nl-NL", "荷兰"),
    "BE": ("amazon.com.be", "fr-BE", "比利时"),
    "PL": ("amazon.pl", "pl-PL", "波兰"),
    "SE": ("amazon.se", "sv-SE", "瑞典"),
    "TR": ("amazon.com.tr", "tr-TR", "土耳其"),
    "AE": ("amazon.ae", "en-AE", "阿联酋"),
    "SA": ("amazon.sa", "ar-SA", "沙特阿拉伯"),
    "EG": ("amazon.eg", "ar-EG", "埃及"),
    "ZA": ("amazon.co.za", "en-ZA", "南非"),
    "IN": ("amazon.in", "en-IN", "印度"),
    "JP": ("amazon.co.jp", "ja-JP", "日本"),
    "AU": ("amazon.com.au", "en-AU", "澳大利亚"),
    "SG": ("amazon.sg", "en-SG", "新加坡"),
}
DOMAIN_MARKETS = {domain: market for market, (domain, _, _) in MARKETPLACES.items()}
ASIN_LINK = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([a-z0-9]{10})(?:/|$)", re.I | re.ASCII)


def market_for_host(host: str) -> str | None:
    host = host.casefold().rstrip(".")
    prefix, _, rest = host.partition(".")
    return DOMAIN_MARKETS.get(rest if prefix in {"www", "m", "smile"} else host)


def market_code(value: str) -> str:
    code = value.strip().upper()
    code = {"GB": "UK", "UAE": "AE"}.get(code, code)
    if code in MARKETPLACES:
        return code
    if market := market_for_host(value.strip()):
        return market
    raise ValueError("站点无效。请选择 Amazon 站点，或填入站点代码，例如 US、UK 或 JP。")


def product_target(value: str, default_market: str = DEFAULT_MARKET) -> tuple[str, str]:
    value = value.strip()
    if re.fullmatch(r"[a-z0-9]{10}", value, re.I | re.ASCII):
        return value.upper(), market_code(default_market)
    try:
        parsed = urlsplit(value if "://" in value else f"https://{value}")
        market = market_for_host(parsed.hostname or "")
        if parsed.scheme in {"https", "http"} and market and parsed.username is None and parsed.port is None:
            if match := ASIN_LINK.search(parsed.path):
                return match[1].upper(), market
    except ValueError:
        pass
    raise ValueError("请输入 10 位 ASIN 或完整的 Amazon 商品详情链接（含 /dp/ASIN）。短链接可打开后复制详情页地址。")


def product_url(asin: str, market: str) -> str:
    return f"https://www.{MARKETPLACES[market][0]}/dp/{asin}"
