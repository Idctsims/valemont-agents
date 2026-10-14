"""The owner's streaming services as TMDB US provider IDs, and the
"Tonight" split (owner, 2026-10-14).

Matched live against GET /watch/providers/{tv,movie}?watch_region=US on
2026-10-14 (docs/screening.md §2). Ads tiers of the owner's services count
as the owner's; the same service resold as an Amazon, Roku or Apple TV
"channel" does NOT (a separate subscription), nor does Amazon Video (10),
which is the rent/buy store. TMDB has no standalone US Showtime provider any
more: Showtime is inside Paramount+ Premium (2303).

The web's twin is apps/web/src/lib/tmdb/services.ts; tests/test_screening.py
fails if the two ever differ.
"""

from __future__ import annotations

from typing import Any, Mapping

#: provider_id -> the owner's service it belongs to.
MY_SERVICES: dict[int, str] = {
    8: "Netflix",
    1796: "Netflix",       # Netflix Standard with Ads
    175: "Netflix",        # Netflix Kids (same subscription)
    1899: "HBO Max",
    2303: "Paramount+",    # Paramount Plus Premium (includes Showtime)
    2616: "Paramount+",    # Paramount Plus Essential (the ads tier)
    43: "Starz",
    9: "Prime Video",
    2100: "Prime Video",   # Amazon Prime Video with Ads
    613: "Prime Video",    # Amazon Prime Video Free with Ads
}

#: The subscription kinds TMDB lists under "flatrate", "ads" and "free":
#: watchable tonight with no purchase.
STREAM_KINDS: tuple[str, ...] = ("flatrate", "ads", "free")
PAY_KINDS: tuple[str, ...] = ("rent", "buy")


def compact(us: Mapping[str, Any] | None) -> dict[str, Any]:
    """TMDB's US watch/providers object as stored on titles.providers: each
    kind a list of {id, name, logo}, plus TMDB's link (to its JustWatch-
    powered page)."""
    out: dict[str, Any] = {}
    if not us:
        return out
    for kind in STREAM_KINDS + PAY_KINDS:
        rows = us.get(kind)
        if isinstance(rows, list) and rows:
            out[kind] = [{"id": int(p["provider_id"]), "name": str(p.get("provider_name") or ""),
                          "logo": p.get("logo_path")} for p in rows if isinstance(p, dict) and "provider_id" in p]
    if isinstance(us.get("link"), str):
        out["link"] = us["link"]
    return out


def split(providers: Mapping[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """{tonight, other_stream, rent, buy}: TONIGHT is every streaming offer
    (flatrate, ads, free) on one of the owner's services, one per service;
    other_stream is the rest of the streaming offers; rent and buy as listed.
    A provider appears once per group."""
    p = providers or {}
    tonight: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    seen_service: set[str] = set()
    seen_other: set[int] = set()
    for kind in STREAM_KINDS:
        for row in p.get(kind) or []:
            pid = int(row.get("id"))
            service = MY_SERVICES.get(pid)
            if service is not None:
                if service not in seen_service:
                    seen_service.add(service)
                    tonight.append({**row, "service": service, "kind": kind})
            elif pid not in seen_other:
                seen_other.add(pid)
                other.append({**row, "kind": kind})
    return {"tonight": tonight, "other_stream": other,
            "rent": list(p.get("rent") or []), "buy": list(p.get("buy") or [])}
