"""Agent modules — one per domain (Hunter, Identity, Scoring, ...).

Each agent exposes `async run(input) -> output` and is idempotent:
same input should produce the same output or a no-op.
"""
