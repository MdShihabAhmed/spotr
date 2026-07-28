"""Stateful tutor agent. State is reconstructed from Postgres per request (deliberately
NOT a LangGraph checkpointer) — simpler and it survives restarts."""
