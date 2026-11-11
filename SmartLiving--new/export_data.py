from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict

from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

from db import db


def build_customers_pdf() -> str:
    customers_col = db.customers
    payments_col = db.payments

    customers = list(customers_col.find({}, {
        "_id": 1,
        "name": 1,
        "location": 1,
        "phone_number": 1
    }))

    last_map: Dict[str, Any] = {}
    try:
        pipeline = [
            {"$match": {"payment_type": {"$ne": "WITHDRAWAL"}}},
            {"$group": {"_id": "$customer_id", "last_payment": {"$max": "$date"}}},
        ]
        for row in payments_col.aggregate(pipeline):
            last_map[str(row["_id"])] = row.get("last_payment")
    except Exception:
        pass

    out_dir = os.path.join(os.getcwd(), "customer data")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"customers_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    file_path = os.path.join(out_dir, filename)

    styles = getSampleStyleSheet()
    title = Paragraph("All Customers Report", styles["Title"])
    subtitle = Paragraph(f"Generated at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC", styles["Normal"])

    data = [["Customer Name", "Location", "Phone Number", "Last Payment", "Customer ID"]]
    for c in customers:
        cid = str(c.get("_id"))
        last_payment = last_map.get(cid) or ""
        if isinstance(last_payment, datetime):
            last_payment = last_payment.strftime("%Y-%m-%d")
        else:
            last_payment = str(last_payment)[:10] if last_payment else ""
        data.append([
            c.get("name") or "N/A",
            c.get("location") or "N/A",
            c.get("phone_number") or "N/A",
            last_payment or "N/A",
            cid
        ])

    doc = SimpleDocTemplate(
        file_path,
        pagesize=landscape(A4),
        leftMargin=20,
        rightMargin=20,
        topMargin=24,
        bottomMargin=24
    )
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e7eb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111827")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]))

    doc.build([title, Spacer(1, 8), subtitle, Spacer(1, 12), table])
    return file_path


if __name__ == "__main__":
    path = build_customers_pdf()
    print(f"PDF saved to: {path}")

