from flask import Blueprint, redirect, url_for

target_bp = Blueprint("target", __name__)


@target_bp.route("/executive/target/set", methods=["GET", "POST"])
def set_target():
    # Legacy endpoint: redirect to new Executive Targets system
    return redirect(url_for("executive_target.executive_targets_page"))
