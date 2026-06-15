from dataclasses import dataclass
from typing import Literal


@dataclass
class Recipient:
    name: str
    role: Literal["traveler", "colleague", "client", "private"]
    whatsapp_number: str  # E.164 format, e.g. "+4917123456789"


@dataclass
class DisruptionEvent:
    traveler_name: str
    original_train: str         # e.g. "ICE 591 München → Berlin"
    delay_minutes: int
    reroute_summary: str        # e.g. "New route via Frankfurt, ICE 543, dep 14:12"
    meeting_time_original: str  # e.g. "16:00"
    meeting_time_new: str       # e.g. "16:45"
    recipients: list[Recipient]
