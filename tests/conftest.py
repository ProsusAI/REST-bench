"""Pytest fixtures for the REST simulation.

Every test that touches engine state runs in an isolated `REST_GAME_DIR`
tmp dir, with the dashboard disabled so no background server is spawned.
"""
from __future__ import annotations

import os
import pytest


@pytest.fixture(autouse=True)
def _disable_dashboard(monkeypatch):
    monkeypatch.setenv("REST_DASHBOARD_DISABLE", "1")


@pytest.fixture
def game_dir(tmp_path, monkeypatch):
    """Per-test isolated game state directory."""
    d = tmp_path / "game"
    d.mkdir()
    monkeypatch.setenv("REST_GAME_DIR", str(d))
    yield str(d)
