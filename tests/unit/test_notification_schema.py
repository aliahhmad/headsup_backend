"""An event type selects its exact payload instead of using substring guesses."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.notification import notification_event

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "event",
    [
        {"type": "alert.published", "payload": {"monitor_id": str(uuid4())}},
        {"type": "alert.published", "payload": {"alert_id": "not-a-uuid"}},
        {"type": "alert.other", "payload": {"alert_id": str(uuid4())}},
        {
            "type": "monitor.paused",
            "payload": {
                "monitor_id": str(uuid4()),
                "monitor_version": 1,
                "previous_status": "paused",
            },
        },
        {
            "type": "monitor.activated",
            "payload": {
                "monitor_id": str(uuid4()),
                "monitor_version": "1",
                "previous_status": "new",
            },
        },
        {
            "type": "monitor.activated",
            "payload": {
                "monitor_id": str(uuid4()),
                "monitor_version": 1,
                "previous_status": "new",
                "alert_id": str(uuid4()),
            },
        },
    ],
)
def test_event_payload_must_match_exact_type(event):
    with pytest.raises(ValidationError):
        notification_event.validate_python(event)
