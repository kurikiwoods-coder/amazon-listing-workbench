from __future__ import annotations

import hashlib
import logging
from threading import Event

from .amazon import CollectionError
from .audit import ENGINE_VERSION, LEGACY_ENGINE, compare, score_listing
from .guidance import GUIDANCE_VERSION
from .llm import LLM, LEGACY_PROMPT_VERSIONS, PROMPT_VERSION, ModelFailed, draft_plan, semantic_audit, suggest_questions
from .nexscope import collect_nexscope
from .public_archive import collect_public_archive
from .rewrite import inspect_candidate, proposed_lines
from .rules import LEGACY_RULE_CHECKS, RULE_CHECKS
from .skill_rewrite import generate_issue_edits
from .store import Conflict, NotFound, Store, encode

logger = logging.getLogger(__name__)


def fingerprint(value: dict) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def scoring_key(payload: dict) -> str:
    basis = {**payload, "rules": {key: value for key, value in payload["rules"].items() if key != "as_of"}}
    return fingerprint({"engine": ENGINE_VERSION, "guidance": GUIDANCE_VERSION, **basis})


def llm_for(stamp: dict) -> LLM:
    client = LLM.configured()
    if not client or stamp["provider_id"] != client.stamp()["provider_id"]:
        raise ModelFailed("复用本次评分需要原有模型服务配置。请核对服务器端设置。")
    if stamp["prompt_version"] not in {PROMPT_VERSION, *LEGACY_PROMPT_VERSIONS}:
        raise ModelFailed("当前程序不支持这一历史提示词版本。")
    return client


def ensure_engine(audit: dict):
    if audit["data"]["engine_version"] not in (ENGINE_VERSION, LEGACY_ENGINE) or audit["data"]["rules"]["checks_version"] not in (RULE_CHECKS, *LEGACY_RULE_CHECKS):
        raise ValueError("当前程序无法复用这一历史评分引擎。可保留报告并发起新的基准评分。")


def preview_candidate(fields, candidate_fields, product, context, rules, engine_version=ENGINE_VERSION) -> dict:
    before = score_listing(product, fields, context, rules, engine_version=engine_version)
    after = score_listing(product, candidate_fields, context, rules, engine_version=engine_version)
    return {"before": before["score"], "after": after["score"], "delta": compare(before, after)["score_delta"]}


def audit_data(params, fields, context, still_running=lambda: True) -> dict | None:
    client = semantic = actual = None
    if params["llm"]:
        client = llm_for(params["llm"])
        semantic, actual = semantic_audit(client, params["product"], fields, context, params["llm"]["model"],
                                          params["llm"]["prompt_version"])
    if not still_running():
        return None
    engine = params.get("engine_version", LEGACY_ENGINE)
    result = score_listing(params["product"], fields, context, params["rules"], semantic, engine_version=engine)
    data = {**params, "result": result, "model": actual, "engine_version": engine}
    return generate_issue_edits(data, fields, context, client) if engine == ENGINE_VERSION else data


class CaptureWorker:
    def __init__(self, store: Store, collect):
        self.store = store
        self.collect = collect
        self.stopped = Event()
        self.wake = Event()

    def run(self):
        while not self.stopped.is_set():
            self.wake.clear()
            try:
                with self.store.connect() as conn:
                    queued = conn.execute("SELECT 1 FROM capture_workflows WHERE status='queued' LIMIT 1").fetchone()
                if queued:
                    with self.store.connect(write=True) as conn:
                        job = self.store.claim_capture(conn)
                    if job:
                        self.process(job)
                    continue
            except Exception:
                logger.exception("Capture worker could not process a task")
            self.wake.wait(0.5)

    def process(self, job):
        def active():
            if self.stopped.is_set():
                return False
            with self.store.connect() as conn:
                return conn.execute("SELECT 1 FROM capture_workflows WHERE id=? AND status='running'", (job["id"],)).fetchone() is not None

        preview = None
        try:
            collect = {"nexscope": collect_nexscope, "public_archive": collect_public_archive}.get(job["provider"], self.collect)
            preview = collect(job["asin"], market=job["market"], still_running=active)
            status = "succeeded"
            detail = "公开归档已读取，待核对" if job["provider"] == "public_archive" else "采集完成，待核对"
        except CollectionError as exc:
            status, detail = "failed", str(exc)
        except Exception:
            logger.exception("Capture failed for task %s", job["id"])
            status, detail = "failed", "采集未完成。可以重新提取或上传文案。"
        if self.stopped.is_set():
            return
        try:
            with self.store.connect(write=True) as conn:
                if not conn.execute("SELECT 1 FROM capture_workflows WHERE id=? AND status='running'", (job["id"],)).fetchone():
                    return
                if preview:
                    if (preview.get("asin"), preview.get("market")) != (job["asin"], job["market"]):
                        raise Conflict("采集结果与目标 ASIN 或站点不一致。请检查商品链接后重新提取。")
                    source = "公开归档 · 待核实" if job["provider"] == "public_archive" else "商品页采集 · 待核实"
                    product = self.store.save_intake(conn, job["asin"], job["market"], preview["fields"], source,
                                                     product_id=job["product_id"], brand=preview["brand"])
                    self.store.save_amazon_mirror(conn, product, preview, job["provider"])
                    preview = {**preview, "product_id": product["id"]}
                elif status == "failed":
                    preview = self.store.amazon_mirror(conn, job["market"], job["asin"], required=False)
                    if preview and job["product_id"] not in (None, preview["product_id"]):
                        preview = None
                self.store.finish_capture(conn, job["id"], status, detail, preview)
        except (Conflict, NotFound) as exc:
            with self.store.connect(write=True) as conn:
                self.store.finish_capture(conn, job["id"], "failed", str(exc))
        except (ValueError, KeyError, TypeError):
            with self.store.connect(write=True) as conn:
                self.store.finish_capture(conn, job["id"], "failed", "采集内容不能导入。请重新提取或上传原文。")


class Worker:
    def __init__(self, store: Store):
        self.store = store
        self.stopped = Event()
        self.wake = Event()

    def run(self):
        while not self.stopped.is_set():
            self.wake.clear()
            try:
                with self.store.connect() as conn:
                    queued = conn.execute("SELECT 1 FROM jobs WHERE status = 'queued' LIMIT 1").fetchone()
                if queued:
                    with self.store.connect(write=True) as conn:
                        job = self.store.claim(conn)
                    if job:
                        self.process(job)
                    continue
            except Exception:
                logger.exception("Worker could not claim a job")
            self.wake.wait(0.5)

    def progress(self, job, amount, detail) -> bool:
        with self.store.connect(write=True) as conn:
            current = self.store.job(conn, job["id"], required=False)
            if not current or current["status"] != "running" or self.stopped.is_set():
                return False
            self.store.set_job(conn, job["id"], "running", amount, detail)
            return True

    def process(self, job):
        try:
            function = {
                "audit": self.run_audit, "rewrite": self.run_rewrite,
                "rescore": self.run_rescore, "questions": self.run_questions,
            }[job["kind"]]
            result_writer = function(job)
            if result_writer is None:
                return
            with self.store.connect(write=True) as conn:
                current = self.store.job(conn, job["id"], required=False)
                if self.stopped.is_set() or not current or current["status"] != "running":
                    return
                result = result_writer(conn)
                self.store.set_job(conn, job["id"], "succeeded", 100, "已完成", result)
        except (ValueError, LookupError, ModelFailed) as exc:
            self.fail(job, str(exc))
        except Exception:
            logger.exception("Unexpected failure in job %s", job["id"])
            self.fail(job, "任务未完成。原文和历史已保留，可以重试。")

    def fail(self, job, message):
        with self.store.connect(write=True) as conn:
            current = self.store.job(conn, job["id"], required=False)
            if current and current["status"] == "running":
                self.store.set_job(conn, job["id"], "failed", current["progress"], message)

    def run_audit(self, job):
        params, pid = job["payload"], job["product_id"]
        with self.store.connect() as conn:
            snapshot = self.store.snapshot(conn, pid, params["snapshot_id"])
            context = self.store.context(conn, pid, params["context_id"])
        if not self.progress(job, 32, "检查字段和买家问句"):
            return None
        data = audit_data(params, snapshot["fields"], context["data"],
                          lambda: self.progress(job, 76, "汇总模块分数和修改意见"))
        if data is None:
            return None

        def commit(conn):
            audit = self.store.new_audit(conn, pid, snapshot["id"], context["id"], "audit", job["fingerprint"], data)
            return {"id": audit["id"], "kind": "audit"}
        return commit

    def run_rewrite(self, job):
        params, pid = job["payload"], job["product_id"]
        with self.store.connect() as conn:
            audit = self.store.audit(conn, pid, params["audit_id"])
            fields = self.store.snapshot(conn, pid, audit["snapshot_id"])["fields"]
            context = self.store.context(conn, pid, audit["context_id"])["data"]
        ensure_engine(audit)
        baseline, facts = audit["data"], context["facts"]
        confirmed = [fact for fact in facts if fact["state"] == "confirmed"]
        if not self.progress(job, 35, "从已确认事实选取文案"):
            return None
        picks = actual = None
        if baseline["llm"] and confirmed:
            client = llm_for(baseline["llm"])
            picks, actual = draft_plan(client, params["strategy"], confirmed, baseline["product"], baseline["result"],
                                       baseline["llm"]["model"])
            if actual != baseline["model"]:
                raise ModelFailed("模型服务返回了不同的模型版本，请先重新基准评分。")
        review = proposed_lines(params["strategy"], fields, facts, baseline["result"], baseline["rules"], picks)
        if not self.progress(job, 75, "检查候选稿的来源和字符数"):
            return None
        validation = inspect_candidate(review, fields, facts, baseline["result"]["issues"], baseline["rules"])
        data = {
            "validation": validation, "mode": baseline["mode"], "model": actual,
            "note": "", "forecast": preview_candidate(fields, validation["fields"], baseline["product"], context,
                                                       baseline["rules"], baseline["engine_version"]),
        }

        def commit(conn):
            candidate = self.store.new_candidate(conn, pid, audit["id"], params["strategy"], data)
            return {"id": candidate["id"], "kind": "rewrite"}
        return commit

    def run_rescore(self, job):
        params, pid = job["payload"], job["product_id"]
        with self.store.connect() as conn:
            candidate = self.store.candidate(conn, pid, params["candidate_id"])
            audit = self.store.audit(conn, pid, candidate["audit_id"])
            context = self.store.context(conn, pid, audit["context_id"])
        ensure_engine(audit)
        frozen = audit["data"]
        fields = candidate["data"]["validation"]["fields"]
        if not self.progress(job, 36, "沿用原评分的规则、事实和问句"):
            return None
        semantic = actual = None
        if frozen["llm"]:
            client = llm_for(frozen["llm"])
            semantic, actual = semantic_audit(client, frozen["product"], fields, context["data"], frozen["llm"]["model"],
                                              frozen["llm"]["prompt_version"])
            if actual != frozen["model"]:
                raise ModelFailed("模型版本发生变化，无法与原评分直接比较。")
        if not self.progress(job, 78, "比较改写前后的问题和问句覆盖"):
            return None
        result = score_listing(frozen["product"], fields, context["data"], frozen["rules"], semantic,
                               engine_version=frozen["engine_version"])
        data = {**frozen, "result": result, "comparison": compare(frozen["result"], result), "model": actual,
                "candidate_id": candidate["id"], "baseline_id": audit["id"]}

        def commit(conn):
            saved = self.store.new_audit(conn, pid, audit["snapshot_id"], context["id"], "rescore", job["fingerprint"], data)
            return {"id": saved["id"], "kind": "rescore"}
        return commit

    def run_questions(self, job):
        params, pid = job["payload"], job["product_id"]
        with self.store.connect() as conn:
            fields = self.store.snapshot(conn, pid, params["snapshot_id"])["fields"]
            context = self.store.context(conn, pid, params["context_id"])["data"]
        if not self.progress(job, 40, "整理有辨识度的买家问句"):
            return None
        options, actual = suggest_questions(llm_for(params["llm"]), params["product"], fields, context["facts"])
        return lambda _conn: {"kind": "questions", "questions": options, "model": actual}
