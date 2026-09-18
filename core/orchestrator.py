"""Scheduler and agent registry. One persistent process, one place timing lives.

Crypto ticks on an interval, equities wake on market hours, props fire against
slate times. APScheduler holds those rules. This process is the thing that is
alive 24/7 on Railway.

TODO(session 2): registry + APScheduler wiring.
"""
