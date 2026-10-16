"""The Arsenal (Chat 5): references and Power Words, verified, dropped daily.

    cards.py    categories, rooms, the prompt, the JSON schemas, validation
    sources.py  Wikipedia, Wikiquote, kaikki/Wiktionary (no keys)
    verify.py   one item's verification (job_queue kind 'arsenal_verify')
    pool.py     the weekly Message Batch (arsenal_pool) and the top-up
    collect.py  reads an ended batch and inserts its cards (arsenal_pool_collect)
    drop.py     today's drop (arsenal_drop)

Design: docs/arsenal.md. Schema: db/054. All SQL is in core/ledger.py; every
Claude call goes through core/ai.py.
"""
