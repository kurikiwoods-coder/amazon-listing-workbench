from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .marketplaces import product_url
from .models import FIELDS, PUBLISHED_COPY, ListingInput, ProductInput, SourceField, utcnow
from .questions import template_questions

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_FIELDS = (*PUBLISHED_COPY, "price", "reviews")
SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY, market TEXT NOT NULL, asin TEXT NOT NULL,
    data TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(market, asin)
);
CREATE TABLE IF NOT EXISTS workbench_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS listing_snapshots (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
    version INTEGER NOT NULL, cause TEXT NOT NULL, note TEXT NOT NULL, fields TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(product_id, version)
);
CREATE TABLE IF NOT EXISTS context_versions (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
    version INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(product_id, version)
);
CREATE TABLE IF NOT EXISTS audit_runs (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
    snapshot_id TEXT NOT NULL REFERENCES listing_snapshots(id),
    context_id TEXT NOT NULL REFERENCES context_versions(id),
    kind TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE, data TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rewrite_versions (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
    audit_id TEXT NOT NULL REFERENCES audit_runs(id), parent_id TEXT REFERENCES rewrite_versions(id),
    strategy TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(product_id, version)
);
CREATE TABLE IF NOT EXISTS publication_events (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
    candidate_id TEXT NOT NULL UNIQUE REFERENCES rewrite_versions(id),
    published_at TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quick_requests (
    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, product_id TEXT NOT NULL REFERENCES products(id),
    snapshot_id TEXT NOT NULL REFERENCES listing_snapshots(id),
    context_id TEXT NOT NULL REFERENCES context_versions(id), audit_fingerprint TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id), filename TEXT NOT NULL,
    media_type TEXT NOT NULL, data BLOB NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id), kind TEXT NOT NULL,
    fingerprint TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL, detail TEXT NOT NULL,
    payload TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS capture_workflows (
    id TEXT PRIMARY KEY, product_id TEXT REFERENCES products(id) ON DELETE CASCADE,
    asin TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
    preview TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, market TEXT NOT NULL DEFAULT 'US',
    provider TEXT NOT NULL DEFAULT 'playwright'
);
CREATE TABLE IF NOT EXISTS amazon_mirrors (
    product_id TEXT PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    archived_at TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS field_suggestion_sets (
    snapshot_id TEXT PRIMARY KEY REFERENCES listing_snapshots(id) ON DELETE CASCADE,
    version TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS field_suggestion_reviews (
    snapshot_id TEXT NOT NULL REFERENCES listing_snapshots(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL, decision TEXT NOT NULL, PRIMARY KEY(snapshot_id, item_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_jobs ON jobs(fingerprint)
    WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS idx_audits_product ON audit_runs(product_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_captures_status ON capture_workflows(status, created_at);
"""


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def unpack(row: sqlite3.Row | None, json_columns=()) -> dict:
    if row is None:
        raise NotFound("记录不存在。")
    values = dict(row)
    for key in json_columns:
        values[key] = json.loads(values[key]) if values[key] is not None else None
    return values


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or os.getenv("LISTING_DB") or ROOT / "data" / "workbench.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self, write=False):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
        with self.connect(write=True) as connection:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(capture_workflows)")}
            if "market" not in columns:
                connection.execute("ALTER TABLE capture_workflows ADD COLUMN market TEXT NOT NULL DEFAULT 'US'")
            if "provider" not in columns:
                connection.execute("ALTER TABLE capture_workflows ADD COLUMN provider TEXT NOT NULL DEFAULT 'playwright'")
            connection.execute(
                "UPDATE jobs SET status='failed',detail=?,updated_at=? WHERE status='running'",
                ("上次运行中断，可以重新运行。", utcnow()),
            )
            connection.execute(
                "UPDATE capture_workflows SET status='failed',detail=?,updated_at=? WHERE status='running'",
                ("上次采集中断，可以重新提取。", utcnow()),
            )

    def product(self, connection, product_id) -> dict:
        row = unpack(connection.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone(), ("data",))
        return {"id": row["id"], **row["data"], "created_at": row["created_at"]}

    def find_product(self, connection, market, asin) -> dict | None:
        if not asin:
            return None
        match = connection.execute("SELECT id FROM products WHERE market=? AND asin=?", (market, asin)).fetchone()
        return self.product(connection, match["id"]) if match else None

    def create_product(self, connection, product, demo=False) -> dict:
        existing = self.find_product(connection, product["market"], product["asin"])
        if existing and demo:
            return existing
        if existing:
            raise Conflict("该站点已经有这个 ASIN，请打开已有项目。")
        product_id = uuid4().hex
        stored_identity = product["asin"] or f"draft-{product_id}"
        connection.execute("INSERT INTO products VALUES (?,?,?,?,?)",
                           (product_id, product["market"], stored_identity, encode({**product, "demo": demo}), utcnow()))
        created = self.product(connection, product_id)
        self.new_snapshot(connection, product_id, ListingInput().normalized(), "", "created")
        self.new_context(connection, product_id, {"facts": [], "questions": template_questions(created)})
        return created

    def check_product_identity(self, connection, product, market, asin):
        if product["asin"] and (product["market"] != market or product["asin"] != asin):
            raise Conflict("当前项目已绑定 ASIN 和站点。请在对应站点的项目中操作，或到快速评分另行采集。")
        other = self.find_product(connection, market, asin)
        if other and other["id"] != product["id"]:
            raise Conflict("这个 ASIN 已有项目，请打开已有项目。")

    def update_product(self, connection, product_id, updates) -> dict:
        product = self.product(connection, product_id)
        self.check_product_identity(connection, product, updates["market"], updates["asin"])
        stored_identity = updates["asin"] or f"draft-{product_id}"
        connection.execute("UPDATE products SET market=?,asin=?,data=? WHERE id=?",
                           (updates["market"], stored_identity, encode({**updates, "demo": product["demo"]}), product_id))
        return self.product(connection, product_id)

    def register_asin(self, connection, product, asin, market) -> dict:
        if product["asin"] == asin and product["market"] == market:
            return product
        identity = {key: product[key] for key in ProductInput.model_fields}
        return self.update_product(connection, product["id"], {**identity, "asin": asin, "market": market})

    def save_intake(self, connection, asin, market, fields, source, *, product_id=None, brand="") -> dict:
        if product_id:
            return self.register_asin(connection, self.product(connection, product_id), asin, market)
        existing = self.find_product(connection, market, asin)
        listing = ListingInput(fields=fields).normalized()
        if existing:
            snapshot = self.snapshot(connection, existing["id"])
            merged = dict(snapshot["fields"])
            for key, row in listing.items():
                before = merged[key]
                if before["state"] == "missing" and (row["text"] or row.get("notes") and not before.get("notes")):
                    merged[key] = row
                elif key == "aplus" and before["state"] == "pending" and row.get("page_lines"):
                    # Upgrade an older partial extraction only when the new source retains all its original lines.
                    old = {s.strip() for s in before["text"].splitlines() if s.strip()}
                    new = {s.strip() for s in row["text"].splitlines() if s.strip()}
                    if old < new or old == new and not before.get("page_lines"):
                        merged[key] = row
            if merged != snapshot["fields"]:
                self.new_snapshot(connection, existing["id"], merged, source, "saved")
            return existing
        title = next((listing[key]["text"].splitlines()[0][:140] for key in PUBLISHED_COPY if listing[key]["text"]), asin)
        product = self.create_product(connection, ProductInput(asin=asin, market=market, name=title, brand=brand).model_dump())
        self.new_snapshot(connection, product["id"], listing, source, "saved")
        return product

    def save_amazon_mirror(self, connection, product, preview, origin):
        normalized = ListingInput(fields=preview["fields"]).normalized()
        fields = {key: {**normalized[key], "state": "pending" if normalized[key]["text"] else "missing"}
                  for key in ARCHIVE_FIELDS if normalized[key]["text"] or normalized[key].get("notes")}
        if not any(fields.get(key, {}).get("text") for key in PUBLISHED_COPY):
            return
        if origin == "public_archive":
            prior = connection.execute("SELECT data FROM amazon_mirrors WHERE product_id=?", (product["id"],)).fetchone()
            if prior and json.loads(prior["data"]).get("origin") != "public_archive":
                return
        provenance = {key: preview[key] for key in ("reference_url", "source_observed_at") if key in preview}
        data = {"fields": fields, "brand": preview.get("brand") or product["brand"], "origin": origin, **provenance}
        connection.execute(
            "INSERT INTO amazon_mirrors VALUES (?,?,?) ON CONFLICT(product_id) DO UPDATE SET "
            "archived_at=excluded.archived_at,data=excluded.data",
            (product["id"], utcnow(), encode(data)),
        )

    def amazon_mirror(self, connection, market, asin, *, required=True) -> dict | None:
        missing = f"Amazon {market} {asin} 暂无本地归档。可先导入商品页或商品数据。"
        product = self.find_product(connection, market, asin)
        if not product:
            if required:
                raise NotFound(missing)
            return None
        row = connection.execute("SELECT archived_at,data FROM amazon_mirrors WHERE product_id=?", (product["id"],)).fetchone()
        if row:
            record = unpack(row, ("data",))
            data, archived_at = record["data"], record["archived_at"]
        else:
            legacy = next((task for task in connection.execute(
                "SELECT preview,updated_at,provider FROM capture_workflows WHERE market=? AND asin=? AND status='succeeded' "
                "AND provider IN ('playwright','nexscope','public_archive') AND preview IS NOT NULL ORDER BY rowid DESC", (market, asin)
            ) if json.loads(task["preview"]).get("product_id") == product["id"]), None)
            if legacy:
                data, archived_at = json.loads(legacy["preview"]), legacy["updated_at"]
                data.setdefault("origin", legacy["provider"])
            else:
                data = None
                for snapshot in connection.execute(
                    "SELECT fields,created_at FROM listing_snapshots WHERE product_id=? AND note=? ORDER BY version DESC",
                    (product["id"], "文件导入 · 待核实"),
                ):
                    fields = {key: field for key, field in json.loads(snapshot["fields"]).items()
                              if field["text"] and field["source"].startswith(f"Amazon {market} 保存的网页 · ")}
                    if any(key in fields for key in PUBLISHED_COPY):
                        data = {"fields": fields, "brand": product["brand"], "origin": "saved_page"}
                        archived_at = snapshot["created_at"]
                        break
                if data is None and not product["demo"]:
                    snapshot = self.snapshot(connection, product["id"])
                    fields = {key: snapshot["fields"][key] for key in PUBLISHED_COPY if snapshot["fields"][key]["text"]}
                    if fields:
                        data = {"fields": fields, "brand": product["brand"], "origin": "project_snapshot"}
                        archived_at = snapshot["created_at"]
        if data is None:
            if required:
                raise NotFound(missing)
            return None
        origins = {"playwright": "Playwright 采集", "saved_page": "浏览器网页导入",
                   "nexscope": "Nexscope 商品详情", "oxylabs_csv": "Oxylabs 商品 CSV",
                   "project_snapshot": "ASIN 库已保存的原文", "public_archive": "img.team 公开商品归档"}
        origin = origins.get(data.get("origin", "playwright"), "商品资料")
        provenance = {key: data[key] for key in ("reference_url", "source_observed_at") if key in data}
        return {"asin": asin, "market": market, "url": product_url(asin, market), "product_id": product["id"],
                "label": "本地商品归档", "archived_at": archived_at, "brand": data["brand"], "fields": data["fields"],
                "message": f"来源为 {origin} 的上次归档。请核对版本、价格和变体后使用。", **provenance}

    def delete_product(self, connection, product_id):
        product = self.product(connection, product_id)
        if product["demo"]:
            connection.execute("INSERT OR IGNORE INTO workbench_settings VALUES ('demo_dismissed', '1')")
        if product["asin"]:
            connection.execute(
                "UPDATE capture_workflows SET status='failed',detail=?,updated_at=? "
                "WHERE product_id IS NULL AND market=? AND asin=? AND status IN ('queued','running')",
                ("项目已删除，原采集任务已终止。可重新提交采集。", utcnow(), product["market"], product["asin"]),
            )
        # Delete in foreign key order so existing databases can use the same transaction.
        for table in ("publication_events", "rewrite_versions", "audit_runs", "quick_requests", "jobs", "imports",
                      "context_versions", "listing_snapshots"):
            connection.execute(f"DELETE FROM {table} WHERE product_id=?", (product_id,))
        connection.execute("DELETE FROM products WHERE id=?", (product_id,))

    def new_snapshot(self, connection, product_id, fields, note, cause) -> dict:
        current = self.snapshot(connection, product_id, required=False)
        if current and current["fields"] == fields and current["note"] == note and cause == "saved":
            return current
        version = current["version"] + 1 if current else 1
        identifier = uuid4().hex
        connection.execute("INSERT INTO listing_snapshots VALUES (?,?,?,?,?,?,?)",
                           (identifier, product_id, version, cause, note, encode(fields), utcnow()))
        return self.snapshot(connection, product_id, identifier)

    def snapshot(self, connection, product_id, identifier=None, required=True) -> dict | None:
        query = "SELECT * FROM listing_snapshots WHERE product_id=?"
        row = connection.execute(
            query + (" AND id=?" if identifier else " ORDER BY version DESC LIMIT 1"),
            (product_id, identifier) if identifier else (product_id,),
        ).fetchone()
        if row is None and not required:
            return None
        snapshot = unpack(row, ("fields",))
        for key in FIELDS.keys() - snapshot["fields"].keys():
            snapshot["fields"][key] = SourceField().model_dump(mode="json")
        return snapshot

    def new_context(self, connection, product_id, data) -> dict:
        current = self.context(connection, product_id, required=False)
        if current and current["data"] == data:
            return current
        version = current["version"] + 1 if current else 1
        identifier = uuid4().hex
        connection.execute("INSERT INTO context_versions VALUES (?,?,?,?,?)",
                           (identifier, product_id, version, encode(data), utcnow()))
        return self.context(connection, product_id, identifier)

    def context(self, connection, product_id, identifier=None, required=True) -> dict | None:
        query = "SELECT * FROM context_versions WHERE product_id=?"
        row = connection.execute(
            query + (" AND id=?" if identifier else " ORDER BY version DESC LIMIT 1"),
            (product_id, identifier) if identifier else (product_id,),
        ).fetchone()
        return unpack(row, ("data",)) if row is not None or required else None

    def new_audit(self, connection, product_id, snapshot_id, context_id, kind, fingerprint, data) -> dict:
        existing = self.cached_audit(connection, fingerprint)
        if existing:
            return existing
        identifier = uuid4().hex
        connection.execute("INSERT INTO audit_runs VALUES (?,?,?,?,?,?,?,?)",
                           (identifier, product_id, snapshot_id, context_id, kind, fingerprint, encode(data), utcnow()))
        return self.audit(connection, product_id, identifier)

    def audit(self, connection, product_id, identifier) -> dict:
        return unpack(connection.execute(
            "SELECT * FROM audit_runs WHERE product_id=? AND id=?", (product_id, identifier)
        ).fetchone(), ("data",))

    def cached_audit(self, connection, fingerprint) -> dict | None:
        row = connection.execute("SELECT * FROM audit_runs WHERE fingerprint=?", (fingerprint,)).fetchone()
        return unpack(row, ("data",)) if row else None

    def new_candidate(self, connection, product_id, audit_id, strategy, data, parent_id=None) -> dict:
        version = connection.execute("SELECT COALESCE(MAX(version),0)+1 FROM rewrite_versions WHERE product_id=?",
                                     (product_id,)).fetchone()[0]
        identifier = uuid4().hex
        connection.execute("INSERT INTO rewrite_versions VALUES (?,?,?,?,?,?,?,?)",
                           (identifier, product_id, audit_id, parent_id, strategy, version, encode(data), utcnow()))
        return self.candidate(connection, product_id, identifier)

    def candidate(self, connection, product_id, identifier) -> dict:
        return unpack(connection.execute(
            "SELECT * FROM rewrite_versions WHERE product_id=? AND id=?", (product_id, identifier)
        ).fetchone(), ("data",))

    def publish(self, connection, product_id, candidate_id, published_at, note) -> dict:
        existing = connection.execute("SELECT * FROM publication_events WHERE candidate_id=?", (candidate_id,)).fetchone()
        if existing:
            if existing["published_at"] != published_at or existing["note"] != note:
                raise Conflict("此文案版本已有上线记录。")
            return dict(existing)
        row = {"id": uuid4().hex, "product_id": product_id, "candidate_id": candidate_id,
               "published_at": published_at, "note": note, "created_at": utcnow()}
        connection.execute("INSERT INTO publication_events VALUES (?,?,?,?,?,?)", tuple(row.values()))
        return row

    def save_image(self, connection, product_id, import_id, filename, media_type, data):
        self.product(connection, product_id)
        connection.execute("INSERT INTO imports VALUES (?,?,?,?,?,?)",
                           (import_id, product_id, filename, media_type, data, utcnow()))

    def enqueue(self, connection, product_id, kind, fingerprint, payload) -> dict:
        current = connection.execute(
            "SELECT * FROM jobs WHERE fingerprint=? AND status IN ('queued','running')", (fingerprint,)
        ).fetchone()
        if current:
            return unpack(current, ("payload", "result"))
        job_id, stamp = uuid4().hex, utcnow()
        connection.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (job_id, product_id, kind, fingerprint, "queued", 0, "已加入队列",
                            encode(payload), None, stamp, stamp))
        return self.job(connection, job_id)

    def job(self, connection, job_id, required=True) -> dict | None:
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return unpack(row, ("payload", "result")) if row is not None or required else None

    def jobs(self, connection, product_id) -> list[dict]:
        return [self.job_view(unpack(row, ("result",))) for row in connection.execute(
            "SELECT * FROM jobs WHERE product_id=? ORDER BY rowid DESC LIMIT 15", (product_id,)
        )]

    def claim(self, connection) -> dict | None:
        row = connection.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
        if not row:
            return None
        self.set_job(connection, row["id"], "running", 12, "正在核对数据")
        return self.job(connection, row["id"])

    def set_job(self, connection, job_id, status, progress, detail, result=None):
        connection.execute("UPDATE jobs SET status=?,progress=?,detail=?,result=?,updated_at=? WHERE id=?",
                           (status, progress, detail, None if result is None else encode(result), utcnow(), job_id))

    @staticmethod
    def job_view(job: dict) -> dict:
        return {key: value for key, value in job.items() if key not in {"payload", "fingerprint"}}

    def enqueue_capture(self, connection, asin, market, product, provider="playwright", *, archive=None) -> dict:
        pid = product["id"] if product else (archive["product_id"] if archive else None)
        current = connection.execute(
            "SELECT id FROM capture_workflows WHERE asin=? AND market=? AND provider=? AND product_id IS ? "
            "AND (status IN ('queued','running') OR (status='succeeded' AND preview=?)) ORDER BY rowid DESC LIMIT 1",
            (asin, market, provider, pid, encode(archive) if archive else None),
        ).fetchone()
        if current:
            return self.capture(connection, current["id"])
        identifier, stamp = uuid4().hex, utcnow()
        archived_title = (archive or {}).get("fields", {}).get("title", {}).get("text", "")[:140]
        title = archived_title or (product["name"] if product else asin)
        status, detail = ("succeeded", "已读取本地商品归档") if archive else ("queued", "等待采集")
        connection.execute("INSERT INTO capture_workflows (id,product_id,asin,title,status,detail,preview,created_at,updated_at,market,provider) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (identifier, pid, asin, title, status, detail, encode(archive) if archive else None,
                            stamp, stamp, market, provider))
        return self.capture(connection, identifier)

    def recent_captures(self, connection) -> list[dict]:
        return [{**dict(row), "has_preview": bool(row["has_preview"])} for row in connection.execute(
            "SELECT id,product_id,asin,title,status,detail,created_at,updated_at,market,provider,"
            "preview IS NOT NULL AS has_preview FROM capture_workflows ORDER BY rowid DESC"
        )]

    def capture(self, connection, identifier) -> dict:
        task = unpack(connection.execute("SELECT * FROM capture_workflows WHERE id=?", (identifier,)).fetchone(), ("preview",))
        return {**task, "has_preview": bool(task["preview"])}

    def claim_capture(self, connection) -> dict | None:
        row = connection.execute("SELECT id FROM capture_workflows WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
        if not row:
            return None
        connection.execute(
            "UPDATE capture_workflows SET status='running',detail=CASE WHEN provider='public_archive' "
            "THEN '正在查找公开归档' ELSE '正在提取商品页' END,updated_at=? WHERE id=?",
            (utcnow(), row["id"]),
        )
        return self.capture(connection, row["id"])

    def finish_capture(self, connection, identifier, status, detail, preview=None):
        title = (preview or {}).get("fields", {}).get("title", {}).get("text", "")[:140]
        archive_owner = preview["product_id"] if preview and preview.get("archived_at") else None
        connection.execute(
            "UPDATE capture_workflows SET status=?,detail=?,preview=?,updated_at=?,title=COALESCE(NULLIF(?,''),title),"
            "product_id=COALESCE(product_id,?) "
            "WHERE id=? AND status='running'",
            (status, detail, encode(preview) if preview else None, utcnow(), title, archive_owner, identifier),
        )

    def delete_capture(self, connection, identifier):
        connection.execute("DELETE FROM capture_workflows WHERE id=?", (identifier,))

    def detail(self, connection, product_id) -> dict:
        product = self.product(connection, product_id)
        snapshots = [dict(row) for row in connection.execute(
            "SELECT id, version, cause, note, created_at FROM listing_snapshots WHERE product_id=? ORDER BY version DESC",
            (product_id,)
        )]
        audits = [unpack(row, ("data",)) for row in connection.execute(
            "SELECT * FROM audit_runs WHERE product_id=? ORDER BY rowid DESC", (product_id,)
        )]
        audit_summaries = [{
            "id": row["id"], "kind": row["kind"], "snapshot_id": row["snapshot_id"], "context_id": row["context_id"],
            "created_at": row["created_at"], "candidate_id": row["data"].get("candidate_id"),
            "score": row["data"]["result"]["score"], "coverage": row["data"]["result"]["coverage"],
            "screen_score": (row["data"]["result"].get("screen") or {}).get("score")
                if "module_scores" not in row["data"]["result"] else None,
            "by_module": "module_scores" in row["data"]["result"],
            "provisional": row["data"]["result"]["provisional"], "mode": row["data"]["mode"],
        } for row in audits]
        candidates = [unpack(row, ("data",)) for row in connection.execute(
            "SELECT * FROM rewrite_versions WHERE product_id=? ORDER BY version DESC", (product_id,)
        )]
        candidate_summaries = [{
            "id": row["id"], "version": row["version"], "strategy": row["strategy"], "audit_id": row["audit_id"],
            "created_at": row["created_at"], "parent_id": row["parent_id"], "ready": row["data"]["validation"]["ready"],
            "pending": sum(item["decision"] == "pending" for item in row["data"]["validation"]["rows"]),
        } for row in candidates]
        publications = [dict(row) for row in connection.execute(
            "SELECT * FROM publication_events WHERE product_id=? ORDER BY published_at DESC", (product_id,)
        )]
        active = self.snapshot(connection, product_id)
        context = self.context(connection, product_id)
        active_audits = [row for row in audits if row["kind"] == "audit" and row["snapshot_id"] == active["id"]]
        latest = next((row for row in active_audits if row["data"]["product"] == product
                       and row["context_id"] == context["id"]), active_audits[0] if active_audits else None)
        return {"product": product, "snapshot": active, "context": context, "latest_audit": latest,
                "snapshots": snapshots, "audits": audit_summaries, "candidates": candidate_summaries,
                "publications": publications, "jobs": self.jobs(connection, product_id)}

    def product_list(self, connection) -> list[dict]:
        products = []
        for row in connection.execute("SELECT id FROM products ORDER BY rowid DESC"):
            product_id = row["id"]
            product = self.product(connection, product_id)
            snapshot = self.snapshot(connection, product_id)
            audit_row = connection.execute(
                "SELECT * FROM audit_runs WHERE product_id=? AND snapshot_id=? AND kind='audit' ORDER BY rowid DESC LIMIT 1",
                (product_id, snapshot["id"]),
            ).fetchone()
            audit = unpack(audit_row, ("data",)) if audit_row else None
            result = audit["data"]["result"] if audit else None
            draft = connection.execute(
                "SELECT data FROM rewrite_versions WHERE product_id=? ORDER BY version DESC LIMIT 1", (product_id,)
            ).fetchone()
            draft_ready = unpack(draft, ("data",))["data"]["validation"]["ready"] if draft else None
            numbers = {key: result[key] if result else None for key in ("score", "coverage", "provisional")}
            products.append({**product, **numbers, "p0": sum(item["priority"] == "P0" for item in result["issues"]) if result else None,
                             "updated_at": audit["created_at"] if audit else None, "draft_ready": draft_ready,
                             "by_module": result is not None and "module_scores" in result,
                             "screen_score": ((result.get("screen") or {}).get("score")
                                              if result and "module_scores" not in result else None)})
        return products
