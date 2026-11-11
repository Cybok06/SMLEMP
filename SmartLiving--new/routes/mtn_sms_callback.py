from flask import Blueprint, request, jsonify

mtn_sms_callback_bp = Blueprint('mtn_sms_callback', __name__)

# Callback URL when deployed:
# https://yourdomain.com/sms-callback


@mtn_sms_callback_bp.route('/sms-callback', methods=['POST', 'GET'])
def sms_callback():
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if not data:
            data = request.form.to_dict() or {}
        print('Received SMS callback data:', data)
        return jsonify({"status": "received"}), 200

    return "SMS callback endpoint is running", 200
