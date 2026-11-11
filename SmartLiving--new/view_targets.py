from flask import Blueprint, redirect, url_for

view_targets_bp = Blueprint("view_targets", __name__)


@view_targets_bp.route("/view_targets", methods=["GET"])
def view_targets():
    # Legacy endpoint: redirect to new Executive Targets system
    return redirect(url_for("executive_target.executive_targets_page"))
