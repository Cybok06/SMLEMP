from __future__ import annotations

from datetime import datetime, timedelta
import re
import traceback
from typing import Any

import requests
from bson import ObjectId
from flask import jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from db import db
from services.manager_suite_scope import scoped_db

db = scoped_db(db)
from hr_backend.hr_dashboard import hr_bp, _hr_access_guard
from hr_backend.hr_employee_add import CF_ACCOUNT_ID, CF_HASH, CF_IMAGES_TOKEN, DEFAULT_VARIANT

recruits_col = db["hr_recruits"]
images_col = db.images

RECRUIT_STATUSES = ["New", "Reviewed", "Shortlisted", "Rejected", "Hired"]
ALLOWED_CV_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
ALLOWED_CV_MIMETYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_CV_SIZE = 6 * 1024 * 1024


def _ensure_indexes() -> None:
    try:
        recruits_col.create_index([("status", 1), ("submitted_at", -1)], background=True)
        recruits_col.create_index([("submitted_at", -1)], background=True)
        recruits_col.create_index([("full_name", 1), ("phone", 1), ("position_applied", 1)], background=True)
    except Exception:
        pass


_ensure_indexes()


def _clean_text(value: Any, max_len: int = 160) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text[:max_len]


def _clean_note(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:1200]


def _valid_email(value: str) -> bool:
    if not value:
        return True
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_CV_EXTENSIONS


def _file_size(file_obj) -> int:
    pos = file_obj.stream.tell()
    file_obj.stream.seek(0, 2)
    size = file_obj.stream.tell()
    file_obj.stream.seek(pos)
    return int(size or 0)


def _upload_cv_to_cloudflare(file_obj) -> tuple[str | None, str | None, str | None]:
    if not file_obj or not file_obj.filename:
        return None, None, "CV image is required."
    if not _allowed_file(file_obj.filename):
        return None, None, "Only JPG, JPEG, PNG, and WEBP CV images are allowed."
    if file_obj.mimetype not in ALLOWED_CV_MIMETYPES:
        return None, None, "Only JPG, JPEG, PNG, and WEBP CV images are allowed."
    if _file_size(file_obj) > MAX_CV_SIZE:
        return None, None, "CV image is too large. Maximum size is 6MB."

    try:
        direct_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/images/v2/direct_upload"
        headers = {"Authorization": f"Bearer {CF_IMAGES_TOKEN}"}
        res = requests.post(direct_url, headers=headers, data={}, timeout=20)
        payload = res.json()
        if not payload.get("success"):
            return None, None, "Cloudflare upload could not be prepared."

        upload_url = payload["result"]["uploadURL"]
        image_id = payload["result"]["id"]
        file_obj.stream.seek(0)
        upload = requests.post(
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
        upload_payload = upload.json()
        if not upload_payload.get("success"):
            return None, None, "CV upload failed."

        image_url = f"https://imagedelivery.net/{CF_HASH}/{image_id}/{DEFAULT_VARIANT}"
        images_col.insert_one(
            {
                "provider": "cloudflare_images",
                "context": "hr_recruit_cv",
                "image_id": image_id,
                "variant": DEFAULT_VARIANT,
                "url": image_url,
                "original_filename": secure_filename(file_obj.filename),
                "mimetype": file_obj.mimetype,
                "created_at": datetime.utcnow(),
            }
        )
        return image_url, image_id, None
    except Exception:
        traceback.print_exc()
        return None, None, "Unable to upload CV image right now."


def _serialize_recruit(doc: dict[str, Any]) -> dict[str, Any]:
    submitted_at = doc.get("submitted_at")
    updated_at = doc.get("updated_at")
    return {
        "id": str(doc.get("_id") or ""),
        "full_name": doc.get("full_name") or "",
        "phone": doc.get("phone") or "",
        "email": doc.get("email") or "",
        "location": doc.get("location") or "",
        "position_applied": doc.get("position_applied") or "",
        "note": doc.get("note") or "",
        "cv_image_url": doc.get("cv_image_url") or "",
        "cv_image_id": doc.get("cv_image_id") or "",
        "status": doc.get("status") or "New",
        "submitted_at": submitted_at.isoformat() if isinstance(submitted_at, datetime) else str(submitted_at or ""),
        "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else str(updated_at or ""),
    }


def _admin_can_delete() -> bool:
    role = (session.get("role") or "").lower().strip()
    return bool(session.get("admin_id") or session.get("executive_id") or role in {"admin", "executive"})


@hr_bp.route("/recruits")
def recruits_page():
    from hr_backend.branch_recruitment import hr_page
    return hr_page()


@hr_bp.route("/recruits/data")
def recruits_data():
    if not _hr_access_guard():
        return jsonify({"ok": False, "message": "Not authorized."}), 401

    search = _clean_text(request.args.get("search"), 120)
    status = _clean_text(request.args.get("status"), 40)
    date_from = _clean_text(request.args.get("date_from"), 20)
    date_to = _clean_text(request.args.get("date_to"), 20)

    query: dict[str, Any] = {}
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"location": {"$regex": search, "$options": "i"}},
            {"position_applied": {"$regex": search, "$options": "i"}},
        ]
    if status in RECRUIT_STATUSES:
        query["status"] = status

    date_query: dict[str, Any] = {}
    if date_from:
        try:
            date_query["$gte"] = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    if date_to:
        try:
            date_query["$lt"] = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            pass
    if date_query:
        query["submitted_at"] = date_query

    docs = list(recruits_col.find(query).sort("submitted_at", -1).limit(500))
    return jsonify({"ok": True, "recruits": [_serialize_recruit(doc) for doc in docs], "can_delete": _admin_can_delete()})


@hr_bp.route("/recruits/new-count")
def recruits_new_count():
    if not _hr_access_guard():
        return jsonify({"ok": False, "count": 0}), 401
    return jsonify({"ok": True, "count": recruits_col.count_documents({"$or":[{"stage":"new"},{"stage":{"$exists":False},"status":{"$nin":["Rejected","Hired"]}}]})})


@hr_bp.route("/recruits/<recruit_id>/status", methods=["POST"])
def update_recruit_status(recruit_id: str):
    if not _hr_access_guard():return jsonify(ok=False,message="Not authorized."),401
    return jsonify(ok=False,message="Use the staged recruitment workflow. HR interview and final HR approval are required."),409


@hr_bp.route("/recruits/<recruit_id>", methods=["DELETE"])
def delete_recruit(recruit_id: str):
    if not _hr_access_guard():
        return jsonify({"ok": False, "message": "Not authorized."}), 401
    if not _admin_can_delete():
        return jsonify({"ok": False, "message": "Only admin or executive users can delete recruits."}), 403
    oid = ObjectId(recruit_id) if ObjectId.is_valid(recruit_id) else None
    if oid is None:
        return jsonify({"ok": False, "message": "Invalid recruit id."}), 400
    doc=recruits_col.find_one({"_id":oid}) or {}
    if doc.get("stage") or doc.get("history"):
        return jsonify(ok=False,message="Recruitment audit records must be preserved. Use an HR rejection decision."),409
    result = recruits_col.delete_one({"_id": oid})
    if result.deleted_count == 0:
        return jsonify({"ok": False, "message": "Recruit not found."}), 404
    return jsonify({"ok": True})


@hr_bp.route("/recruits/apply", methods=["GET"], endpoint="public_recruit_apply")
def public_recruit_apply():
    return render_template("hr_pages/public_recruit_apply.html")


@hr_bp.route("/recruits/apply", methods=["POST"], endpoint="public_recruit_submit")
def public_recruit_submit():
    form = request.form
    full_name = _clean_text(form.get("full_name"), 120)
    phone = _clean_text(form.get("phone"), 40)
    email = _clean_text(form.get("email"), 120).lower()
    location = _clean_text(form.get("location"), 120)
    position = _clean_text(form.get("position_applied"), 120)
    note = _clean_note(form.get("note"))

    if not full_name or not phone or not location or not position:
        return jsonify({"ok": False, "message": "Full name, phone, location, and position are required."}), 400
    if not _valid_email(email):
        return jsonify({"ok": False, "message": "Enter a valid email address."}), 400

    image_url, image_id, upload_error = _upload_cv_to_cloudflare(request.files.get("cv_image"))
    if upload_error:
        return jsonify({"ok": False, "message": upload_error}), 400

    now = datetime.utcnow()
    doc = {
        "full_name": full_name,
        "phone": phone,
        "email": email,
        "location": location,
        "position_applied": position,
        "note": note,
        "cv_image_url": image_url,
        "cv_image_id": image_id,
        "status": "New",
        "submitted_at": now,
        "updated_at": now,
    }
    from hr_backend.branch_recruitment import notice
    doc.update(stage="new",version=0,source="public",recruiting_branch=None,history=[{"action":"Public application submitted","actor":{"role":"public","name":full_name},"at":now.isoformat(),"details":{}}],notifications=[notice("New public application: assign recruiting branch","hr")])
    recruits_col.insert_one(doc)
    return jsonify({"ok": True, "message": "Application submitted successfully."})
