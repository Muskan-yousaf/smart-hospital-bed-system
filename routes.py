from collections import Counter, defaultdict
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import schemas, services
from .database import get_db
from .models import (User, Hospital, Capacity, ReferralRequest, AuditLog,
                     RESOURCE_TYPES, utcnow)
from .security import (hash_password, verify_password, create_token,
                       current_user, require_roles)

auth_r = APIRouter(prefix="/auth", tags=["Auth"])
hosp_r = APIRouter(tags=["Hospitals & Capacity"])
search_r = APIRouter(tags=["Smart Matching"])
req_r = APIRouter(prefix="/requests", tags=["Referral Requests"])
ana_r = APIRouter(prefix="/analytics", tags=["Analytics"])
admin_r = APIRouter(prefix="/admin", tags=["Admin"])


# ====================== AUTH ======================
@auth_r.post("/register", response_model=schemas.UserOut)
def register(data: schemas.RegisterIn, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == data.username)):
        raise HTTPException(400, "Username already taken")
    u = User(username=data.username, password_hash=hash_password(data.password),
             full_name=data.full_name, role=data.role)
    db.add(u); db.commit(); db.refresh(u)
    return u


@auth_r.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.username == form.username))
    if not u or not u.is_active or not verify_password(form.password, u.password_hash):
        raise HTTPException(401, "Wrong username or password")
    return {"access_token": create_token(u), "token_type": "bearer",
            "role": u.role, "hospital_id": u.hospital_id}


@auth_r.get("/me", response_model=schemas.UserOut)
def me(user: User = Depends(current_user)):
    return user


# ====================== HOSPITALS / CAPACITY ======================
def hospital_out(h: Hospital) -> dict:
    out = schemas.HospitalOut.model_validate(h).model_dump()
    for c_out, c in zip(out["capacities"], h.capacities):
        c_out["outdated"] = services.is_outdated(c)
    return out


@hosp_r.get("/hospitals", response_model=list[schemas.HospitalOut])
def list_hospitals(
    resource: str | None = None, service: str | None = None, name: str | None = None,
    emergency_only: bool = False, only_available: bool = False,
    lat: float | None = None, lon: float | None = None, max_distance_km: float | None = None,
    db: Session = Depends(get_db),
):
    """Search/filter hospitals (bed type, ICU, ventilator, service, distance, emergency)."""
    out = []
    for h in db.scalars(select(Hospital).where(Hospital.verification_status == "verified")).all():
        if name and name.lower() not in h.name.lower(): continue
        if emergency_only and not h.emergency_available: continue
        if service and service.lower() not in h.service_list: continue
        if resource:
            c = next((c for c in h.capacities if c.resource_type == resource), None)
            if not c or (only_available and c.available_capacity <= 0): continue
        if lat is not None and lon is not None and max_distance_km is not None:
            if services.haversine_km(lat, lon, h.latitude, h.longitude) > max_distance_km: continue
        out.append(hospital_out(h))
    return out


@hosp_r.get("/hospitals/{hospital_id}", response_model=schemas.HospitalOut)
def get_hospital(hospital_id: int, db: Session = Depends(get_db)):
    h = db.get(Hospital, hospital_id)
    if not h: raise HTTPException(404, "Hospital not found")
    return hospital_out(h)


@hosp_r.post("/hospitals", response_model=schemas.HospitalOut)
def create_hospital(data: schemas.HospitalIn, db: Session = Depends(get_db),
                    admin: User = Depends(require_roles("admin"))):
    h = Hospital(**data.model_dump(), verification_status="verified")
    for r in RESOURCE_TYPES:
        h.capacities.append(Capacity(resource_type=r, total_capacity=0))
    db.add(h)
    services.audit(db, admin.id, None, "hospital_created", data.name)
    db.commit(); db.refresh(h)
    return hospital_out(h)


@hosp_r.put("/hospitals/{hospital_id}/capacity/{resource_type}", response_model=schemas.CapacityOut)
def update_capacity(hospital_id: int, resource_type: str, data: schemas.CapacityUpdateIn,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_roles("hospital_staff", "admin"))):
    if user.role == "hospital_staff" and user.hospital_id != hospital_id:
        raise HTTPException(403, "You can only update your own hospital")
    cap = services.get_capacity(db, hospital_id, resource_type)
    if not cap: raise HTTPException(404, "Resource type not found for this hospital")
    old = {"total": cap.total_capacity, "occupied": cap.occupied_capacity,
           "unavailable": cap.unavailable_capacity}
    if data.total_capacity is not None: cap.total_capacity = data.total_capacity
    if data.occupied_capacity is not None: cap.occupied_capacity = data.occupied_capacity
    if data.unavailable_capacity is not None: cap.unavailable_capacity = data.unavailable_capacity
    if cap.occupied_capacity + cap.reserved_capacity + cap.unavailable_capacity > cap.total_capacity:
        db.rollback()
        raise HTTPException(400, "occupied + reserved + unavailable cannot exceed total capacity")
    new = {"total": cap.total_capacity, "occupied": cap.occupied_capacity,
           "unavailable": cap.unavailable_capacity}
    # flag large sudden drops/jumps for admin review
    suspicious = abs(new["occupied"] - old["occupied"]) > max(10, 0.5 * max(1, old["total"]))
    cap.last_updated = utcnow()
    services.audit(db, user.id, hospital_id, "capacity_update",
                   {"resource": resource_type, "old": old, "new": new}, suspicious)
    db.commit(); db.refresh(cap)
    out = schemas.CapacityOut.model_validate(cap)
    return out


# ====================== SMART MATCHING ======================
@search_r.post("/search/match")
def smart_match(data: schemas.MatchIn, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    bad = [r for r in data.required_resources if r not in RESOURCE_TYPES]
    if bad: raise HTTPException(400, f"Unknown resource types: {bad}. Valid: {RESOURCE_TYPES}")
    return services.match_hospitals(db, data.required_resources, data.required_service,
                                    data.latitude, data.longitude, data.urgency, data.max_distance_km)


@search_r.post("/classify")
def classify(data: schemas.ClassifyIn, user: User = Depends(current_user)):
    """Convert free-text patient description into structured request info."""
    return services.classify_description(data.description)


# ====================== REFERRAL REQUESTS ======================
def _get_req(db, rid) -> ReferralRequest:
    services.expire_old_reservations(db)
    r = db.get(ReferralRequest, rid)
    if not r: raise HTTPException(404, "Request not found")
    return r


def _check_access(user: User, r: ReferralRequest):
    if user.role == "admin": return
    if user.role == "hospital_staff" and user.hospital_id == r.selected_hospital_id: return
    if r.created_by == user.id: return
    raise HTTPException(403, "No access to this request")


@req_r.post("", response_model=schemas.RequestOut)
def create_request(data: schemas.RequestIn, db: Session = Depends(get_db),
                   user: User = Depends(require_roles("patient", "coordinator"))):
    if data.required_resource not in RESOURCE_TYPES:
        raise HTTPException(400, f"Unknown resource. Valid: {RESOURCE_TYPES}")
    h = db.get(Hospital, data.hospital_id)
    if not h or h.verification_status != "verified":
        raise HTTPException(404, "Hospital not found or not verified")
    r = ReferralRequest(**data.model_dump(exclude={"hospital_id"}), selected_hospital_id=h.id,
                        created_by=user.id, request_status="request_sent")
    cap = services.get_capacity(db, h.id, data.required_resource)
    if not cap or cap.available_capacity <= 0:
        r.request_status = "no_capacity"
        r.note = "No capacity available at time of request"
    db.add(r)
    services.audit(db, user.id, h.id, "request_created", {"resource": data.required_resource})
    db.commit(); db.refresh(r)
    return r


@req_r.get("", response_model=list[schemas.RequestOut])
def list_requests(status: str | None = None, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    services.expire_old_reservations(db)
    q = select(ReferralRequest).order_by(ReferralRequest.created_at.desc())
    if user.role == "hospital_staff":
        q = q.where(ReferralRequest.selected_hospital_id == user.hospital_id)
    elif user.role != "admin":
        q = q.where(ReferralRequest.created_by == user.id)
    if status: q = q.where(ReferralRequest.request_status == status)
    return db.scalars(q).all()


@req_r.get("/{rid}", response_model=schemas.RequestOut)
def get_request(rid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = _get_req(db, rid); _check_access(user, r)
    return r


@req_r.post("/{rid}/review", response_model=schemas.RequestOut)
def start_review(rid: int, db: Session = Depends(get_db),
                 user: User = Depends(require_roles("hospital_staff", "admin"))):
    r = _get_req(db, rid); _check_access(user, r)
    if r.request_status != "request_sent":
        raise HTTPException(400, f"Cannot review a request in status '{r.request_status}'")
    r.request_status = "hospital_reviewing"
    db.commit(); db.refresh(r)
    return r


@req_r.post("/{rid}/respond", response_model=schemas.RequestOut)
def respond(rid: int, data: schemas.RespondIn, db: Session = Depends(get_db),
            user: User = Depends(require_roles("hospital_staff", "admin"))):
    r = _get_req(db, rid); _check_access(user, r)
    if r.request_status not in ("request_sent", "hospital_reviewing"):
        raise HTTPException(400, f"Cannot respond to a request in status '{r.request_status}'")
    r.response_seconds = (utcnow() - r.created_at).total_seconds()
    r.note = data.note
    if not data.accept:
        r.request_status = "rejected"
    elif services.reserve(db, r):   # holds one unit for 15 minutes -> no double assignment
        r.request_status = "accepted"
    else:
        r.request_status = "no_capacity"
        r.note = (data.note + " | ").lstrip(" |") + "Capacity ran out before acceptance"
    services.audit(db, user.id, r.selected_hospital_id, "request_response",
                   {"request_id": r.id, "result": r.request_status})
    db.commit(); db.refresh(r)
    return r


@req_r.post("/{rid}/transfer", response_model=schemas.RequestOut)
def mark_transferred(rid: int, db: Session = Depends(get_db),
                     user: User = Depends(require_roles("patient", "coordinator", "admin"))):
    r = _get_req(db, rid); _check_access(user, r)
    if r.request_status != "accepted":
        raise HTTPException(400, f"Only accepted requests can be transferred (now '{r.request_status}')")
    r.request_status = "patient_transferred"
    r.expires_at = None  # patient on the way; hospital must now confirm admission
    db.commit(); db.refresh(r)
    return r


@req_r.post("/{rid}/admit", response_model=schemas.RequestOut)
def admit(rid: int, db: Session = Depends(get_db),
          user: User = Depends(require_roles("hospital_staff", "admin"))):
    r = _get_req(db, rid); _check_access(user, r)
    if r.request_status not in ("accepted", "patient_transferred"):
        raise HTTPException(400, f"Cannot admit from status '{r.request_status}'")
    services.confirm_admission(db, r)   # reserved -> occupied (capacity updated automatically)
    r.request_status = "admitted"
    services.audit(db, user.id, r.selected_hospital_id, "patient_admitted", {"request_id": r.id})
    db.commit(); db.refresh(r)
    return r


@req_r.post("/{rid}/cancel", response_model=schemas.RequestOut)
def cancel(rid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = _get_req(db, rid); _check_access(user, r)
    if r.request_status in ("admitted", "rejected", "cancelled", "expired", "no_capacity"):
        raise HTTPException(400, f"Cannot cancel a request in status '{r.request_status}'")
    services.release(db, r)
    r.request_status = "cancelled"
    db.commit(); db.refresh(r)
    return r


# ====================== ANALYTICS ======================
@ana_r.get("/hospital/{hospital_id}")
def hospital_analytics(hospital_id: int, db: Session = Depends(get_db),
                       user: User = Depends(require_roles("hospital_staff", "admin"))):
    if user.role == "hospital_staff" and user.hospital_id != hospital_id:
        raise HTTPException(403, "Not your hospital")
    h = db.get(Hospital, hospital_id)
    if not h: raise HTTPException(404, "Hospital not found")
    caps = {c.resource_type: c for c in h.capacities}
    beds = [c for t, c in caps.items() if t in ("general", "emergency", "icu", "nicu", "isolation")]
    total = sum(c.total_capacity for c in beds)
    occupied = sum(c.occupied_capacity for c in beds)
    icu = caps.get("icu")
    reqs = db.scalars(select(ReferralRequest).where(ReferralRequest.selected_hospital_id == hospital_id)).all()
    status_count = Counter(r.request_status for r in reqs)
    resp = [r.response_seconds for r in reqs if r.response_seconds is not None]
    daily = Counter(r.admitted_at.date().isoformat() for r in reqs if r.admitted_at)
    return {
        "hospital": h.name,
        "total_beds": total, "occupied_beds": occupied, "available_beds": sum(c.available_capacity for c in beds),
        "icu_occupancy_percent": round(100 * icu.occupied_capacity / icu.total_capacity, 1) if icu and icu.total_capacity else 0,
        "capacity_utilization_percent": round(100 * occupied / total, 1) if total else 0,
        "emergency_requests": sum(1 for r in reqs if r.urgency in ("high", "critical")),
        "accepted_referrals": status_count["accepted"] + status_count["patient_transferred"] + status_count["admitted"],
        "rejected_referrals": status_count["rejected"],
        "requests_by_status": dict(status_count),
        "average_response_seconds": round(sum(resp) / len(resp), 1) if resp else None,
        "daily_admissions": dict(daily),
        "resources": [schemas.CapacityOut.model_validate(c).model_dump() for c in caps.values()],
    }


@ana_r.get("/system")
def system_analytics(db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    hospitals = db.scalars(select(Hospital)).all()
    occ = []
    for h in hospitals:
        t = sum(c.total_capacity for c in h.capacities if c.resource_type != "ambulance")
        o = sum(c.occupied_capacity + c.reserved_capacity for c in h.capacities if c.resource_type != "ambulance")
        occ.append({"hospital": h.name, "occupancy_percent": round(100 * o / t, 1) if t else 0})
    occ.sort(key=lambda x: -x["occupancy_percent"])
    reqs = db.scalars(select(ReferralRequest)).all()
    resp = [r.response_seconds for r in reqs if r.response_seconds is not None]
    by_hour = Counter(r.created_at.hour for r in reqs)
    trend = defaultdict(int)
    for log in db.scalars(select(AuditLog).where(AuditLog.action == "capacity_update")).all():
        trend[log.timestamp.date().isoformat()] += 1
    return {
        "total_hospitals": len(hospitals),
        "total_requests": len(reqs),
        "highest_occupancy_hospitals": occ[:5],
        "low_capacity_areas": [o for o in occ if o["occupancy_percent"] >= 85],
        "most_requested_resources": Counter(r.required_resource for r in reqs).most_common(5),
        "average_response_seconds": round(sum(resp) / len(resp), 1) if resp else None,
        "capacity_update_trend_by_date": dict(trend),
        "peak_demand_hours": [{"hour": h, "requests": n} for h, n in by_hour.most_common(3)],
    }


# ====================== ADMIN ======================
@admin_r.post("/users", response_model=schemas.UserOut)
def admin_create_user(data: schemas.AdminCreateUserIn, db: Session = Depends(get_db),
                      admin: User = Depends(require_roles("admin"))):
    if db.scalar(select(User).where(User.username == data.username)):
        raise HTTPException(400, "Username already taken")
    if data.role == "hospital_staff" and not db.get(Hospital, data.hospital_id or 0):
        raise HTTPException(400, "hospital_staff needs a valid hospital_id")
    u = User(username=data.username, password_hash=hash_password(data.password),
             full_name=data.full_name, role=data.role, hospital_id=data.hospital_id)
    db.add(u); db.commit(); db.refresh(u)
    return u


@admin_r.get("/users", response_model=list[schemas.UserOut])
def admin_list_users(db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    return db.scalars(select(User)).all()


@admin_r.patch("/users/{uid}/active")
def admin_toggle_user(uid: int, active: bool, db: Session = Depends(get_db),
                      admin: User = Depends(require_roles("admin"))):
    u = db.get(User, uid)
    if not u: raise HTTPException(404, "User not found")
    u.is_active = active; db.commit()
    return {"id": uid, "is_active": active}


@admin_r.patch("/hospitals/{hid}/verify")
def admin_verify(hid: int, status: str = Query(pattern="^(verified|rejected|pending)$"),
                 db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    h = db.get(Hospital, hid)
    if not h: raise HTTPException(404, "Hospital not found")
    h.verification_status = status
    services.audit(db, admin.id, hid, "verification_changed", status)
    db.commit()
    return {"hospital_id": hid, "verification_status": status}


@admin_r.get("/outdated")
def outdated_hospitals(db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    out = []
    for h in db.scalars(select(Hospital)).all():
        old = [c.resource_type for c in h.capacities if c.total_capacity and services.is_outdated(c)]
        if old: out.append({"hospital_id": h.id, "name": h.name, "outdated_resources": old})
    return out


@admin_r.get("/audit-logs")
def audit_logs(suspicious_only: bool = False, limit: int = 100, db: Session = Depends(get_db),
               admin: User = Depends(require_roles("admin"))):
    q = select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit)
    if suspicious_only: q = q.where(AuditLog.suspicious == True)  # noqa: E712
    return [{"id": a.id, "user_id": a.user_id, "hospital_id": a.hospital_id, "action": a.action,
             "details": a.details, "suspicious": a.suspicious, "timestamp": a.timestamp}
            for a in db.scalars(q).all()]
