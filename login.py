from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, g, has_request_context
from flask_bcrypt import Bcrypt
from flask_login import login_user, logout_user, current_user
from bson.objectid import ObjectId
from services.account_access import login_blocked
from services.agent_approval import agent_pending_approval
from user_model import User  # used for agent login via Flask-Login
from datetime import datetime, timedelta
from urllib.parse import urlparse
import requests
import bcrypt as bcrypt_lib
from db import db
from services.login_audit import ensure_login_log_indexes, ensure_user_indexes

login_bp = Blueprint('login', __name__)
bcrypt = Bcrypt()

# MongoDB collections
users_col   = db.users
exited_users_col = db["Exited"]
logins_col  = db.login_logs  # Login logs
ROLE_CONFIG = {
    "admin": {"endpoint": "login.admin_dashboard", "session_key": "admin_id", "name_key": "username"},
    "manager": {"endpoint": "manager_dashboard.manager_dashboard_view", "session_key": "manager_id", "name_key": "manager_name"},
    "agent": {"endpoint": "dashboard_agent.agent_dashboard", "session_key": "agent_id", "name_key": None},
    "executive": {"endpoint": "executive_dashboard.executive_dashboard", "session_key": "executive_id", "name_key": "executive_name"},
    "inventory": {"endpoint": "inventory_react_app", "session_key": "inventory_id", "name_key": "inventory_name"},
    "customer_support": {"endpoint": "operations_management_react_app", "session_key": "customer_support_id", "name_key": "customer_support_name"},
    "hr": {"endpoint": "hr.dashboard", "session_key": "hr_id", "name_key": "hr_name"},
    "accounting": {"endpoint": "acc_dashboard.accounting_dashboard", "session_key": "accounting_id", "name_key": "accounting_name"},
}
ROLE_SESSION_KEYS = [cfg["session_key"] for cfg in ROLE_CONFIG.values()] + ["operations_management_id"]
complaints_col = db.complaints  # ✅ Complaints collection for dashboard/badges

# ---------------------------
# Utilities
# ---------------------------
def get_location(ip: str):
    try:
        resp = requests.get(f"http://ip-api.com/json/{ip}", timeout=5).json()
        return {
            "country": resp.get("country"),
            "region": resp.get("regionName"),
            "city": resp.get("city"),
            "isp": resp.get("isp")
        }
    except Exception:
        return {}


def _parse_device(user_agent: str | None) -> dict:
    ua = user_agent or ""
    try:
        from user_agents import parse as ua_parse
        parsed = ua_parse(ua)
        return {
            "browser": f"{parsed.browser.family} {parsed.browser.version_string}".strip(),
            "os": f"{parsed.os.family} {parsed.os.version_string}".strip(),
            "is_mobile": bool(parsed.is_mobile),
            "raw": ua,
        }
    except Exception:
        ua_lc = ua.lower()
        browser = "Unknown"
        if "chrome" in ua_lc and "safari" in ua_lc and "edge" not in ua_lc:
            browser = "Chrome"
        elif "safari" in ua_lc and "chrome" not in ua_lc:
            browser = "Safari"
        elif "firefox" in ua_lc:
            browser = "Firefox"
        elif "edge" in ua_lc:
            browser = "Edge"
        elif "msie" in ua_lc or "trident" in ua_lc:
            browser = "IE"

        os_name = "Unknown"
        if "windows" in ua_lc:
            os_name = "Windows"
        elif "mac os" in ua_lc or "macintosh" in ua_lc:
            os_name = "macOS"
        elif "android" in ua_lc:
            os_name = "Android"
        elif "iphone" in ua_lc or "ipad" in ua_lc:
            os_name = "iOS"
        elif "linux" in ua_lc:
            os_name = "Linux"

        is_mobile = "mobi" in ua_lc or "android" in ua_lc or "iphone" in ua_lc
        return {"browser": browser, "os": os_name, "is_mobile": bool(is_mobile), "raw": ua}

def _date_to_str(d):
    """Safe date->YYYY-MM-DD (or empty)."""
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    return d or ""


def _dashboard_endpoint_for_role(role: str) -> str:
    role_lc = _normalize_role(role)
    return ROLE_CONFIG.get(role_lc, {}).get("endpoint", "login.login")


def _normalize_role(value: str | None) -> str:
    raw = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"operations_management", "customer_support", "customer_supports"}:
        return "customer_support"
    return raw


def _account_group_id_for_doc(user_doc: dict | None) -> str:
    if not user_doc:
        return ""
    return str(user_doc.get("account_group_id") or user_doc.get("primary_user_id") or user_doc.get("_id") or "")


def _linked_role_docs(user_doc: dict | None) -> list[dict]:
    if not user_doc:
        return []
    group_id = _account_group_id_for_doc(user_doc)
    username = (user_doc.get("username") or "").strip()
    docs = list(users_col.find({"account_group_id": group_id}).sort([("date_registered", 1), ("created_at", 1)])) if group_id else []
    if username:
        extra = list(users_col.find({"username": username}).sort([("date_registered", 1), ("created_at", 1)]))
        seen = {str(d.get("_id") or "") for d in docs}
        for row in extra:
            rid = str(row.get("_id") or "")
            if rid not in seen:
                docs.append(row)
                seen.add(rid)
    if not docs and user_doc.get("_id") is not None:
        docs = [user_doc]
    return docs


def _find_identity_doc(user_id: str) -> dict | None:
    cache = getattr(g, '_identity_documents', {}) if has_request_context() else {}
    if user_id in cache:
        return cache[user_id]
    query = {"_id": ObjectId(user_id)} if ObjectId.is_valid(user_id) else {"_id": user_id}
    doc = users_col.find_one(query)
    archived = not bool(doc)
    if archived:
        doc = exited_users_col.find_one(query)
    if has_request_context():
        cache[user_id] = doc
        g._identity_documents = cache
        sources = getattr(g, '_identity_archived', {})
        sources[user_id] = archived
        g._identity_archived = sources
    return doc


def get_available_role_profiles(user_doc: dict | None) -> list[dict]:
    if not user_doc:
        return []
    primary_user_id = str(user_doc.get("primary_user_id") or user_doc.get("_id") or "")
    profiles = []
    seen = set()
    for row in _linked_role_docs(user_doc):
        user_id = str(row.get("_id") or "")
        role = _normalize_role(row.get("role"))
        if not user_id or not role or (user_id, role) in seen:
            continue
        seen.add((user_id, role))
        profiles.append({
            "user_id": user_id,
            "role": role,
            "role_label": role.replace("_", " ").title(),
            "branch": row.get("branch") or row.get("store_name") or row.get("store") or "",
            "is_primary": user_id == primary_user_id,
        })
    profiles.sort(key=lambda x: (not x["is_primary"], x["role_label"]))
    return profiles


def _clear_role_sessions() -> None:
    for key in ROLE_SESSION_KEYS:
        session.pop(key, None)


def _safe_next_url(next_url: str | None) -> str | None:
    if not next_url:
        return None
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return None
    if not parsed.path.startswith("/"):
        return None
    return next_url


def _is_truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "on"}
    return False


def get_current_identity() -> dict:
    cached = getattr(g, "_current_identity", None)
    if cached is not None:
        return cached

    if getattr(current_user, "is_authenticated", False):
        role = (getattr(current_user, "role", "") or "").lower()
        user_id = str(getattr(current_user, "id", "") or "")
    else:
        role = (session.get("role") or "").lower().strip()
        user_id = str(session.get("user_id") or "")

    if not role or not user_id:
        role_map = [
            ("executive_id", "executive"),
            ("manager_id", "manager"),
            ("admin_id", "admin"),
            ("inventory_id", "inventory"),
            ("customer_support_id", "customer_support"),
            ("operations_management_id", "customer_support"),
            ("agent_id", "agent"),
            ("hr_id", "hr"),
            ("accounting_id", "accounting"),
        ]
        for key, r in role_map:
            if session.get(key):
                role = r
                user_id = str(session.get(key))
                break

    if not role or not user_id:
        g._current_identity = {"is_authenticated": False}
        return g._current_identity

    user_doc = _find_identity_doc(user_id)
    name = (user_doc or {}).get("name") or (user_doc or {}).get("username") or "User"
    username = (user_doc or {}).get("username") or getattr(current_user, "username", "") or ""
    is_main_admin = _is_truthy((user_doc or {}).get("main_admin"))
    available_roles = get_available_role_profiles(user_doc or {})

    g._current_identity = {
        "is_authenticated": True,
        "role": role,
        "user_id": user_id,
        "name": name,
        "username": username,
        "is_main_admin": is_main_admin,
        "dashboard_endpoint": _dashboard_endpoint_for_role(role),
        "account_group_id": _account_group_id_for_doc(user_doc or {}),
        "available_roles": available_roles,
    }
    return g._current_identity


def role_required(*roles):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            ident = get_current_identity()
            if not ident.get("is_authenticated"):
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Unauthorized"}), 401
                return redirect(url_for("login.login", next=request.path))
            requested_roles = set(roles or ())
            full_inventory_access = (
                ident.get("role") in {"executive", "inventory"}
                and "inventory" in requested_roles
                and (request.path.startswith("/api/inventory") or request.path.startswith("/inventory/app"))
            )
            if full_inventory_access:
                # Inventory staff and executives share full access inside the
                # inventory module. This flag is request-local, never persisted.
                ident["is_main_admin"] = True
            main_admin_allowed = bool(ident.get("is_main_admin")) and bool({"admin", "inventory"} & requested_roles)
            manager_support_allowed = (
                ident.get('role') == 'manager'
                and 'customer_support' in requested_roles
                and request.path.startswith('/api/customer-support/')
            )
            if manager_support_allowed:
                from services.manager_suite_scope import manager_scope
                manager_scope(db)
            # Suite access keeps the manager identity/session intact across tabs.
            if roles and ident.get("role") not in roles and not main_admin_allowed and not manager_support_allowed:
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Forbidden"}), 403
                return "Forbidden", 403
            return fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator

@login_bp.record_once
def on_load(state):
    bcrypt.init_app(state.app)
    ensure_login_log_indexes()
    ensure_user_indexes()

@login_bp.route('/')
def home():
    ident = get_current_identity()
    if ident.get("is_authenticated"):
        return redirect(url_for(ident["dashboard_endpoint"]))
    return redirect(url_for('login.login'))

# ---------------------------
# Role helpers (easy to extend)
# ---------------------------
def _set_role_session(role: str, user_data: dict) -> tuple[str, str]:
    """
    Sets the appropriate session keys for a role and returns (session_key, redirect_endpoint).
    Add new roles here as needed.
    """
    if login_blocked(user_data):
        raise ValueError('Login is blocked for this account.')
    session['auth_version'] = str(user_data.get('auth_version', '0'))
    user_id_str = str(user_data['_id'])
    username = user_data.get('username') or user_data.get('name', '')

    role_lc = _normalize_role(role)
    cfg = ROLE_CONFIG.get(role_lc)
    if not cfg:
        raise ValueError(f"Unsupported role: {role}")

    _clear_role_sessions()
    session.pop('daily_progress_seen', None)
    session.pop('daily_progress_token', None)
    session['user_id'] = user_id_str
    session['role'] = role_lc
    session['active_role_user_id'] = user_id_str
    session['account_group_id'] = _account_group_id_for_doc(user_data)
    session_key = cfg["session_key"]
    session[session_key] = user_id_str
    if cfg.get("name_key"):
        session[cfg["name_key"]] = user_data.get("name", username)
    if role_lc == 'agent':
        login_user(User(user_data), remember=True)
    else:
        logout_user()
    return session_key, cfg['endpoint']

# ---------------------------
# Login
# ---------------------------
@login_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        geo_lat = request.form.get('geo_lat')
        geo_lng = request.form.get('geo_lng')
        geo_accuracy = request.form.get('geo_accuracy')
        geo_ts = request.form.get('geo_ts')
        geo_available_raw = (request.form.get('geo_available') or '').strip().lower()
        geo_reason = (request.form.get('geo_reason') or '').strip()

        try:
            geo_lat_f = float(geo_lat)
            geo_lng_f = float(geo_lng)
            geo_accuracy_f = float(geo_accuracy) if geo_accuracy is not None else None
        except Exception:
            geo_lat_f = None
            geo_lng_f = None
            geo_accuracy_f = None
        geo_available = geo_available_raw == "true"
        if geo_lat_f is not None and geo_lng_f is not None:
            if not (-90 <= geo_lat_f <= 90 and -180 <= geo_lng_f <= 180):
                geo_lat_f = None
                geo_lng_f = None
                geo_available = False

        user_candidates = list(users_col.find({"username": username}).sort([("date_registered", 1), ("created_at", 1)]))
        # Archived employees remain outside the live users collection. Only
        # HR-enabled records are eligible for authentication. Include these even
        # when the username also has another live role profile.
        archived_candidates = list(exited_users_col.find({
            "username": username,
            "is_active": True,
            "account_locked": {"$ne": True},
        }).sort([("date_registered", 1), ("created_at", 1)]))
        seen_candidate_ids = {str(row.get("_id")) for row in user_candidates}
        user_candidates.extend(
            row for row in archived_candidates if str(row.get("_id")) not in seen_candidate_ids
        )

        if user_candidates:
            user_data = None
            stored_hash = None
            role = ""
            for candidate in user_candidates:
                cand_hash = candidate.get('password')
                ok = False
                if cand_hash and str(cand_hash).startswith("$2"):
                    try:
                        ok = bcrypt_lib.checkpw(password.encode("utf-8"), str(cand_hash).encode("utf-8"))
                    except Exception:
                        ok = False
                else:
                    ok = (password == (cand_hash or ""))
                if ok:
                    user_data = candidate
                    stored_hash = cand_hash
                    role = (candidate.get('role') or '').lower()
                    break

            if user_data is None:
                flash("Invalid username or password.", "error")
                return redirect(url_for('login.login'))

            if agent_pending_approval(user_data):
                flash('Your account is under review. Please wait for approval from an executive, HR or admin.', 'error')
                return redirect(url_for('login.login'))

            if login_blocked(user_data):
                flash("Your login has been blocked. Contact an executive.", "error")
                return redirect(url_for('login.login'))

            # Optional: respect soft-disable flags
            status_val = str(user_data.get('status') or '').strip().lower()
            if status_val in ('not active', 'disabled', 'inactive'):
                flash("Your account is not active. Contact an administrator.", "error")
                return redirect(url_for('login.login'))

            if user_data.get("account_locked") is True or user_data.get("is_active") is False:
                flash("Your account is not active. Contact an administrator.", "error")
                return redirect(url_for('login.login'))

            ok = True
            if stored_hash and not str(stored_hash).startswith("$2"):
                try:
                    new_hash = bcrypt_lib.hashpw(password.encode("utf-8"), bcrypt_lib.gensalt(rounds=12)).decode("utf-8")
                    source_col = exited_users_col if user_data.get("employment_status") == "Exited" else users_col
                    source_col.update_many(
                        {"_id": user_data.get("_id")},
                        {"$set": {"password": new_hash, "updated_at": datetime.utcnow()}},
                    )
                    stored_hash = new_hash
                except Exception:
                    pass

            if ok:
                session.permanent = True
                try:
                    primary_user_id = str(user_data.get("primary_user_id") or user_data.get("_id") or "")
                    active_doc = user_data
                    if primary_user_id and primary_user_id != str(user_data.get("_id") or ""):
                        primary_doc = _find_identity_doc(primary_user_id)
                        if primary_doc:
                            active_doc = primary_doc
                            role = (primary_doc.get("role") or role).lower()
                    session_key, endpoint = _set_role_session(role, active_doc)
                except ValueError:
                    flash("Your role is not supported. Contact an administrator.", "error")
                    return redirect(url_for('login.login'))

                # Log login activity
                ip = request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or request.remote_addr
                user_agent = request.headers.get('User-Agent')
                location_data = {}
                device = _parse_device(user_agent)
                user_id = str(active_doc['_id'])

                logins_col.insert_one({
                    session_key: user_id,
                    "user_id": user_id,
                    "username": active_doc.get('username'),
                    "role": role,
                    "ip": ip,
                    "user_agent": user_agent,
                    "device": device,
                    "geo": {
                        "lat": geo_lat_f,
                        "lng": geo_lng_f,
                        "accuracy_m": geo_accuracy_f,
                        "source": "browser",
                        "browser_ts": geo_ts,
                    },
                    "location_available": bool(geo_available and geo_lat_f is not None and geo_lng_f is not None),
                    "location_reason": geo_reason or None,
                    "ip_location": location_data,
                    "ip_enrichment": {"state": "pending", "attempts": 0, "next_attempt": datetime.utcnow()},
                    "timestamp": datetime.utcnow()
                })

                # Redirect
                next_url = _safe_next_url(request.args.get("next") or request.form.get("next") or session.pop("next", None))
                if next_url:
                    return redirect(next_url)
                return redirect(url_for(endpoint))

        flash("Invalid username or password.", "error")
        return redirect(url_for('login.login'))

    next_qs = _safe_next_url(request.args.get("next"))
    if next_qs:
        session["next"] = next_qs
    ident = get_current_identity()
    if ident.get("is_authenticated"):
        return redirect(url_for(ident["dashboard_endpoint"]))
    return render_template('login_react.html', next_url=next_qs)

# ---------------------------
# Admin dashboard (local route) ✅ UPDATED to pass admin + complaint summaries
# ---------------------------
@login_bp.route('/admin/dashboard')
def admin_dashboard():
    if 'admin_id' not in session:
        return redirect(url_for('login.login'))

    # Admin doc for hero section/profile chip
    try:
        admin = users_col.find_one({'_id': ObjectId(session['admin_id'])}) or {}
    except Exception:
        admin = {}

    # Complaint summary tiles
    now = datetime.utcnow()
    start_today = datetime(now.year, now.month, now.day)
    end_today = start_today + timedelta(days=1)

    q_unresolved = {"status": {"$nin": ["Resolved", "Closed"]}}
    q_breaching  = {"status": {"$nin": ["Resolved", "Closed"]}, "sla_due": {"$lte": now}}
    q_resolved_30 = {
        "status": {"$in": ["Resolved", "Closed"]},
        "date_closed": {"$gte": now - timedelta(days=30)}
    }

    stats = {
        "open": complaints_col.count_documents(q_unresolved),
        "breaching": complaints_col.count_documents(q_breaching),
        "resolved_30": complaints_col.count_documents(q_resolved_30),
        "opened_today": complaints_col.count_documents({"created_at": {"$gte": start_today, "$lt": end_today}}),
        "closed_today": complaints_col.count_documents({
            "status": {"$in": ["Resolved", "Closed"]},
            "date_closed": {"$gte": start_today, "$lt": end_today}
        }),
    }

    # Top issue types (small chart/list)
    pipeline = [
        {"$group": {"_id": "$issue_type", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 6},
    ]
    issue_tops_raw = list(complaints_col.aggregate(pipeline))
    issue_tops = [{"issue": (x.get("_id") or "Uncategorized"), "count": x.get("n", 0)} for x in issue_tops_raw]

    # Recent complaints (compact list)
    recent = list(complaints_col.find({}).sort([("created_at", -1)]).limit(8))
    for r in recent:
        r["_id"] = str(r["_id"])
        r["date_reported"] = _date_to_str(r.get("date_reported"))
        r["date_closed"]   = _date_to_str(r.get("date_closed"))
        r["sla_due"]       = _date_to_str(r.get("sla_due"))
        r["created_at"]    = _date_to_str(r.get("created_at"))
        r["updated_at"]    = _date_to_str(r.get("updated_at"))

    # Render with rich context (admin_dashboard.html expects these now)
    return render_template(
        'admin_dashboard.html',
        admin=admin,
        stats=stats,
        issue_tops=issue_tops,
        recent=recent
    )

# ---------------------------
# Admin: lightweight counts for sidebar badges ✅ NEW
# ---------------------------
@login_bp.route('/admin/complaints_open_count')
def admin_complaints_open_count():
    if 'admin_id' not in session:
        return {"ok": False, "message": "Unauthorized"}, 401

    now = datetime.utcnow()
    open_count = complaints_col.count_documents({"status": {"$nin": ["Resolved", "Closed"]}})
    breaching  = complaints_col.count_documents({"status": {"$nin": ["Resolved", "Closed"]}, "sla_due": {"$lte": now}})
    return {"ok": True, "open": int(open_count), "breaching": int(breaching)}

# ---------------------------
# Manager: Agent management (unchanged)
# ---------------------------
@login_bp.route('/agents')
def agent_list():
    if 'manager_id' not in session:
        return redirect(url_for('login.login'))

    try:
        manager_id = ObjectId(session['manager_id'])
    except Exception:
        flash("Invalid manager session ID.", "error")
        return redirect(url_for('login.logout'))

    search = (request.args.get('search') or '').strip()
    status = (request.args.get('status') or 'all').strip().lower()
    per_page = 10
    try:
        page = max(1, int(request.args.get('page', 1)))
    except Exception:
        page = 1

    query = {'manager_id': manager_id, 'role': 'agent'}
    if search:
        query['$or'] = [
            {'name': {'$regex': search, '$options': 'i'}},
            {'phone': {'$regex': search, '$options': 'i'}}
        ]

    if status == 'active':
        query['status'] = {'$in': ['Active', 'active']}
    elif status == 'under_review':
        query['status'] = {'$regex': '^under review$', '$options': 'i'}
    elif status == 'not_active':
        query['status'] = {'$nin': ['Active', 'active']}

    total_agents = users_col.count_documents({'manager_id': manager_id, 'role': 'agent'})
    total_active = users_col.count_documents({
        'manager_id': manager_id,
        'role': 'agent',
        'status': {'$in': ['Active', 'active']}
    })
    total_not_active = max(total_agents - total_active, 0)

    total_agents_filtered = users_col.count_documents(query)
    total_pages = max(1, (total_agents_filtered + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    skip = (page - 1) * per_page
    agents = list(users_col.find(query).sort([('name', 1)]).skip(skip).limit(per_page))

    return render_template(
        'agent_list.html',
        agents=agents,
        search=search,
        status=status,
        page=page,
        total_pages=total_pages,
        total_agents=total_agents,
        total_active=total_active,
        total_not_active=total_not_active
    )

@login_bp.route('/agent/<agent_id>')
def view_agent(agent_id):
    if 'manager_id' not in session:
        return redirect(url_for('login.login'))

    try:
        oid = ObjectId(agent_id)
        manager_oid = ObjectId(session['manager_id'])
    except Exception:
        return "Invalid agent ID."

    agent = users_col.find_one({'_id': oid, 'manager_id': manager_oid, 'role': 'agent'})
    if not agent:
        return "Agent not found or access denied."

    return render_template('profile_agent.html', agent=agent)

@login_bp.route('/agent/<agent_id>/toggle_status', methods=['POST'])
def toggle_agent_status(agent_id):
    if 'manager_id' not in session:
        return redirect(url_for('login.login'))

    try:
        oid = ObjectId(agent_id)
        manager_oid = ObjectId(session['manager_id'])
    except Exception:
        flash("Invalid agent ID.", "error")
        return redirect(url_for('login.agent_list'))

    agent = users_col.find_one({'_id': oid, 'manager_id': manager_oid})
    if not agent:
        flash("Agent not found or unauthorized.", "error")
        return redirect(url_for('login.agent_list'))

    if agent_pending_approval(agent):
        flash('This agent is under review. An executive, HR or admin must approve the account.', 'error')
        return redirect(url_for('login.view_agent', agent_id=agent_id))
    new_status = 'Not Active' if agent.get('status') == 'Active' else 'Active'
    users_col.update_one({'_id': oid}, {'$set': {'status': new_status}})
    flash(f"Agent status changed to {new_status}.", "success")
    return redirect(url_for('login.view_agent', agent_id=agent_id))

@login_bp.route('/agent/<agent_id>/edit', methods=['GET', 'POST'])
def edit_agent(agent_id):
    if 'manager_id' not in session:
        return redirect(url_for('login.login'))

    try:
        oid = ObjectId(agent_id)
        manager_oid = ObjectId(session['manager_id'])
    except Exception:
        flash("Invalid agent ID.", "error")
        return redirect(url_for('login.agent_list'))

    agent = users_col.find_one({'_id': oid, 'manager_id': manager_oid})
    if not agent:
        flash("Agent not found or unauthorized.", "error")
        return redirect(url_for('login.agent_list'))

    if request.method == 'POST':
        updated = {
            'name': request.form.get('name'),
            'phone': request.form.get('phone'),
            'email': request.form.get('email'),
            'gender': request.form.get('gender'),
            'branch': request.form.get('branch'),
            'position': request.form.get('position'),
            'location': request.form.get('location'),
            'start_date': request.form.get('start_date'),
            'image_url': request.form.get('image_url'),
            'assets': [x.strip() for x in (request.form.get('assets') or '').split(',') if x.strip()],
        }
        if request.form.get('password'):
            updated['password'] = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')

        users_col.update_one({'_id': oid}, {'$set': updated})
        flash('Agent details updated successfully.', 'success')
        return redirect(url_for('login.view_agent', agent_id=agent_id))

    return render_template('edit_agent.html', agent=agent)

# ---------------------------
# Logout (all roles)
# ---------------------------
@login_bp.route('/logout')
def logout():
    session.clear()
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for('login.login'))


@login_bp.route('/auth/switch-role', methods=['POST'])
def switch_role():
    ident = get_current_identity()
    if not ident.get("is_authenticated"):
        if request.is_json:
            return jsonify(ok=False, message="Unauthorized"), 401
        return redirect(url_for('login.login', next=request.path))

    payload = request.get_json(silent=True) if request.is_json else {}
    target_role = _normalize_role((payload or {}).get("role") or request.form.get("role"))
    if not target_role:
        return jsonify(ok=False, message="Role is required."), 400

    active_id = str(ident.get("user_id") or "")
    active_doc = users_col.find_one({"_id": ObjectId(active_id)}) if ObjectId.is_valid(active_id) else users_col.find_one({"_id": active_id})
    if not active_doc:
        return jsonify(ok=False, message="Active profile not found."), 404

    target_doc = None
    for row in _linked_role_docs(active_doc):
        if _normalize_role(row.get("role")) == target_role:
            target_doc = row
            break
    if not target_doc:
        return jsonify(ok=False, message="Selected role is not assigned."), 404

    try:
        _, endpoint = _set_role_session(target_role, target_doc)
    except ValueError:
        return jsonify(ok=False, message="Unsupported role."), 400

    logins_col.insert_one({
        "user_id": str(target_doc.get("_id") or ""),
        "username": target_doc.get("username") or "",
        "role": target_role,
        "ip": request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or request.remote_addr,
        "user_agent": request.headers.get('User-Agent'),
        "device": _parse_device(request.headers.get('User-Agent')),
        "timestamp": datetime.utcnow(),
        "switch_event": True,
        "switch_from_role": ident.get("role") or "",
    })

    redirect_url = url_for(endpoint)
    if request.is_json:
        return jsonify(ok=True, redirect_url=redirect_url)
    return redirect(redirect_url)
