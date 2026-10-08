"""P7 HTTP behavior over an explicit local runtime protocol double."""
import json
import pytest
import threading
from types import SimpleNamespace
from dataclasses import dataclass

from fastapi.testclient import TestClient
from app.config import Settings
from app.contracts import AccessContext
from app.models import ChatResponse

QUERY_KEY = "q" * 40
OPERATOR_KEY = "o" * 40


def configuration(**updates):
    return Settings(_env_file=None, app_env="test", api_secret_key=QUERY_KEY,
                    operator_api_secret_key=OPERATOR_KEY, **updates)


@dataclass(frozen=True)
class PreparedControlDouble:
    body: bytes
    status_code: int
    access: AccessContext
    admission_policy: int
    deadline: float
    request_id: str
    ledger_digest: str | None = None


class RuntimeDouble:
    def __init__(self):
        self.calls = []
        self.authenticated = []
        self.control_preparations = []
        self.control_commits = []
        self.control_commit_finished = threading.Event()
        self.policy_epoch = 1
        self.blocked_credentials = set()

    def authenticate_epoch(self, access: AccessContext):
        self.authenticated.append(access)
        return access.model_copy(update={"policy_epoch": 1})

    def execute(self, request, access, *, thread_id, request_id, deadline):
        self.calls.append((request, access, deadline))
        response = ChatResponse(response="Informação não localizada.", thread_id=thread_id,
                                processing_time_ms=1, request_id=request_id, status="abstained",
                                corpus_generation_id="synthetic-generation", reason_code="insufficient_evidence")
        return SimpleNamespace(response=response, body=response.model_dump_json().encode())

    def commit(self, prepared, emit, *, stream=False):
        body = prepared.body
        if stream:
            body = b"event: answer\ndata: " + body + b"\n\n"
        emit(body)

    def prepare_control(self, operation, access, *, request_id, deadline, encode):
        self.control_preparations.append(operation)
        admission_policy = self.policy_epoch
        if operation == "ready":
            raw = self.readiness()
        elif operation == "metrics":
            raw = self.metrics()
        elif operation == "policy":
            raw = self.policy_status(access)
        else:
            raise AssertionError("unexpected_control_operation")
        status_code, body = encode(raw)
        return PreparedControlDouble(body, status_code, access, admission_policy, deadline, request_id)

    def commit_control(self, prepared, emit):
        from app.answer_contracts import AnswerServiceError
        if prepared.access.credential_id in self.blocked_credentials:
            raise AnswerServiceError("forbidden", request_id=prepared.request_id)
        if prepared.admission_policy != self.policy_epoch:
            raise AnswerServiceError("service_unavailable", request_id=prepared.request_id)
        emit(prepared.body)
        self.control_commits.append(prepared)
        self.control_commit_finished.set()

    def readiness(self):
        return True

    def policy_status(self, access):
        return {"ready": True, "policy_epoch": 1}

    def metrics(self):
        return {"cache_hits": 0, "question": "must-not-escape"}


def test_factory_has_public_minimum_health_and_strict_auth():
    from app.main import create_app
    runtime = RuntimeDouble()
    with TestClient(create_app(runtime=runtime, settings=configuration())) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        denied = client.post("/chat", json={"message": "sigiloso CPF 123.456.789-01"})
        assert denied.status_code == 401
        assert denied.json()["code"] == "unauthorized"
        assert denied.headers["x-request-id"] == denied.json()["request_id"]
        assert runtime.calls == []
        assert runtime.authenticated == []


def test_chat_and_answer_server_correlation_commit_and_sse():
    from app.main import create_app
    runtime = RuntimeDouble()
    with TestClient(create_app(runtime=runtime, settings=configuration())) as client:
        headers = {"X-API-Key": QUERY_KEY, "X-Request-ID": "client-id"}
        chat = client.post("/chat", headers=headers, json={"message": "CPF 123.456.789-01", "thread_id": "correlation-only"})
        assert chat.status_code == 200
        assert chat.json()["thread_id"] == "correlation-only"
        assert chat.json()["request_id"] == chat.headers["x-request-id"] != "client-id"
        assert runtime.calls[0][0].plan.filters.party_identifiers == ["12345678901"]
        assert runtime.calls[0][1].policy_epoch == 1
        answer = client.post("/v1/answer", headers=headers, json={"question": "honorários", "request_id": "untrusted"})
        assert answer.status_code == 200
        assert answer.json()["request_id"] != "untrusted"
        sse = client.post("/v1/answer/stream", headers=headers, json={"question": "honorários"})
        assert sse.status_code == 200
        assert sse.headers["content-type"].startswith("text/event-stream")
        assert sse.text.count("event: answer") == 1
        body = json.loads(sse.text.split("data: ", 1)[1].strip())
        assert body["status"] == "abstained"
        assert body["request_id"] == sse.headers["x-request-id"]
        assert len(runtime.authenticated) == 3


@pytest.mark.parametrize("state", ["answered", "abstained", "needs_clarification"])
def test_actual_json_three_states_matches_schema_and_prepared_bytes(state):
    from app.main import create_app
    from app.contracts import Citation, CitationLocation, CitationParty
    class StateRuntime(RuntimeDouble):
        def execute(self, *args, **kwargs):
            prepared = super().execute(*args, **kwargs)
            fields = prepared.response.model_dump()
            fields["status"] = state
            if state == "answered":
                fields["response"] = "Contrato Sintético; partes Cliente e Terceiro; Cláusula 4ª: § 2º Sem inferência."
                fields["reason_code"] = None
                fields["citations"] = [Citation(citation_id="citation-synthetic", contract_id="contract-synthetic",
                    contract_title="Contrato Sintético", document_version=1,
                    parties=[CitationParty(name="Cliente Sintético", role="contratante"), CitationParty(name="Terceiro Sintético", role="contratado")],
                    location=CitationLocation(label="Cláusula 4ª", clause="4ª"), quote="§ 2º Sem inferência.",
                    source_id="source-synthetic", evidence_id="evidence-synthetic")]
            elif state == "needs_clarification":
                fields["reason_code"] = "ambiguous_instrument"
            prepared.response = ChatResponse.model_validate(fields)
            prepared.body = json.dumps(prepared.response.model_dump(), indent=2, ensure_ascii=False).encode()
            self.prepared_body = prepared.body
            return prepared
    runtime = StateRuntime()
    with TestClient(create_app(runtime=runtime, settings=configuration())) as client:
        result = client.post("/v1/answer", headers={"X-API-Key":QUERY_KEY}, json={"question":"x"})
        assert result.status_code == 200
        response = ChatResponse.model_validate(result.json())
        assert response.status == state
        assert result.content == runtime.prepared_body  # transport never reserializes prepared bytes
        if state == "answered":
            assert response.citations[0].quote == "§ 2º Sem inferência."
            assert response.citations[0].parties[1].name == "Terceiro Sintético"
        schema = client.get("/openapi.json", headers={"X-API-Key":QUERY_KEY}).json()
        assert schema["components"]["schemas"]["ChatResponse"]["properties"]["status"]["enum"] == ["answered", "abstained", "needs_clarification"]
