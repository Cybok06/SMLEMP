# hr_backend/hr_employee_add.py

from datetime import datetime
from typing import Dict, Any, List
import traceback
import re

import requests
from werkzeug.utils import secure_filename
from bson import ObjectId
from flask import (
    request,
    redirect,
    url_for,
    jsonify,
)

from flask_bcrypt import Bcrypt

from db import db
from services.manager_suite_scope import scoped_db

db = scoped_db(db)
from services.activity_audit import audit_action
from hr_backend.hr_dashboard import hr_bp, _hr_access_guard
from hr_backend.hr_employees_directory import (
    RECRUITMENT_DEFAULT_STEPS,
    users_col,
)
from hr_backend.hr_roles import roles_col, DEFAULT_ROLE_KEYS, normalize_role_key

bcrypt = Bcrypt()

# -------------------------------
# Cloudflare Images
# -------------------------------
CF_ACCOUNT_ID   = "63e6f91eec9591f77699c4b434ab44c6"
CF_IMAGES_TOKEN = "Brz0BEfl_GqEUjEghS2UEmLZhK39EUmMbZgu_hIo"
CF_HASH         = "h9fmMoa1o2c2P55TcWJGOg"
DEFAULT_VARIANT = "public"

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

images_col = db.images


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _clean_phone(s: str) -> str:
    s = (s or "").strip()
    # keep + and digits only
    s = re.sub(r"[^\d+]", "", s)
    return s


def _clean_username(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", "", s)
    return s.lower()


def _upload_employee_image_to_cloudflare(file_obj) -> str | None:
    if not file_obj or not file_obj.filename:
        return None

    if not _allowed_file(file_obj.filename):
        return None

    try:
        direct_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/images/v2/direct_upload"
        headers = {"Authorization": f"Bearer {CF_IMAGES_TOKEN}"}

        res = requests.post(direct_url, headers=headers, data={}, timeout=20)
        try:
            j = res.json()
        except Exception:
            traceback.print_exc()
            return None

        if not j.get("success"):
            traceback.print_exc()
            return None

        upload_url = j["result"]["uploadURL"]
        image_id   = j["result"]["id"]

        up = requests.post(
            upload_url,
            files={
                "file": (
                    secure_filename(file_obj.filename),
                    file_obj.stream,
                    file_obj.mimetype or "application/octet-stream",
                )
            },
            timeout=60,
        )

        try:
            uj = up.json()
        except Exception:
            traceback.print_exc()
            return None

        if not uj.get("success"):
            traceback.print_exc()
            return None

        variant   = DEFAULT_VARIANT
        image_url = f"https://imagedelivery.net/{CF_HASH}/{image_id}/{variant}"

        images_col.insert_one(
            {
                "provider": "cloudflare_images",
                "context": "hr_employee",
                "image_id": image_id,
                "variant": variant,
                "url": image_url,
                "original_filename": secure_filename(file_obj.filename),
                "mimetype": file_obj.mimetype,
                "created_at": datetime.utcnow(),
            }
        )

        return image_url

    except Exception:
        traceback.print_exc()
        return None


@hr_bp.route("/employees/add", methods=["POST"])
@audit_action("employee.created", "Created Employee", entity_type="employee")
def add_employee():
    from login import get_current_identity
    ident=get_current_identity()
    if not ident.get("is_authenticated"):return jsonify(success=False,message="Unauthorized"),401
    if ident.get('role') == 'manager':
        from services.manager_suite_scope import manager_scope
        manager_scope(db)
        destination = '/hr/recruits?stage=branch_interview_completed'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(success=False, message='Register the candidate, complete the branch interview, then onboard in Recruitment. Main HR approval is not required.', redirect_url=destination), 409
        return redirect(destination)
    if ident.get("role")!="hr":return jsonify(success=False,message="Final recruitment and onboarding must be completed by HR."),403
    if request.headers.get("X-Requested-With")=="XMLHttpRequest":
        return jsonify(success=False,message="Complete branch interview, HR interview and approval, then onboard from Recruitment.",redirect_url="/hr/recruits?stage=approved"),409
    return redirect("/hr/recruits?stage=approved")

