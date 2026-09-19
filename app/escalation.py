"""
Human handoffs: detecting them in a reply, and recording them for the operator.

The agent cannot hand a conversation to a person by itself -- and the support
branch deliberately has no tools at all -- so the support/sales prompts ask the
model to end a hand-off reply with a machine-readable line:

    [HANDOFF: what the human needs to do]

`extract_handoff()` pulls that line out before the customer ever sees it, and the
routes store what is left in the `escalations` table. That table is what the
"Support queue" on the admin dashboard reads.

Models occasionally ignore formatting instructions, so a deliberately small
phrase check runs as a fallback: the operator can see which of the two fired in
the `trigger` column, and nothing is ever invented for a normal answer.
"""
import re

from app.extensions import db
from app.models import ESCALATION_OPEN, Escalation

#: "[HANDOFF: refund needs approval]", "(hand-off: call back about the invoice)" ...
MARKER_PATTERN = re.compile(r"[\[(]\s*hand[ _-]?off\s*:?\s*(?P<reason>[^\])]*)[\])]", re.IGNORECASE)

#: Fallback wording that clearly promises a human follow-up. Kept short and
#: specific on purpose -- an ordinary answer must never end up in the queue.
HANDOFF_PHRASES = (
    "connect you with a human",
    "connect you to a human",
    "connect you with one of our",
    "connect you with our",
    "connecting you with",
    "team member right away",
    "pass you to a human",
    "pass this to a",
    "passed this to",
    "forwarded your",
    "forwarded this",
    "forward this to",
    "escalate this",
    "escalated this",
    "human agent",
    "human teammate",
    "human will reach out",
    "specialist will reach out",
    "reach out to you shortly",
    "someone from our team will",
)


def _without_marker(text: str, match) -> str:
    """Drop the marker, and the whole line with it when it sits on its own line."""
    start, end = match.span()
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    if not text[line_start:start].strip() and not text[end:line_end].strip():
        cleaned = text[:line_start] + text[line_end:]
        return re.sub(r"\n{3,}", "\n\n", cleaned).rstrip()
    return (text[:start] + text[end:]).strip()


def extract_handoff(text):
    """Split a reply into (customer-facing text, reason, trigger).

    `trigger` is "marker" when the model followed the format, "phrase" when the
    fallback wording matched, and None for an ordinary answer (reason is then
    also None). A marker without text after it yields an empty reason.

    When only the fallback wording matches (no marker), the stored reason is a
    generic placeholder so the operator still gets a useful line instead of the
    raw matched phrase.
    """
    if not text:
        return text, None, None

    match = MARKER_PATTERN.search(text)
    if match:
        reason = " ".join(match.group("reason").split())
        return _without_marker(text, match), reason, "marker"

    lowered = text.lower()
    for phrase in HANDOFF_PHRASES:
        if phrase in lowered:
            return text, "Customer requested human assistance", "phrase"

    return text, None, None


def record_handoff(conversation_id: int, handoff: dict, message_id: int = None):
    """Queue a hand-off for the operator; returns the Escalation row or None.

    A conversation keeps at most one *open* row: if the agent promises a human
    twice in the same thread, the operator gets one queue item with the newest
    reason rather than two duplicate ones.
    """
    if not handoff or not handoff.get("trigger"):
        return None

    reason = (handoff.get("reason") or "").strip()
    existing = Escalation.query.filter_by(conversation_id=conversation_id, status=ESCALATION_OPEN).first()
    if existing:
        if reason:
            existing.reason = reason
        existing.message_id = message_id or existing.message_id
        db.session.commit()
        return existing

    escalation = Escalation(
        conversation_id=conversation_id,
        message_id=message_id,
        reason=reason,
        trigger=handoff.get("trigger") or "marker",
        status=ESCALATION_OPEN,
    )
    db.session.add(escalation)
    db.session.commit()
    return escalation
