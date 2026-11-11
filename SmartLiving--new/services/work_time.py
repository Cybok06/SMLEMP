"""Work schedules and reporting analytics shared by all ERP roles."""
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

ROLES = ("agent", "manager", "inventory", "hr", "admin", "executive", "customer_support", "accounting")
DEFAULT = {"start_time": "08:00", "end_time": "17:00", "grace_minutes": 0, "workdays": [0,1,2,3,4], "timezone": "Africa/Accra", "tracking_from": None}

def settings(database):
    doc = database.work_time_settings.find_one({"_id": "organization"}) or {}
    return {**DEFAULT, **{k:doc[k] for k in DEFAULT if k in doc}}

def local_now(database, now=None):
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(settings(database)["timezone"]))

def location(payload, stamp):
    value = payload.get("location")
    status = payload.get("location_status", "unavailable")
    if status not in {"granted", "denied", "unavailable", "timeout"}:
        raise ValueError("Invalid location status.")
    if value is None:
        if status == "granted":
            raise ValueError("Coordinates are required when location is available.")
        return {"status":status, "captured_at":stamp}
    if not isinstance(value, dict):
        raise ValueError("Invalid location.")
    try:
        lat, lon, accuracy = (float(value[k]) for k in ("latitude", "longitude", "accuracy"))
        if not all(isfinite(x) for x in (lat,lon,accuracy)) or not -90 <= lat <= 90 or not -180 <= lon <= 180 or accuracy < 0:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise ValueError("Invalid location coordinates or accuracy.")
    return {"status":"granted", "latitude":lat, "longitude":lon, "accuracy":accuracy, "captured_at":stamp}

def schedule_snapshot(database, now):
    policy = settings(database)
    local = now.astimezone(ZoneInfo(policy["timezone"]))
    start = datetime.combine(local.date(), time.fromisoformat(policy["start_time"]), tzinfo=local.tzinfo)
    scheduled = local.weekday() in policy["workdays"]
    late = scheduled and local > start + timedelta(minutes=policy["grace_minutes"])
    seconds = max(0, (local-start).total_seconds())
    return {**policy, "scheduled_day":scheduled, "expected_start_at":start.isoformat(), "is_late":bool(late), "late_minutes":int((seconds+59)//60) if late else 0}

def range_dates(database, args):
    today = local_now(database).date()
    try:
        start=date.fromisoformat(args.get("from") or today.replace(day=1).isoformat())
        end=date.fromisoformat(args.get("to") or today.isoformat())
    except (ValueError, TypeError):
        raise ValueError("Enter valid report dates.")
    if end < start or (end-start).days > 365 or end > today:
        raise ValueError("Report range must be up to 366 days and cannot end in the future.")
    return start,end

def leave_rows(user):
    rows=[]
    for leave in user.get("leaves", []):
        item={k:v for k,v in leave.items() if k != "_id"}
        item["id"]=str(leave.get("_id", ""))
        item["employee_id"]=str(user["_id"])
        item["employee_name"]=user.get("name") or user.get("username", "Staff")
        item["role"]=user.get("role", "")
        for key in ("created_at", "updated_at", "approved_at"):
            if isinstance(item.get(key),datetime):item[key]=item[key].isoformat()
        rows.append(item)
    return rows

def summary(database, user, start, end, records=None, policy_document=None):
    user_id=str(user["_id"])
    rows=records if records is not None else list(database.attendance_workdays.find({"user_id":user_id,"attendance_date":{"$gte":start.isoformat(),"$lte":end.isoformat()}}))
    reported={d["attendance_date"] for d in rows if d.get("report_to_work_at")}
    closed=sum(bool(d.get("leave_office_at")) for d in rows)
    late=[d for d in rows if d.get("report_to_work_at") and d.get("schedule",{}).get("is_late")]
    leaves=leave_rows(user)
    approved_dates=set();approved_requests=0;approved_ranges=[]
    for leave in leaves:
        if leave.get("status", "Approved") != "Approved":continue
        try:
            first=date.fromisoformat(str(leave.get("start_date", ""))[:10]);last=first+timedelta(days=int(leave.get("days",0))-1)
        except (ValueError, TypeError, OverflowError):continue
        if last < start or first > end:continue
        approved_requests+=1
        approved_ranges.append((first.isoformat(),last.isoformat()))
        d=max(start,first)
        while d<=min(last,end):approved_dates.add(d.isoformat());d+=timedelta(days=1)
    policy_document = policy_document if policy_document is not None else database.work_time_settings.find_one({"_id":"organization"}) or {}
    policies=policy_document.get("history",[])
    current={**DEFAULT,**{k:policy_document[k] for k in DEFAULT if k in policy_document}};tracking=current.get("tracking_from")
    missing=[];approved_absent=[]
    today=datetime.now(timezone.utc).astimezone(ZoneInfo(current["timezone"])).date()
    joined=user.get("hire_date") or user.get("start_date") or user.get("created_at")
    joined=joined.date().isoformat() if isinstance(joined,datetime) else str(joined or "")[:10]
    d=start
    while d<=min(end,today-timedelta(days=1)):
        ds=d.isoformat()
        applicable=[p for p in policies if p["effective_date"] <= ds]
        policy=applicable[-1] if applicable else DEFAULT
        eligible=bool(tracking and ds>=tracking and (not joined or ds>=joined) and d.weekday() in policy["workdays"])
        if ds not in reported:
            if ds in approved_dates and d.weekday() in policy["workdays"]:
                approved_absent.append(ds)
            elif eligible:missing.append(ds)
        d+=timedelta(days=1)
    return {"reported_days":len(reported), "closed_days":closed, "late_days":len(late), "late_minutes":sum(d["schedule"]["late_minutes"] for d in late), "approved_leave_requests":approved_requests, "approved_absence_days":len(approved_absent), "approved_absence_occasions":sum(any(first<=d<=last for d in approved_absent) for first,last in approved_ranges), "missing_clock_in_days":len(missing), "missing_dates":missing, "approved_absence_dates":approved_absent, "pending_leave_requests":sum(l.get("status")=="Pending" for l in leaves), "from":start.isoformat(),"to":end.isoformat(),"tracking_from":tracking}
