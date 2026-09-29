"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _no_network_link_checks(monkeypatch):
    """discover() verifies top picks' links over HTTP; tests never touch the network, so every
    check reads as inconclusive (kept). Tests that exercise the checker patch it themselves."""
    from copilot import linkcheck
    monkeypatch.setattr(linkcheck, "check_url", lambda url, timeout=15.0: "unknown")
