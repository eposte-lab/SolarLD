"""Unit tests for compliance hash helpers — deterministic + normalized."""

from __future__ import annotations

from src.agents.compliance import ComplianceAgent


def test_hash_b2b_deterministic() -> None:
    a = ComplianceAgent.hash_b2b("Acme Srl", "12345678901")
    b = ComplianceAgent.hash_b2b("Acme Srl", "12345678901")
    assert a == b
    assert len(a) == 64


def test_hash_b2b_case_insensitive() -> None:
    a = ComplianceAgent.hash_b2b("ACME SRL", "12345678901")
    b = ComplianceAgent.hash_b2b("acme srl", "12345678901")
    assert a == b


def test_hash_b2c_distinct() -> None:
    a = ComplianceAgent.hash_b2c("Mario Rossi", "Via Roma 1, Napoli")
    b = ComplianceAgent.hash_b2c("Luigi Verdi", "Via Roma 1, Napoli")
    assert a != b
