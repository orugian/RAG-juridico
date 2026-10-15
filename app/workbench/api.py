"""Workbench HTTP API and web interface (same host, same key, tailnet only).

Routes under /wb/api require the operator key (X-API-Key with "operate");
/ui serves the static single-page interface. Chat goes through the governed
P7 /chat route, never through this module.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.http_contracts import HttpFailure, authenticate
from app.workbench.store import EXPECTED_STATUSES, FEEDBACK_CATEGORIES, REFERENCE_KINDS, RELATION_TYPES

_UI = Path(__file__).with_name("ui.html")
_BLOCK_NUMBER = re.compile(r"(\d+)$")
_SAFE_TEXT = re.compile(r"^[^\x00-\x08\x0b\x0c\x0e-\x1f]{1,500}$")


class PartyIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    role: str = "parte"
    clean_identifier: str | None = None


class DocumentIn(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    parties: list[PartyIn] | None = Field(default=None, max_length=20)
    excluded_reason: str | None = Field(default=None, max_length=500)


class ReferenceIn(BaseModel):
    source_id: str
    block_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    literal: str = Field(max_length=500)
    kind: str
    target_block_ids: list[str] = Field(default_factory=list, max_length=20)


class RelationIn(BaseModel):
    from_instrument_id: str
    to_instrument_id: str
    relation_type: str
    affected_block_ids: list[str] = Field(min_length=1, max_length=200)
    support_block_ids: list[str] = Field(min_length=1, max_length=200)


class CaseIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    instrument_ids: list[str] = Field(default_factory=list, max_length=20)
    expected_status: str
    expected_instrument_ids: list[str] = Field(default_factory=list, max_length=20)
    expected_terms: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=2000)


class FeedbackIn(BaseModel):
    request_id: str
    rating: str
    category: str
    comment: str = Field(default="", max_length=4000)
    case: CaseIn | None = None


class PublishIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
    strict: bool = False


def _block_order(block_id):
    match = _BLOCK_NUMBER.search(block_id)
    return int(match.group(1)) if match else 0


def _unit_order(unit):
    return min(_block_order(block_id) for block_id in unit.block_ids)


def create_workbench_app(*, runtime, settings, workbench, evaluation, data_root: Path | None, pub: str | None,
                         spawn=subprocess.run):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def operator(x_api_key: str | None = Header(default=None), x_reviewer: str | None = Header(default=None)):
        try:
            access = runtime.authenticate_epoch(authenticate(settings, x_api_key))
        except HttpFailure as failure:
            raise HTTPException(failure.status) from None
        except Exception:
            raise HTTPException(401) from None
        if "operate" not in access.permissions:
            raise HTTPException(403)
        reviewer = (x_reviewer or "").strip()
        return {"access": access, "reviewer": reviewer if reviewer and _SAFE_TEXT.fullmatch(reviewer) else "revisor"}

    def loaded():
        try:
            return runtime.manager._active()
        except Exception:
            raise HTTPException(503) from None

    def report():
        if data_root is None or pub is None:
            return {}
        path = Path(data_root) / "pubs" / pub / "bundle" / "report.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def documents():
        bundle = loaded().artifacts.bundle
        decided = {(row["source_id"], row["block_id"], row["start"], row["end"]) for row in workbench.reference_decisions()}
        by_source = {}
        for unit in bundle.units:
            entry = by_source.setdefault(unit.source_ids[0], {
                "source_id": unit.source_ids[0], "instrument_id": unit.instrument_id, "title": unit.contract_title,
                "parties": [{"name": p.name, "role": getattr(p.role, "value", p.role), "clean_identifier": p.clean_identifier}
                            for p in unit.parties], "units": 0, "open_references": 0})
            entry["units"] += 1
            for reference in unit.references:
                coordinate = (unit.source_ids[0], reference.block_id, reference.start, reference.end)
                if reference.binding_origin == "review_mapping" and reference.target_unit_ids == [unit.unit_id] \
                        and coordinate not in decided:
                    entry["open_references"] += 1
        return by_source

    @app.get("/ui", response_class=HTMLResponse)
    def ui():
        return HTMLResponse(_UI.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @app.get("/wb/api/overview")
    def overview(user=Depends(operator)):
        current = loaded()
        docs = documents()
        rep = report()
        since = workbench.last_published_at()
        return {"generation_id": current.manifest.generation_id, "publication": pub,
                "documents": len(docs), "units": len(current.artifacts.bundle.units),
                "chunks": len(current.artifacts.bundle.chunks), "relations": len(current.artifacts.bundle.relations),
                "open_references": sum(d["open_references"] for d in docs.values()),
                "reserved": len(rep.get("reserved", [])), "rejected": len(rep.get("rejected", [])),
                "skipped": len(rep.get("skipped", [])), "stats": workbench.stats(),
                "pending_changes": workbench.pending_changes(since), "last_published_at": since,
                "reviewer": user["reviewer"]}

    @app.get("/wb/api/catalog")
    def catalog(q: str = "", user=Depends(operator)):
        from app.client import _fold
        wanted = _fold(q.strip())
        digits = re.sub(r"\W", "", q).upper()
        rows = sorted(documents().values(), key=lambda d: _block_order(d["instrument_id"]))
        if wanted:
            rows = [d for d in rows if wanted in _fold(d["title"]) or wanted == d["instrument_id"].split(":")[1]
                    or any(wanted in _fold(p["name"]) or (len(digits) >= 8 and digits == (p["clean_identifier"] or ""))
                           for p in d["parties"])]
        decided = {row["source_id"] for row in workbench._rows("SELECT source_id FROM document_decisions")}
        for row in rows:
            row["has_decision"] = row["source_id"] in decided
        return rows[:300]

    @app.get("/wb/api/document/{source_id:path}")
    def document(source_id: str, user=Depends(operator)):
        bundle = loaded().artifacts.bundle
        units = sorted([u for u in bundle.units if u.source_ids[0] == source_id], key=_unit_order)
        if not units:
            raise HTTPException(404)
        decisions = {(r["block_id"], r["start"], r["end"]): r for r in workbench.reference_decisions(source_id)}
        by_id = {u.unit_id: u for u in units}
        payload = []
        for unit in units:
            references = []
            for reference in unit.references:
                targets = [by_id[target].location.label for target in reference.target_unit_ids if target in by_id]
                self_bound = reference.binding_origin == "review_mapping" and reference.target_unit_ids == [unit.unit_id]
                references.append({"block_id": reference.block_id, "start": reference.start, "end": reference.end,
                                   "literal": reference.literal, "label": reference.normalized_label,
                                   "origin": "pending" if self_bound else reference.binding_origin,
                                   "targets": targets,
                                   "decision": decisions.get((reference.block_id, reference.start, reference.end))})
            payload.append({"unit_id": unit.unit_id, "label": unit.location.label, "block_ids": unit.block_ids,
                            "first_block": unit.block_ids[0], "text": unit.verbatim_text,
                            "closure": [by_id[c].location.label for c in unit.closure_unit_ids if c in by_id],
                            "references": references})
        instrument = units[0].instrument_id
        relations = [r for r in workbench.relations() if instrument in (r["from_instrument_id"], r["to_instrument_id"])]
        published = [r.model_dump(mode="json") for r in bundle.relations
                     if instrument in (r.from_instrument_id, r.to_instrument_id)]
        return {"source_id": source_id, "instrument_id": instrument, "title": units[0].contract_title,
                "parties": [{"name": p.name, "role": getattr(p.role, "value", p.role), "clean_identifier": p.clean_identifier}
                            for p in units[0].parties],
                "decision": workbench.document_decision(source_id), "units": payload,
                "relation_decisions": relations, "published_relations": published}

    @app.post("/wb/api/document/{source_id:path}")
    def save_document(source_id: str, body: DocumentIn, user=Depends(operator)):
        from app.identifiers import normalize_identifier
        parties = None
        if body.parties is not None:
            parties = [{"name": p.name.strip(), "role": p.role,
                        "clean_identifier": normalize_identifier(p.clean_identifier) if p.clean_identifier else None}
                       for p in body.parties]
        try:
            workbench.save_document(source_id, reviewer=user["reviewer"], title=body.title, parties=parties,
                                    excluded_reason=body.excluded_reason)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return {"saved": True}

    @app.delete("/wb/api/document/{source_id:path}")
    def clear_document(source_id: str, user=Depends(operator)):
        workbench.clear_document(source_id)
        return {"cleared": True}

    @app.post("/wb/api/reference")
    def save_reference(body: ReferenceIn, user=Depends(operator)):
        if body.kind not in REFERENCE_KINDS:
            raise HTTPException(400)
        try:
            workbench.save_reference(**body.model_dump(), reviewer=user["reviewer"])
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return {"saved": True}

    @app.post("/wb/api/reference/clear")
    def clear_reference(body: ReferenceIn, user=Depends(operator)):
        workbench.clear_reference(source_id=body.source_id, block_id=body.block_id, start=body.start, end=body.end)
        return {"cleared": True}

    @app.get("/wb/api/relations")
    def relations(user=Depends(operator)):
        return {"decisions": workbench.relations(), "types": RELATION_TYPES}

    @app.post("/wb/api/relations")
    def save_relation(body: RelationIn, user=Depends(operator)):
        try:
            key = workbench.save_relation(**body.model_dump(), reviewer=user["reviewer"])
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return {"relation_key": key}

    @app.delete("/wb/api/relations/{relation_key:path}")
    def delete_relation(relation_key: str, user=Depends(operator)):
        workbench.delete_relation(relation_key)
        return {"deleted": True}

    @app.get("/wb/api/pending")
    def pending(user=Depends(operator)):
        rep = report()
        decided = {row["source_id"]: row for row in workbench._rows("SELECT * FROM document_decisions")}
        for key in ("reserved", "rejected"):
            for row in rep.get(key, []):
                decision = decided.get(row["source_id"])
                row["has_decision"] = decision is not None
        return {"reserved": rep.get("reserved", []), "rejected": rep.get("rejected", []),
                "skipped": rep.get("skipped", []), "excluded": rep.get("excluded", [])}

    @app.get("/wb/api/preview/{source_id:path}")
    def preview(source_id: str, user=Depends(operator)):
        """First blocks of a document outside the index (reserved), re-extracted from the original."""
        text = _preview(Path(data_root) if data_root else None, source_id)
        if text is None:
            raise HTTPException(404)
        return {"source_id": source_id, "blocks": text, "decision": workbench.document_decision(source_id)}

    @app.get("/wb/api/traces")
    def traces(status: str | None = None, feedback: str | None = None, limit: int = 100, user=Depends(operator)):
        return workbench.traces(limit=min(max(limit, 1), 500), status=status, with_feedback=feedback)

    @app.get("/wb/api/traces/{request_id}")
    def trace(request_id: str, user=Depends(operator)):
        item = workbench.trace(request_id)
        if item is None:
            raise HTTPException(404)
        units = {u.unit_id: u for u in loaded().artifacts.bundle.units}
        def describe(unit_id):
            unit = units.get(unit_id)
            if unit is None:
                return {"unit_id": unit_id, "missing": True}
            return {"unit_id": unit_id, "instrument_id": unit.instrument_id, "source_id": unit.source_ids[0],
                    "title": unit.contract_title, "label": unit.location.label, "text": unit.verbatim_text[:1500]}
        item["offered_units"] = [describe(u) for u in item["offered"]]
        item["selected_set"] = list(item["selected"])
        return item

    @app.post("/wb/api/feedback")
    def feedback(body: FeedbackIn, user=Depends(operator)):
        if body.rating not in ("good", "bad") or body.category not in FEEDBACK_CATEGORIES:
            raise HTTPException(400)
        try:
            identifier = workbench.add_feedback(body.request_id, rating=body.rating, category=body.category,
                                                comment=body.comment, reviewer=user["reviewer"])
        except KeyError:
            raise HTTPException(404) from None
        case_id = None
        if body.case is not None:
            if body.case.expected_status not in EXPECTED_STATUSES:
                raise HTTPException(400)
            case_id = workbench.add_case(**body.case.model_dump(), feedback_id=identifier)
        return {"feedback_id": identifier, "case_id": case_id}

    @app.get("/wb/api/eval/cases")
    def cases(user=Depends(operator)):
        return workbench.cases()

    @app.post("/wb/api/eval/cases")
    def add_case(body: CaseIn, user=Depends(operator)):
        if body.expected_status not in EXPECTED_STATUSES:
            raise HTTPException(400)
        return {"case_id": workbench.add_case(**body.model_dump())}

    @app.delete("/wb/api/eval/cases/{case_id}")
    def remove_case(case_id: str, user=Depends(operator)):
        workbench.deactivate_case(case_id)
        return {"removed": True}

    @app.post("/wb/api/eval/run")
    def run_eval(user=Depends(operator)):
        if not workbench.cases():
            raise HTTPException(400, "no_cases")
        try:
            return {"run_id": evaluation.start()}
        except RuntimeError:
            raise HTTPException(409) from None

    @app.get("/wb/api/eval/runs")
    def runs(user=Depends(operator)):
        return {"runs": workbench.runs(), "busy": evaluation.busy()}

    @app.get("/wb/api/eval/runs/{run_id}")
    def run_results(run_id: str, user=Depends(operator)):
        return workbench.run_results(run_id)

    @app.get("/wb/api/publications")
    def publications(user=Depends(operator)):
        logs = Path(data_root) / "logs" if data_root else None
        rows = workbench.publications()
        tail = ""
        if logs and rows:
            log_file = logs / f"{rows[0]['name']}.log"
            if log_file.is_file():
                tail = "\n".join(log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
        return {"publications": rows, "log_tail": tail,
                "pending_changes": workbench.pending_changes(workbench.last_published_at())}

    @app.post("/wb/api/publish")
    def publish(body: PublishIn, user=Depends(operator)):
        if data_root is None:
            raise HTTPException(503)
        if not _SAFE_TEXT.fullmatch(body.reason) or body.reason.startswith("-") or user["reviewer"].startswith("-"):
            raise HTTPException(400)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        name = f"pub-{stamp}"
        # The row is created here, under the store lock, so a second request sees it.
        publication_id = workbench.claim_publication(name, user["reviewer"])
        if publication_id is None:
            raise HTTPException(409, "publication_running")
        logs = Path(data_root) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_file = logs / f"{name}.log"
        command = ["systemd-run", "--user", f"--unit=rag-publish-{stamp}", "--collect", "-p", "MemoryMax=5G",
                   "-p", "MemorySwapMax=0", "-p", "Nice=10", "-E", "MALLOC_ARENA_MAX=2",
                   "-p", f"StandardOutput=append:{log_file}", "-p", f"StandardError=append:{log_file}",
                   f"--working-directory={Path.cwd()}", sys.executable, "-m", "app.publish",
                   "--data-root", str(data_root), "--reviewer", user["reviewer"], "--reason", body.reason,
                   "--name", name, "--publication-id", publication_id, "--activate"]
        if body.strict:
            command.append("--strict")
        try:
            result = spawn(command, capture_output=True, text=True, timeout=30)
        except Exception:
            result = None
        if result is None or result.returncode != 0:
            workbench.finish_publication(publication_id, "failed", {"error": "spawn_failed"})
            return JSONResponse({"started": False, "error": "spawn_failed"}, status_code=500)
        return {"started": True, "unit": f"rag-publish-{stamp}", "log": log_file.name}

    return app


def _preview(data_root, source_id, limit=30):
    if data_root is None:
        return None
    match = re.fullmatch(r"doc:(\d+):v(\d+):file:(\w+)", source_id)
    if not match:
        return None
    mfiles_id, file_id = int(match.group(1)), match.group(3)
    staging = next((p for p in sorted((data_root / "staging").iterdir()) if p.is_dir()), None)
    if staging is None:
        return None
    for path in staging.glob("*.json"):
        if len(path.name) != 69:
            continue
        entry = json.loads(path.read_text(encoding="utf-8"))
        if entry.get("mfiles_id") != mfiles_id or str(entry.get("file_id")) != file_id:
            continue
        parsed = entry.get("parsed_document") or {}
        blocks = parsed.get("blocks") or []
        if not blocks:
            conversions = sorted((data_root / "conversions").glob(f"{mfiles_id}_{file_id}-*.docx"))
            if conversions:
                from app.ingestion.docx_parser import extract_docx_blocks
                blocks = [b.model_dump(mode="json") for b in extract_docx_blocks(conversions[0], doc_id=mfiles_id)]
        return [b.get("text_raw", "")[:1200] for b in blocks[:limit]]
    return None


class WorkbenchDispatcher:
    """Routes /ui and /wb/* to the workbench app; everything else (and lifespan) to P7."""

    def __init__(self, api, workbench_app):
        self.api, self.workbench_app = api, workbench_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (scope["path"] == "/ui" or scope["path"].startswith("/wb/")):
            return await self.workbench_app(scope, receive, send)
        return await self.api(scope, receive, send)
