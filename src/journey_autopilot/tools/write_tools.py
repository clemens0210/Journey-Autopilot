"""Write tools — side-effectful, gated by the policy layer.

Every tool here is classified ``write``: it changes state in the outside world
and therefore runs through ``policy.py`` (auto / ask) and the veto gate before
it fires. These are the ADK-facing wrappers the Executor and Communicator call.

STATUS: scaffold for milestone M4. The underlying side-effecting behaviour
already exists in the integrations layer — the WhatsApp sender + approval/veto
queue live in ``integrations/whatsapp.py``. M4 exposes these (and the booking /
calendar / compensation actions) as ADK FunctionTools behind the policy.

Planned write tools (build spec §5/§8):
- send_whatsapp_to_user        (auto — user is recipient + veto channel)
- send_email_to_participants   (ask — affects third parties)
- book_alternative_connection  (ask if cost over threshold)
- book_hotel                   (ask)
- reschedule_outlook_event     (auto if tentative, ask if confirmed)
- file_compensation_claim      (auto — purely beneficial)
"""

from __future__ import annotations

# TODO(M4): wrap integrations.whatsapp + the booking/calendar/compensation
# actions as ADK FunctionTools, each resolved through policy.resolve(...).
