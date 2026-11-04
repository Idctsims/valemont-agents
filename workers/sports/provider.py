"""Where sports events come from: ESPN, with API-Sports as a documented
fallback that is wired to nothing (docs/sports_data.md §7).

`SPORTS_PROVIDER` selects one: `espn` (the default) or `api_sports`. The
second refuses loudly at the job's start: there is no key and no
implementation in Phase 1 (owner: only if ESPN fails us). The switch:
get the API-Sports free key, implement ApiSportsProvider.events against the
same SportsEvent rows, set API_SPORTS_KEY and SPORTS_PROVIDER=api_sports on
Railway.
"""

from __future__ import annotations

from datetime import date
from typing import Callable, Mapping, Protocol, Sequence

from sports.espn import Espn, Http, League, SportsEvent, httpx_get

ENV = "SPORTS_PROVIDER"


class ProviderUnavailable(RuntimeError):
    """The selected provider cannot run (no key, not built, unknown name)."""


class Provider(Protocol):
    name: str
    calls: int
    wire_bytes: int

    def events(self, lg: League, days: Sequence[date]) -> list[SportsEvent]: ...

    def close(self) -> None: ...


class EspnProvider:
    name = "espn"

    def __init__(self, http: Http | None = None, *, sleep: Callable[[float], None] | None = None) -> None:
        self._close: Callable[[], None] | None = None
        if http is None:
            http, self._close = httpx_get()
        self._espn = Espn(http, sleep=sleep) if sleep is not None else Espn(http)

    @property
    def calls(self) -> int:
        return self._espn.calls

    @property
    def wire_bytes(self) -> int:
        return self._espn.wire_bytes

    def events(self, lg: League, days: Sequence[date]) -> list[SportsEvent]:
        return self._espn.events(lg, days)

    def close(self) -> None:
        if self._close is not None:
            self._close()
            self._close = None


class ApiSportsProvider:
    """The fallback's place in the code. Not built: constructing it refuses."""

    name = "api_sports"

    def __init__(self, env: Mapping[str, str]) -> None:
        if not env.get("API_SPORTS_KEY", "").strip():
            raise ProviderUnavailable("SPORTS_PROVIDER=api_sports, but API_SPORTS_KEY is not set "
                                      "(docs/sports_data.md §7)")
        raise ProviderUnavailable("SPORTS_PROVIDER=api_sports: the API-Sports provider is not built yet "
                                  "(docs/sports_data.md §7)")


def provider_from_env(env: Mapping[str, str], *, http: Http | None = None) -> Provider:
    name = env.get(ENV, "").strip().lower() or "espn"
    if name == "espn":
        return EspnProvider(http)
    if name == "api_sports":
        return ApiSportsProvider(env)  # type: ignore[return-value]  # always raises
    raise ProviderUnavailable(f"{ENV}={name!r}: expected espn or api_sports")
