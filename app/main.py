"""P7 ASGI transport: explicit factory, synchronous runtime, off-loop guarded emission.

No application, settings, executor, runtime or client is constructed at import.
An injected runtime is externally owned; this factory never opens a corpus.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import threading
import hashlib
import time
import uuid
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Security
from fastapi.security import APIKeyHeader
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import Response

from app.answer_contracts import AnswerServiceError
from app.config import Settings
from app.contracts import SelectionFilters
from app.http_contracts import CODE_STATUS, ERRORS, HttpConfig, HttpFailure, authenticate, encode_control, error_body, reauthenticate_context, validate_http_auth
from app.models import ChatRequest, ChatResponse, ErrorResponse
from pydantic import ValidationError

if TYPE_CHECKING:
    from app.answer_runtime import GovernedAnswerRuntime


def _status(error):
    if isinstance(error, HttpFailure):
        return error.status
    if isinstance(error, HTTPException):
        return error.status_code if error.status_code in ERRORS else 500
    if isinstance(error, AnswerServiceError):
        return CODE_STATUS.get(error.code, 500)
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return 504
    return 500


def _error(status, request_id):
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    if status in {429, 503}:
        headers["Retry-After"] = "1"
    return Response(error_body(status, request_id), status_code=status, media_type="application/json", headers=headers)


class _RateTable:
    """Bounded fixed-window counters; unknown new keys fail closed at capacity."""
    def __init__(self, maximum, window):
        self.maximum, self.window = maximum, window
        self.entries = {}
        self.lock = threading.Lock()

    def check(self, namespace, key, limit):
        now = time.monotonic()
        key = (namespace, hashlib.sha256(key.encode()).digest())
        with self.lock:
            self.entries = {k: v for k, v in self.entries.items() if v[0] > now}
            if key not in self.entries and len(self.entries) >= self.maximum:
                raise HttpFailure(429)
            expires, count = self.entries.get(key, (now + self.window, 0))
            if count >= limit:
                raise HttpFailure(429)
            self.entries[key] = (expires, count + 1)


class _BoundedPool:
    """Admission counts actual concurrent futures, not cancelled HTTP waiters."""
    def __init__(self, workers, queue, name):
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        self.limit = workers + queue
        self.pending = set()
        self.lock = threading.Lock()
        self.closed = False

    def submit(self, function, *args):
        with self.lock:
            if self.closed:
                raise HttpFailure(503)
            if len(self.pending) >= self.limit:
                raise HttpFailure(429)
            future = self.executor.submit(function, *args)
            self.pending.add(future)
        def finished(completed):
            with self.lock:
                self.pending.discard(completed)
            if not completed.cancelled():
                completed.exception()
        future.add_done_callback(finished)
        return future

    async def call(self, deadline, function, *args):
        future = asyncio.wrap_future(self.submit(function, *args))
        future.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.wait_for(asyncio.shield(future), max(0, deadline - time.monotonic()))

    def snapshot(self):
        with self.lock:
            pending = tuple(self.pending)
        return {"pending": len(pending), "active": sum(future.running() for future in pending),
                "queued": sum(not future.running() and not future.done() for future in pending)}

    async def shutdown(self, seconds):
        with self.lock:
            self.closed = True
            pending = tuple(self.pending)
        self.executor.shutdown(wait=False, cancel_futures=True)
        if pending:
            wrapped = [asyncio.wrap_future(future) for future in pending]
            for future in wrapped:
                future.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            await asyncio.wait(wrapped, timeout=seconds)


class _GuardedResponse(Response):
    """One physical worker from preparation through guarded ASGI send/unwind."""
    pool_name = "queries"

    def __init__(self, state, access, request_id, deadline, *, stream=False):
        super().__init__(content=b"", media_type="text/event-stream" if stream else "application/json")
        self.state, self.access = state, access
        self.request_id, self.deadline, self.stream = request_id, deadline, stream

    def prepare(self):
        raise NotImplementedError

    def commit(self, prepared, emit):
        raise NotImplementedError

    def prepared_status(self, prepared):
        return 200

    async def __call__(self, scope, receive, send):
        loop = asyncio.get_running_loop()
        stopped = threading.Event()
        started = False
        emission_tasks = set()
        status_code = 200

        def stop_output():
            if stopped.is_set():
                return  # A second cancel must not detach an ASGI task still unwinding.
            stopped.set()
            for task in tuple(emission_tasks):
                task.cancel()

        async def emission(body):
            nonlocal started
            task = asyncio.current_task()
            emission_tasks.add(task)
            try:
                if stopped.is_set() or time.monotonic() >= self.deadline:
                    raise HttpFailure(504)
                async def transmit():
                    nonlocal started
                    reauthenticate_context(self.state.settings, self.access)
                    started = True
                    headers = [(b"content-type", b"text/event-stream; charset=utf-8" if self.stream else b"application/json"),
                               (b"x-request-id", self.request_id.encode()), (b"cache-control", b"no-store"),
                               (b"content-length", str(len(body)).encode())]
                    if status_code == 503:
                        headers.append((b"retry-after", b"1"))
                    await send({"type": "http.response.start", "status": status_code, "headers": headers})
                    await send({"type": "http.response.body", "body": body})
                await asyncio.wait_for(transmit(), max(0, self.deadline - time.monotonic()))
            finally:
                emission_tasks.discard(task)

        def emit(body):
            if stopped.is_set() or time.monotonic() >= self.deadline:
                raise HttpFailure(504)
            if not isinstance(body, bytes):
                raise HttpFailure(500)
            reauthenticate_context(self.state.settings, self.access)
            # Do not cancel this concurrent future: cancel the actual async task so
            # result() settles only AFTER send has unwound and guards can be released.
            asyncio.run_coroutine_threadsafe(emission(body), loop).result()

        def work():
            nonlocal status_code
            if stopped.is_set() or time.monotonic() >= self.deadline:
                raise HttpFailure(504)
            prepared = self.prepare()
            status_code = self.prepared_status(prepared)
            if type(status_code) is not int or status_code not in {200, 503}:
                raise HttpFailure(500)
            if stopped.is_set() or time.monotonic() >= self.deadline:
                raise HttpFailure(504)
            self.commit(prepared, emit)
            if not started:
                raise HttpFailure(500)

        async def disconnected():
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return

        future = asyncio.wrap_future(getattr(self.state, self.pool_name).submit(work))
        self.state.output_stoppers.add(stop_output)
        listener = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait((future, listener), timeout=max(0, self.deadline - time.monotonic()),
                                         return_when=asyncio.FIRST_COMPLETED)
            if listener in done:
                await listener  # Receive/deadline failures are not a disconnect.
                return
            if future not in done:
                raise HttpFailure(504)
            await asyncio.shield(future)
        except asyncio.CancelledError:
            if not stopped.is_set():
                raise
        except Exception as error:
            stop_output()  # No original worker may emit while fallback send yields.
            if not started:
                await _error(_status(error), self.request_id)(scope, receive, send)
        finally:
            stop_output()
            self.state.output_stoppers.discard(stop_output)
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
            # Cancelling the HTTP task never cancels/releases a running worker.
            future.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)


class _QueryResponse(_GuardedResponse):
    def __init__(self, state, request, access, thread_id, request_id, deadline, stream, builder=None):
        super().__init__(state, access, request_id, deadline, stream=stream)
        self.request, self.builder, self.thread_id = request, builder, thread_id

    def prepare(self):
        try:
            payload = self.builder(self.request) if self.builder is not None else self.request
        except ValidationError:
            raise HttpFailure(400) from None
        payload = payload.model_copy(update={"request_id": self.request_id})
        return self.state.runtime.execute(payload, self.access, thread_id=self.thread_id,
                                          request_id=self.request_id, deadline=self.deadline)

    def commit(self, prepared, emit):
        self.state.runtime.commit(prepared, emit, stream=self.stream)


class _ControlResponse(_GuardedResponse):
    pool_name = "controls"

    def __init__(self, state, operation, access, request_id, deadline):
        super().__init__(state, access, request_id, deadline)
        self.operation = operation

    def encode(self, raw):
        pools = {name: pool.snapshot() for name, pool in (("query", self.state.queries), ("control", self.state.controls))}
        return encode_control(self.operation, raw, request_id=self.request_id,
                              local_metrics=dict(self.state.http_metrics), pool_metrics=pools)

    def prepare(self):
        return self.state.runtime.prepare_control(self.operation, self.access, request_id=self.request_id,
                                                  deadline=self.deadline, encode=self.encode)

    def prepared_status(self, prepared):
        return prepared.status_code

    def commit(self, prepared, emit):
        self.state.runtime.commit_control(prepared, emit)


class _Perimeter:
    def __init__(self, app, *, state):
        self.app, self.state = app, state

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid.uuid4().hex
        request_started = time.monotonic()
        self.state.http_metrics["total_requests"] += 1
        scope.setdefault("state", {})["request_id"] = request_id
        scope["state"]["deadline"] = time.monotonic() + self.state.http_config.deadline_seconds
        started = False
        origins = [value.decode("latin-1") for name, value in scope["headers"] if name.lower() == b"origin"]
        origin = origins[0] if len(origins) == 1 else None
        cors_allowed = origin in self.state.http_config.allowed_origins

        async def correlated(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                if message["status"] >= 400:
                    self.state.http_metrics["total_errors"] += 1
                message = dict(message)
                message["headers"] = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"x-request-id"] + [(b"x-request-id", request_id.encode())]
                if cors_allowed:
                    message["headers"] += [(b"access-control-allow-origin", origin.encode("latin-1")),
                                           (b"vary", b"Origin"), (b"access-control-expose-headers", b"X-Request-ID, Retry-After")]
            await send(message)

        body_bytes = 0
        body_complete = False

        async def bounded_receive():
            nonlocal body_bytes, body_complete
            if body_complete:
                return await receive()
            try:
                message = await asyncio.wait_for(receive(), max(0, scope["state"]["deadline"] - time.monotonic()))
            except TimeoutError:
                raise HTTPException(504) from None
            if message["type"] == "http.request":
                body_bytes += len(message.get("body", b""))
                if body_bytes > self.state.http_config.max_body_bytes:
                    raise HttpFailure(400)
                body_complete = not message.get("more_body", False)
            return message

        try:
            preflight = scope["method"] == "OPTIONS" and any(name.lower() == b"access-control-request-method" for name, _ in scope["headers"])
            if preflight:
                if not cors_allowed:
                    raise HttpFailure(403)
                requested = {value.decode("latin-1").upper() for name, value in scope["headers"] if name.lower() == b"access-control-request-method"}
                requested_headers = {header.strip().lower() for name, value in scope["headers"] if name.lower() == b"access-control-request-headers" for header in value.decode("latin-1").split(",")}
                if not requested <= {"GET", "POST"} or not requested_headers <= {"content-type", "x-api-key"}:
                    raise HttpFailure(403)
                result = Response(status_code=200, headers={"Access-Control-Allow-Methods": "GET, POST",
                                  "Access-Control-Allow-Headers": "Content-Type, X-API-Key", "Access-Control-Max-Age": "600"})
                return await result(scope, receive, correlated)
            if scope["path"] != "/health":
                if self.state.stopping:
                    raise HttpFailure(503)
                peer = str((scope.get("client") or ("unknown", 0))[0])
                self.state.rates.check("perimeter", peer, self.state.http_config.perimeter_requests)
                keys = [value.decode("latin-1") for name, value in scope["headers"] if name.lower() == b"x-api-key"]
                access = authenticate(self.state.settings, keys[0] if len(keys) == 1 else None)
                lengths = [value for name, value in scope["headers"] if name.lower() == b"content-length"]
                if lengths and (len(lengths) != 1 or not lengths[0].isdigit() or len(lengths[0]) > 16
                                or int(lengths[0]) > self.state.http_config.max_body_bytes):
                    raise HttpFailure(400)
                self.state.rates.check("principal", access.principal_id, self.state.http_config.rate_requests)
                if scope["path"] in {"/metrics", "/admin/policy"} and "operate" not in access.permissions:
                    raise HttpFailure(403)
                if self.state.runtime is None:
                    raise HttpFailure(503)
                access = await self.state.controls.call(scope["state"]["deadline"], self.state.runtime.authenticate_epoch, access)
                required = "operate" if scope["path"] in {"/metrics", "/admin/policy"} else "query"
                if required not in access.permissions:
                    raise HttpFailure(403)
                scope["state"]["access"] = access
            await self.app(scope, bounded_receive, correlated)
        except Exception as error:
            if not started:
                await _error(_status(error), request_id)(scope, receive, correlated)
        finally:
            self.state.http_metrics["completed_requests"] += 1
            self.state.http_metrics["latency_total_ms"] += (time.monotonic() - request_started) * 1000


def create_app(*, runtime=None, settings=None, http_config=None):
    config = http_config or HttpConfig()
    # P6's closed request model is imported lazily; no P6 clients are constructed.
    from app.generation import AnswerRequest as P6AnswerRequest
    from app.retrieval.hybrid import plan_query
    from pydantic import field_validator

    class AnswerRequest(P6AnswerRequest):
        @field_validator("request_id", mode="before")
        @classmethod
        def discard_client_request_id(cls, value):
            return None

    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings if settings is not None else Settings(_env_file=None)
        try:
            validate_http_auth(app.state.settings)
            app.state.auth_ready = True
        except (ValueError, TypeError):
            app.state.auth_ready = False
        app.state.runtime = runtime
        app.state.rates = _RateTable(config.rate_max_keys, config.rate_window_seconds)
        app.state.output_stoppers = set()
        app.state.http_metrics = {"total_requests": 0, "total_errors": 0, "completed_requests": 0, "latency_total_ms": 0.0}
        app.state.stopping = False
        app.state.queries = _BoundedPool(config.workers, config.max_queue, "p7-query")
        app.state.controls = _BoundedPool(config.control_workers, config.control_queue, "p7-control")
        try:
            yield
        finally:
            app.state.stopping = True
            for stop_output in tuple(app.state.output_stoppers):
                stop_output()
            await asyncio.gather(app.state.queries.shutdown(config.shutdown_seconds),
                                 app.state.controls.shutdown(config.shutdown_seconds))

    app = FastAPI(lifespan=lifespan)
    app.state.http_config = config
    app.add_middleware(_Perimeter, state=app.state)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return _error(422, request.state.request_id)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return _error(error.status_code if error.status_code in ERRORS else 400, request.state.request_id)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    errors = {status: {"model": ErrorResponse} for status in ERRORS}
    internal = [Security(APIKeyHeader(name="X-API-Key", auto_error=False))]

    def response(request, payload, thread_id="default", stream=False, builder=None):
        if builder is None:
            payload = payload.model_copy(update={"request_id": request.state.request_id})
        return _QueryResponse(app.state, payload, request.state.access, thread_id, request.state.request_id,
                              request.state.deadline, stream, builder)

    def plan_chat(payload):
        plan = plan_query(payload.message, instrument_ids=payload.filters.instrument_ids,
                          selection_mode=payload.filters.selection_mode)
        plan.filters = SelectionFilters(instrument_ids=plan.filters.instrument_ids,
                                        party_identifiers=list(dict.fromkeys(plan.filters.party_identifiers + payload.filters.party_identifiers)),
                                        selection_mode=payload.filters.selection_mode)
        return AnswerRequest(question=payload.message, plan=plan)

    @app.post("/chat", response_model=ChatResponse, responses=errors, dependencies=internal)
    async def chat(payload: ChatRequest, request: Request):
        return response(request, payload, payload.thread_id, builder=plan_chat)

    @app.post("/v1/answer", response_model=ChatResponse, responses=errors, dependencies=internal)
    async def answer(payload: AnswerRequest, request: Request):
        return response(request, payload)

    @app.post("/v1/answer/stream", response_class=Response, dependencies=internal, responses={**errors, 200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}})
    async def stream_answer(payload: AnswerRequest, request: Request):
        return response(request, payload, stream=True)

    def control(request, operation):
        return _ControlResponse(app.state, operation, request.state.access, request.state.request_id,
                                request.state.deadline)

    @app.get("/ready", responses=errors, dependencies=internal)
    async def ready(request: Request):
        return control(request, "ready")

    @app.get("/metrics", responses=errors, dependencies=internal)
    async def metrics(request: Request):
        return control(request, "metrics")

    @app.get("/admin/policy", responses=errors, dependencies=internal)
    async def policy(request: Request):
        return control(request, "policy")

    return app
