from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_bcrypt import Bcrypt
from datetime import datetime
from bson.objectid import ObjectId
from db import db

register_manager_bp = Blueprint('register_manager', __name__)
bcrypt = Bcrypt()

users_col = db.users

# Ensure bcrypt is initialized with the app
@register_manager_bp.record_once
def on_load(state):
    bcrypt.init_app(state.app)

@register_manager_bp.route('/register_manager', methods=['GET', 'POST'])
def register_manager():
    from login import get_current_identity
    from flask import jsonify
    ident=get_current_identity()
    if not ident.get("is_authenticated"):return redirect(url_for("login.login"))
    if ident.get("role") not in {"hr","admin","executive"}:return "Forbidden",403
    if request.method=="POST":return jsonify(ok=False,message="Manager recruitment requires HR interview, approval and onboarding.",redirect_url="/hr/recruits"),409
    return redirect("/hr/recruits")
