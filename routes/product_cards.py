from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from login import role_required, get_current_identity
from services.product_cards_service import (
    create_or_update_product_card,
    executive_overview,
    executive_transfer,
    manager_overview,
    manager_transfer,
    agent_overview,
    recent_activity,
)


product_cards_bp = Blueprint("product_cards", __name__)


@product_cards_bp.get("/executive/product-cards")
@role_required("executive")
def executive_product_cards_page():
    return render_template("product_cards/executive.html")


@product_cards_bp.get("/manager/product-cards")
@role_required("manager")
def manager_product_cards_page():
    return render_template("product_cards/manager.html")


@product_cards_bp.get("/agent/product-cards")
@role_required("agent")
def agent_product_cards_page():
    return render_template("product_cards/agent.html")


@product_cards_bp.get("/api/product-cards/executive/overview")
@role_required("executive")
def executive_product_cards_overview():
    payload = executive_overview()
    payload["activity"] = recent_activity()
    return jsonify(ok=True, **payload)


@product_cards_bp.post("/api/product-cards/executive/save")
@role_required("executive")
def executive_product_cards_save():
    data = request.get_json(silent=True) or request.form
    product_id = (data.get("product_id") or "").strip()
    qty_raw = data.get("qty")
    try:
        qty = int(qty_raw)
    except Exception:
        return jsonify(ok=False, message="Invalid quantity."), 400
    ok, message = create_or_update_product_card(product_id, qty)
    status = 200 if ok else 400
    return jsonify(ok=ok, message=message), status


@product_cards_bp.post("/api/product-cards/executive/transfer")
@role_required("executive")
def executive_product_cards_transfer():
    ident = get_current_identity()
    data = request.get_json(silent=True) or request.form
    product_id = (data.get("product_id") or "").strip()
    manager_id = (data.get("manager_id") or "").strip()
    qty_raw = data.get("qty")
    try:
        qty = int(qty_raw)
    except Exception:
        return jsonify(ok=False, message="Invalid quantity."), 400
    ok, message = executive_transfer(product_id, manager_id, qty, ident.get("user_id") or "")
    status = 200 if ok else 400
    return jsonify(ok=ok, message=message), status


@product_cards_bp.get("/api/product-cards/manager/overview")
@role_required("manager")
def manager_product_cards_overview():
    ident = get_current_identity()
    return jsonify(ok=True, **manager_overview(ident.get("user_id") or ""))


@product_cards_bp.post("/api/product-cards/manager/transfer")
@role_required("manager")
def manager_product_cards_transfer():
    ident = get_current_identity()
    data = request.get_json(silent=True) or request.form
    product_id = (data.get("product_id") or "").strip()
    agent_id = (data.get("agent_id") or "").strip()
    qty_raw = data.get("qty")
    try:
        qty = int(qty_raw)
    except Exception:
        return jsonify(ok=False, message="Invalid quantity."), 400
    ok, message = manager_transfer(product_id, ident.get("user_id") or "", agent_id, qty)
    status = 200 if ok else 400
    return jsonify(ok=ok, message=message), status


@product_cards_bp.get("/api/product-cards/agent/overview")
@role_required("agent")
def agent_product_cards_overview():
    ident = get_current_identity()
    return jsonify(ok=True, **agent_overview(ident.get("user_id") or ""))
