"""Kalshi venue layer: shared by every Kalshi agent in every sport.

Client, fee schedule, edge gate and the `KalshiContractAgent` base class that
implements `resolve()` and `capture_close()` once. Sport modules supply only
`observe()`, `form_thesis()` and `build_commitment()` (docs/kalshi_nfl.md §2).
"""
