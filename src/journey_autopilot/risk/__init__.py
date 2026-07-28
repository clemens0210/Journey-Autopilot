"""Delay statistics and risk forecasting for monitored journeys — the domain model.

All of it is deterministic Python; the agents interpret the numbers, they never
compute them. Two complementary sources:

- ``delay_reference`` + ``predictor`` — the multi-month historical baseline
  (built by ``scripts/build_delay_stats.py`` from piebro/deutsche-bahn-data),
  i.e. how delay-prone a route normally is. Re-exported below.
- ``live_stats`` — today's actual situation, aggregated from the DB arrival
  board via the db_service sidecar. Kept out of these re-exports so that
  importing the package does not drag in the sidecar client; import it
  explicitly as ``from ...risk import live_stats``.

The agent-facing tool wrappers around both live in ``tools/read/pretrip_risk.py``.
"""

from .predictor import connection_risks, forecast_trip, live_connection_risks, missed_connections

__all__ = ["forecast_trip", "connection_risks", "live_connection_risks", "missed_connections"]
