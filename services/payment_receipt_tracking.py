"""Public receipt audit fields, shared by profile views and printing APIs."""
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

FORMATS = ('short', 'full')


def receipt_payment_id(payment):
    if payment.get('payment_id'):
        return payment['payment_id']
    try:
        date = payment['date']
        if isinstance(date, str):
            date = datetime.strptime(date[:10], '%Y-%m-%d')
        amount = Decimal(str(payment['amount'])).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        amount_id = format(amount, 'f').rstrip('0').rstrip('.') if amount % 1 else str(int(amount))
        return f'SM{date:%d%m%Y}{amount_id}'
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return ''


def receipt_tracking(payment):
    records = payment.get('receipt_prints') or {}
    legacy_locked = not records and (payment.get('printed') or payment.get('print_status') in ('printing', 'uncertain'))
    formats = {}
    for name in FORMATS:
        record = records.get(name) or {}
        formats[name] = {key: record.get(key) for key in ('printed_at', 'print_delay_seconds')}
        formats[name].update(printed=bool(record.get('printed')),
                             status=record.get('status') or ('legacy_locked' if legacy_locked else 'pending'))
    return dict(payment_id=receipt_payment_id(payment), printed=bool(payment.get('printed')),
                payment_recorded_at=payment.get('payment_recorded_at') or payment.get('created_at'),
                printed_at=payment.get('printed_at'), print_delay_seconds=payment.get('print_delay_seconds'),
                print_status=payment.get('print_status', 'not_tracked'),
                receipt_prints=formats, legacy_print_locked=bool(legacy_locked))
