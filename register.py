from flask import Blueprint, request, render_template, redirect, url_for, session, jsonify, flash
from flask_bcrypt import Bcrypt
from bson.objectid import ObjectId
from datetime import datetime
from db import db
from werkzeug.utils import secure_filename
import requests
import traceback
from services.agent_approval import UNDER_REVIEW
import secrets

register_bp = Blueprint('register', __name__)
bcrypt = Bcrypt()

# MongoDB collection
users_collection = db.users
images_col = db.images

# Cloudflare Images (same configuration used by customer registration)
CF_ACCOUNT_ID = "63e6f91eec9591f77699c4b434ab44c6"
CF_IMAGES_TOKEN = "Brz0BEfl_GqEUjEghS2UEmLZhK39EUmMbZgu_hIo"
CF_HASH = "h9fmMoa1o2c2P55TcWJGOg"
DEFAULT_VARIANT = "public"
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@register_bp.record_once
def on_load(state):
    bcrypt.init_app(state.app)

@register_bp.route('/register', methods=['GET', 'POST'])
def register():
    from login import get_current_identity
    from flask import jsonify
    ident = get_current_identity()
    if not ident.get('is_authenticated'):
        return redirect(url_for('login.login'))
    if ident.get('role') != 'manager':
        return 'Forbidden', 403
    manager_id = ident.get('user_id')
    if not ObjectId.is_valid(manager_id):
        return 'Invalid manager session', 403
    manager = users_collection.find_one({'_id': ObjectId(manager_id), 'role': 'manager'})
    if not manager:
        return 'Forbidden', 403
    session.setdefault('agent_registration_csrf', secrets.token_urlsafe(32))
    if request.method == 'POST':
        if not secrets.compare_digest(request.form.get('csrf_token', ''), session['agent_registration_csrf']):
            return 'Invalid form token', 400
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        name = request.form.get('name', '').strip()
        if not username or not password or not name:
            flash('Username, password and full name are required.', 'error')
            return render_template('register.html'), 400
        if users_collection.find_one({'username': username}) or db['Exited'].find_one({'username': username}):
            flash('This username is already in use.', 'error')
            return render_template('register.html'), 409
        payload = {key: request.form.get(key, '').strip() for key in (
            'phone', 'email', 'gender', 'position', 'location', 'start_date', 'image_url', 'image_id'
        )}
        payload.update(
            username=username, name=name,
            password=bcrypt.generate_password_hash(password).decode('utf-8'),
            role='agent', manager_id=manager['_id'], branch=manager.get('branch', ''),
            status=UNDER_REVIEW, agent_approval_status='pending', is_active=False,
            date_registered=datetime.utcnow(),
            assets=[item.strip() for item in request.form.get('assets', '').split(',') if item.strip()],
        )
        users_collection.insert_one(payload)
        flash('Agent created with status Under Review. An executive, HR or admin must approve the account before login.', 'success')
        return redirect(url_for('login.agent_list'))
    return render_template('register.html')

@register_bp.route('/register/upload_image', methods=['POST'])
def upload_agent_image():
    try:
        if 'manager_id' not in session:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401

        image = request.files.get('image')
        if not image or not image.filename:
            return jsonify({'success': False, 'error': 'No image selected'}), 400
        if not allowed_file(image.filename):
            return jsonify({'success': False, 'error': 'File type not allowed'}), 400

        direct_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/images/v2/direct_upload"
        direct_response = requests.post(
            direct_url,
            headers={"Authorization": f"Bearer {CF_IMAGES_TOKEN}"},
            data={},
            timeout=20,
        )
        try:
            direct_data = direct_response.json()
        except Exception:
            return jsonify({'success': False, 'error': 'Cloudflare (direct_upload) returned non-JSON'}), 502

        if not direct_data.get('success'):
            return jsonify({'success': False, 'error': 'Cloudflare direct_upload failed', 'details': direct_data}), 400

        upload_url = direct_data['result']['uploadURL']
        image_id = direct_data['result']['id']
        upload_response = requests.post(
            upload_url,
            files={'file': (
                secure_filename(image.filename),
                image.stream,
                image.mimetype or 'application/octet-stream',
            )},
            timeout=60,
        )
        try:
            upload_data = upload_response.json()
        except Exception:
            return jsonify({'success': False, 'error': 'Cloudflare (upload) returned non-JSON'}), 502

        if not upload_data.get('success'):
            return jsonify({'success': False, 'error': 'Cloudflare upload failed', 'details': upload_data}), 400

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
            'module': 'agent_register',
            'manager_id': ObjectId(session['manager_id']),
        })
        return jsonify({'success': True, 'image_url': image_url, 'image_id': image_id, 'variant': variant})

    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
