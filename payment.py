from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session, has_request_context
from flask_login import login_required, current_user
from bson import ObjectId
from datetime import datetime, timedelta, timezone
import requests
import os
import re
import uuid
import secrets
import hmac
import hashlib
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pymongo import ReturnDocument
from dotenv import load_dotenv
from urllib.parse import quote  # ✅ Correct quote function
from db import db
from services.activation_groups import get_accessible_agent_ids
from services.printer_access import has_printer_access, payment_print_defaults, agent_branch
from services.payment_printing import claim_print, finish_print
from services.payment_receipt_tracking import FORMATS, receipt_payment_id, receipt_tracking
from services.payment_account_routing import get_momo_payout_account, payout_snapshot

load_dotenv()

payment_bp = Blueprint('payment', __name__)
paystack_webhook_bp = Blueprint('paystack_webhook', __name__)

customers_collection = db["customers"]
payments_collection  = db["payments"]
users_collection     = db["users"]
sales_close_collection = db["sales_close"]  # ✅ NEW: daily rollup per agent
archived_customers_collection = db["Archived_customers"]
paystack_attempts_collection = db["paystack_payment_attempts"]

ARKESEL_API_KEY = 'RGZSU1ZDTWF4am1SQnNkZEZubkc'

PAYSTACK_CHARGE_URL = "https://api.paystack.co/charge"
PAYSTACK_SUBMIT_OTP_URL = "https://api.paystack.co/charge/submit_otp"


def _paystack_amount(amount_raw) -> int:
    """Convert GHS to pesewas without binary floating-point rounding."""
    try:
        amount = Decimal(str(amount_raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return 0
    return int(amount * 100)


def _paystack_keys():
    # Accept the conventional names plus the user's public/private naming.
    public_key = (os.getenv("PAYSTACK_PUBLIC_KEY") or "").strip()
    secret_key = (
        os.getenv("PAYSTACK_SECRET_KEY")
        or os.getenv("PAYSTACK_PRIVATE_KEY")
        or ""
    ).strip()
    return public_key, secret_key


def _normalize_momo_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("233"):
        digits = "0" + digits[3:]
    if len(digits) == 10 and digits.startswith("0"):
        return digits
    return None


def _paystack_email_for_phone(phone: str) -> str:
    return f"{phone}+smartliving@gmail.com"


def _paystack_data_matches_attempt(data: dict, attempt: dict) -> bool:
    return bool(
        data.get('status') == 'success'
        and data.get('reference') == attempt.get('reference')
        and int(data.get('amount') or 0) == int(attempt.get('amount_pesewas') or 0)
        and data.get('currency') == 'GHS'
        and data.get('channel') == 'mobile_money'
    )


def _finalize_momo_attempt(reference: str, paystack_data: dict):
    """Record a verified MoMo payment exactly once; never credit sales_close."""
    existing = payments_collection.find_one({'paystack_reference': reference})
    if existing:
        return existing, False

    attempt = paystack_attempts_collection.find_one({'reference': reference})
    if not attempt or not _paystack_data_matches_attempt(paystack_data, attempt):
        return None, False

    claimed = paystack_attempts_collection.find_one_and_update(
        {'_id': attempt['_id'], 'status': {'$in': ['pending', 'verified']}},
        {'$set': {
            'status': 'finalizing',
            'paystack_verification': paystack_data,
            'verified_at': datetime.utcnow(),
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        return payments_collection.find_one({'paystack_reference': reference}), False

    now_utc = datetime.utcnow()
    payment_doc = {
        'customer_id': claimed['customer_id'],
        'agent_id': claimed['agent_id'],
        'recorded_by_agent_id': claimed['recorded_by_agent_id'],
        'manager_id': claimed['manager_id'],
        'method': 'MoMo',
        'amount': claimed['amount'],
        'date': claimed['date'],
        'time': now_utc.strftime('%H:%M:%S'),
        'payment_type': claimed['payment_type'],
        'created_at': now_utc,
        'paystack_reference': reference,
        'paystack_transaction_id': paystack_data.get('id'),
        'paystack_channel': 'mobile_money',
        'paystack_currency': 'GHS',
        'paystack_paid_at': paystack_data.get('paid_at') or paystack_data.get('paidAt'),
        'paystack_email': claimed.get('email'),
        'momo_phone': claimed.get('momo_phone'),
        'momo_provider': claimed.get('momo_provider'),
        'status': 'confirmed',
    }
    payment_doc.update(payment_print_defaults(now_utc, claimed['recorded_by_agent_id']))
    payment_doc['payment_id'] = receipt_payment_id(payment_doc)
    payout_account_id = claimed.get('payout_account_id')
    if payout_account_id:
        payment_doc.update({
            'account_id': payout_account_id,
            'payout_account_id': payout_account_id,
            'payout_account_name': claimed.get('payout_account_name') or '',
            'payout_account_type': claimed.get('payout_account_type') or 'bank',
            'bank_name': claimed.get('payout_bank_name') or '',
            'account_last4': claimed.get('payout_account_last4') or '',
            'accounting_credit_source': 'paystack_momo',
        })
    if claimed['payment_type'] == 'PRODUCT':
        payment_doc.update({
            'product_index': claimed.get('product_index'),
            'product_name': claimed.get('product_name'),
            'product_total': claimed.get('product_total'),
        })
    for key in ('activation_id', 'activation_team_name', 'activation_leader_id'):
        if claimed.get(key) is not None:
            payment_doc[key] = claimed[key]

    try:
        result = payments_collection.insert_one(payment_doc)
    except Exception:
        paystack_attempts_collection.update_one(
            {'_id': claimed['_id'], 'status': 'finalizing'},
            {'$set': {'status': 'verified', 'updated_at': datetime.utcnow()}}
        )
        raise

    customers_collection.update_one(
        {
            '_id': claimed['customer_id'],
            '$or': [{'lead_stage': {'$exists': False}}, {'lead_stage': 'lead'}],
        },
        {'$set': {'lead_stage': 'customer', 'lead_converted_at': now_utc}},
    )
    paystack_attempts_collection.update_one(
        {'_id': claimed['_id']},
        {'$set': {
            'status': 'completed',
            'payment_id': result.inserted_id,
            'completed_at': now_utc,
        }},
    )
    payment_doc['_id'] = result.inserted_id
    return payment_doc, True


def _is_ajax(req) -> bool:
    return req.headers.get("X-Requested-With", "").lower() == "xmlhttprequest"


def _payment_receipt(payment):
    """Build a receipt from a saved payment, never from browser form values."""
    if not payment:
        return None
    customer = customers_collection.find_one({'_id': payment['customer_id']}) or {}
    agent_id = payment.get('recorded_by_agent_id') or payment.get('agent_id')
    agent = users_collection.find_one({'_id': ObjectId(agent_id)}) if agent_id else {}
    agent = agent or {}
    date = datetime.strptime(payment['date'], '%Y-%m-%d')
    amount = Decimal(str(payment['amount'])).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    amount_id = format(amount, 'f').rstrip('0').rstrip('.') if amount % 1 else str(int(amount))
    receipt = {
        'payment_id': receipt_payment_id(payment),
        'date': date.strftime('%d/%m/%Y'),
        'agent': agent.get('name') or agent.get('username') or str(agent_id or ''),
        'customer': customer.get('name') or 'Customer',
        'phone': customer.get('phone_number') or '',
        'description': payment.get('product_name') or payment.get('payment_type') or 'Payment',
        'method': payment.get('method') or 'Cash',
        'amount': format(amount, '.2f'),
    }
    if payment.get('_id'):
        scope = {'customer_id': payment['customer_id'],
                 'payment_type': payment.get('payment_type'),
                 '_id': {'$ne': payment['_id']},
                 'created_at': {'$lte': payment.get('created_at', datetime.utcnow())}}
        if payment.get('payment_type') == 'PRODUCT':
            scope['product_index'] = payment.get('product_index')
        previous = sum(float(p.get('amount') or 0) for p in payments_collection.find(scope, {'amount': 1}))
        total_paid = round(previous + float(amount), 2)
        product_total = float(payment.get('product_total') or 0)
        receipt.update(
            document_id=str(payment['_id']), receipt_number='SL-' + str(payment['_id']).upper(),
            printer_enabled=bool(payment.get('printer_feature_enabled') and has_request_context()
                                 and current_user.is_authenticated and has_printer_access(current_user.id)),
            printed=bool(payment.get('printed')), print_status=payment.get('print_status', 'not_requested'),
            receipt_prints=_json_safe(receipt_tracking(payment)['receipt_prints']),
            legacy_print_locked=receipt_tracking(payment)['legacy_print_locked'],
            recorded_at=_json_safe(payment.get('payment_recorded_at')),
            printed_at=_json_safe(payment.get('printed_at')), print_delay_seconds=payment.get('print_delay_seconds'),
            branch=agent_branch(agent), customer_id=str(payment['customer_id']),
            account_number=str(customer.get('account_number') or customer.get('card_number') or ''),
            agent_id=str(agent_id or ''), time=payment.get('time', ''),
            susu_mode=payment.get('payment_type') == 'SUSU', product_total=product_total,
            previously_paid=round(previous, 2), total_paid=total_paid,
            amount_left=round(max(0, product_total - total_paid), 2),
        )
    return receipt


def _normalize_phone(raw: str) -> str | None:
    if not raw:
        return None
    p = raw.strip().replace(' ', '').replace('-', '').replace('+', '')
    if p.startswith('0') and len(p) == 10:
        p = '233' + p[1:]
    if p.startswith('233') and len(p) == 12:
        return p
    return None


def _json_safe(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    return value


def _existing_payment_info(customer_id: ObjectId, payment_date_str: str, is_susu: bool, product_index: int | None):
    """
    Look for payments already recorded for the same customer on the same date
    and within the same scope (SUSU vs specific PRODUCT index).
    Returns (count, total, scope_label).
    """
    q = {
        'customer_id': customer_id,
        'date': payment_date_str,
        'payment_type': {'$ne': 'WITHDRAWAL'}  # ignore withdrawals
    }

    scope_label = "SUSU" if is_susu else "PRODUCT"

    if is_susu:
        q['payment_type'] = 'SUSU'
    else:
        q['payment_type'] = 'PRODUCT'
        q['product_index'] = product_index if product_index is not None else -1

    docs = list(payments_collection.find(q))
    count = len(docs)
    total = sum(float(d.get('amount', 0.0)) for d in docs)

    if not is_susu:
        # Try to include product name in label (if any prior payment had it)
        for d in docs:
            name = d.get('product_name')
            if name:
                scope_label = f"PRODUCT: {name}"
                break

    return count, total, scope_label


def _restore_archived_customer(customer_id_raw: str, agent_ids: list[str]):
    """
    If customer exists in Archived_customers for this agent, move back to customers.
    Returns (customer_doc or None, error_message or None)
    """
    candidates = [customer_id_raw]
    try:
        candidates.append(ObjectId(customer_id_raw))
    except Exception:
        pass

    archived = archived_customers_collection.find_one({
        "_id": {"$in": candidates},
        "agent_id": {"$in": agent_ids}
    })
    if not archived:
        return None, "Archived customer not found."

    # Do not overwrite if already exists in active customers
    existing = customers_collection.find_one({"_id": archived.get("_id")})
    if existing:
        return existing, None

    now_utc = datetime.utcnow()
    restored = dict(archived)
    restored.update({
        "restored_at": now_utc,
        "restored_by": str(current_user.id),
        "restored_by_role": "agent",
        "restored_from": "Archived_customers",
        "restored_reason": "Payment initiated by agent"
    })

    try:
        customers_collection.insert_one(restored)
    except Exception:
        return None, "Failed to restore archived customer."

    deleted = archived_customers_collection.delete_one({"_id": archived.get("_id")})
    if not deleted or deleted.deleted_count != 1:
        return None, "Restored customer, but failed to remove from archive."

    return restored, None


@payment_bp.route('/add_payment', methods=['GET', 'POST'])
@login_required
def add_payment():
    today_date = datetime.today().date()

    if request.method == 'POST':
        accessible_agent_ids = get_accessible_agent_ids(current_user.id)
        customer_id = request.form.get('customer_id')
        product_index_raw = request.form.get('product_id')
        method_raw = (request.form.get('method') or "Cash").strip().lower()
        method = "MoMo" if method_raw == "momo" else "Cash"
        amount_raw = request.form.get('amount')
        # Payment dates are server-controlled; clients cannot backdate payments.
        date_str = today_date.strftime('%Y-%m-%d')
        is_susu = request.form.get('is_susu') == 'yes'
        send_sms = request.form.get('send_sms') == 'yes'
        force_insert = request.form.get('force') == 'yes'
        momo_prepare = request.form.get('momo_prepare') == 'yes'
        momo_phone = _normalize_momo_phone(request.form.get('momo_phone') or '')
        momo_provider = (request.form.get('momo_provider') or '').strip().lower()

        # ---- Basic validation ----
        if not all([customer_id, amount_raw]):
            if _is_ajax(request):
                return jsonify(ok=False, message='All fields are required!'), 400
            flash('All fields are required!', 'danger')
            return redirect(url_for('payment.add_payment'))

        try:
            amount = float(amount_raw)
            payment_date = datetime.combine(today_date, datetime.min.time())
        except ValueError:
            if _is_ajax(request):
                return jsonify(ok=False, message='Invalid amount.'), 400
            flash('Invalid payment amount.', 'danger')
            return redirect(url_for('payment.add_payment'))

        if amount <= 0:
            if _is_ajax(request):
                return jsonify(ok=False, message='Payment amount must be greater than zero.'), 400
            flash('Payment amount must be greater than zero.', 'danger')
            return redirect(url_for('payment.add_payment'))

        # ---- Auth + entities ----
        try:
            cust_oid = ObjectId(customer_id)
        except Exception:
            if _is_ajax(request):
                return jsonify(ok=False, message='Invalid customer id.'), 400
            flash('Invalid customer.', 'danger')
            return redirect(url_for('payment.add_payment'))

        customer = customers_collection.find_one({
            '_id': cust_oid,
            'agent_id': {'$in': accessible_agent_ids}
        })

        # If not active, try restore from archive on payment initiation
        if not customer:
            restored, err = _restore_archived_customer(customer_id, accessible_agent_ids)
            if err:
                if _is_ajax(request):
                    return jsonify(ok=False, message=err), 403
                flash(err, 'danger')
                return redirect(url_for('payment.add_payment'))
            customer = restored

        if not customer:
            if _is_ajax(request):
                return jsonify(ok=False, message='Unauthorized or customer not found.'), 403
            flash('Unauthorized access or customer not found.', 'danger')
            return redirect(url_for('payment.add_payment'))

        agent = users_collection.find_one({
            "_id": ObjectId(current_user.id),
            "role": "agent"
        })

        if not agent or "manager_id" not in agent:
            if _is_ajax(request):
                return jsonify(ok=False, message='Agent not linked to a manager. Contact admin.'), 400
            flash('Agent not linked to a manager. Contact admin.', 'danger')
            return redirect(url_for('payment.add_payment'))

        owner_agent_id = str(customer.get('agent_id') or current_user.id)
        owner_manager_id = customer.get('manager_id') or agent.get('manager_id')
        if owner_manager_id is None:
            if _is_ajax(request):
                return jsonify(ok=False, message='Customer owner is not linked to a manager.'), 400
            flash('Customer owner is not linked to a manager.', 'danger')
            return redirect(url_for('payment.add_payment'))

        # ---- Product scope (if PRODUCT mode) ----
        product_index = None
        product_name = None
        product_total = None
        if not is_susu:
            try:
                product_index = int(product_index_raw)
            except Exception:
                if _is_ajax(request):
                    return jsonify(ok=False, message='Invalid product selected.'), 400
                flash('Invalid product selected.', 'danger')
                return redirect(url_for('payment.add_payment'))

            purchases = customer.get('purchases', [])
            if product_index < 0 or product_index >= len(purchases):
                if _is_ajax(request):
                    return jsonify(ok=False, message='Selected product not found for this customer.'), 404
                flash('Selected product not found for this customer.', 'danger')
                return redirect(url_for('payment.add_payment'))

            sel = purchases[product_index]
            prod = sel.get('product', {})
            product_name = prod.get('name', 'Unnamed Product')
            try:
                product_total = float(prod.get('total', 0))
            except Exception:
                product_total = 0.0

        # ---- Duplicate check (same customer, same date, same scope) ----
        existing_count, existing_total, scope_label = _existing_payment_info(
            cust_oid, payment_date.strftime('%Y-%m-%d'), is_susu, product_index
        )

        if existing_count > 0 and not force_insert:
            # Tell the frontend to confirm override
            if _is_ajax(request):
                return jsonify(
                    ok=False,
                    needs_confirm=True,
                    existing_count=existing_count,
                    existing_total=round(existing_total, 2),
                    scope=scope_label,
                    message=f"You already recorded {existing_count} payment(s) totaling GHS {existing_total:.2f} for this {scope_label} on {date_str}. Proceed anyway?"
                ), 409
            # Non-AJAX fallback: inform via flash (user can re-submit with force=yes)
            flash(f"You already recorded {existing_count} payment(s) totaling GHS {existing_total:.2f} for this {scope_label} on {date_str}. Resubmit to confirm.", 'warning')
            return redirect(url_for('payment.add_payment'))

        # ---- Initiate Paystack Mobile Money directly from the backend ----
        if method == "MoMo" and momo_prepare:
            if not momo_phone:
                return jsonify(ok=False, message="Enter a valid Ghana MoMo number."), 400
            if momo_provider not in {'mtn', 'atl', 'vod'}:
                return jsonify(ok=False, message="Select MTN, ATMoney, or Telecel."), 400

            payout_account = get_momo_payout_account()
            if not payout_account:
                return jsonify(
                    ok=False,
                    message="MoMo payments are temporarily unavailable because Accounting has not configured a receiving account."
                ), 503

            _, secret_key = _paystack_keys()
            if not secret_key:
                return jsonify(
                    ok=False,
                    message="Paystack is not configured. Set PAYSTACK_SECRET_KEY (or PAYSTACK_PRIVATE_KEY)."
                ), 503

            amount_pesewas = _paystack_amount(amount_raw)
            if amount_pesewas <= 0:
                return jsonify(ok=False, message="Invalid MoMo payment amount."), 400

            reference = f"SLMOMO{uuid.uuid4().hex}"
            paystack_email = _paystack_email_for_phone(momo_phone)
            attempt_doc = {
                'reference': reference,
                'status': 'initializing',
                'customer_id': cust_oid,
                'agent_id': owner_agent_id,
                'recorded_by_agent_id': str(current_user.id),
                'manager_id': owner_manager_id,
                'amount': amount,
                'amount_pesewas': amount_pesewas,
                'currency': 'GHS',
                'email': paystack_email,
                'momo_phone': momo_phone,
                'momo_provider': momo_provider,
                'date': payment_date.strftime('%Y-%m-%d'),
                'is_susu': is_susu,
                'payment_type': 'SUSU' if is_susu else 'PRODUCT',
                'product_index': product_index,
                'product_name': product_name,
                'product_total': product_total,
                'force_insert': force_insert,
                'send_sms': send_sms,
                'created_at': datetime.utcnow(),
                **payout_snapshot(payout_account),
            }
            if customer.get('activation'):
                attempt_doc.update({
                    'activation_id': customer.get('activation_id'),
                    'activation_team_name': customer.get('activation_team_name') or 'Activation Team',
                    'activation_leader_id': customer.get('activation_leader_id') or owner_agent_id,
                })
            paystack_attempts_collection.insert_one(attempt_doc)

            try:
                response = requests.post(
                    PAYSTACK_CHARGE_URL,
                    headers={
                        'Authorization': f'Bearer {secret_key}',
                        'Content-Type': 'application/json',
                    },
                    json={
                        'email': paystack_email,
                        'amount': amount_pesewas,
                        'currency': 'GHS',
                        'reference': reference,
                        'mobile_money': {
                            'phone': momo_phone,
                            'provider': momo_provider,
                        },
                        'metadata': {
                            'smartliving_customer_id': str(cust_oid),
                            'smartliving_agent_id': owner_agent_id,
                            'payment_type': 'SUSU' if is_susu else 'PRODUCT',
                        },
                    },
                    timeout=25,
                )
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                paystack_attempts_collection.update_one(
                    {'reference': reference},
                    {'$set': {'status': 'charge_failed', 'error': str(exc), 'updated_at': datetime.utcnow()}}
                )
                return jsonify(ok=False, message="Unable to connect to Paystack. Please try again."), 502

            data = payload.get('data') or {}
            if (
                not response.ok
                or not payload.get('status')
                or data.get('reference') != reference
            ):
                paystack_attempts_collection.update_one(
                    {'reference': reference},
                    {'$set': {'status': 'charge_failed', 'paystack_response': payload, 'updated_at': datetime.utcnow()}}
                )
                return jsonify(ok=False, message=payload.get('message') or "Paystack MoMo charge failed."), 400

            paystack_attempts_collection.update_one(
                {'reference': reference},
                {'$set': {
                    'status': 'pending',
                    'paystack_charge': payload,
                    'updated_at': datetime.utcnow(),
                }}
            )
            completed_immediately = False
            completed_payment = None
            if data.get('status') == 'success':
                completed_payment, _ = _finalize_momo_attempt(reference, data)
                completed_immediately = completed_payment is not None
            return jsonify(
                ok=True,
                requires_momo_authorization=not completed_immediately,
                requires_otp=(data.get('status') == 'send_otp'),
                reference=reference,
                status='success' if completed_immediately else (data.get('status') or 'pending'),
                receipt=_payment_receipt(completed_payment),
                display_text=data.get('display_text') or 'Approve the Mobile Money prompt on the payer phone.',
            )

        if method == "MoMo":
            return jsonify(ok=False, message="Start the MoMo payment request first."), 400

        # ---- Build payment doc & insert ----
        now_utc = datetime.utcnow()
        time_str = now_utc.strftime('%H:%M:%S')     # ✅ NEW: separate time
        date_only_str = payment_date.strftime('%Y-%m-%d')

        payment_doc = {
            'customer_id': cust_oid,
            'agent_id': owner_agent_id,
            'recorded_by_agent_id': str(current_user.id),
            'manager_id': owner_manager_id,
            'method': method,
            'amount': amount,
            'date': date_only_str,                   # YYYY-MM-DD
            'time': time_str,                        # ✅ NEW: HH:MM:SS
            'payment_type': 'SUSU' if is_susu else 'PRODUCT',
            'created_at': now_utc
        }

        payment_doc.update(payment_print_defaults(now_utc, current_user.id))
        payment_doc['payment_id'] = receipt_payment_id(payment_doc)

        if not is_susu:
            payment_doc.update({
                'product_index': product_index,
                'product_name': product_name,
                'product_total': product_total
            })

        if customer.get('activation'):
            payment_doc.update({
                'activation_id': customer.get('activation_id'),
                'activation_team_name': customer.get('activation_team_name') or 'Activation Team',
                'activation_leader_id': customer.get('activation_leader_id') or owner_agent_id
            })

        payment_result = payments_collection.insert_one(payment_doc)

        # Lead lifecycle: convert lead -> customer on first non-withdrawal payment
        customers_collection.update_one(
            {
                "_id": cust_oid,
                "$or": [{"lead_stage": {"$exists": False}}, {"lead_stage": "lead"}]
            },
            {
                "$set": {
                    "lead_stage": "customer",
                    "lead_converted_at": now_utc
                }
            }
        )

        # ---- NEW: roll-up / daily close per agent in `sales_close` ----
        sales_close_filter = {
            'agent_id': owner_agent_id,
            'date': date_only_str
        }
        sales_close_update = {
            '$setOnInsert': {
                'agent_id': owner_agent_id,
                'manager_id': owner_manager_id,
                'date': date_only_str,
                'created_at': now_utc
            },
            '$inc': {
                'total_amount': amount,
                'count': 1
            },
            '$set': {
                'last_payment_at': now_utc,
                'updated_at': now_utc,
                'last_recorded_by_agent_id': str(current_user.id)
            }
        }
        # Only physical cash payments credit the agent's sales-close account.
        if method == "Cash":
            sales_close_collection.update_one(sales_close_filter, sales_close_update, upsert=True)

        # ---- Optional SMS ----
        sms_status = None
        if send_sms:
            try:
                phone = _normalize_phone(customer.get('phone_number', ''))
                if phone is None:
                    sms_status = 'invalid_phone'
                else:
                    full_name = customer.get('name', 'Customer').strip()
                    first_name = full_name.split()[0] if full_name else 'Customer'

                    if is_susu:
                        total_paid = sum(
                            float(p.get('amount', 0))
                            for p in payments_collection.find({'customer_id': cust_oid, 'payment_type': 'SUSU'})
                        )
                        message = (
                            f"Dear {first_name}, we received GHS {amount:.2f} for your SUSU savings. "
                            f"Total saved: GHS {total_paid:.2f}. Keep saving with Smart Living!"
                        )
                    else:
                        all_payments = list(payments_collection.find({
                            'customer_id': cust_oid,
                            'product_index': product_index
                        }))
                        total_paid = sum(float(p.get('amount', 0)) for p in all_payments)
                        message = (
                            f"Dear {first_name}, We received GHS {amount:.2f} for your order. "
                            f"Total paid: GHS {total_paid:.2f}. For inquiries, visit our office. "
                            f"Call/WhatsApp 0556064611 / 0509639836 / 0598948132"
                        )

                    sms_url = (
                        f"https://sms.arkesel.com/sms/api?action=send-sms"
                        f"&api_key={ARKESEL_API_KEY}"
                        f"&to={phone}"
                        f"&from=SMARTLIVING"
                        f"&sms={quote(message)}"
                    )

                    resp = requests.get(sms_url, timeout=12)
                    if resp.status_code == 200 and '"code":"ok"' in resp.text:
                        sms_status = 'sent'
                    else:
                        sms_status = 'failed'
            except Exception as e:
                print("SMS sending error:", str(e))
                sms_status = 'error'

            payments_collection.update_one(
                {'_id': payment_result.inserted_id},
                {'$push': {'sms_events': {'status': sms_status or 'not_sent', 'message': message if 'message' in locals() else '', 'created_at': datetime.utcnow()}}}
            )

        # ---- Respond (AJAX vs normal) ----
        if _is_ajax(request):
            msg = 'Payment added successfully.'
            if sms_status == 'sent':
                msg = 'Payment added and SMS sent successfully.'
            elif sms_status in ('failed', 'error'):
                msg = 'Payment added, but SMS delivery failed.'
            elif sms_status == 'invalid_phone':
                msg = 'Payment added; phone number invalid for SMS.'

            return jsonify(ok=True, message=msg, receipt=_payment_receipt(payment_doc))

        # Non-AJAX fallback
        if sms_status == 'sent':
            flash('Payment added and SMS sent successfully.', 'success')
        elif sms_status in ('failed', 'error'):
            flash('Payment added but SMS delivery failed.', 'warning')
        elif sms_status == 'invalid_phone':
            flash('Payment added; phone number invalid for SMS.', 'warning')
        else:
            flash('Payment added successfully. (SMS not sent)', 'success')

        return redirect(url_for('payment.add_payment'))

    # ---------- GET ----------
    accessible_agent_ids = get_accessible_agent_ids(current_user.id)
    raw_customers = list(customers_collection.find(
        {'agent_id': {'$in': accessible_agent_ids}},
        {'name': 1, 'phone_number': 1, 'email': 1, 'purchases': 1, 'image_url': 1}
    ))

    raw_archived = list(archived_customers_collection.find(
        {'agent_id': {'$in': accessible_agent_ids}},
        {'name': 1, 'phone_number': 1, 'email': 1, 'purchases': 1, 'archived_at': 1, 'image_url': 1}
    ))

    customers = []
    for c in raw_customers:
        c['_id'] = str(c['_id'])
        c['archived'] = False
        customers.append(_json_safe(c))
    for c in raw_archived:
        c['_id'] = str(c['_id'])
        c['archived'] = True
        customers.append(_json_safe(c))

    return render_template('add_payment.html', customers=customers,
                           printer_enabled=has_printer_access(current_user.id),
                           printer_csrf=session.setdefault('payment_print_csrf', secrets.token_urlsafe(32)))


@payment_bp.get('/payment-receipts/pending')
@login_required
def pending_receipts():
    if not has_printer_access(current_user.id):
        return jsonify(ok=False, message='Printer access is required.'), 403
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    query = {
        'payment_recorded_at': {'$gte': day_start, '$lt': day_start + timedelta(days=1)},
        'recorded_by_agent_id': str(current_user.id), 'printer_feature_enabled': True,
        'printed': False,
    }
    count = payments_collection.count_documents(query)
    rows = payments_collection.find(query).sort('payment_recorded_at', -1).limit(30)
    return jsonify(ok=True, count=count, receipts=[_payment_receipt(p) for p in rows])


@payment_bp.post('/payment-receipts/<payment_id>/<action>')
@login_required
def receipt_print_action(payment_id, action):
    data = request.get_json(silent=True) or {}
    csrf = session.get('payment_print_csrf', '')
    supplied_csrf = request.headers.get('X-Printer-CSRF', '')
    if not csrf or not hmac.compare_digest(csrf, supplied_csrf):
        return jsonify(ok=False, message='Refresh the payment page and try again.'), 403
    if not ObjectId.is_valid(payment_id):
        return jsonify(ok=False, message='Invalid payment.'), 400
    oid = ObjectId(payment_id)
    receipt_format = data.get('format', 'short')
    if receipt_format not in FORMATS:
        return jsonify(ok=False, message='Select a short or full receipt.'), 400
    if action == 'finish':
        # Completion remains allowed after access revocation for an existing claim.
        outcome = data.get('outcome')
        token = data.get('token')
        if outcome not in ('printed', 'uncertain') or not isinstance(token, str) or not token:
            return jsonify(ok=False, message='Invalid print result.'), 400
        payment = finish_print(oid, current_user.id, token, outcome, receipt_format)
    else:
        if not has_printer_access(current_user.id):
            return jsonify(ok=False, message='Printer access is required.'), 403
        if action == 'claim':
            payment = claim_print(oid, current_user.id, receipt_format)
        else:
            return jsonify(ok=False, message='Invalid print action.'), 400
    if not payment:
        return jsonify(ok=False, message='This receipt format is unavailable or already claimed. Each format can be printed once.'), 409
    payload = dict(ok=True, receipt=_payment_receipt(payment))
    if action == 'claim':
        payload['token'] = payment['receipt_prints'][receipt_format]['token']
    return jsonify(**payload)


@paystack_webhook_bp.route('/payments/paystack/webhook', methods=['POST'])
def paystack_webhook():
    """Receive Paystack events for backend-initiated Mobile Money charges."""
    _, secret_key = _paystack_keys()
    if not secret_key:
        return '', 503

    raw_body = request.get_data(cache=True)
    received_signature = request.headers.get('x-paystack-signature', '')
    expected_signature = hmac.new(
        secret_key.encode('utf-8'), raw_body, hashlib.sha512
    ).hexdigest()
    if not received_signature or not hmac.compare_digest(received_signature, expected_signature):
        return '', 401

    event = request.get_json(silent=True) or {}
    if event.get('event') != 'charge.success':
        return '', 200

    data = event.get('data') or {}
    reference = str(data.get('reference') or '')
    attempt = paystack_attempts_collection.find_one({'reference': reference})
    if not attempt:
        # Acknowledge valid Paystack events that do not belong to this module.
        return '', 200
    if not _paystack_data_matches_attempt(data, attempt):
        paystack_attempts_collection.update_one(
            {'_id': attempt['_id']},
            {'$set': {
                'status': 'verification_mismatch',
                'paystack_webhook': data,
                'updated_at': datetime.utcnow(),
            }}
        )
        return '', 200

    try:
        _finalize_momo_attempt(reference, data)
    except Exception as exc:
        print(f"Paystack webhook finalization error for {reference}: {exc}")
        return '', 500
    return '', 200


@paystack_webhook_bp.route('/payments/paystack/status/<reference>', methods=['GET'])
@login_required
def paystack_payment_status(reference):
    attempt = paystack_attempts_collection.find_one({
        'reference': reference,
        'recorded_by_agent_id': str(current_user.id),
    })
    if not attempt:
        return jsonify(ok=False, message='Payment request not found.'), 404

    status = attempt.get('status') or 'pending'
    if status == 'completed':
        payment = payments_collection.find_one({'paystack_reference': reference})
        return jsonify(ok=True, status='success', message='MoMo payment received and recorded.',
                       receipt=_payment_receipt(payment))
    if status in {'charge_failed', 'verification_mismatch', 'failed', 'timeout'}:
        return jsonify(ok=False, status='failed', message='The MoMo payment was not completed.'), 400
    return jsonify(ok=True, status='pending', message='Waiting for approval on the payer phone.')


@paystack_webhook_bp.route('/payments/paystack/submit-otp', methods=['POST'])
@login_required
def paystack_submit_otp():
    body = request.get_json(silent=True) or request.form
    reference = str(body.get('reference') or '').strip()
    otp = str(body.get('otp') or '').strip()
    if not reference or not re.fullmatch(r'\d{4,10}', otp):
        return jsonify(ok=False, message='Enter the numeric one-time password sent to the payer phone.'), 400

    attempt = paystack_attempts_collection.find_one({
        'reference': reference,
        'recorded_by_agent_id': str(current_user.id),
        'status': {'$in': ['pending', 'verified']},
    })
    if not attempt:
        payment = payments_collection.find_one({
            'paystack_reference': reference, 'recorded_by_agent_id': str(current_user.id)
        })
        if payment:
            return jsonify(ok=True, status='success', message='MoMo payment already recorded.',
                           receipt=_payment_receipt(payment))
        return jsonify(ok=False, message='Payment request was not found or has expired.'), 404

    _, secret_key = _paystack_keys()
    if not secret_key:
        return jsonify(ok=False, message='Paystack secret key is not configured.'), 503

    try:
        response = requests.post(
            PAYSTACK_SUBMIT_OTP_URL,
            headers={
                'Authorization': f'Bearer {secret_key}',
                'Content-Type': 'application/json',
            },
            json={'otp': otp, 'reference': reference},
            timeout=25,
        )
        payload = response.json()
    except (requests.RequestException, ValueError):
        return jsonify(ok=False, message='Unable to submit the OTP to Paystack. Please try again.'), 502

    data = payload.get('data') or {}
    if not response.ok or not payload.get('status') or data.get('reference') != reference:
        return jsonify(ok=False, message=payload.get('message') or 'Paystack rejected the OTP.'), 400

    paystack_attempts_collection.update_one(
        {'_id': attempt['_id']},
        {'$set': {
            'paystack_otp_response': payload,
            'next_action': data.get('status'),
            'updated_at': datetime.utcnow(),
        }}
    )

    completed = False
    payment = None
    if data.get('status') == 'success':
        payment, _ = _finalize_momo_attempt(reference, data)
        completed = payment is not None

    return jsonify(
        ok=True,
        status='success' if completed else (data.get('status') or 'pending'),
        receipt=_payment_receipt(payment),
        requires_otp=(data.get('status') == 'send_otp'),
        requires_momo_authorization=not completed,
        reference=reference,
        display_text=data.get('display_text') or 'Approve the payment prompt on the payer phone.',
    )


@payment_bp.route('/all_payments', methods=['GET'])
@login_required
def view_all_payments():
    selected_date = request.args.get('date') or datetime.today().strftime('%Y-%m-%d')
    accessible_agent_ids = get_accessible_agent_ids(current_user.id)

    agent_customers = list(customers_collection.find({'agent_id': {'$in': accessible_agent_ids}}))
    agent_customer_ids = [c['_id'] for c in agent_customers]

    payments_on_date = list(payments_collection.find({
        'customer_id': {'$in': agent_customer_ids},
        'date': selected_date,
        'payment_type': {'$ne': 'WITHDRAWAL'}  # ✅ Exclude withdrawals
    }))

    grouped = {}
    for p in payments_on_date:
        customer = customers_collection.find_one({'_id': p.get('customer_id')})
        if not customer:
            continue

        cid = str(p['customer_id'])
        if cid not in grouped:
            grouped[cid] = {
                'customer_name': customer.get('name', 'Unknown'),
                'phone_number': customer.get('phone_number', 'N/A'),
                'date': selected_date,
                'amounts': [],
                'receipt_records': [],
                'total_amount': 0.0,
                'payment_count': 0
            }

        grouped[cid]['amounts'].append(f"{float(p.get('amount', 0)):.2f}")
        grouped[cid]['receipt_records'].append(p)
        grouped[cid]['total_amount'] += float(p.get('amount', 0))
        grouped[cid]['payment_count'] += 1

    summaries = list(grouped.values())
    return render_template('view_all_payments.html', payments=summaries, selected_date=selected_date)


@payment_bp.route('/payment/product_paid', methods=['GET'])
def get_product_paid():
    if not current_user.is_authenticated:
        return jsonify(ok=False, message='Unauthorized.'), 401
    customer_id = request.args.get('customer_id')
    product_index_raw = request.args.get('product_index')
    if not customer_id or product_index_raw is None:
        return jsonify(ok=False, message='Missing customer or product.'), 400

    try:
        cust_oid = ObjectId(customer_id)
        product_index = int(product_index_raw)
    except Exception:
        return jsonify(ok=False, message='Invalid customer or product.'), 400

    customer = customers_collection.find_one({
        '_id': cust_oid,
        'agent_id': {'$in': get_accessible_agent_ids(current_user.id)}
    })
    if not customer:
        return jsonify(ok=False, message='Unauthorized or customer not found.'), 403

    purchases = customer.get('purchases', [])
    if product_index < 0 or product_index >= len(purchases):
        return jsonify(ok=False, message='Product not found for this customer.'), 404

    product = purchases[product_index].get('product', {})
    try:
        product_total = float(product.get('total', 0))
    except Exception:
        product_total = 0.0

    deposits = payments_collection.find({
        'customer_id': cust_oid,
        'payment_type': 'PRODUCT',
        'product_index': product_index
    })
    withdrawals = payments_collection.find({
        'customer_id': cust_oid,
        'payment_type': 'WITHDRAWAL',
        'product_index': product_index
    })

    paid_sum = sum(float(p.get('amount', 0)) for p in deposits) - sum(float(p.get('amount', 0)) for p in withdrawals)
    paid_sum = round(paid_sum, 2)
    amount_left = max(0, round(product_total - paid_sum, 2))

    return jsonify(ok=True, paid=paid_sum, total=product_total, left=amount_left)
