"""Select the record audit policy without mixing HR and Support histories."""
from flask import has_request_context, request
from services import hr_record_audit, support_record_audit

PROJECTION = {**hr_record_audit.PROJECTION, **support_record_audit.PROJECTION}
ATTRIBUTION_FIELDS = hr_record_audit.ATTRIBUTION_FIELDS | support_record_audit.ATTRIBUTION_FIELDS


def policy():
    if has_request_context() and request.path.startswith('/api/customer-support/'):
        return support_record_audit
    return hr_record_audit


def actor(database):
    return policy().actor(database)


def prepare(database, name, value, insert=False, query=None):
    selected = policy()
    if selected is support_record_audit:
        return selected.prepare(database, name, value, insert=insert, query=query)
    return selected.prepare(database, name, value, insert=insert)


def record(database, name, operation, documents, update=None, session=None):
    return policy().record(database, name, operation, documents, update=update, session=session)
