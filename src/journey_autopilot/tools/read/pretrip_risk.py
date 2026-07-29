"""Risk tools: the pre-trip delay assessment (before a journey has started).

Three complementary sources, and the Monitoring agent is meant to combine them:
the multi-month historical baseline (``get_historical_delay_baseline``), today's
actual situation on the route (``get_recent_delay_history``), and the scheduled
times that anchor the ETA (``get_planned_connection``). Every one of them is
live-first with a simulated fallback, and reports which it used in ``source``.

All three compute nothing themselves — the statistics live in the ``risk``
package (``risk.predictor`` for the baseline, ``risk.live_stats`` for the
board aggregation); this module is only the live-or-mock wrapper that turns
them into agent tools.
"""

from __future__ import annotations

from ... import risk
from ...demo import mock_data
from ...errors import with_resilience
from ...risk import live_stats


def get_historical_delay_baseline(origin: str, destination: str, train: str = "") -> dict:
    """Returns the pre-trip risk forecast (historical baseline) for a connection.

    Scores how delay-prone the connection normally is, from the historical DB
    punctuality archive (piebro/deutsche-bahn-data) via the risk module — the
    reliable "normal case" for the pre-trip assessment. Pair it with
    ``get_recent_delay_history`` (today's situation) and
    ``get_planned_connection`` (the scheduled-arrival ETA anchor).

    Args:
        origin: Departure station (context only; the arrival at the destination is scored).
        destination: Destination station, e.g. "Berlin Hbf".
        train: Optional train name (e.g. "ICE 1006") — determines the train type;
            omitted falls back to the station-wide baseline.

    Returns:
        A dict with ``risk_level`` (LOW/MEDIUM/HIGH), ``risk_score`` (0-100),
        ``expected_delay_minutes``, ``confidence`` (from sample size), ``factors``
        (a plain-language note), and ``source``. Returns an error if the route
        cannot be forecasted.
    """
    try:
        trip = {"origin": origin, "destination": destination, "train": train}
        # forecast_leg scores from destination + train type; times are not read.
        legs = [
            {
                "origin": {"name": origin},
                "destination": {"name": destination},
                "train": train,
                "current_delay_minutes": 0,
            }
        ]

        forecasts = risk.forecast_trip(trip, legs)
        if forecasts:
            forecast = forecasts[0]
            return {
                "origin": origin,
                "destination": destination,
                "train": train,
                "risk_level": forecast.get("level", "medium").upper(),
                "risk_score": forecast.get("risk_score", 50),
                "expected_delay_minutes": forecast.get("expected_delay_minutes", 0),
                "confidence": forecast.get("confidence", 0.5),
                "factors": forecast.get("factors", []),
                "source": forecast.get("source", "db_history"),
            }
        else:
            return {
                "origin": origin,
                "destination": destination,
                "error": "Could not compute risk forecast for this connection.",
            }
    except Exception as e:
        return {
            "origin": origin,
            "destination": destination,
            "error": f"Risk forecast error: {e}",
        }


def recent_delay_history(
    origin: str, destination: str, train: str = "", *, details: bool = False
) -> dict:
    """Shared live/fallback resolution for the connection delay history.

    Live sidecar first (arrival board at the destination), simulated history on
    failure or an empty sample. ``details=True`` asks the live source for the
    individual considered arrivals (``samples``) — used by the verbose risk
    scenario; the agent-facing tool keeps it off to stay context-lean.
    """
    def _primary() -> dict:
        stats = live_stats.connection_delay_history(
            origin, destination, train=train, details=details
        )
        stats["source"] = "db_service_live"
        return stats

    def _fallback() -> dict:
        mock = mock_data.CONNECTION_DELAY_HISTORY.get((origin, destination))
        if mock is None:
            return {
                "origin": origin,
                "destination": destination,
                "error": "No delay history available for this connection.",
            }
        result = dict(mock)
        result.update(
            {"origin": origin, "destination": destination, "train": train or None, "source": "mock_history"}
        )
        return result

    # An empty sample (sample_count == 0) counts as a miss, just like an
    # unreachable sidecar.
    return with_resilience(
        _primary,
        _fallback,
        tool="get_recent_delay_history",
        accept=lambda r: r.get("sample_count", 0) > 0,
    ).value


def get_recent_delay_history(origin: str, destination: str, train: str = "") -> dict:
    """Returns delay metrics for a connection from the LAST FEW HOURS.

    Today's situation on the route, not its long-term norm: how punctually have
    the trains on this connection arrived in the rolling window the DB feed
    still carries actual delays for (roughly the last 5-6 hours)? Use it to
    detect that today is unusually bad; for the normal case use
    ``get_historical_delay_baseline``, which reads a multi-month archive.

    First tries real DB data via the db_service sidecar (arrival board at the
    destination); if the sidecar is unreachable or returns no sample, a
    simulated history is used. The ``source`` field makes transparent where the
    numbers come from.

    Args:
        origin: Departure station, e.g. "Munich Hbf".
        destination: Destination station, e.g. "Berlin Hbf".
        train: Optional train name (e.g. "ICE 1006"), context only.

    Returns:
        A dict with ``sample_count``, mean/median/p90 delay, punctuality rate,
        cancellations, most common causes, and ``source`` ("db_service_live" |
        "mock_history"). Contains "error" if neither live nor mock data is
        available for the connection.
    """
    return recent_delay_history(origin, destination, train)


def get_planned_connection(origin: str, destination: str, departure: str = "") -> dict:
    """Returns the planned connection (scheduled times) as the anchor for the ETA.

    The risk module needs the scheduled arrival time to derive the expected
    arrival (ETA = scheduled arrival + expected delay). Tries real DB data via
    the db_service sidecar; otherwise falls back to simulated scheduled times.

    Args:
        origin: Departure station, e.g. "Munich Hbf".
        destination: Destination station, e.g. "Berlin Hbf".
        departure: Optional departure time (ISO "YYYY-MM-DDTHH:MM:SS"); empty =
            next connection.

    Returns:
        A dict with ``train``, ``planned_departure``, ``planned_arrival``,
        ``transfers``, any real-time arrival delay, and ``source``. Contains
        "error" if no connection was found.
    """
    def _primary() -> dict | None:
        conn = live_stats.scheduled_connection(origin, destination, departure or None)
        if conn:
            conn["source"] = "db_service_live"
        return conn  # None (no journey found) is rejected -> fall back

    def _fallback() -> dict:
        mock = mock_data.PLANNED_CONNECTIONS.get((origin, destination))
        if mock is None:
            return {
                "origin": origin,
                "destination": destination,
                "error": "No planned connection found for this route.",
            }
        result = dict(mock)
        result.update({"origin": origin, "destination": destination, "source": "mock_planned"})
        return result

    return with_resilience(_primary, _fallback, tool="get_planned_connection").value
