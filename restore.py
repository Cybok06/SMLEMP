# restore_deleted_user.py
from bson import ObjectId
from datetime import datetime
from db import db

users_col = db["users"]

TARGET_ID = ObjectId("698e4513fd3619e22b552932")

# Check if user already exists
existing = users_col.find_one({"_id": TARGET_ID})

if existing:
    print("❌ User already exists. Aborting restore.")
    exit()

user_doc = {
    "_id": TARGET_ID,
    "username": "Larbi@20",
    "password": "$2b$12$Djp2oXnfD0FdK6ohtZTjY.ZtGzjOW8XOrJ7zlx2J5N7pKN1Cy5ROy",
    "role": "agent",
    "name": "Larbi",
    "phone": "123456789",
    "email": "Leonard@smartliving.com",
    "gender": "Male",
    "branch": "Agormanya",
    "position": "Sales personel",
    "location": "Agormanya",
    "start_date": "2025-06-12",
    "image_url": "https://share.icloud.com/photos/0f2Y7gXlk97sg-aCXI0928l2Q",
    "status": "Active",
    "assets": ["noneh"],
    "date_registered": datetime.fromisoformat("2025-06-12T19:02:05.100"),
    "manager_id": ObjectId("68475b5bf432e3c28d045b85"),
    "case_records": [
        {
            "_id": ObjectId("695a7605429f4eea3344ba6a"),
            "title": "None",
            "details": "Customers complaining of items not delivered due to inaccurate",
            "case_type": "Theft",
            "severity": "High",
            "status": "Open",
            "incident_date": "2026-01-02",
            "due_date": "2026-01-31",
            "loss_amount": None,
            "followups": [],
            "created_at": datetime(2026,1,4,14,15,33),
            "updated_at": datetime(2026,1,4,14,15,33),
            "recorded_by": "HR"
        }
    ],
    "updated_at": datetime(2026,1,16,12,12,42),
    "favorites_customer_ids": [
        ObjectId("6858368d4b409bc7469e7fe6"),
        ObjectId("685836334b409bc7469e7fe2"),
        ObjectId("695c7a63b5cf7a2522389e2c")
    ]
}

result = users_col.insert_one(user_doc)

print("✅ USER SUCCESSFULLY RESTORED")
print("Inserted ID:", result.inserted_id)