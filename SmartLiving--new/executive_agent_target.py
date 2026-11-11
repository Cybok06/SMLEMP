from flask import Blueprint, redirect, url_for, jsonify

executive_agent_target_bp = Blueprint("executive_agent_target", __name__)


@executive_agent_target_bp.route("/executive/agent-targets", methods=["GET"])
def executive_agent_targets_home():
    # Legacy endpoint: redirect to new Executive Targets system
    return redirect(url_for("executive_target.executive_targets_page"))


@executive_agent_target_bp.route("/executive/agent-targets/agents", methods=["GET"])
def executive_agents_for_manager_json():
    # Legacy endpoint placeholder
    return jsonify(ok=True, agents=[])


@executive_agent_target_bp.route("/executive/agent-targets/commission/global", methods=["POST"])
def executive_set_global_commission():
    # Legacy endpoint placeholder
    return redirect(url_for("executive_target.executive_targets_page"))


@executive_agent_target_bp.route("/executive/agent-targets/commission/agent", methods=["POST"])
def executive_set_agent_commission():
    # Legacy endpoint placeholder
    return redirect(url_for("executive_target.executive_targets_page"))
