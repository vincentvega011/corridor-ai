"""Planner and LangGraph state graph; proposes actions but never executes them (INV-1).

The only code that changes sandbox state is ``corridor.executor``. Phase P1 uses temporary stubs for
policy and execution (ADR 0005); phase P2 replaces them.
"""
