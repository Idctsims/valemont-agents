"""BaseAgent — the shared loop. Adapters inherit; they do not reimplement.

    observe() -> form_thesis() -> build_commitment() -> [wait] -> resolve()

Scheduling, persistence, scoring, event emission, retries and error handling
belong here and are written exactly once. An adapter that starts growing its
own version of any of that is a signal the abstraction is wrong.

TODO(session 2): abstract base + the run cycle.
"""
