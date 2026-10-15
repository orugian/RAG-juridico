"""Per-stage latency of one answer on the real generation (operator diagnostic).

    python -m app.stage_profile [--question "..." --instrument doc:NNNN]...

Runs in-process with the service composition from .env (stop the service first
to avoid sharing the 4 vCPU). Prints only timings and statuses, never text.
"""
import argparse
import json
import time

DEFAULT = [("Qual o valor dos honorários?", "doc:4889"), ("Qual o prazo de vigência do contrato?", "doc:4454"),
           ("Qual o foro eleito?", "doc:6750"), ("Qual a forma de pagamento?", "doc:5456"),
           ("Há multa por descumprimento?", "doc:4398")]


class StageTimer:
    def __init__(self):
        self.totals = {}

    def wrap(self, name, function):
        def timed(*args, **kwargs):
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                total, count = self.totals.get(name, (0.0, 0))
                self.totals[name] = (total + time.perf_counter() - started, count + 1)
        return timed

    def report(self):
        return {name: {"seconds": round(total, 3), "calls": count} for name, (total, count) in self.totals.items()}


def main(argv=None):
    from app.config import Settings
    from app.contracts import AccessContext
    from app.generation import AnswerRequest, GovernedAnswerService
    from app.retrieval import hybrid
    from app.retrieval.hybrid import plan_query
    from app.service import ServiceSettings, build_runtime, openrouter_zdr_client
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", action="append")
    parser.add_argument("--instrument", action="append")
    parser.add_argument("--cprofile", action="store_true", help="Top functions of the retrieval stage")
    arguments = parser.parse_args(argv)
    cases = list(zip(arguments.question, arguments.instrument)) if arguments.question else DEFAULT
    timer = StageTimer()

    def factory(model, settings):
        client = openrouter_zdr_client(model, settings)

        class Timed:
            def invoke(self, messages, timeout=None):
                return timer.wrap("llm", client.invoke)(messages, timeout=timeout)
        return Timed()

    started = time.perf_counter()
    runtime = build_runtime(ServiceSettings(), Settings(), client_factory=factory)
    runtime.manager.readiness()
    print(json.dumps({"startup_s": round(time.perf_counter() - started, 1)}))
    encoder = runtime.manager.embeddings
    encoder.embed_query = timer.wrap("embed_query", encoder.embed_query)
    original_retrieve = hybrid.GovernedRetriever.retrieve
    if arguments.cprofile:
        import cProfile
        import io
        import pstats

        def profiled(self, *args, **kwargs):
            profile = cProfile.Profile()
            try:
                return profile.runcall(original_retrieve, self, *args, **kwargs)
            finally:
                stream = io.StringIO()
                pstats.Stats(profile, stream=stream).sort_stats("tottime").print_stats(15)
                print("\n".join(line[:170] for line in stream.getvalue().splitlines()[4:30]))
        hybrid.GovernedRetriever.retrieve = timer.wrap("retrieve_total", profiled)
    else:
        hybrid.GovernedRetriever.retrieve = timer.wrap("retrieve_total", original_retrieve)
    counter = timer.wrap("count_tokens", runtime.token_counter)
    snapshot = runtime.manager.journal.snapshot()
    access = AccessContext(principal_id="profile", credential_id="profile", permissions=["query"],
                           policy_epoch=snapshot.policy_epoch, access_scope_digest="profile")
    for question, instrument in cases:
        timer.totals.clear()
        started = time.perf_counter()
        pinned = timer.wrap("pin", runtime.manager.pin)(access)
        outcome = GovernedAnswerService(pinned, generator=runtime.generator, config=runtime.config,
                                        token_counter=counter).answer(
            AnswerRequest(question=question, plan=plan_query(question, instrument_ids=[instrument])))
        print(json.dumps({"instrument": instrument, "total_s": round(time.perf_counter() - started, 2),
                          "status": outcome.payload.status, "stages": timer.report()}))


if __name__ == "__main__":
    main()
