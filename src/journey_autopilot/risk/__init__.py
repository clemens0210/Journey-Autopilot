"""Pre-trip risk forecasting for monitored journeys.

This package will hold the real risk logic (delay prediction from historical
connection data, network disruptions, weather, ...). For now it ships only a
deterministic mock so the API contract and the trip-detail UI are in place —
swap ``mock_predictor`` for the real model without changing callers.
"""

from .mock_predictor import forecast_trip

__all__ = ["forecast_trip"]
