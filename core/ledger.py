"""All database access lives here. If SQL appears anywhere else, that's a bug.

Deliberately thin and boring. Every write an agent makes goes through one of
these functions, which is what lets us guarantee shape and keep the Supabase
switch to a single environment variable.

TODO(session 2): start_run, end_run, commit, add_leg, resolve, emit_event.
"""
