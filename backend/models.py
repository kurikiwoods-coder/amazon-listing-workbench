from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from .marketplaces import DEFAULT_MARKET, market_code, product_target

State = Literal["confirmed", "pending", "missing", "absent", "na"]
Role = Literal["identity", "use", "feature", "spec", "boundary", "difference", "proof"]
FieldKey = Literal["title", "highlights", "bullets", "aplus", "raw_text", "public_specs", "attributes", "search_terms",
                   "price", "reviews", "qa", "keywords", "competitors"]
ProductType = Literal["unknown", "non_media", "media"]
AnswerState = Literal["supported", "partial", "contradicted", "not_found", "unknown"]
Strategy = Literal["A", "B", "C"]
FieldText = Annotated[str, Field(max_length=12_000)]
ShortText = Annotated[str, Field(max_length=600)]
ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
MarketCode = Annotated[str, AfterValidator(market_code)]

FIELDS = {
    "title": "商品标题", "highlights": "Item Highlights", "bullets": "五点描述",
    "aplus": "A+ 内容", "raw_text": "商品描述 / 未分段原文", "public_specs": "公开商品规格",
    "attributes": "后台属性", "search_terms": "Search Terms",
    "price": "价格", "reviews": "评论摘要", "qa": "Q&A", "keywords": "目标关键词",
    "competitors": "竞品 ASIN / 问题",
}
PUBLIC = ("title", "highlights", "bullets", "aplus")
PUBLISHED_COPY = (*PUBLIC, "raw_text", "public_specs")
CONTENT = (*PUBLISHED_COPY, "attributes", "search_terms")
SOURCE_STATUS = {
    "confirmed": "已采集并确认", "pending": "已采集待核实", "missing": "未采集 / 未提供",
    "absent": "已确认不存在", "na": "当前类目不适用",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def get_asin(value: str) -> str:
    return product_target(value)[0]


class ProductInput(Model):
    asin: Annotated[str, Field(max_length=2048)] = ""
    name: Annotated[str, Field(min_length=1, max_length=140)]
    sku: Annotated[str, Field(max_length=100)] = ""
    market: MarketCode = DEFAULT_MARKET
    brand: Annotated[str, Field(max_length=120)] = ""
    category: Annotated[str, Field(max_length=120)] = ""
    product_type: ProductType = "unknown"

    @model_validator(mode="after")
    def normalize_product_target(self) -> ProductInput:
        if self.asin:
            self.asin, self.market = product_target(self.asin, self.market)
        return self


class PageLine(Model):
    line: int = Field(ge=0, le=12_000)
    kind: Literal["html", "alt", "ocr"]
    scope: Literal["product", "brand", "comparison"] = "product"
    location: Annotated[str, Field(min_length=1, max_length=180)]


class SourceField(Model):
    state: State = "missing"
    text: Annotated[str, Field(max_length=32_000)] = ""
    source: ShortText = ""
    collected_at: datetime | None = None
    page_lines: list[PageLine] = Field(default_factory=list, max_length=500)
    notes: list[ShortText] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def consistent_state(self) -> SourceField:
        if self.state in {"confirmed", "pending"} and not self.text:
            raise ValueError("已采集的字段需要提供原文。")
        if self.state in {"confirmed", "pending", "absent", "na"} and not self.source:
            raise ValueError("请填写来源或确认依据。")
        if self.state in {"missing", "absent", "na"} and self.text:
            raise ValueError("未提供、不存在或不适用的字段不能包含正文。")
        if self.collected_at is not None and self.collected_at.tzinfo is None:
            raise ValueError("采集时间需要携带时区。")
        count = len([line for line in self.text.splitlines() if line.strip()])
        indices = {item.line for item in self.page_lines}
        if len(indices) != len(self.page_lines) or any(index >= count for index in indices):
            raise ValueError("网页段落位置与正文不匹配，请重新导入或核对段落。")
        return self


class ListingInput(Model):
    fields: dict[FieldKey, SourceField] = Field(default_factory=dict)
    note: ShortText = ""

    @model_validator(mode="after")
    def check_field_shape(self) -> ListingInput:
        if any(len(value.text) > 12_000 for key, value in self.fields.items() if key != "raw_text"):
            raise ValueError("单一字段最多 12,000 字符。")
        for key in ("title", "highlights"):
            row = self.fields.get(key)
            if row and len([part for part in row.text.splitlines() if part.strip()]) > 1:
                raise ValueError(f"{FIELDS[key]}应保存在一行。")
        bullets = self.fields.get("bullets")
        if bullets and len([part for part in bullets.text.splitlines() if part.strip()]) > 100:
            raise ValueError("五点字段超过 100 条。请核对每行是否对应一条完整要点。")
        return self

    def normalized(self) -> dict:
        rows = {}
        for key in FIELDS:
            value = self.fields.get(key, SourceField()).model_dump(mode="json")
            for optional in ("page_lines", "notes"):
                if not value[optional]:
                    del value[optional]
            if value["state"] != "missing" and not value["collected_at"]:
                value["collected_at"] = utcnow()
            rows[key] = value
        return rows


class SnapshotInput(ListingInput):
    asin: Annotated[str, Field(max_length=2048)] = ""
    market: MarketCode | None = None
    base_snapshot_id: ID | None = None


class QuickAuditInput(ListingInput):
    text: Annotated[str, Field(max_length=32_000)] = ""
    asin: Annotated[str, Field(max_length=2048)] = ""
    market: MarketCode = DEFAULT_MARKET
    category: Annotated[str, Field(max_length=120)] = ""
    brand: Annotated[str, Field(max_length=120)] = ""
    product_type: ProductType = "unknown"
    mode: Literal["rules", "llm"] = "rules"

    @model_validator(mode="after")
    def has_copy(self) -> QuickAuditInput:
        if bool(self.text) == bool(self.fields):
            raise ValueError("请提供粘贴原文或一份导入预览。")
        return self


class SaveListingProject(Model):
    product: ProductInput
    listing: ListingInput


class AmazonCollectInput(Model):
    asin: Annotated[str, Field(min_length=1, max_length=2048)]
    market: MarketCode | None = None


class CaptureInput(AmazonCollectInput):
    product_id: ID | None = None
    provider: Literal["auto", "playwright", "nexscope", "public_archive"] = "playwright"


class Fact(Model):
    id: ID
    label: Annotated[str, Field(min_length=1, max_length=140)]
    approved_copy: Annotated[str, Field(alias="copy", min_length=1, max_length=800)]
    source: ShortText = ""
    state: Literal["confirmed", "pending", "rejected"] = "pending"
    role: Role = "feature"
    conflicts: list[ShortText] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def valid_fact(self) -> Fact:
        if self.state == "confirmed" and not self.source:
            raise ValueError("已确认的产品事实需要填写来源。")
        if "\n" in self.approved_copy or "\r" in self.approved_copy:
            raise ValueError("每条英文事实请填写在一行。")
        self.conflicts = list(dict.fromkeys(term for term in self.conflicts if term))
        if any(term.casefold() in self.approved_copy.casefold() for term in self.conflicts):
            raise ValueError("冲突措辞不能出现在本条核实文案中。")
        return self


class Question(Model):
    id: ID
    text: Annotated[str, Field(min_length=1, max_length=450)]
    intent: Annotated[str, Field(max_length=400)] = ""
    source: Literal["template", "manual", "alexa", "review", "competitor", "ai"] = "manual"
    priority: Literal["high", "standard"] = "standard"
    fields: list[FieldKey] = Field(default_factory=lambda: list(PUBLISHED_COPY), min_length=1, max_length=8)
    anchors: list[ShortText] = Field(default_factory=list, max_length=12)
    fact_ids: list[ID] = Field(default_factory=list, max_length=15)

    @model_validator(mode="after")
    def unique_scope(self) -> Question:
        self.fields = list(dict.fromkeys(self.fields))
        self.anchors = list(dict.fromkeys(term for term in self.anchors if term))
        self.fact_ids = list(dict.fromkeys(self.fact_ids))
        if any(key not in CONTENT for key in self.fields):
            raise ValueError("买家问题的证据仅可定位至 Listing 内容字段。")
        if self.source == "alexa":
            self.priority = "high"
        return self


class ContextInput(Model):
    facts: list[Fact] = Field(default_factory=list, max_length=80)
    questions: list[Question] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_references(self) -> ContextInput:
        ids = {item.id for item in self.facts}
        if len(ids) != len(self.facts) or len({item.id for item in self.questions}) != len(self.questions):
            raise ValueError("事实或问句 ID 重复。")
        if any(set(question.fact_ids) - ids for question in self.questions):
            raise ValueError("问句关联了无法找到的产品事实。")
        return self


class AuditInput(Model):
    snapshot_id: str | None = None
    mode: Literal["rules", "llm"] = "rules"


class RewriteInput(Model):
    audit_id: str
    strategy: Strategy


class FieldRewriteInput(Model):
    text: Annotated[str, Field(max_length=32_000)] = ""
    focus_question: ShortText = ""
    field: FieldKey | None = None
    snapshot_id: ID | None = None
    context_id: ID | None = None
    audit_id: ID | None = None
    issue_key: ShortText = ""
    market: MarketCode = DEFAULT_MARKET
    mode: Literal["auto", "rules"] = "auto"

    @model_validator(mode="after")
    def has_input(self) -> FieldRewriteInput:
        if not self.text and not (self.audit_id and self.issue_key):
            raise ValueError("请粘贴 Listing 原文，或从一条修改意见进入。")
        return self


class FieldReplacement(Model):
    field: Literal["title", "highlights", "bullets", "aplus"]
    line: int = Field(ge=0, le=12_000)
    before: FieldText
    text: Annotated[str, Field(min_length=1, max_length=12_000)]


class FieldDraftInput(Model):
    snapshot_id: ID
    context_id: ID
    strategy: Strategy = "B"
    edits: list[FieldReplacement] = Field(min_length=1, max_length=20)


class ReviewLine(Model):
    key: Annotated[str, Field(pattern=r"^(?:title|highlights|aplus|bullets\.(?:[0-9]|[1-9][0-9]))$")]
    text: FieldText
    decision: Literal["pending", "accepted", "rejected", "kept"]


class ReviewInput(Model):
    lines: list[ReviewLine] = Field(min_length=1, max_length=103)
    note: ShortText = ""


class PublicationInput(Model):
    published_at: datetime
    note: ShortText = ""

    @field_validator("published_at")
    @classmethod
    def has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("上线时间必须包含时区。")
        if value > datetime.now(timezone.utc):
            raise ValueError("实际上线时间不能晚于当前时间。")
        return value
