"""The learning engine's feed (Chat 3): sources, ingest, story clustering and
Haiku summaries. Scheduling, persistence and error handling stay in core/
(system jobs via core/jobs.run_tracked, SQL in core/ledger.py, embeddings via
core/embed.py, Claude via core/ai.py); this package holds the feed's own
logic, as sports/nfl holds NFL's.
"""
