"""Concurrent-user latency probe for the MVP API (operator tool, not an SLO).

    python -m app.loadtest --users 1 3 5 --rounds 4

Each simulated user sends distinct questions (instrument-filtered, so they do
not hit the response cache) and waits for the answer before the next one. The
key comes from the local .env and is never printed; questions are generic.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import statistics
import time

import httpx

from app.client import ClientSettings

TOPICS = ("Qual o valor dos honorários?", "Qual o prazo de vigência do contrato?", "Qual o foro eleito?",
          "Em quais hipóteses o contrato pode ser rescindido?", "Qual a forma de pagamento?",
          "Qual o objeto do contrato?", "Há multa por descumprimento?", "Como é feito o reajuste?")


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def summarize(samples):
    """samples: list of (seconds, http_status, answer_status). Errors are any non-200."""
    latencies = [seconds for seconds, http, _ in samples if http == 200]
    statuses = {}
    for _, http, status in samples:
        key = status if http == 200 else f"http_{http}"
        statuses[key] = statuses.get(key, 0) + 1
    return {"requests": len(samples), "ok": len(latencies), "errors": len(samples) - len(latencies),
            "p50_s": percentile(latencies, .5), "p95_s": percentile(latencies, .95),
            "max_s": max(latencies) if latencies else None,
            "mean_s": statistics.fmean(latencies) if latencies else None, "outcomes": statuses}


def plan_questions(instruments, users, rounds, offset=0):
    """Distinct (question, instrument) pairs per user and round."""
    pairs = [(topic, instrument) for instrument in instruments for topic in TOPICS]
    needed = users * rounds
    if len(pairs) < needed + offset:
        raise ValueError("not_enough_distinct_questions")
    chosen = pairs[offset:offset + needed]
    return [chosen[user::users] for user in range(users)]


def run(base_url, key, instruments, users, rounds, *, offset=0, transport=None):
    work = plan_questions(instruments, users, rounds, offset)
    samples = []

    def user_session(questions):
        local = []
        with httpx.Client(base_url=base_url, timeout=90, transport=transport) as client:
            for question, instrument in questions:
                started = time.perf_counter()
                response = client.post("/chat", headers={"X-API-Key": key},
                                       json={"message": question, "filters": {"instrument_ids": [instrument]}})
                elapsed = time.perf_counter() - started
                status = response.json().get("status") if response.status_code == 200 else None
                local.append((elapsed, response.status_code, status))
        return local

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=users) as pool:
        for result in pool.map(user_session, work):
            samples.extend(result)
    summary = summarize(samples)
    summary.update(users=users, rounds=rounds, wall_s=time.perf_counter() - started)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, nargs="+", default=[1, 3, 5])
    parser.add_argument("--rounds", type=int, default=4)
    arguments = parser.parse_args(argv)
    settings = ClientSettings()
    catalog = json.loads(settings.rag_mvp_catalog.read_text(encoding="utf-8"))
    instruments = [entry["instrument_id"] for entry in catalog]
    offset, results = int(time.time()) % 97, []
    for users in arguments.users:
        summary = run(settings.rag_mvp_url, settings.rag_mvp_query_key.get_secret_value(), instruments,
                      users, arguments.rounds, offset=offset)
        offset += users * arguments.rounds
        results.append(summary)
        print(json.dumps(summary, ensure_ascii=False))
    return results


if __name__ == "__main__":
    main()
