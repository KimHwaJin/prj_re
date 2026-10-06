"""Run application boundary: admission, execution, cancellation and projection.

The public HTTP resource stays in PublicRunService. Workers use execution;
protocols preserve durable LangGraph delivery receipts. No import-time resources.
"""
