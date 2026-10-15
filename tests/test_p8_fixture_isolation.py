"""Behavioral pytest fixture regressions, not product/Qwen qualification.

Run the actual E2E consumer collection in a fresh process. Bodies and the costly
encoder are replaced only here to measure setup failures and successful reuse;
the offline integral separately exercises the unchanged physical oracles.
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("mode,failure", [
    ("embedded", "first_build"), ("embedded", "every_build"),
    ("server", "first_build"), ("server", "every_build"), ("server", "first_start"),
])
def test_build_failure_is_local_to_current_consumer_and_only_success_is_reused(mode, failure):
    root = Path(__file__).resolve().parents[1]
    script = r'''
import json
import sys
import pytest
class Measure:
    def __init__(self):
        self.calls = 0
        self.reports = []
        self.environment = object()
        self.roots = []
        self.starts = self.stops = 0
    @pytest.hookimpl(tryfirst=True)
    def pytest_fixture_setup(self, fixturedef, request):
        if fixturedef.argname == "qwen":
            value = (None, None)
            fixturedef.cached_result = (value, fixturedef.cache_key(request), None)
            return value
    def pytest_collection_modifyitems(self, items):
        owner = self
        class Server:
            port = 12345
            def __init__(self, root):
                owner.roots.append(str(root))
            def start(self):
                owner.starts += 1
                if sys.argv[1] == "first_start" and owner.starts == 1:
                    raise RuntimeError("controlled_build_failure")
            def stop(self):
                owner.stops += 1
        def build(root, **kwargs):
            self.calls += 1
            if sys.argv[2] == "embedded":
                self.roots.append(str(root))
            if sys.argv[1] == "every_build" or (sys.argv[1] == "first_build" and self.calls == 1):
                raise RuntimeError("controlled_build_failure")
            return self.environment
        def consumer(**kwargs):
            assert kwargs["physical"][0] is self.environment
        for item in items:
            item.module.build_p8_environment = build
            item.module.LocalServer = Server
            item._obj = consumer
    def pytest_runtest_logreport(self, report):
        self.reports.append((report.when, report.outcome,
                             "controlled_build_failure" in str(report.longrepr)))
p = Measure()
code = pytest.main(["tests/test_p8_e2e.py", "-q", "-k", sys.argv[2], "--tb=no",
                    "-p", "no:cacheprovider"], plugins=[p])
print("FIXTURE_MEASURE " + json.dumps({
    "exit": int(code), "calls": p.calls,
    "setup_errors": sum(w == "setup" and o == "failed" for w,o,_ in p.reports),
    "controlled_errors": sum(w == "setup" and o == "failed" and c for w,o,c in p.reports),
    "passed": sum(w == "call" and o == "passed" for w,o,_ in p.reports),
    "roots": len(p.roots), "unique_roots": len(set(p.roots)),
    "starts": p.starts, "stops": p.stops}))
'''
    completed = subprocess.run([sys.executable, "-c", script, failure, mode], cwd=root,
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    line = next(line for line in completed.stdout.splitlines() if line.startswith("FIXTURE_MEASURE "))
    observed = json.loads(line.removeprefix("FIXTURE_MEASURE "))
    expected = ({"exit": 1, "calls": 23, "setup_errors": 23, "controlled_errors": 23, "passed": 0}
                if failure == "every_build" else
                {"exit": 1, "calls": 1 if failure == "first_start" else 2,
                 "setup_errors": 1, "controlled_errors": 1, "passed": 22})
    attempts = 23 if failure == "every_build" else 2
    expected.update(roots=attempts, unique_roots=attempts,
                    starts=attempts if mode == "server" else 0,
                    stops=attempts if mode == "server" else 0)
    assert observed == expected, {"observed": observed, "expected": expected, "output": completed.stdout}
    print("FIXTURE_ISOLATION " + json.dumps({"mode": mode, "failure": failure, **observed}, sort_keys=True))
