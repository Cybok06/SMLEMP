from flask import Blueprint, render_template, request, session, redirect, url_for
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from datetime import datetime
from db import db
login_logs_bp = Blueprint('login_logs', __name__)

# MongoDB connection

login_logs_col = db.login_logs


def _parse_user_agent(user_agent_str: str) -> dict:
    ua = user_agent_str or ""
    try:
        from user_agents import parse as ua_parse

        parsed = ua_parse(ua)
        return {
            "browser": parsed.browser.family or "Unknown",
            "platform": parsed.os.family or "Unknown",
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

        platform = "Unknown"
        if "windows" in ua_lc:
            platform = "Windows"
        elif "mac os" in ua_lc or "macintosh" in ua_lc:
            platform = "macOS"
        elif "android" in ua_lc:
            platform = "Android"
        elif "iphone" in ua_lc or "ipad" in ua_lc:
            platform = "iOS"
        elif "linux" in ua_lc:
            platform = "Linux"

        return {"browser": browser, "platform": platform}

@login_logs_bp.route('/login_logs', methods=['GET'])
def login_logs():
    if not any(role in session for role in ['admin_id', 'manager_id', 'agent_id']):
        return redirect(url_for('login.login'))

    user_id = str(session.get('admin_id') or session.get('manager_id') or session.get('agent_id'))
    username = session.get('username') or session.get('manager_name', 'Agent')

    selected_date = request.args.get('date')
    query = {'$or': [
        {'admin_id': user_id},
        {'manager_id': user_id},
        {'agent_id': user_id}
    ]}

    if selected_date:
        try:
            date_obj = datetime.strptime(selected_date, "%Y-%m-%d")
            start = datetime(date_obj.year, date_obj.month, date_obj.day)
            end = datetime(date_obj.year, date_obj.month, date_obj.day, 23, 59, 59)
            query['timestamp'] = {"$gte": start, "$lte": end}
        except:
            pass

    logs_raw = login_logs_col.find(query).sort("timestamp", -1)
    logs = []

    for log in logs_raw:
        timestamp = log.get("timestamp")
        user_agent_str = log.get("user_agent", "")
        ua = _parse_user_agent(user_agent_str)

        logs.append({
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else "Unknown",
            "ip": log.get("ip", "N/A"),
            "browser": ua.get("browser", "Unknown"),
            "platform": ua.get("platform", "Unknown"),
            "location": {k: v for k, v in log.get("location", {}).items() if v}
        })

    return render_template('login_logs.html', logs=logs, username=username, selected_date=selected_date)
