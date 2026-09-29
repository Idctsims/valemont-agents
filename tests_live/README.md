# Live-database suite

Covers what `tests/` structurally cannot: anything that lives in SQL or in the
database. The stub ledger in `tests/support.py` refuses the database on purpose,
so a defect in a query, a CHECK, a trigger or a server-computed column is
invisible to the fast suite by construction.

```
python -m unittest discover -s tests      -t .   # fast gate, offline, <1s
python -m unittest discover -s tests_live -t .   # this suite, needs Postgres
```

Run the fast one before and after every change (CLAUDE.md §6). Run this one as
well when you have touched `core/ledger.py`'s SQL or added a migration.

**Isolation.** Every row is written as `_test` (`is_test = true`, seeded by
`db/006`), quarantined from every track-record calculation. Rows are permanent —
the append-only triggers mean quarantine is the only cleanup there is, and a
test agent whose rows *could* be deleted would be testing a different database
from the real one. Each test also asserts afterwards that it wrote nothing as a
real agent.

**It skips rather than fails** when `DATABASE_URL` is unset, the project is
paused, or `db/006` has not been pasted. A red suite should mean a broken
invariant, not an unplugged cable.
