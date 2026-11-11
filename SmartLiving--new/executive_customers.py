from flask import Blueprint, render_template, request, redirect, url_for, flash
from bson.objectid import ObjectId
from datetime import datetime
from collections import defaultdict
from db import db

executive_customers_bp = Blueprint("executive_customers", __name__)

# Collections
customers_col = db["customers"]
users_col = db["users"]
payments_col = db["payments"]

# ================================
# 1. Summary: Customers per Branch
# ================================
@executive_customers_bp.route("/executive/customers")
def executive_customers():
    managers = users_col.find({"role": "manager"}, {"_id": 1, "branch": 1})
    manager_branch_map = {str(m["_id"]): m.get("branch", "Unknown") for m in managers}

    agents = users_col.find({"role": "agent"}, {"_id": 1, "manager_id": 1})
    agent_to_manager_map = {str(agent["_id"]): str(agent.get("manager_id", "")) for agent in agents}

    branch_counts = defaultdict(int)
    for customer in customers_col.find({}, {"agent_id": 1}):
        agent_id = str(customer.get("agent_id"))
        manager_id = agent_to_manager_map.get(agent_id)
        if manager_id:
            branch = manager_branch_map.get(manager_id, "Unknown")
            branch_counts[branch] += 1

    data = [{"branch": branch, "count": count} for branch, count in branch_counts.items()]
    return render_template("executive_customers.html", data=data)

# ====================================
# 2. Searchable Executive Customer List + Pagination
# ====================================
@executive_customers_bp.route("/executive/customers/list")
def executive_customers_list():
    search_term = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    per_page = 20
    skip = (page - 1) * per_page

    query = {}
    if search_term:
        query["$or"] = [
            {"name": {"$regex": search_term, "$options": "i"}},
            {"phone_number": {"$regex": search_term, "$options": "i"}}
        ]

    total_customers = customers_col.count_documents(query)
    customers_cursor = customers_col.find(query, {
        "_id": 1,
        "name": 1,
        "phone_number": 1,
        "image_url": 1
    }).skip(skip).limit(per_page)

    customers_data = [{
        "id": str(c["_id"]),
        "name": c.get("name", "N/A"),
        "phone": c.get("phone_number", "N/A"),
        "image_url": c.get("image_url", "https://via.placeholder.com/80")
    } for c in customers_cursor]

    total_pages = (total_customers + per_page - 1) // per_page

    return render_template(
        "executive_customer_list.html",
        customers=customers_data,
        search_term=search_term,
        current_page=page,
        total_pages=total_pages
    )

# ==========================
# 3. Executive Customer View
# ==========================
@executive_customers_bp.route("/executive/customer/<customer_id>")
def executive_customer_profile(customer_id):
    # Executive should use the same customer profile page as agents.
    # Reuse the agent route to avoid maintaining a separate template.
    return redirect(url_for("view.view_customer_profile", customer_id=customer_id))
