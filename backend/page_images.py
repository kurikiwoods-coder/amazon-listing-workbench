"""Read A+ image text from the current browser or from bytes inside a page archive."""

import base64
import binascii
import io
import os
import re
import shutil
import subprocess
from time import monotonic
from urllib.parse import unquote, urljoin

from PIL import Image, ImageOps

from .marketplaces import MARKETPLACES

MAX_IMAGE_BYTES = 6 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
LANGUAGES = {"en": "eng", "es": "spa", "de": "deu", "fr": "fra", "it": "ita", "nl": "nld",
             "pt": "por", "pl": "pol", "sv": "swe", "tr": "tur", "ar": "ara", "ja": "jpn", "zh": "chi_sim"}


def archive_images(message, page_url):
    images = {}
    for part in message.walk():
        if part.get_content_type() not in IMAGE_TYPES:
            continue
        location = str(part.get("Content-Location", ""))
        cid = str(part.get("Content-ID", "")).strip().strip("<>")
        if location:
            images[urljoin(page_url, location)] = part
        if cid:
            images[f"cid:{cid}"] = part

    def load(node):
        for name in ("src", "data-src", "data-a-hires"):
            path = node.get(name)
            if not path:
                continue
            part = images.get(urljoin(page_url, unquote(path)))
            if part is not None:
                return part.get_payload(decode=True)
        return None
    return load


def embedded_image(node):
    for name in ("src", "data-src", "data-a-hires"):
        value = node.get(name, "")
        if not value.lower().startswith("data:image/"):
            continue
        header, _, data = value.partition(",")
        if header.lower().partition(";")[0][5:] not in IMAGE_TYPES or ";base64" not in header.lower():
            continue
        if len(data) > 4 * MAX_IMAGE_BYTES // 3 + 4096:
            continue
        try:
            return base64.b64decode("".join(data.split()), validate=True)
        except (binascii.Error, ValueError):
            continue
    return None


def ocr_language(market, page_language=""):
    lang = (page_language or MARKETPLACES[market][1]).lower().split("-")[0]
    return os.getenv("LISTING_OCR_LANG") or LANGUAGES.get(lang, "")


class PageImageReader:
    def __init__(self, market, page_language="", *, binary=None, loader=None):
        self.language = ocr_language(market, page_language)
        self.binary = shutil.which(binary if binary is not None else os.getenv("LISTING_EDITOR_OCR", "tesseract"))
        self.loader = loader
        self.deadline = None
        self.attempts = 0
        self.skipped = set()

    def read(self, node):
        if not self.binary or not self.language:
            self.skipped.add("识别图片文字可安装 Tesseract 和页面语言包，或上传转录文案。")
            return ""
        if self.deadline is None:
            self.deadline = monotonic() + 12
        if self.attempts >= 4 or self.deadline - monotonic() < 1:
            self.skipped.add("本次图片识别已达上限，其余图片可分开上传或人工核对。")
            return ""
        data = embedded_image(node) or (self.loader(node) if self.loader else None)
        if not data:
            self.skipped.add("部分图片没有可读图像。可上传含图片的 MHTML、SingleFile 网页或 A+ 截图。")
            return ""
        self.attempts += 1
        if len(data) > MAX_IMAGE_BYTES:
            self.skipped.add("部分原图过大，可裁剪对应 A+ 图片后上传。")
            return ""
        try:
            with Image.open(io.BytesIO(data)) as source:
                if max(source.size) < 150 or min(source.size) < 28:
                    return ""
                if source.width * source.height > 16_000_000:
                    self.skipped.add("部分原图像素过大，可裁剪对应 A+ 图片后上传。")
                    return ""
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((2250, 2250))
                buf = io.BytesIO()
                image.save(buf, "PNG")
            timeout = max(.1, min(5, self.deadline - monotonic()))
            result = subprocess.run([self.binary, "stdin", "stdout", "-l", self.language, "--psm", "11"],
                                    input=buf.getvalue(), capture_output=True, timeout=timeout, check=True)
            return result.stdout.decode("utf-8", errors="replace").strip()[:2000]
        except (OSError, ValueError, Image.DecompressionBombError, subprocess.SubprocessError):
            self.skipped.add("部分图片文字识别失败，请核对 Tesseract 对应语言包或手动转录。")
            return ""


def meaningful_alt(value):
    value = " ".join(value.split())
    if not value or len(value) < 3 or len(value) > 800:
        return ""
    if re.fullmatch(r"(?:image|picture|photo|logo|banner|图像|图片|画像|A\+|module|图)(?:[\s_#-]*\d+)?", value, re.I):
        return ""
    if re.fullmatch(r"[\w./%-]+\.(?:png|gif|jpg|jpeg|webp)(?:\?.*)?", value, re.I) or value.startswith(("data:", "https:", "http:")):
        return ""
    return value
