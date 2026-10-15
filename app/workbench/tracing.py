"""Per-request tracing and evaluation on top of the governed runtime.

Traces record what the reviewer needs to understand an answer: question,
filters, candidate units offered to the LLM, its raw selection, cited units,
final status/reason and stage timings. They stay on the serving host (the
workbench SQLite); nothing is sent to an external tracing service. Recording
failures never change the answer.
"""
import contextvars
import json
import logging
import threading
import time
import unicodedata
import uuid

from app.answer_contracts import AnswerServiceError
from app.answer_runtime import GovernedAnswerRuntime

_LOG = logging.getLogger(__name__)
_COLLECTOR = contextvars.ContextVar("rag_trace_collector", default=None)
MAX_RAW = 8000


def _add_time(name, seconds):
    collector = _COLLECTOR.get()
    if collector is not None:
        collector["timings"][name] = round(collector["timings"].get(name, 0.0) + seconds, 4)


class TracingGenerator:
    """Wraps the candidate generator; same name so cache identities are unchanged."""

    def __init__(self, inner):
        self.inner, self.name = inner, inner.name

    def generate(self, request):
        started, raw = time.perf_counter(), None
        try:
            raw = self.inner.generate(request)
            return raw
        finally:
            _add_time("llm_s", time.perf_counter() - started)
            collector = _COLLECTOR.get()
            if collector is not None:
                collector["offered"] = [unit.unit_id for unit in request.units]
                collector["attempts"] = collector.get("attempts", 0) + 1
                if raw is not None:
                    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                    collector["llm_raw"] = text[:MAX_RAW]


def time_queries(encoder):
    """Instance-level timing of query embedding; identity is untouched."""
    original = encoder.embed_query

    def embed_query(text):
        started = time.perf_counter()
        try:
            return original(text)
        finally:
            _add_time("embed_s", time.perf_counter() - started)
    encoder.embed_query = embed_query
    return encoder


class TracingRuntime(GovernedAnswerRuntime):
    def __init__(self, manager, generator, *, workbench, **kwargs):
        super().__init__(manager, TracingGenerator(generator), **kwargs)
        self.workbench = workbench

    def execute(self, request, access, *, thread_id, request_id, deadline, eval_run_id=None):
        collector = {"timings": {}}
        token = _COLLECTOR.set(collector)
        started = time.perf_counter()
        filters = request.plan.filters.model_dump(mode="json") if request.plan else {}
        try:
            prepared = super().execute(request, access, thread_id=thread_id, request_id=request_id, deadline=deadline)
        except AnswerServiceError as error:
            self._record({"request_id": request_id, "question": request.question, "filters": filters,
                          "error_code": error.code, "latency_ms": (time.perf_counter() - started) * 1000,
                          "timings": collector["timings"], "offered": collector.get("offered"),
                          "llm_raw": collector.get("llm_raw"), "eval_run_id": eval_run_id})
            raise
        finally:
            _COLLECTOR.reset(token)
        response = prepared.response
        audit = getattr(prepared, "audit", None)
        timings = dict(collector["timings"])
        timings["total_s"] = round(time.perf_counter() - started, 4)
        self._record({
            "request_id": request_id, "question": request.question, "filters": filters,
            "status": response.status, "reason_code": response.reason_code,
            "detail_code": getattr(audit, "detail_code", None), "generation_id": response.corpus_generation_id,
            "cached": int(response.cached), "latency_ms": response.processing_time_ms, "timings": timings,
            "offered": collector.get("offered"), "selected": list(getattr(audit, "selected_unit_ids", []) or []),
            "citations": [citation.model_dump(mode="json") for citation in response.citations],
            "response": response.response, "llm_raw": collector.get("llm_raw"), "eval_run_id": eval_run_id})
        return prepared

    def commit(self, prepared, emit, *, stream=False):
        super().commit(prepared, emit, stream=stream)
        try:
            self.workbench.mark_delivered(prepared.response.request_id)
        except Exception:
            _LOG.warning("trace_delivery_mark_failed")

    def _record(self, trace):
        try:
            self.workbench.record_trace(trace)
        except Exception:
            _LOG.warning("trace_record_failed")


def _fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def check_case(case, response) -> tuple[bool, dict]:
    """Expected status; expected contracts among the citations; expected terms in the quotes."""
    cited = {citation["contract_id"] for citation in response.get("citations", [])}
    quotes = _fold(" ".join(citation["quote"] for citation in response.get("citations", [])))
    checks = {"status": response.get("status") == case["expected_status"]}
    if case["expected_instrument_ids"]:
        checks["contracts"] = set(case["expected_instrument_ids"]) <= cited
    if case["expected_terms"]:
        checks["terms"] = all(_fold(term) in quotes for term in case["expected_terms"])
    return all(checks.values()), checks


class EvaluationRunner:
    """Runs active cases through the same governed runtime and output boundary."""

    def __init__(self, runtime: TracingRuntime, access_factory):
        self.runtime, self.access_factory = runtime, access_factory
        self._lock = threading.Lock()

    def busy(self):
        return self._lock.locked()

    def start(self):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("evaluation_already_running")
        generation = self.runtime.manager._active().manifest.generation_id
        run_id = self.runtime.workbench.start_run(generation)
        threading.Thread(target=self._run, args=(run_id,), daemon=True, name="rag-eval").start()
        return run_id

    def _run(self, run_id):
        from app.generation import AnswerRequest
        from app.retrieval.hybrid import plan_query
        store, passed, total, latencies = self.runtime.workbench, 0, 0, []
        try:
            for case in store.cases():
                request_id = "eval-" + uuid.uuid4().hex
                started = time.perf_counter()
                try:
                    plan = plan_query(case["question"], instrument_ids=case["instrument_ids"])
                    prepared = self.runtime.execute(AnswerRequest(question=case["question"], plan=plan),
                                                    self.access_factory(), thread_id="eval", request_id=request_id,
                                                    deadline=time.monotonic() + 30, eval_run_id=run_id)
                    self.runtime.commit(prepared, lambda body: None)
                    response = json.loads(prepared.body)
                    ok, checks = check_case(case, response)
                    status = response["status"]
                except AnswerServiceError as error:
                    ok, checks, status = False, {"error": error.code}, "error"
                latency = (time.perf_counter() - started) * 1000
                latencies.append(latency)
                total, passed = total + 1, passed + int(ok)
                store.record_result(run_id, case["id"], request_id=request_id, status=status, passed=ok,
                                    checks=checks, latency_ms=latency)
            latencies.sort()
            store.finish_run(run_id, {"cases": total, "passed": passed,
                                      "accuracy": round(passed / total, 4) if total else None,
                                      "p50_ms": latencies[len(latencies) // 2] if latencies else None})
        except Exception as error:
            store.finish_run(run_id, {"error": type(error).__name__}, state="failed")
        finally:
            self._lock.release()
