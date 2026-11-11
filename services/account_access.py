"""Request-wide revocation for role sessions and Flask-Login remember cookies."""
from flask import current_app, flash, jsonify, redirect, request, session, url_for
from flask_login import logout_user
from flask_login.utils import decode_cookie
from services.agent_approval import agent_pending_approval


def wants_json():
    return (request.path.startswith('/api/') or request.path.endswith('/count')
            or request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or request.accept_mimetypes.best == 'application/json')


def login_blocked(doc):
    return bool(doc.get("login_blocked")) or agent_pending_approval(doc)


def split_login_token(token):
    user_id, separator, version = str(token).partition(":")
    return user_id, version if separator else "0"


def enforce_account_access(find_user, role_keys):
    token = session.get("_user_id")
    if not token and not session.get("user_id"):
        cookie = request.cookies.get(current_app.config.get("REMEMBER_COOKIE_NAME", "remember_token"))
        token = decode_cookie(cookie) if cookie else None
    user_id = session.get("user_id") or next((session[k] for k in role_keys if session.get(k)), None)
    version = str(session.get("auth_version", "0"))
    if token and not user_id:
        user_id, version = split_login_token(token)
    if not user_id:
        return None
    doc = find_user(str(user_id))
    if doc and not login_blocked(doc) and version == str(doc.get("auth_version", "0")):
        return None
    session.clear()
    logout_user()
    # Also discard the remember cookie when only legacy role keys were present.
    session["_remember"] = "clear"
    message = "Your login has been blocked. Contact an executive." if doc and login_blocked(doc) else "Your session has ended. Please sign in again."
    if wants_json():
        return jsonify(error=message, redirect_url=url_for("login.login")), 401
    flash(message, "error")
    return redirect(url_for("login.login"))
