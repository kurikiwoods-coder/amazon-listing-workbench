from __future__ import annotations

import os
import re
from contextlib import suppress
from urllib.parse import urlsplit

from .amazon_content import AP_IDS, CAPTCHA, CHALLENGE, SIGN_IN, TITLE, access_barrier, document, make_preview, selected_asin
from .marketplaces import DEFAULT_MARKET, MARKETPLACES, market_for_host, product_target, product_url

START_TIMEOUT = 25_000
RESPONSE_TIMEOUT = 30_000
CONTENT_TIMEOUT = 15_000


class CollectionError(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


def enabled() -> bool:
    return os.getenv("LISTING_AMAZON_PLAYWRIGHT") == "1"


def check_page(root, actual_url: str, asin: str, market: str, *, rendered: dict | None = None):
    url = urlsplit(actual_url)
    barrier = access_barrier(root, rendered=rendered)
    if barrier is None and url.path.startswith("/ap/"):
        barrier = ("登录", "页面转到了 Amazon 登录入口")
    if barrier:
        kind, evidence = barrier
        raise CollectionError(502, f"Amazon 页面要求{kind}（{evidence}），本次在线采集已停止。"
                                   "可读取已有归档，或上传已保存的商品详情页。")
    try:
        url_asin, _ = product_target(actual_url)
    except ValueError:
        url_asin = ""
    displayed = selected_asin(root)
    actual_market = market_for_host(url.hostname or "")
    if url.scheme == "https" and actual_market == market and (url_asin or displayed) and \
            (not url_asin or url_asin == asin) and (not displayed or displayed == asin):
        return
    destination = f"Amazon {actual_market}" if actual_market else (url.hostname or "未知地址")
    observed = f"{destination} {url_asin or '无商品 ASIN'}"
    if displayed and displayed != url_asin:
        observed += f"，页面选中变体为 {displayed}"
    raise CollectionError(502, f"当前目标为 Amazon {market} {asin}，实际打开 {observed}。"
                               "可在常用浏览器确认商品后导入保存的网页。")


def collect_amazon(value: str, *, market: str = DEFAULT_MARKET, still_running=lambda: True) -> dict:
    asin, market = product_target(value, market)
    if not enabled():
        raise CollectionError(409, "Playwright 采集尚未启用。请按 README 安装浏览器并启用采集，或先上传商品资料。")

    def ensure_running():
        if not still_running():
            raise CollectionError(409, "采集已取消。")

    ensure_running()
    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ImportError as exc:
        raise CollectionError(503, "请先按 README 安装 Playwright 和 Chromium。") from exc

    url = product_url(asin, market)
    stage = "launch"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(timeout=START_TIMEOUT)
            try:
                page = browser.new_page(locale=MARKETPLACES[market][1])
                page.set_default_timeout(CONTENT_TIMEOUT)
                ensure_running()
                stage = "response"
                reply = page.goto(url, wait_until="commit", timeout=RESPONSE_TIMEOUT)
                ensure_running()
                if reply and reply.status >= 400:
                    hint = "商品页未找到" if reply.status == 404 else "拒绝访问或暂时不可用"
                    raise CollectionError(502, f"Amazon {market} {hint}（HTTP {reply.status}）。请核对站点商品页或上传原文。")
                stage = "content"
                ready = True
                try:
                    page.wait_for_function(
                        r"""({title, challenge}) =>
                            location.pathname.startsWith('/ap/') ||
                            /^(?:(?:Amazon[\w.]*[: -]+)?(?:Robot Check|Captcha)|Amazon[\w.]* Sign[ -]?In)$/i.test(document.title.trim()) ||
                            Array.from(document.querySelectorAll(challenge)).some(node =>
                                node.getClientRects().length && getComputedStyle(node).visibility === 'visible') ||
                            (document.readyState !== 'loading' &&
                             Array.from(document.querySelectorAll(title)).some(node => node.textContent.trim()))""",
                        arg={"title": TITLE, "challenge": CHALLENGE}, timeout=CONTENT_TIMEOUT,
                    )
                except TimeoutError:
                    ready = False
                ensure_running()
                try:
                    section_selector = ", ".join(f"#{ident}" for ident in AP_IDS)
                    public_sections = ", ".join((section_selector, TITLE, "#feature-bullets", "#featurebullets_feature_div",
                        "#productDescription_feature_div", "#productDescription", "#productOverview_feature_div",
                        "#itemHighlights_feature_div", "[data-feature-name='itemHighlights' i]", ".aplus-v2", ".aplus-v3"))
                    def current_page():
                        snapshot = page.evaluate(
                            """({title, captcha, signIn, sections}) => {
                                const shown = (selector, withText = false) =>
                                    Array.from(document.querySelectorAll(selector)).some(node =>
                                        getComputedStyle(node).visibility === 'visible' &&
                                        Array.from(node.getClientRects()).some(rect => rect.width > 0 && rect.height > 0) &&
                                        (!withText || node.textContent.trim()));
                                const copy = document.documentElement.cloneNode(true);
                                const blocks = Array.from(document.documentElement.querySelectorAll(sections));
                                const copies = copy.querySelectorAll(sections);
                                const isHidden = n => {
                                    const style = getComputedStyle(n);
                                    return style.display === 'none' || style.visibility === 'hidden';
                                };
                                const markHidden = (n, c) => {
                                    if (isHidden(n)) {
                                        c.setAttribute('hidden', '');
                                        return;
                                    }
                                    let a = n.firstElementChild, b = c.firstElementChild;
                                    while (a && b) {
                                        markHidden(a, b);
                                        a = a.nextElementSibling;
                                        b = b.nextElementSibling;
                                    }
                                };
                                blocks.forEach((node, i) => {
                                    if (node.parentElement?.closest(sections)) return;
                                    for (let p = node.parentElement; p; p = p.parentElement) {
                                        if (isHidden(p)) {
                                            copies[i].setAttribute('hidden', '');
                                            return;
                                        }
                                    }
                                    markHidden(node, copies[i]);
                                });
                                return {
                                    url: location.href, html: copy.outerHTML,
                                    captcha: shown(captcha), sign_in: shown(signIn), product: shown(title, true),
                                };
                            }""",
                            {"title": TITLE, "captcha": CAPTCHA, "signIn": SIGN_IN, "sections": public_sections},
                        )
                        root = document(snapshot["html"])
                        check_page(root, snapshot["url"], asin, market, rendered=snapshot)
                        return root

                    root = current_page()
                    if ready and any(root.xpath("//*[@id=$ident]", ident=ident) for ident in AP_IDS):
                        section = page.locator(section_selector).first
                        with suppress(TimeoutError):
                            section.scroll_into_view_if_needed(timeout=1600)
                            page.wait_for_function("""selector => Array.from(document.querySelectorAll(selector)).some(
                                n => n.innerText.trim().length > 35 || n.querySelector('img, .aplus-module'))""",
                                arg=section_selector, timeout=1800)
                        ensure_running()
                        root = current_page()

                    def rendered_image(node):
                        ensure_running()
                        locator = page.locator("xpath=" + node.getroottree().getpath(node))
                        try:
                            original = node.get("src", "")
                            available = locator.evaluate("""(n, src) => n.tagName === 'IMG' &&
                                (n.getAttribute('src') || '') === src && n.complete && n.naturalWidth >= 150 &&
                                n.naturalHeight >= 28 && n.clientWidth >= 120 && n.clientHeight >= 24 &&
                                n.clientWidth * n.clientHeight <= 8000000""", original, timeout=650)
                            if available:
                                return locator.screenshot(timeout=1400, animations="disabled")
                        except Error:
                            pass
                        return None

                    preview = make_preview(root, asin, market, f"Amazon {market} 商品页 · {url}", image_loader=rendered_image)
                    if page.url != url or preview["fields"].get("aplus", {}).get("page_lines"):
                        current_page()
                    preview["origin"] = "playwright"
                except CollectionError:
                    raise
                except ValueError as exc:
                    raise CollectionError(502, str(exc)) from exc
                if not ready:
                    preview["message"] = "页面尚未全部加载，已保留可读取的字段。请核对原文后再应用。"
                return preview
            finally:
                with suppress(Error):
                    browser.close()
    except TimeoutError as exc:
        detail = {
            "launch": "本机 Chromium 启动超时。请关闭旧的工作台进程，用 start-with-playwright.cmd 重新启动。",
            "response": f"连接 Amazon {market} 超时（{RESPONSE_TIMEOUT // 1000} 秒内无页面响应）。请在同一台电脑打开商品页，检查网络或系统代理。",
            "content": f"Amazon {market} 已响应，读取商品正文超时。可在浏览器打开商品页后，粘贴或上传原文。",
        }[stage]
        raise CollectionError(504, detail) from exc
    except Error as exc:
        error = re.search(r"\bERR_[A-Z0-9_]+\b", str(exc))
        if "Executable doesn't exist" in str(exc):
            detail = "Chromium 尚未安装。请运行 python -m playwright install chromium。"
        elif stage == "launch":
            detail = "本机 Chromium 无法启动。请检查工作台终端中的安装或启动报错。"
        elif error:
            detail = f"连接 Amazon {market} 失败（{error.group()}）。请在同一台电脑核对商品链接、网络和系统代理。"
        else:
            detail = f"读取 Amazon {market} 商品页失败。可在浏览器打开商品页后粘贴或上传原文。"
        raise CollectionError(502, detail) from exc
