"""Offline by default, including collection: no .env, secrets or external network."""
import ipaddress
import os
import socket

import pytest

_restores = []


def pytest_addoption(parser):
    parser.addoption("--run-online", action="store_true", default=False, help="Enable explicitly marked synthetic online tests")
    parser.addoption("--qwen-artifact", default=None, help="Local verified Qwen artifact for offline tokenizer concurrency regression; never downloads")


def _local(address):
    if not isinstance(address, tuple):
        return True
    try:
        return ipaddress.ip_address(address[0]).is_loopback
    except ValueError:
        return address[0] == "localhost"


def pytest_configure(config):
    config.addinivalue_line("markers", "online: synthetic test requiring explicit --run-online")
    if config.getoption("--run-online"):
        return
    from app.config import Settings, get_settings
    for key in Settings.model_fields:
        for env_key in (key, key.upper()):
            if env_key in os.environ:
                previous = os.environ.pop(env_key)
                _restores.append(lambda k=env_key, v=previous: os.environ.__setitem__(k, v))
    for key, value in {"OPENAI_API_KEY": "offline-test-key", "APP_ENV": "test", "LANGSMITH_TRACING": "false", "LANGSMITH_TRACING_V2": "false", "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_API_KEY": "", "LANGCHAIN_API_KEY": "", "ANONYMIZED_TELEMETRY": "false"}.items():
        previous = os.environ.get(key)
        _restores.append(lambda k=key, v=previous: os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v))
        os.environ[key] = value
    previous_config = Settings.model_config
    Settings.model_config = previous_config | {"env_file": None}
    _restores.append(lambda: setattr(Settings, "model_config", previous_config))
    get_settings.cache_clear()
    for name in ("connect", "connect_ex", "sendto"):
        original = getattr(socket.socket, name)
        def guarded(self, *args, _original=original, _name=name, **kwargs):
            address = args[-1] if _name == "sendto" else args[0]
            if not _local(address):
                raise RuntimeError("offline profile blocks external network")
            return _original(self, *args, **kwargs)
        setattr(socket.socket, name, guarded)
        _restores.append(lambda n=name, o=original: setattr(socket.socket, n, o))
    original_resolve = socket.getaddrinfo
    def local_resolve(host, *args, **kwargs):
        if host is not None and not _local((host, 0)):
            raise RuntimeError("offline profile blocks external DNS")
        return original_resolve(host, *args, **kwargs)
    socket.getaddrinfo = local_resolve
    _restores.append(lambda: setattr(socket, "getaddrinfo", original_resolve))


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-online"):
        for item in items:
            if "online" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="requires explicit --run-online"))


def pytest_unconfigure(config):
    for restore in reversed(_restores):
        restore()
    _restores.clear()
