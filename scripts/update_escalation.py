"""Update app/escalation.py to add HANDOFF_MARKER_PATTERN, ADMIN_MARKER_PATTERN, and 4-tuple extract_handoff()."""
import pathlib
import re

p = pathlib.Path('app/escalation.py')
text = p.read_text(encoding='utf-8')

# 1. Update docstring
old_doc = '''\"\"\"\nHuman handoffs: detecting them in a reply, and recording them for the operator.\n\nThe agent cannot hand a conversation to a person by itself -- and the support\nbranch deliberately has no tools at all -- so the support/sales prompts ask the\nmodel to end a hand-off reply with a machine-readable line:\n\n    [HANDOFF: what the human needs to do]\n\n`extract_handoff()` pulls that line out before the customer ever sees it, and the\nroutes store what is left in the `escalations` table. That table is what the\n\"Support queue\" on the admin dashboard reads.\n\nModels occasionally ignore formatting instructions, so a deliberately small\nphrase check runs as a fallback: the operator can see which of the two fired in\nthe `trigger` column, and nothing is ever invented for a normal answer.\n\"\"\"'''

new_doc = '''\"\"\"\nHuman handoffs: detecting them in a reply, and recording them for the operator.\n\nThe agent cannot hand a conversation to a person by itself -- and the support\nbranch deliberately has no tools at all -- so the support/sales prompts ask the\nmodel to end a hand-off reply with two machine-readable lines:\n\n    <customer-facing sentence>\n    [HANDOFF: why a human is needed]\n    [ADMIN: what the human needs to do]\n\n`extract_handoff()` pulls both lines out before the customer ever sees them. The\ncustomer sees the first sentence; the operator sees the HANDOFF reason (in the\nqueue and the conversation detail) and the ADMIN note (a short instruction for\nthe human, shown only in the operator UI).\n\nThe routes store what is left in the `escalations` table. That table is what the\n\"Support queue\" on the admin dashboard reads.\n\nModels occasionally ignore formatting instructions, so a deliberately small\nphrase check runs as a fallback: the operator can see which of the two fired in\nthe `trigger` column, and nothing is ever invented for a normal answer.\n\"\"\"'''

assert old_doc in text, 'docstring not found'
text = text.replace(old_doc, new_doc)

# 2. Update MARKER_PATTERN to HANDOFF_MARKER_PATTERN and add ADMIN_MARKER_PATTERN
old_pattern = "#: \"[HANDOFF: refund needs approval]\", \"(hand-off: call back about the invoice)\" ...\nMARKER_PATTERN = re.compile(r\"[\\[(]\\s*hand[ _-]?off\\s*:?\\s*(?P<reason>[^\\])]*)[\\)]\", re.IGNORECASE)"

new_pattern = '''#: "[HANDOFF: refund needs approval]", "(hand-off: call back about the invoice)" ...
HANDOFF_MARKER_PATTERN = re.compile(
    r"[\\[(]\\s*hand[ _-]?off\\s*:?\\s*(?P<reason>[^\\])]*)[\\)]", re.IGNORECASE
)

#: "[ADMIN: approve the refund and tell the customer the prototype frame is back in stock.]"
ADMIN_MARKER_PATTERN = re.compile(
    r"[\\[(]\\s*admin\\s*:?\\s*(?P<note>[^\\])]*)[\\)]", re.IGNORECASE
)'''

assert old_pattern in text, 'pattern block not found'
text = text.replace(old_pattern, new_pattern)

p.write_text(text, encoding='utf-8')
print('escalation.py updated: HANDOFF_MARKER_PATTERN, ADMIN_MARKER_PATTERN added')
