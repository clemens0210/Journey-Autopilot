"""Executor Agent — executes the approved option (write).

Per the architecture (build spec §5) the Executor is the write-side agent that
carries out the option the user approved: book the alternative connection, book
a hotel, reschedule the Outlook event, file the compensation claim. Every action
runs through the policy layer (``policy.py``) and the veto gate.

STATUS: scaffold for milestone M4. Booking is deliberately NOT a tool yet — the
Planner proposes and the user keeps the veto. When the write path is built, the
Executor gets the write tools from ``tools/write_tools.py``; Monitoring and
Planner remain write-free (capability isolation).

See docs/journey-autopilot-build-spec.md §5/§8.
"""

from __future__ import annotations


def build_executor_agent():
    """Create the Executor LlmAgent.

    TODO(M4): wrap the write tools (book_alternative_connection, book_hotel,
    reschedule_outlook_event, file_compensation_claim) behind the policy layer.
    """
    raise NotImplementedError("Executor (write path) lands in M4; see build spec §5.")
