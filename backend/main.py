from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import timezone
from pathlib import Path
from threading import Thread
from typing import Literal
from uuid import uuid4
from zipfile import BadZipFile

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from openpyxl.utils.exceptions import InvalidFileException
from PIL import UnidentifiedImageError

from .amazon import CollectionError, collect_amazon, enabled as amazon_enabled
from .audit import ENGINE_VERSION, SCORE_MODULES, score_listing
from .demo import seed_demo
from .field_suggestions import VERSION as SUGGESTION_VERSION, eligible, model_items, public_records, suggested_fields
from .importers import MAX_UPLOAD, MAX_WEBPAGE_UPLOAD, parse_import, quick_fields, webpage_format
from .jobs import CaptureWorker, Worker, audit_data, ensure_engine, fingerprint, preview_candidate, scoring_key
from .llm import LLM, ModelFailed
from .marketplaces import DEFAULT_MARKET, MARKETPLACES, market_code, product_target
from .models import (AmazonCollectInput, AuditInput, CaptureInput, ContextInput, FieldDraftInput, FieldRewriteInput,
                     ListingInput, ProductInput, PublicationInput,
                     QuickAuditInput, ReviewInput, RewriteInput, SaveListingProject, SnapshotInput, FIELDS, PUBLISHED_COPY,
                     SOURCE_STATUS, utcnow)
from .nexscope import enabled as nexscope_enabled
from .questions import template_questions
from .reports import EXPORTS, alexa_skill_input, report_sections
from .rewrite import STRATEGIES, inspect_candidate
from .rules import rules_for
from .skill_rewrite import SKILL, issue_edit, preview_rewrite, staged_review, with_issue_edits
from .store import ROOT, Conflict, NotFound, Store, encode, unpack

ImageField = Literal["auto", "aplus", "highlights", "bullets", "raw_text", "public_specs"]
ReviewDecision = Literal["pending", "shortlisted", "dismissed"]


def create_app(db_path: Path | None = None, web_path: Path | None = None, demo=True):
    load_dotenv(ROOT / ".env")
    store = Store(db_path)
    worker = Worker(store)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        if demo and os.getenv("LISTING_SEED_DEMO", "1") == "1":
            seed_demo(store, automatic=True)
        worker.stopped.clear()
        collector = CaptureWorker(store, lambda asin, **options: collect_amazon(asin, **options))
        app.state.collector = collector
        threads = [Thread(target=runner.run, daemon=True, name=name) for runner, name in (
            (worker, "listing-jobs"), (collector, "amazon-capture-1"), (collector, "amazon-capture-2"),
        )]
        for thread in threads:
            thread.start()
        try:
            yield
        finally:
            for runner in (worker, collector):
                runner.stopped.set()
                runner.wake.set()
            for thread in threads:
                thread.join(timeout=1)

    app = FastAPI(title="Listing 工作台", version="1.3.2", lifespan=lifespan)

    @app.middleware("http")
    async def enforce_same_origin(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            hosts = {f"http://{request.url.netloc}", f"https://{request.url.netloc}"}
            if origin and origin not in hosts:
                return JSONResponse(status_code=403, content={"detail": "请在工作台页面内提交该操作。"})
        response = await call_next(request)
        if request.url.path in {"/", "/index.html"}:
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(NotFound)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        messages = [".".join(str(key) for key in issue["loc"] if key != "body") + " · " +
                    issue["msg"].removeprefix("Value error, ") for issue in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": "；".join(messages[:5])})

    @app.get("/api/config")
    def config():
        client = LLM.configured()
        return {"model": client.model if client else None, "fields": FIELDS, "source_status": SOURCE_STATUS,
                "strategies": STRATEGIES, "max_upload_mb": MAX_UPLOAD // (1024 * 1024),
                "max_webpage_mb": MAX_WEBPAGE_UPLOAD // (1024 * 1024),
                "amazon_playwright": amazon_enabled(), "nexscope": nexscope_enabled(), "default_market": DEFAULT_MARKET,
                "marketplaces": [{"code": code, "domain": domain, "label": label}
                                 for code, (domain, _, label) in MARKETPLACES.items()]}

    @app.get("/api/products")
    def products():
        with store.connect() as conn:
            return store.product_list(conn)

    @app.post("/api/demo", status_code=201)
    def demo_product():
        return seed_demo(store)

    @app.post("/api/products", status_code=201)
    def new_product(body: ProductInput):
        with store.connect(write=True) as conn:
            return store.create_product(conn, body.model_dump())

    @app.post("/api/listing-projects", status_code=201)
    def save_listing_project(body: SaveListingProject):
        identity = body.product.model_dump()
        if not any(row.text for key, row in body.listing.fields.items() if key in PUBLISHED_COPY):
            raise HTTPException(422, "请至少提供一项商品页文案。")
        with store.connect(write=True) as conn:
            product = store.find_product(conn, identity["market"], identity["asin"]) or store.create_product(conn, identity)
            current = store.snapshot(conn, product["id"])
            updated = {key: row for key, row in body.listing.normalized().items() if row["state"] != "missing"}
            if any(current["fields"][key] != row for key, row in updated.items()):
                store.new_snapshot(conn, product["id"], {**current["fields"], **updated}, body.listing.note, "saved")
            return product

    @app.get("/api/products/{pid}")
    def detail(pid: str):
        with store.connect() as conn:
            data = store.detail(conn, pid)
            suggestions = read_suggestions(conn, data["snapshot"], data["product"])
            if audit := data["latest_audit"]:
                context = store.context(conn, pid, audit["context_id"])["data"]
                data["latest_audit"] = with_issue_edits(audit, data["snapshot"]["fields"], context)
        return {**data, "current_rules": rules_for(data["product"]), "field_suggestions": suggestions}

    def read_suggestions(conn, snap, product):
        row = conn.execute("SELECT data FROM field_suggestion_sets WHERE snapshot_id=? AND version=?",
                           (snap["id"], SUGGESTION_VERSION)).fetchone()
        reviews = dict(conn.execute("SELECT item_id,decision FROM field_suggestion_reviews WHERE snapshot_id=?", (snap["id"],)))
        return suggested_fields(snap["fields"], product, saved=unpack(row, ("data",))["data"] if row else None, decisions=reviews)

    def generate_suggestions(fields, product):
        records = public_records(fields)
        if not records or not eligible(fields):
            raise HTTPException(422, "请先补充前台原文，并检查是否有需要推演的字段。")
        client = LLM.configured()
        if not client:
            raise HTTPException(409, "开启 AI 扩展可配置本机 Ollama 或模型 API。本地建议可以直接查看。")
        try:
            items, model = model_items(fields, product, records, client)
        except ModelFailed as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"items": items, "model": model, "prompt_version": SUGGESTION_VERSION}

    @app.get("/api/products/{pid}/snapshots/{sid}/field-suggestions")
    def snapshot_suggestions(pid: str, sid: str):
        with store.connect() as conn:
            return read_suggestions(conn, store.snapshot(conn, pid, sid), store.product(conn, pid))

    @app.post("/api/products/{pid}/snapshots/{sid}/field-suggestions")
    def expand_suggestions(pid: str, sid: str):
        with store.connect() as conn:
            snap, product = store.snapshot(conn, pid, sid), store.product(conn, pid)
            existing = read_suggestions(conn, snap, product)
        if existing["model"]:
            return existing
        saved = generate_suggestions(snap["fields"], product)
        with store.connect(write=True) as conn:
            store.snapshot(conn, pid, sid)
            conn.execute("INSERT INTO field_suggestion_sets VALUES (?,?,?) ON CONFLICT(snapshot_id) DO UPDATE "
                         "SET version=excluded.version,data=excluded.data WHERE version<>excluded.version",
                         (sid, SUGGESTION_VERSION, encode(saved)))
            return read_suggestions(conn, snap, product)

    @app.put("/api/products/{pid}/snapshots/{sid}/field-suggestions/{item_id}")
    def review_suggestion(pid: str, sid: str, item_id: str, decision: ReviewDecision):
        with store.connect(write=True) as conn:
            snap, product = store.snapshot(conn, pid, sid), store.product(conn, pid)
            proposals = read_suggestions(conn, snap, product)
            if not any(item["id"] == item_id for item in proposals["items"]):
                raise NotFound("当前快照中没有这条候选建议。")
            conn.execute("INSERT INTO field_suggestion_reviews VALUES (?,?,?) ON CONFLICT(snapshot_id,item_id) "
                         "DO UPDATE SET decision=excluded.decision", (sid, item_id, decision))
            return {"id": item_id, "decision": decision}

    @app.post("/api/quick-field-suggestions")
    def quick_suggestions(body: QuickAuditInput):
        fields = ListingInput(fields=body.fields or quick_fields(body.text)).normalized()
        market = product_target(body.asin, body.market)[1] if body.asin else body.market
        product = {"brand": body.brand, "market": market}
        return suggested_fields(fields, product, saved=generate_suggestions(fields, product))

    @app.put("/api/products/{pid}")
    def update_product(pid: str, body: ProductInput):
        with store.connect(write=True) as conn:
            return store.update_product(conn, pid, body.model_dump())

    @app.delete("/api/products/{pid}", status_code=204)
    def delete_product(pid: str):
        with store.connect(write=True) as conn:
            store.delete_product(conn, pid)
        return Response(status_code=204)

    @app.get("/api/products/{pid}/snapshots/{sid}")
    def snapshot(pid: str, sid: str):
        with store.connect() as conn:
            return store.snapshot(conn, pid, sid)

    @app.post("/api/products/{pid}/snapshots")
    def save_snapshot(pid: str, body: SnapshotInput):
        with store.connect(write=True) as conn:
            product = store.product(conn, pid)
            if body.base_snapshot_id and store.snapshot(conn, pid)["id"] != body.base_snapshot_id:
                raise Conflict("原文已经有更新。请重新打开编辑窗口并核对最新原文后再保存。")
            if body.asin:
                asin, market = product_target(body.asin, body.market or product["market"])
                store.register_asin(conn, product, asin, market)
            return store.new_snapshot(conn, pid, body.normalized(), body.note, "saved")

    @app.post("/api/products/{pid}/snapshots/{sid}/restore")
    def restore(pid: str, sid: str):
        with store.connect(write=True) as conn:
            source = store.snapshot(conn, pid, sid)
            return store.new_snapshot(conn, pid, source["fields"], f"恢复自原文 V{source['version']}", "restored")

    @app.put("/api/products/{pid}/context")
    def save_context(pid: str, body: ContextInput):
        with store.connect(write=True) as conn:
            store.product(conn, pid)
            return store.new_context(conn, pid, body.model_dump(by_alias=True))

    @app.get("/api/products/{pid}/imports")
    def imports(pid: str):
        with store.connect() as conn:
            store.product(conn, pid)
            return [dict(row) for row in conn.execute(
                "SELECT id,filename,created_at FROM imports WHERE product_id=? ORDER BY rowid DESC", (pid,)
            )]

    def read_upload(file, asin, market, image_field):
        prefix = file.file.read(16384)
        name = Path((file.filename or "upload").replace("\\", "/"))
        webpage = webpage_format(prefix, name.suffix.lower())
        limit = MAX_WEBPAGE_UPLOAD if webpage else MAX_UPLOAD
        content = prefix + file.file.read(limit - len(prefix) + 1)
        if len(content) > limit:
            raise HTTPException(413, f"文件超过 {limit // (1024 * 1024)} MB，请拆分资料或裁剪图片。")
        if not content:
            raise HTTPException(422, "上传的文件是空的。")
        suffix = name.suffix
        filename = (name.stem[:200 - len(suffix)] + suffix if len(suffix) < 200 else name.name[:200]) or "upload"
        try:
            imported = parse_import(content, filename, asin, os.getenv("LISTING_EDITOR_OCR", "tesseract"), market, image_field=image_field)
        except (BadZipFile, UnidentifiedImageError, InvalidFileException, OSError, IndexError, RecursionError) as exc:
            raise HTTPException(422, "无法读取资料。请核对文件格式和内容后重试。") from exc
        return filename, imported

    @app.post("/api/listing-import")
    def import_preview(asin: str = "", market: str = DEFAULT_MARKET, file: UploadFile = File(...), image_field: ImageField = "auto"):
        target, selected_market = product_target(asin, market) if asin else ("", market_code(market))
        filename, imported = read_upload(file, target, selected_market, image_field)
        imported.pop("image")
        if not imported["fields"]:
            raise HTTPException(422, "文件未识别出商品文案。可在项目的编辑 / 导入中保存截图并手动转录，或粘贴原文。")
        normalized = ListingInput(fields=imported["fields"]).normalized()
        fields = {key: normalized[key] for key in imported["fields"]}
        target = imported["asin"] or target
        selected_market = imported["market"]
        brand = imported.get("brand", "")
        project = None
        if target:
            with store.connect(write=True) as conn:
                project = store.save_intake(conn, target, selected_market, fields, "文件导入 · 待核实", brand=brand)
                if imported.get("origin"):
                    store.save_amazon_mirror(conn, project, imported, imported["origin"])
        return {"label": filename, "asin": target, "market": selected_market, "brand": brand, "url": imported.get("url"), "fields": fields,
                "product_id": project["id"] if project else None, "message": imported["message"]}

    @app.post("/api/amazon/collect")
    def amazon_preview(body: AmazonCollectInput):
        try:
            return collect_amazon(body.asin, market=body.market or DEFAULT_MARKET)
        except CollectionError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @app.get("/api/amazon/mirror")
    def amazon_mirror(asin: str, market: str = DEFAULT_MARKET):
        target, selected_market = product_target(asin, market)
        with store.connect() as conn:
            return store.amazon_mirror(conn, selected_market, target)

    @app.get("/api/amazon/workflows")
    def capture_workflows():
        with store.connect() as conn:
            return store.recent_captures(conn)

    @app.post("/api/amazon/workflows", status_code=202)
    def create_capture(body: CaptureInput, request: Request):
        with store.connect(write=True) as conn:
            product = store.product(conn, body.product_id) if body.product_id else None
            asin, market = product_target(body.asin, body.market or (product["market"] if product else DEFAULT_MARKET))
            if product:
                store.check_product_identity(conn, product, market, asin)
            provider = body.provider
            if provider == "auto":
                archive = store.amazon_mirror(conn, market, asin, required=False)
                if archive:
                    return store.enqueue_capture(conn, asin, market, product, "archive", archive=archive)
                if nexscope_enabled():
                    provider = "nexscope"
                elif market == "US":
                    provider = "public_archive"
                elif amazon_enabled():
                    provider = "playwright"
                else:
                    raise HTTPException(409, f"Amazon {market} {asin} 暂无本地资料，在线采集也尚未启用。"
                                             "请上传已保存的商品详情页或 Listing 文件。")
            if provider == "public_archive" and market != "US":
                raise HTTPException(422, "公开归档仅适用于 Amazon 美国站。")
            if provider == "nexscope" and not nexscope_enabled():
                raise HTTPException(409, "请先在服务端配置 Nexscope API Key，再使用商品接口。")
            if provider == "playwright" and not amazon_enabled():
                raise HTTPException(409, "Playwright 采集尚未启用。请用 start-with-playwright.cmd 启动，或上传商品资料。")
            job = store.enqueue_capture(conn, asin, market, product, provider)
        request.app.state.collector.wake.set()
        return job

    @app.get("/api/amazon/workflows/{identifier}")
    def capture_detail(identifier: str):
        with store.connect() as conn:
            task = store.capture(conn, identifier)
            if task["preview"]:
                task["preview"].setdefault("market", task["market"])
            linked_id = (task["preview"] or {}).get("product_id")
            if linked_id and not conn.execute("SELECT 1 FROM products WHERE id=?", (linked_id,)).fetchone():
                task["preview"]["product_id"] = None
            return task

    @app.delete("/api/amazon/workflows/{identifier}", status_code=204)
    def delete_capture(identifier: str):
        with store.connect(write=True) as conn:
            store.delete_capture(conn, identifier)
        return Response(status_code=204)

    @app.post("/api/products/{pid}/imports")
    def upload(pid: str, asin: str = "", market: str = "", file: UploadFile = File(...), image_field: ImageField = "auto"):
        with store.connect() as conn:
            product = store.product(conn, pid)
            selected_market = market_code(market) if market else product["market"]
            target, selected_market = product_target(asin or product["asin"], selected_market) if asin or product["asin"] else ("", selected_market)
            if target:
                store.check_product_identity(conn, product, selected_market, target)
        filename, imported = read_upload(file, target, selected_market, image_field)
        image = imported.pop("image")
        identifier = None
        target = imported["asin"] or target
        selected_market = imported["market"]
        if image or target and imported["fields"]:
            with store.connect(write=True) as conn:
                if target and imported["fields"]:
                    project = store.register_asin(conn, store.product(conn, pid), target, selected_market)
                    if imported.get("origin"):
                        store.save_amazon_mirror(conn, project, imported, imported["origin"])
                if image:
                    identifier = uuid4().hex
                    store.save_image(conn, pid, identifier, filename, "image/png", image)
        if identifier:
            for field in imported["fields"].values():
                field["source"] += f" · 截图 {identifier[:8]}"
        return {**imported, "asin": target if imported["fields"] else product["asin"],
                "market": selected_market, "filename": filename, "image_id": identifier}

    @app.get("/api/products/{pid}/imports/{iid}")
    def get_image(pid: str, iid: str):
        with store.connect() as conn:
            row = conn.execute("SELECT media_type,data FROM imports WHERE product_id=? AND id=?", (pid, iid)).fetchone()
        if not row:
            raise NotFound("截图不存在。")
        return Response(content=row["data"], media_type=row["media_type"], headers={"X-Content-Type-Options": "nosniff"})

    def queued(conn, pid, kind, key, payload):
        job = store.enqueue(conn, pid, kind, key, payload)
        worker.wake.set()
        return {"job": store.job_view(job), "cached": None}

    @app.post("/api/products/{pid}/audit")
    def start_audit(pid: str, body: AuditInput):
        client = LLM.configured() if body.mode == "llm" else None
        if body.mode == "llm" and not client:
            raise HTTPException(409, "服务器端尚未配置模型，可先使用本地规则。")
        with store.connect(write=True) as conn:
            product = store.product(conn, pid)
            snap = store.snapshot(conn, pid, body.snapshot_id)
            context = store.context(conn, pid)
            return submit_audit(conn, product, snap, context, body.mode, client)

    def audit_payload(product, snap, context, mode, client):
        return {"product": product, "snapshot_id": snap["id"], "context_id": context["id"],
                "rules": rules_for(product), "mode": mode, "llm": client.stamp() if client else None,
                "engine_version": ENGINE_VERSION}

    def submit_audit(conn, product, snap, context, mode, client):
        if all(row["state"] == "missing" for row in snap["fields"].values()):
            raise HTTPException(422, "请至少录入一项 Listing 资料再评分。")
        payload = audit_payload(product, snap, context, mode, client)
        return resolve_audit(conn, product["id"], scoring_key(payload), payload)

    def resolve_audit(conn, pid, key, payload):
        cached = store.cached_audit(conn, key)
        if cached:
            return {"job": None, "cached": {"kind": "audit", "id": cached["id"]}}
        return queued(conn, pid, "audit", key, payload)

    @app.post("/api/quick-audit")
    def quick_audit(body: QuickAuditInput):
        client = LLM.configured() if body.mode == "llm" else None
        if body.mode == "llm" and not client:
            raise HTTPException(409, "服务器端尚未配置模型，可先使用本地规则。")
        values = quick_fields(body.text) if body.text else body.fields
        listing = ListingInput(fields=values).normalized()
        names = [listing[key]["text"].splitlines()[0] for key in PUBLISHED_COPY if listing[key]["text"]]
        if not any(listing[key]["text"] for key in SCORE_MODULES):
            raise HTTPException(422, "请至少提供一个模块的原文。")
        identity = ProductInput.model_validate({
            "asin": body.asin, "market": body.market, "name": names[0][:140] if names else "临时商品",
            "brand": body.brand, "category": body.category,
            "product_type": body.product_type,
        }).model_dump()
        stamp = utcnow()
        product = {**identity, "id": uuid4().hex, "demo": False, "created_at": stamp}
        snap = {"id": uuid4().hex, "version": 1, "cause": "quick", "note": body.note, "fields": listing, "created_at": stamp}
        context = {"id": uuid4().hex, "version": 1, "data": {"facts": [], "questions": template_questions(product)}, "created_at": stamp}
        payload = audit_payload(product, snap, context, body.mode, client)
        try:
            data = audit_data(payload, listing, context["data"])
        except ModelFailed as exc:
            raise HTTPException(502, str(exc)) from exc
        audit = {"id": uuid4().hex, "snapshot_id": snap["id"], "context_id": context["id"], "kind": "audit",
                 "created_at": stamp, "data": data}
        return {"product": product, "snapshot": snap, "context": context, "latest_audit": with_issue_edits(audit, listing, context["data"]),
                "current_rules": payload["rules"], "field_suggestions": suggested_fields(listing, product)}

    @app.post("/api/products/{pid}/question-template")
    def question_template(pid: str):
        with store.connect() as conn:
            return template_questions(store.product(conn, pid))

    @app.get("/api/products/{pid}/audits/{aid}")
    def get_audit(pid: str, aid: str):
        with store.connect() as conn:
            audit = store.audit(conn, pid, aid)
            if candidate_id := audit["data"].get("candidate_id"):
                fields = store.candidate(conn, pid, candidate_id)["data"]["validation"]["fields"]
            else:
                fields = store.snapshot(conn, pid, audit["snapshot_id"])["fields"]
            context = store.context(conn, pid, audit["context_id"])["data"]
        return with_issue_edits(audit, fields, context)

    @app.post("/api/quick-field-rewrite")
    def quick_field_rewrite(body: FieldRewriteInput):
        if not body.text:
            raise HTTPException(422, "请先粘贴 Listing 原文。")
        product = ProductInput(name="临时改写", market=body.market).model_dump()
        client = LLM.configured() if body.mode == "auto" else None
        return preview_rewrite(body, ListingInput().normalized(), product, {"facts": [], "questions": []},
                               rules_for(product), client=client)

    def rewrite_basis(conn, pid, body):
        product = store.product(conn, pid)
        snapshot = store.snapshot(conn, pid)
        context = store.context(conn, pid)
        if ((body.snapshot_id and body.snapshot_id != snapshot["id"])
                or (body.context_id and body.context_id != context["id"])):
            raise Conflict("项目的原文或事实已更新。请刷新项目后重新改写。")
        return product, snapshot, context

    @app.post("/api/products/{pid}/field-rewrite")
    def rewrite_field(pid: str, body: FieldRewriteInput):
        with store.connect() as conn:
            conn.execute("BEGIN")
            product, snapshot, context = rewrite_basis(conn, pid, body)
            audit = store.audit(conn, pid, body.audit_id) if body.issue_key and body.audit_id else None
        fields = snapshot["fields"]
        basis = context["data"]
        rules = rules_for(product)
        issue = None
        reviewed = None
        if audit:
            if (audit["kind"] != "audit" or audit["snapshot_id"] != snapshot["id"] or audit["context_id"] != context["id"]
                    or audit["data"]["product"] != product):
                raise Conflict("这条修改意见对应历史版本。请使用当前资料评分后再打开。")
            rules, result = audit["data"]["rules"], audit["data"]["result"]
            finding = next((item for item in result["issues"] if item["key"] == body.issue_key), None)
            if finding is None:
                raise NotFound("找不到这条修改意见。")
            issue = issue_edit(finding, fields, basis, result, rules)
            if not issue["rewrite"]:
                raise HTTPException(422, "该建议需要核对资料后才能生成文案。")
            saved = finding.get("edit", {})
            if (saved.get("model_generated") and body.mode == "auto"
                    and body.focus_question in ("", issue["question"])):
                reviewed = saved
            suggestions = []
        else:
            result = score_listing(product, fields, basis, rules)
            suggestions = [issue_edit(item, fields, basis, result, rules) for item in result["issues"]]
        client = LLM.configured() if body.mode == "auto" and reviewed is None else None
        preview = preview_rewrite(body, fields, product, basis, rules, issue=issue, suggestions=suggestions, client=client)
        if reviewed:
            preview["drafts"][0].update(
                after=reviewed["after"], reason=reviewed["action"], answers=reviewed["answers"],
                status="draft", model_generated=True, fact_labels=reviewed["fact_labels"],
                warnings=[warning for warning in preview["drafts"][0]["warnings"] if warning.startswith("原文来源")],
            )
            preview["model"] = reviewed["model"]
        return {**preview, "snapshot_id": snapshot["id"], "context_id": context["id"]}

    @app.post("/api/products/{pid}/field-rewrite/candidate", status_code=201)
    def save_field_rewrite(pid: str, body: FieldDraftInput):
        with store.connect(write=True) as conn:
            product, snapshot, context = rewrite_basis(conn, pid, body)
            fields = snapshot["fields"]
            edits = [item.model_dump() for item in body.edits]
            review = staged_review(fields, edits)
            payload = audit_payload(product, snapshot, context, "rules", None)
            audit_key = scoring_key(payload)
            audit = store.cached_audit(conn, audit_key)
            if not audit:
                baseline = audit_data(payload, fields, context["data"])
                audit = store.new_audit(conn, pid, snapshot["id"], context["id"], "audit", audit_key, baseline)
            rules, result = audit["data"]["rules"], audit["data"]["result"]
            validation = inspect_candidate(review, fields, context["data"]["facts"], result["issues"], rules)
            generation = fingerprint({"skill": SKILL["version"], "audit": audit["id"], "strategy": body.strategy, "review": review})
            for row in conn.execute("SELECT id,data FROM rewrite_versions WHERE audit_id=? AND parent_id IS NULL", (audit["id"],)):
                if unpack(row, ("data",))["data"].get("field_rewrite_key") == generation:
                    return store.candidate(conn, pid, row["id"])
            forecast = preview_candidate(fields, validation["fields"], product, context["data"], rules)
            data = {"validation": validation, "mode": "rules", "model": None, "note": "", "forecast": forecast,
                    "skill": SKILL, "field_rewrite_key": generation}
            return store.new_candidate(conn, pid, audit["id"], body.strategy, data)

    @app.post("/api/products/{pid}/rewrite")
    def rewrite(pid: str, body: RewriteInput):
        with store.connect(write=True) as conn:
            audit = store.audit(conn, pid, body.audit_id)
            if audit["kind"] != "audit":
                raise HTTPException(422, "请选择一条基准评分生成候选稿。")
            ensure_engine(audit)
            context = store.context(conn, pid, audit["context_id"])
            if not any(row["state"] == "confirmed" for row in context["data"]["facts"]):
                raise HTTPException(422, "这次评分没有已核实的产品事实。请先确认事实，再运行一次评分。")
            params = body.model_dump()
            return queued(conn, pid, "rewrite", fingerprint({"rewrite": params}), params)

    @app.get("/api/products/{pid}/candidates/{cid}")
    def get_candidate(pid: str, cid: str):
        with store.connect() as conn:
            return store.candidate(conn, pid, cid)

    @app.post("/api/products/{pid}/candidates/{cid}/review")
    def review(pid: str, cid: str, body: ReviewInput):
        with store.connect(write=True) as conn:
            existing = store.candidate(conn, pid, cid)
            audit = store.audit(conn, pid, existing["audit_id"])
            ensure_engine(audit)
            baseline = audit["data"]
            context = store.context(conn, pid, audit["context_id"])["data"]
            fields = store.snapshot(conn, pid, audit["snapshot_id"])["fields"]
            items = [line.model_dump() for line in body.lines]
            before = existing["data"]["validation"]["rows"]
            if {row["key"]: row for row in items} == {
                row["key"]: {name: row[name] for name in ("key", "text", "decision")} for row in before
            } and body.note == existing["data"]["note"]:
                return existing
            validation = inspect_candidate(items, fields, context["facts"], baseline["result"]["issues"], baseline["rules"])
            data = {**existing["data"], "validation": validation, "note": body.note,
                    "forecast": preview_candidate(fields, validation["fields"], baseline["product"], context,
                                                  baseline["rules"], baseline["engine_version"])}
            return store.new_candidate(conn, pid, audit["id"], existing["strategy"], data, existing["id"])

    @app.post("/api/products/{pid}/candidates/{cid}/rescore")
    def rescore(pid: str, cid: str):
        with store.connect(write=True) as conn:
            candidate = store.candidate(conn, pid, cid)
            audit = store.audit(conn, pid, candidate["audit_id"])
            ensure_engine(audit)
            key = fingerprint({"rescore": cid, "baseline": audit["id"]})
            cached = store.cached_audit(conn, key)
            if cached:
                return {"job": None, "cached": {"id": cached["id"], "kind": "rescore"}}
            return queued(conn, pid, "rescore", key, {"candidate_id": cid})

    @app.post("/api/products/{pid}/candidates/{cid}/publication")
    def publish(pid: str, cid: str, body: PublicationInput):
        with store.connect(write=True) as conn:
            candidate = store.candidate(conn, pid, cid)
            if not candidate["data"]["validation"]["ready"]:
                raise HTTPException(409, "请先完成审核、事实和适用规则检查。")
            if not store.product(conn, pid)["asin"]:
                raise HTTPException(409, "记录实际上线前请先在商品资料登记 ASIN。")
            return store.publish(conn, pid, cid, body.published_at.astimezone(timezone.utc).isoformat(), body.note)

    @app.post("/api/products/{pid}/suggest-questions")
    def questions(pid: str):
        client = LLM.configured()
        if not client:
            raise HTTPException(409, "服务器端尚未配置模型。当前项目已有可编辑的类目问句模板。")
        with store.connect(write=True) as conn:
            product = store.product(conn, pid)
            snap = store.snapshot(conn, pid)
            context = store.context(conn, pid)
            payload = {"product": product, "snapshot_id": snap["id"], "context_id": context["id"], "llm": client.stamp()}
            return queued(conn, pid, "questions", fingerprint({"questions": payload}), payload)

    @app.get("/api/jobs/{jid}")
    def get_job(jid: str):
        with store.connect() as conn:
            return store.job_view(store.job(conn, jid))

    @app.post("/api/jobs/{jid}/cancel")
    def cancel(jid: str):
        with store.connect(write=True) as conn:
            job = store.job(conn, jid)
            if job["status"] in {"queued", "running"}:
                store.set_job(conn, jid, "cancelled", job["progress"], "已取消")
            return store.job_view(store.job(conn, jid))

    @app.get("/api/products/{pid}/alexa-skill-input")
    def export_alexa_skill_input(pid: str):
        with store.connect() as conn:
            conn.execute("BEGIN")
            detail = store.detail(conn, pid)
        product, snapshot = detail["product"], detail["snapshot"]
        if not any(snapshot["fields"][key]["text"] for key in PUBLISHED_COPY):
            raise HTTPException(422, "请先保存 Listing 原文，再导出 Alexa Skill 输入。")
        filename = f"{product['asin'] or 'listing'}-{product['market']}-alexa-skill.md"
        content = alexa_skill_input(product, snapshot, detail["context"], rules_for(product), detail["latest_audit"])
        return Response(content, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/api/products/{pid}/export")
    def export(pid: str, audit_id: str, format: Literal["md", "xlsx", "pdf"] = "md", candidate_id: str | None = None):
        with store.connect() as conn:
            audit = store.audit(conn, pid, audit_id)
            candidate_id = candidate_id or audit["data"].get("candidate_id")
            candidate = store.candidate(conn, pid, candidate_id) if candidate_id else None
            if candidate and candidate["audit_id"] != audit["data"].get("baseline_id", audit["id"]):
                raise HTTPException(409, "该候选稿与评分使用不同的基准。")
            product = audit["data"]["product"]
            snap = store.snapshot(conn, pid, audit["snapshot_id"])
            context = store.context(conn, pid, audit["context_id"])
        generator, mime = EXPORTS[format]
        assessed = candidate["data"]["validation"]["fields"] if audit["kind"] == "rescore" and candidate else snap["fields"]
        audit = with_issue_edits(audit, assessed, context["data"])
        sections = report_sections(product, snap, audit, context, candidate)
        filename = f"{product['asin'] or 'listing-draft'}-listing.{format}"
        return Response(generator(f"{product['name']} · Listing 诊断", sections), media_type=mime,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    assets = Path(web_path or ROOT / "web")
    if assets.is_dir():
        app.mount("/", StaticFiles(directory=assets, html=True), name="workbench")
    else:
        @app.get("/")
        def missing_ui():
            return {"detail": "前端还未构建。请按 README 运行，或打开 /docs 试用 API。"}

    return app


app = create_app()
