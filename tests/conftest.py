"""Shared fixtures for SuperGrok OAuth tests."""

from __future__ import annotations

import threading

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in tests."""
    return


@pytest.fixture(autouse=True)
def expected_lingering_timers():
    """Allow brief aiohttp shutdown timers during HA test teardown."""
    return True


@pytest.fixture(autouse=True)
def ignore_aiohttp_shutdown_threads(monkeypatch):
    """Ignore aiohttp's daemon safe-shutdown helper threads in PHACC cleanup."""
    real_enumerate = threading.enumerate

    def _enumerate():
        return [
            thread
            for thread in real_enumerate()
            if "_run_safe_shutdown_loop" not in thread.name
        ]

    monkeypatch.setattr(threading, "enumerate", _enumerate)


# Example /models answer. Tests use these ids as data; the integration ships none.
LIVE_MODELS = ["grok-4.6", "grok-4.5", "grok-imagine-image-2.0"]


@pytest.fixture(autouse=True)
def live_models(monkeypatch):
    """First-run picker sees an example account model list instead of the network."""
    from unittest.mock import AsyncMock

    from custom_components.supergrok.config_flow import GrokOAuthConfigFlow

    mock = AsyncMock(return_value=list(LIVE_MODELS))
    monkeypatch.setattr(GrokOAuthConfigFlow, "_async_live_models", mock)
    return mock
