"""Approval state shared by registration, authentication and agent management."""

UNDER_REVIEW = 'Under Review'


def agent_pending_approval(doc):
    return (str(doc.get('role', '')).lower() == 'agent' and (
        doc.get('agent_approval_status') == 'pending'
        or str(doc.get('status', '')).strip().lower() == 'under review'
    ))
