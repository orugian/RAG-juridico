"""Load probe helpers: distinct questions per user and honest error accounting."""
import json

import httpx
import pytest

from app.loadtest import percentile, plan_questions, run, summarize


def test_questions_are_distinct_across_users_and_rounds():
    work = plan_questions([f"doc:{n}" for n in range(3)], users=3, rounds=4)
    flat = [pair for user in work for pair in user]
    assert len(work) == 3 and all(len(user) == 4 for user in work)
    assert len(set(flat)) == 12
    with pytest.raises(ValueError):
        plan_questions(["doc:1"], users=5, rounds=4)


def test_summary_counts_errors_separately_from_latency():
    summary = summarize([(1.0, 200, "answered"), (3.0, 200, "abstained"), (30.0, 504, None)])
    assert summary["ok"] == 2 and summary["errors"] == 1
    assert summary["outcomes"] == {"answered": 1, "abstained": 1, "http_504": 1}
    assert summary["max_s"] == 3.0 and percentile([], .5) is None


def test_run_sends_key_header_and_filtered_distinct_questions():
    seen = []

    def handler(request):
        seen.append((request.headers["X-API-Key"], json.loads(request.content)))
        return httpx.Response(200, json={"status": "answered"})

    summary = run("http://rag.test", "k" * 40, ["doc:1", "doc:2"], users=2, rounds=3,
                  transport=httpx.MockTransport(handler))
    assert summary["requests"] == 6 and summary["errors"] == 0
    assert all(key == "k" * 40 for key, _ in seen)
    assert len({(body["message"], tuple(body["filters"]["instrument_ids"])) for _, body in seen}) == 6
