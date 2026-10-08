"""P8 controlled moderate HTTP concurrency, not natural latency/SLO/process load.

Qwen embedding/tokenization and 1024-D Chroma are real and local in both modes.
Only the injectable candidate selector and explicit Event stalls are synthetic.
No sleeps, polling, corpus downloads, operational services or private pool probes.
"""
import asyncio
import json
from pathlib import Path
import threading

import httpx
import pytest

from app.answer_runtime import GovernedAnswerRuntime
from app.cache import GovernedResponseCache
from app.generation import AnswerConfig, AnswerRequest
from app.http_contracts import HttpConfig
from app.main import create_app
from app.retrieval.vector_store import ChromaVectorStore
from tests.p6_corpus import build_p6_environment
from tests.test_api import OPERATOR_KEY, QUERY_KEY, configuration
from tests.test_p4_vector_store import LocalServer
from tests.test_p6_pipeline import pick, plan

HEADERS = {"X-API-Key": QUERY_KEY}
OPERATOR = {"X-API-Key": OPERATOR_KEY}
WAIT = 20

# Declared before parsing or response generation; literal synthetic DOCX oracle.
FEES = "Qual o valor dos honorários da Alpha?"
RENT = "Qual o valor da locação comercial Gamma?"
CONSULTING = "O que consta sobre consultoria tributária?"
PENALTY = "Qual a multa de rescisão imotivada?"
RECOVERY = "Qual o valor dos honorários da Contratante Alpha?"
EXPECTED = {
    FEES: {
        "doc:101": ("Contrato de Honorários Alpha Beta", "Cláusula 1ª",
                    "Cláusula 1ª - Os honorários devidos pela Contratante Alpha são de R$ 50.000,00.",
                    {"Alpha Serviços Ltda", "Beta Participações", "Andrade Advogados"}),
        "doc:102": ("Primeiro Aditivo ao Contrato Alpha Beta", "Cláusula 1ª",
                    "Cláusula 1ª - Fica ajustado o valor dos honorários para R$ 75.000,00.",
                    {"Alpha Serviços Ltda", "Beta Participações"}),
    },
    RENT: {
        "doc:201": ("Contrato de Locação Comercial Gamma Delta", "Cláusula 1ª",
                    "Cláusula 1ª - Locação comercial do imóvel pelo valor mensal de R$ 12.000,00.",
                    {"Gamma Inovações Ltda", "Delta Locadora S.A."}),
    },
    CONSULTING: {
        "doc:301": ("Contrato de Prestação Epsilon Zeta", "Cláusula 1ª",
                    "Cláusula 1ª - Prestação de serviços de consultoria tributária à Contratante.",
                    {"Epsilon Consultoria Ltda", "Zeta Tributos S.A."}),
        "doc:302": ("Distrato do Contrato Epsilon Zeta", "Cláusula 1ª",
                    "Cláusula 1ª - Ficam extintas, por distrato, as obrigações de consultoria tributária.",
                    {"Epsilon Consultoria Ltda", "Zeta Tributos S.A."}),
    },
    PENALTY: {
        "doc:101": ("Contrato de Honorários Alpha Beta", "Cláusula 2ª",
                    "Cláusula 2ª - Em caso de rescisão imotivada, multa de 10% sobre o saldo remanescente.",
                    {"Alpha Serviços Ltda", "Beta Participações", "Andrade Advogados"}),
    },
}
EXPECTED[RECOVERY] = EXPECTED[FEES]
SELECTION = {
    FEES: ("doc:101", "Cláusula 1ª"), RENT: ("doc:201", "Cláusula 1ª"),
    CONSULTING: ("doc:301", "Cláusula 1ª"), PENALTY: ("doc:101", "Cláusula 2ª"),
    RECOVERY: ("doc:101", "Cláusula 1ª"),
}


@pytest.fixture(scope="module")
def qwen(request):
    artifact = request.config.getoption("--qwen-artifact")
    if artifact is None:
        pytest.skip("P8 requires explicit fixed local --qwen-artifact; no real Qwen proof without it; no download")
    from app.embeddings.qwen import QwenEmbeddings
    from app.ingestion.evidence import QwenTokenBudget
    # The accepted adapters validate the fixed revision/manifest and fail clearly.
    return QwenEmbeddings(Path(artifact)), QwenTokenBudget(Path(artifact))


@pytest.fixture(scope="module", params=["embedded", "server"])
def physical(request, tmp_path_factory, qwen):
    root = tmp_path_factory.mktemp("p8-load-" + request.param)
    encoder, budget = qwen
    server = None
    try:
        if request.param == "server":
            directory = root / "server"
            directory.mkdir()
            server = LocalServer(directory)
            server.start()
            vector = ChromaVectorStore(mode="server", dimension=1024, host="127.0.0.1", port=server.port)
        else:
            vector = ChromaVectorStore(mode="embedded", dimension=1024)
        env = build_p6_environment(root / "env", encoder=encoder, budget=budget,
                                   embedding_dimension=1024, vector_store=vector)
        yield env, request.param
    finally:
        # Also runs on start/build failure; only this fixture's child is owned.
        if server is not None:
            server.stop()


class GatedSelector:
    """Deterministic ID-only selector; count actual physical generate calls."""
    name = "p8-event-gated-synthetic-selector"

    def __init__(self, env, *, blocked=()):
        self.env = env
        self.entered = {question: threading.Event() for question in SELECTION}
        self.release = {question: threading.Event() for question in SELECTION}
        self.lock = threading.Lock()
        self.queued_started = threading.Event()
        self.calls = []
        self.active = self.max_active = 0
        for question, event in self.release.items():
            if question not in blocked:
                event.set()

    def generate(self, request):
        question = request.question
        with self.lock:
            self.calls.append(question)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.entered[question].set()
            if question in (CONSULTING, PENALTY):
                self.queued_started.set()
            assert self.release[question].wait(40), "synthetic gate was not released"
            doc, label = SELECTION[question]
            selected = self.env.unit(doc, label)
            assert selected in {unit.unit_id for unit in request.units}, "selector must use offered real proof"
            return pick(self.env, label, doc=doc, items=request.question_item_ids)
        finally:
            with self.lock:
                self.active -= 1

    def release_all(self):
        for event in self.release.values():
            event.set()


class ReceiveHandshake:
    """Observe only public ASGI receive, never app state or private executors.

    A single-body httpx request's next receive waits for disconnect. This gives
    an Event rendezvous with the HTTP task rather than guessing admission from
    auth completion. Public /metrics then independently asserts actual capacity.
    """
    def __init__(self, app):
        self.app = app
        self.listening = {}

    async def __call__(self, scope, receive, send):
        case = dict(scope.get("headers", ())).get(b"x-p8-case")
        delivered = False

        async def observed_receive():
            nonlocal delivered
            if delivered and case in self.listening:
                self.listening[case].set()
            message = await receive()
            if message["type"] == "http.request" and not message.get("more_body", False):
                delivered = True
            return message

        return await self.app(scope, observed_receive, send)


def body(question):
    doc, _ = SELECTION[question]
    return AnswerRequest(question=question, plan=plan(doc=doc)).model_dump(mode="json")


def assert_answer(response, question, *, cached):
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "answered" and value["reason_code"] is None
    assert value["cached"] is cached
    assert value["request_id"] == response.headers["x-request-id"]
    expected = EXPECTED[question]
    assert len(value["citations"]) == len(expected)
    assert {citation["contract_id"] for citation in value["citations"]} == set(expected)
    for citation in value["citations"]:
        title, label, quote, parties = expected[citation["contract_id"]]
        assert citation["contract_title"] == title
        assert citation["location"]["label"] == label
        assert citation["quote"] == quote and quote in value["response"]
        assert {party["name"] for party in citation["parties"]} == parties
        assert all(party["role"] for party in citation["parties"])
        assert citation["document_version"] == 1
        doc_number = citation["contract_id"].split(":")[1]
        assert citation["source_id"] == f"doc:{doc_number}:v1:file:f{doc_number}"
        assert citation["evidence_id"] and citation["citation_id"]
    return value


def semantic(value):
    return {key: item for key, item in value.items()
            if key not in {"request_id", "timestamp", "processing_time_ms", "cached"}}


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    value = response.json()
    assert set(value) == {"message", "code", "request_id", "timestamp"}
    assert value["code"] == code
    assert value["request_id"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    if status == 429:
        assert response.headers["retry-after"].isdigit() and int(response.headers["retry-after"]) > 0
    for private in (QUERY_KEY, OPERATOR_KEY, "50.000", "75.000", "12.000", "12345678901", "Traceback"):
        assert private not in response.text
    for case in EXPECTED.values():
        for _, _, quote, _ in case.values():
            assert quote not in response.text


async def event_wait(event):
    assert await asyncio.to_thread(event.wait, WAIT), "controlled physical worker did not reach Event"


async def gauges(client):
    response = await asyncio.wait_for(client.get("/metrics", headers=OPERATOR), WAIT)
    assert response.status_code == 200, response.text
    assert "50.000" not in response.text and QUERY_KEY not in response.text
    return response.json()


def test_two_workers_two_queue_overflow_controls_and_cache_integrity(physical):
    env, mode = physical
    cases = (FEES, RENT, CONSULTING, PENALTY)
    generator = GatedSelector(env, blocked=cases)
    # Keep generator capacity ABOVE the HTTP worker cap: the observed <=2
    # physical calls must follow HTTP scheduling, not a second semaphore of 2.
    runtime = GovernedAnswerRuntime(env.manager, generator, cache=GovernedResponseCache(),
                                   token_counter=env.budget.count, max_generator_calls=5)
    app = create_app(runtime=runtime, settings=configuration(),
                     http_config=HttpConfig(workers=2, max_queue=2, rate_requests=100, perimeter_requests=200))

    async def exercise():
        observed = ReceiveHandshake(app)
        tasks = []
        results = []
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observed), base_url="http://p8-local") as client:
                async def start(question, number):
                    case = str(number).encode()
                    observed.listening[case] = asyncio.Event()
                    task = asyncio.create_task(client.post("/v1/answer", json=body(question),
                                                           headers=HEADERS | {"X-P8-Case": case.decode()}))
                    tasks.append(task)
                    await asyncio.wait_for(observed.listening[case].wait(), WAIT)
                    return task

                try:
                    await start(FEES, 0)
                    await event_wait(generator.entered[FEES])
                    await start(RENT, 1)
                    await event_wait(generator.entered[RENT])
                    await start(CONSULTING, 2)
                    await start(PENALTY, 3)
                    occupied = await gauges(client)
                    assert (occupied["query_pending"], occupied["query_active"], occupied["query_queued"]) == (4, 2, 2)
                    assert generator.calls == [FEES, RENT]
                    assert not generator.entered[CONSULTING].is_set() and not generator.entered[PENALTY].is_set()
                    assert all(not task.done() for task in tasks)
                    overflow = await asyncio.wait_for(client.post("/v1/answer", headers=HEADERS,
                                                                  json=body(RECOVERY)), WAIT)
                    assert_error(overflow, 429, "rate_limited")
                    # Controls/health complete while neither saturated generator is released.
                    health = await asyncio.wait_for(client.get("/health"), WAIT)
                    assert health.status_code == 200 and health.json() == {"status": "ok"}
                    policy = await asyncio.wait_for(client.get("/admin/policy", headers=OPERATOR), WAIT)
                    assert policy.status_code == 200 and policy.json()["authority_current"] is True
                    ready = await asyncio.wait_for(client.get("/ready", headers=HEADERS), WAIT)
                    assert ready.status_code == 200
                    assert not any(generator.release[question].is_set() for question in cases)
                    assert (await gauges(client))["query_pending"] == 4
                    generator.release[FEES].set()
                    await asyncio.wait_for(tasks[0], WAIT)
                    await event_wait(generator.queued_started)
                    # Do not require a particular queue scheduling policy: only
                    # one of the queued requests may use the single freed slot.
                    assert sum(generator.entered[question].is_set() for question in cases[2:]) == 1
                    generator.release[RENT].set()
                    await asyncio.wait_for(tasks[1], WAIT)
                    for question in cases[2:]:
                        await event_wait(generator.entered[question])
                    generator.release[CONSULTING].set()
                    generator.release[PENALTY].set()
                    results = await asyncio.wait_for(asyncio.gather(*tasks), WAIT)
                    misses = [assert_answer(response, question, cached=False) for response, question in zip(results, cases)]
                    ids = {value["request_id"] for value in misses}
                    assert len(ids) == 4 and overflow.json()["request_id"] not in ids
                    assert generator.max_active == 2 and generator.active == 0
                    for question, miss in zip(cases, misses):
                        warm = await client.post("/v1/answer", headers=HEADERS, json=body(question))
                        hit = assert_answer(warm, question, cached=True)
                        assert semantic(hit) == semantic(miss)
                        assert hit["request_id"] not in ids
                        ids.add(hit["request_id"])
                    assert len(generator.calls) == 4, "warm answers must not swap proof or rerun generation"
                    recovery = await client.post("/v1/answer", headers=HEADERS, json=body(RECOVERY))
                    recovered = assert_answer(recovery, RECOVERY, cached=False)
                    assert recovered["request_id"] not in ids | {overflow.json()["request_id"]}
                    assert len(generator.calls) == 5 and generator.max_active <= 2
                    final = await gauges(client)
                    assert final["query_pending"] == final["query_queued"] == final["query_active"] == 0
                    assert final["cache_hits"] == 4 and final["cache_misses"] == 5
                    print(json.dumps({"scenario": "controlled_event_stalls_not_SLO", "mode": mode,
                                      "admitted": 4, "overflow_429": 1, "max_physical_generators": generator.max_active,
                                      "cache_hits": final["cache_hits"], "cache_misses": final["cache_misses"],
                                      "controlled_latency_p50_ms": final["latency_p50_ms"],
                                      "controlled_latency_p95_ms": final["latency_p95_ms"]}, sort_keys=True))
                finally:
                    generator.release_all()
                    if tasks:
                        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), WAIT)
    asyncio.run(exercise())


class PromptCountStall:
    """Injectable synchronous token-counter stall, NOT an abandoned provider call.

    Real Qwen counts first, then the query worker is deliberately held. P6's
    provider threads have their own retention semantics; this test does not
    conflate that separate semaphore with the HTTP query pool.
    """
    def __init__(self, budget):
        self.budget = budget
        self.entered, self.release = threading.Event(), threading.Event()
        self.lock = threading.Lock()
        self.once = True
        self.observed_tokens = None

    def __call__(self, text):
        count = self.budget.count(text)
        with self.lock:
            block, self.once = self.once, False
        if block:
            self.observed_tokens = count
            self.entered.set()
            assert self.release.wait(40), "synthetic prompt-count stall was not released"
        return count


class CompletionObservedRuntime(GovernedAnswerRuntime):
    """Observe completion of the injected public execute port, not private futures."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.execution_finished = threading.Event()

    def execute(self, *args, **kwargs):
        try:
            return super().execute(*args, **kwargs)
        finally:
            self.execution_finished.set()


def test_deadline_keeps_stalled_query_worker_until_release_then_recovers(physical):
    env, mode = physical
    generator = GatedSelector(env)
    stall = PromptCountStall(env.budget)
    runtime = CompletionObservedRuntime(env.manager, generator, cache=GovernedResponseCache(),
                                         token_counter=stall, max_generator_calls=1,
                                         config=AnswerConfig(max_primary_attempts=1))
    app = create_app(runtime=runtime, settings=configuration(),
                     http_config=HttpConfig(workers=1, max_queue=0, deadline_seconds=8,
                                            rate_requests=100, perimeter_requests=200))

    async def exercise():
        task = None
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p8-local") as client:
                try:
                    task = asyncio.create_task(client.post("/v1/answer", headers=HEADERS, json=body(FEES)))
                    await event_wait(stall.entered)
                    assert stall.observed_tokens > 0 and generator.calls == []
                    timed_out = await asyncio.wait_for(task, WAIT)
                    assert_error(timed_out, 504, "request_timeout")
                    assert not stall.release.is_set() and not runtime.execution_finished.is_set()
                    occupied = await gauges(client)
                    assert (occupied["query_pending"], occupied["query_active"], occupied["query_queued"]) == (1, 1, 0)
                    assert occupied["cache_entries"] == 0
                    rejected = await client.post("/v1/answer", headers=HEADERS, json=body(RECOVERY))
                    assert_error(rejected, 429, "rate_limited")
                    assert rejected.json()["request_id"] != timed_out.json()["request_id"]
                    assert generator.calls == [], "overflow must not reach the selector"
                    assert (await client.get("/health")).json() == {"status": "ok"}
                    policy = await client.get("/admin/policy", headers=OPERATOR)
                    assert policy.status_code == 200 and policy.json()["authority_current"] is True
                    assert (await gauges(client))["query_pending"] == 1
                    stall.release.set()
                    await event_wait(runtime.execution_finished)
                    idle = await gauges(client)
                    assert (idle["query_pending"], idle["query_active"], idle["query_queued"]) == (0, 0, 0)
                    assert idle["cache_entries"] == 0 and generator.calls == []
                    recovery = await client.post("/v1/answer", headers=HEADERS, json=body(RECOVERY))
                    recovered = assert_answer(recovery, RECOVERY, cached=False)
                    warm = await client.post("/v1/answer", headers=HEADERS, json=body(RECOVERY))
                    hit = assert_answer(warm, RECOVERY, cached=True)
                    assert semantic(hit) == semantic(recovered)
                    assert len({timed_out.json()["request_id"], rejected.json()["request_id"],
                                recovered["request_id"], hit["request_id"]}) == 4
                    final = await gauges(client)
                    assert final["query_pending"] == final["query_active"] == final["query_queued"] == 0
                    assert final["cache_entries"] == final["cache_hits"] == 1
                    assert final["cache_misses"] == 2  # One expired admitted miss, one recovered miss.
                    assert generator.calls == [RECOVERY] and generator.max_active == 1
                    print(json.dumps({"scenario": "controlled_sync_prompt_count_stall_not_provider_or_SLO",
                                      "mode": mode, "deadline_504": 1, "while_stalled_429": 1,
                                      "physical_query_active_before_release": 1, "recovery": "answered"}, sort_keys=True))
                finally:
                    stall.release.set()
                    generator.release_all()
                    if task is not None:
                        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), WAIT)
                    if stall.entered.is_set():
                        await event_wait(runtime.execution_finished)
    asyncio.run(exercise())


@pytest.mark.parametrize("transfer", ["content_length", "chunked"])
def test_http_body_limit_rejects_before_generation_and_recovers(physical, transfer):
    env, mode = physical
    generator = GatedSelector(env)
    runtime = GovernedAnswerRuntime(env.manager, generator, cache=GovernedResponseCache(),
                                   token_counter=env.budget.count)
    app = create_app(runtime=runtime, settings=configuration(),
                     http_config=HttpConfig(max_body_bytes=512, rate_requests=100, perimeter_requests=200))

    async def exercise():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p8-local") as client:
                oversized = body(FEES)
                oversized["question"] += " CPF 12345678901 " + "x" * 1024
                payload = json.dumps(oversized, ensure_ascii=False).encode()
                assert len(payload) > 512
                headers = HEADERS | {"Content-Type": "application/json"}
                if transfer == "chunked":
                    async def chunks():
                        yield payload[:512]
                        yield payload[512:]
                    denied = await client.post("/v1/answer", headers=headers, content=chunks())
                    assert "content-length" not in denied.request.headers
                else:
                    denied = await client.post("/v1/answer", headers=headers, content=payload)
                    assert int(denied.request.headers["content-length"]) > 512
                assert_error(denied, 400, "invalid_request")
                assert generator.calls == []
                rejected_metrics = await gauges(client)
                assert rejected_metrics["query_pending"] == rejected_metrics["cache_entries"] == rejected_metrics["cache_misses"] == 0
                recovery = await client.post("/v1/answer", headers=HEADERS, json=body(FEES))
                miss = assert_answer(recovery, FEES, cached=False)
                warm = await client.post("/v1/answer", headers=HEADERS, json=body(FEES))
                hit = assert_answer(warm, FEES, cached=True)
                assert semantic(hit) == semantic(miss)
                assert len({denied.json()["request_id"], miss["request_id"], hit["request_id"]}) == 3
                assert generator.calls == [FEES]
                final = await gauges(client)
                assert final["cache_hits"] == final["cache_misses"] == final["cache_entries"] == 1
                assert final["query_pending"] == 0
                print(json.dumps({"scenario": "http_body_limit_not_prompt_budget", "mode": mode,
                                  "transfer": transfer, "rejected_400": 1, "generation_on_rejection": 0,
                                  "recovery": "answered"}, sort_keys=True))
    try:
        asyncio.run(exercise())
    finally:
        generator.release_all()


def test_principal_rate_limit_precedes_warm_cache_and_other_principal_recovers(physical):
    env, mode = physical
    generator = GatedSelector(env)
    runtime = GovernedAnswerRuntime(env.manager, generator, cache=GovernedResponseCache(),
                                   token_counter=env.budget.count)
    app = create_app(runtime=runtime, settings=configuration(),
                     http_config=HttpConfig(rate_requests=2, perimeter_requests=1000, rate_window_seconds=3600))

    async def exercise():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p8-local") as client:
                first = await client.post("/v1/answer", headers=HEADERS, json=body(FEES))
                miss = assert_answer(first, FEES, cached=False)
                repeated = await client.post("/v1/answer", headers=HEADERS, json=body(FEES))
                hit = assert_answer(repeated, FEES, cached=True)
                assert semantic(hit) == semantic(miss)
                denied = await client.post("/v1/answer", headers=HEADERS, json=body(FEES))
                assert_error(denied, 429, "rate_limited")
                assert generator.calls == [FEES], "rate rejection cannot generate or consume warm cache"
                # No waiting for window reset: independent authorized operator principal.
                recovery = await client.post("/v1/answer", headers=OPERATOR, json=body(FEES))
                other = assert_answer(recovery, FEES, cached=False)
                assert semantic(other) == semantic(miss)
                assert len({miss["request_id"], hit["request_id"], denied.json()["request_id"],
                            other["request_id"]}) == 4
                final = await gauges(client)  # Operator's second request; query principal remains rate-limited.
                assert final["cache_hits"] == 1 and final["cache_misses"] == final["cache_entries"] == 2
                assert final["query_pending"] == final["query_active"] == final["query_queued"] == 0
                assert generator.calls == [FEES, FEES]
                for _ in range(3):
                    health = await client.get("/health")
                    assert health.status_code == 200 and health.json() == {"status": "ok"}
                print(json.dumps({"scenario": "principal_rate_before_warm_cache_not_SLO", "mode": mode,
                                  "query_principal_successes": 2, "rejected_429": 1,
                                  "operator_recovery": "answered", "cache_hits": final["cache_hits"],
                                  "cache_misses": final["cache_misses"]}, sort_keys=True))
    try:
        asyncio.run(exercise())
    finally:
        generator.release_all()
