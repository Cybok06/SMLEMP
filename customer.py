from bson import ObjectId
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from db import db
from datetime import datetime
import traceback
import requests
from services.activation_groups import get_activation_group_context, get_next_approved_activation_for_user

customer_bp = Blueprint('customer', __name__)
customers_collection = db["customers"]
users_collection = db["users"]
images_col = db["images"]

# ===== Cloudflare (hardcoded as requested) =====
CF_ACCOUNT_ID   = "63e6f91eec9591f77699c4b434ab44c6"
CF_IMAGES_TOKEN = "Brz0BEfl_GqEUjEghS2UEmLZhK39EUmMbZgu_hIo"
CF_HASH         = "h9fmMoa1o2c2P55TcWJGOg"
DEFAULT_VARIANT = "public"

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _safe_oid(raw):
    if not raw:
        return None
    try:
        return ObjectId(str(raw))
    except Exception:
        return None


def _owner_manager_id(user_doc):
    if not user_doc:
        return None
    role = (user_doc.get("role") or "").lower()
    if role == "manager":
        return user_doc.get("_id")
    return user_doc.get("manager_id")


# Show registration page
@customer_bp.route('/register', methods=['GET'])
@login_required
def register_customer():
    role = getattr(current_user, "role", None)
    if role not in (None, "agent", "manager"):
        return "Forbidden", 403

    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    active_activation = get_next_approved_activation_for_user(str(current_user.id))
    group_ctx = get_activation_group_context(str(current_user.id), active_activation)

    activation_info = None
    if active_activation:
        activation_info = {
            "id": str(active_activation.get("_id")),
            "title": active_activation.get("title") or "",
            "location": active_activation.get("location") or "",
            "datetime": active_activation.get("activationDateTime"),
            "status": active_activation.get("status") or "upcoming",
            "team_name": active_activation.get("teamName") or "Activation Team",
            "leader_name": group_ctx.get("leader_name") or "",
            "is_leader": bool(group_ctx.get("is_leader")),
            "leader_selected": bool(group_ctx.get("leader_selected")),
            "ownership_active": bool(group_ctx.get("ownership_active")),
            "group_state": group_ctx.get("group_state") or "not_selected",
            "started_at": active_activation.get("startedAt"),
            "ended_at": active_activation.get("endedAt"),
        }

    return render_template(
        'register_customer.html',
        agent_id=(str(group_ctx.get("owner_agent_id") or current_user.id) if role == "agent" else ""),
        today_str=today_str,
        activation_mode=bool(activation_info),
        activation_info=activation_info,
    )


# =============== Upload directly to Cloudflare ===============
@customer_bp.route('/upload_image', methods=['POST'])
def upload_customer_image():
    try:
        if 'image' not in request.files:
            return jsonify({'success': False, 'error': 'No file part in request'}), 400

        image = request.files['image']
        if image.filename == '':
            return jsonify({'success': False, 'error': 'No selected file'}), 400

        if not (image and allowed_file(image.filename)):
            return jsonify({'success': False, 'error': 'File type not allowed'}), 400

        direct_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/images/v2/direct_upload"
        headers = {"Authorization": f"Bearer {CF_IMAGES_TOKEN}"}
        data = {}

        res = requests.post(direct_url, headers=headers, data=data, timeout=20)
        try:
            j = res.json()
        except Exception:
            return jsonify({'success': False, 'error': 'Cloudflare (direct_upload) returned non-JSON'}), 502

        if not j.get('success'):
            return jsonify({'success': False, 'error': 'Cloudflare direct_upload failed', 'details': j}), 400

        upload_url = j['result']['uploadURL']
        image_id = j['result']['id']

        up = requests.post(
            upload_url,
            files={'file': (secure_filename(image.filename), image.stream, image.mimetype or 'application/octet-stream')},
            timeout=60
        )
        try:
            uj = up.json()
        except Exception:
            return jsonify({'success': False, 'error': 'Cloudflare (upload) returned non-JSON'}), 502

        if not uj.get('success'):
            return jsonify({'success': False, 'error': 'Cloudflare upload failed', 'details': uj}), 400

        variant = request.args.get('variant', DEFAULT_VARIANT)
        image_url = f"https://imagedelivery.net/{CF_HASH}/{image_id}/{variant}"

        images_col.insert_one({
            'provider': 'cloudflare_images',
            'image_id': image_id,
            'variant': variant,
            'url': image_url,
            'original_filename': secure_filename(image.filename),
            'mimetype': image.mimetype,
            'size_bytes': request.content_length,
            'created_at': datetime.utcnow(),
            'module': 'customer_register'
        })

        return jsonify({'success': True, 'image_url': image_url, 'image_id': image_id, 'variant': variant})

    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# Add a new customer
@customer_bp.route('/add', methods=['POST'])
@login_required
def add_customer():
    try:
        role = getattr(current_user, "role", None)
        if role not in (None, "agent", "manager"):
            return jsonify({'error': 'Unauthorized'}), 403

        name = request.form.get('name')
        location = request.form.get('location')
        occupation = request.form.get('occupation')
        phone_number = (request.form.get('phone_number') or '').strip()
        comment = request.form.get('comment')
        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')
        image_url = (request.form.get('image_url') or '').strip()
        image_id = (request.form.get('image_id') or '').strip()
        date_registered_str = request.form.get('date_registered')

        if not phone_number:
            return jsonify({'error': 'Phone number is required'}), 400

        group_ctx = get_activation_group_context(str(current_user.id))
        owner_agent_id = str(group_ctx.get("owner_agent_id") or current_user.id)
        ownership_active = bool(group_ctx.get("ownership_active"))
        activation_doc = group_ctx.get("activation")
        leader_manager_id = None
        if activation_doc and ownership_active:
            leader_oid = _safe_oid(group_ctx.get("leader_id") or owner_agent_id)
            leader_doc = users_collection.find_one({"_id": leader_oid}, {"role": 1, "manager_id": 1}) if leader_oid else None
            leader_manager_id = _owner_manager_id(leader_doc)
            if not leader_manager_id:
                return jsonify({'error': 'Activation leader is not linked to a manager'}), 400

        if role in (None, 'agent'):
            if activation_doc and ownership_active:
                agent_id = owner_agent_id
                manager_id = leader_manager_id
            else:
                agent_id = request.form.get('agent_id') or owner_agent_id
                if not agent_id:
                    return jsonify({'error': 'Agent ID is required'}), 400

                agent_oid = _safe_oid(agent_id)
                agent = users_collection.find_one({"_id": agent_oid, "role": "agent"}) if agent_oid else users_collection.find_one({"_id": agent_id, "role": "agent"})
                if not agent or "manager_id" not in agent:
                    return jsonify({'error': 'Manager ID not found for this agent'}), 400

                manager_id = agent["manager_id"]
        else:
            if activation_doc and ownership_active:
                agent_id = owner_agent_id
                manager_id = leader_manager_id
            else:
                agent_id = None
                manager_id = _safe_oid(str(current_user.id)) or str(current_user.id)

        # Allow registration after an upload attempt even when the provider fails.
        if not image_url and request.form.get('image_upload_completed') != '1':
            return jsonify({'error': 'Customer image upload is required'}), 400

        try:
            if date_registered_str:
                date_registered = datetime.strptime(date_registered_str, "%Y-%m-%d")
            else:
                date_registered = datetime.utcnow()
        except ValueError:
            date_registered = datetime.utcnow()

        customer = {
            'name': name,
            'image_url': image_url,
            'cf_image_id': image_id or None,
            'location': location,
            'occupation': occupation,
            'phone_number': phone_number,
            'comment': comment,
            'agent_id': agent_id,
            'registered_by_agent_id': str(current_user.id),
            'manager_id': manager_id,
            'date_registered': date_registered,
            'lead_stage': 'lead',
            'lead_registered_at': date_registered,
            'activation': False,
        }

        if activation_doc:
            customer['activation'] = True
            customer['activation_id'] = activation_doc.get('_id')
            customer['activation_title'] = activation_doc.get('title') or ''
            customer['activation_location'] = activation_doc.get('location') or ''
            customer['activation_datetime'] = activation_doc.get('activationDateTime')
            customer['activation_team_name'] = activation_doc.get('teamName') or 'Activation Team'
            if ownership_active:
                customer['activation_leader_id'] = group_ctx.get('leader_id') or owner_agent_id
                customer['activation_leader_name'] = group_ctx.get('leader_name') or ''
                customer['activation_leader_routed_at'] = datetime.utcnow()
            customer['activation_registered_by_id'] = str(current_user.id)
            customer['activation_leader_ownership_active'] = ownership_active

        if latitude and longitude:
            try:
                customer['coordinates'] = {
                    'latitude': float(latitude),
                    'longitude': float(longitude)
                }
            except ValueError:
                print("Invalid latitude or longitude format")

        inserted_id = customers_collection.insert_one(customer).inserted_id
        return jsonify({
            'ok': True,
            'message': 'Customer registered successfully!',
            'customer_id': str(inserted_id),
            'customer_name': name
        }), 200

    except Exception as e:
        print("Error:", str(e))
        return jsonify({'error': 'Failed to register customer'}), 500
