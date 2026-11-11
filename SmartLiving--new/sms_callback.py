from flask import Flask, request, jsonify

app = Flask(__name__)

# Callback URL when deployed:
# https://yourdomain.com/sms-callback

@app.route('/sms-callback', methods=['POST', 'GET'])
def sms_callback():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        print('Received SMS callback data:', data)
        return jsonify({"status": "received"}), 200

    return "SMS callback endpoint is running", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
