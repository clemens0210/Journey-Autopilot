"""Deutsche Bahn live data, via the ``db_service`` Node sidecar.

There is no usable public DB API for Python, so a small Node service wrapping
``db-vendo-client`` runs alongside (``cd db_service && npm start``, port 3000)
and this package is its HTTP client:

- ``ops``      — the sidecar calls themselves (journeys, arrivals, departures,
                 locations, prices) plus the normalizers that turn raw
                 ``db-vendo-client`` payloads into our internal shapes. Raises
                 ``DBServiceError`` when the sidecar is unreachable — that is
                 the signal every tool's mock fallback keys on.
- ``stations`` — station name -> EVA number resolution, with a static table in
                 front of the sidecar lookup.

Deliberately not re-exported here: both modules are used as namespaces
(``ops.journeys(...)``, ``stations.resolve_eva(...)``) and their surfaces are
too broad to keep a re-export list honest. Import the module you need.
"""
