"""Business logic: matching, classification, reservation, audit."""
import json
from datetime import timedelta
from math import radians, sin, cos, asin, sqrt
from sqlalchemy.orm import Session
from sqlalchemy import select
from .models import Hospital, Capacity, ReferralRequest, AuditLog, utcnow

RESERVATION_MINUTES = 15
STALE_MINUTES = 60
AVG_AMBULANCE_KMH = 40
ROAD_FACTOR = 1.3


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def is_outdated(cap: Capacity) -> bool:
    return utcnow() - cap.last_updated > timedelta(minutes=STALE_MINUTES)


def audit(db: Session, user_id, hospital_id, action, details: dict | str = "", suspicious=False):
    if isinstance(details, dict):
        details = json.dumps(details)
    db.add(AuditLog(user_id=user_id, hospital_id=hospital_id, action=action,
                    details=details, suspicious=suspicious))


# ---------------- Smart hospital matching ----------------
def match_hospitals(db: Session, required_resources, service, lat, lon, urgency, max_distance_km):
    hospitals = db.scalars(select(Hospital).where(
        Hospital.verification_status == "verified", Hospital.account_status == "active")).all()
    results = []
    for h in hospitals:
        caps = {c.resource_type: c for c in h.capacities}
        dist = haversine_km(lat, lon, h.latitude, h.longitude)
        travel = round(dist * ROAD_FACTOR / AVG_AMBULANCE_KMH * 60)
        reasons, suitable = [], True

        if dist > max_distance_km:
            suitable = False
            reasons.append(f"Too far ({dist:.1f} km)")
        if urgency in ("high", "critical") and not h.emergency_available:
            suitable = False
            reasons.append("Emergency department not available")
        if service and service.lower() not in h.service_list:
            suitable = False
            reasons.append(f"Service '{service}' not offered")

        avail_scores, resource_status = [], {}
        for r in required_resources:
            c = caps.get(r)
            if not c or c.available_capacity <= 0:
                suitable = False
                reasons.append(f"{r} not available")
                resource_status[r] = "Not Available"
            else:
                resource_status[r] = "Available"
                avail_scores.append(min(1.0, c.available_capacity / max(1, c.total_capacity * 0.3)))

        fresh = [c for c in caps.values() if c.resource_type in required_resources]
        outdated = any(is_outdated(c) for c in fresh)

        match_pct = 0
        if suitable:
            dist_score = max(0.0, 1 - dist / max_distance_km)
            avail_score = sum(avail_scores) / len(avail_scores)
            fresh_score = 0.5 if outdated else 1.0
            # critical cases care more about distance/time
            wd = 0.6 if urgency == "critical" else 0.5 if urgency == "high" else 0.4
            wf = 0.1
            wa = 1 - wd - wf
            match_pct = round(100 * (wd * dist_score + wa * avail_score + wf * fresh_score))

        results.append({
            "hospital_id": h.id, "hospital_name": h.name, "location": h.location,
            "contact": h.contact, "latitude": h.latitude, "longitude": h.longitude,
            "distance_km": round(dist, 1), "estimated_travel_min": travel,
            "resources": resource_status,
            "match_percent": match_pct if suitable else None,
            "suitable": suitable,
            "reasons_not_suitable": reasons,
            "warning": "Capacity information may be outdated" if outdated else None,
        })
    results.sort(key=lambda x: (not x["suitable"], -(x["match_percent"] or 0), x["distance_km"]))
    return results


# ---------------- Patient request classification (rule-based "AI") ----------------
RULES = [
    (["ventilator", "breathing", "respiratory failure", "intubat"], "ventilator", None),
    (["newborn", "neonat", "premature", "baby"], "nicu", "neonatology"),
    (["accident", "trauma", "fracture", "bleeding", "gunshot", "head injury"], "trauma", "orthopedics"),
    (["heart attack", "chest pain", "cardiac", "stroke", "unconscious", "critical", "icu"], "icu", "cardiology"),
    (["dialysis", "kidney failure"], "dialysis", "nephrology"),
    (["surgery", "operation", "appendix", "appendicitis"], "operation_theatre", "surgery"),
    (["covid", "infectious", "isolation", "contagious"], "isolation", None),
    (["pregnan", "delivery", "labour", "labor"], "emergency", "gynecology"),
]
CRITICAL_WORDS = ["unconscious", "not breathing", "heart attack", "severe bleeding", "critical", "stroke"]
HIGH_WORDS = ["accident", "chest pain", "fracture", "emergency", "labour", "labor", "ventilator"]


def classify_description(text: str) -> dict:
    t = text.lower()
    resources, service = [], None
    for words, res, svc in RULES:
        if any(w in t for w in words):
            if res not in resources:
                resources.append(res)
            service = service or svc
    if not resources:
        resources = ["general"]
    urgency = ("critical" if any(w in t for w in CRITICAL_WORDS)
               else "high" if any(w in t for w in HIGH_WORDS) else "medium")
    summary = (text.strip()[:140] + "...") if len(text.strip()) > 140 else text.strip()
    return {"required_resources": resources, "required_service": service,
            "urgency": urgency, "summary": summary}


# ---------------- Reservation handling ----------------
def get_capacity(db: Session, hospital_id: int, resource: str) -> Capacity | None:
    return db.scalar(select(Capacity).where(Capacity.hospital_id == hospital_id,
                                            Capacity.resource_type == resource))


def reserve(db: Session, req: ReferralRequest) -> bool:
    """Hold one unit of capacity. Returns False if none available."""
    cap = get_capacity(db, req.selected_hospital_id, req.required_resource)
    if not cap or cap.available_capacity <= 0:
        return False
    cap.reserved_capacity += 1
    cap.last_updated = utcnow()
    req.reserved = True
    req.accepted_at = utcnow()
    req.expires_at = req.accepted_at + timedelta(minutes=RESERVATION_MINUTES)
    return True


def release(db: Session, req: ReferralRequest):
    if req.reserved:
        cap = get_capacity(db, req.selected_hospital_id, req.required_resource)
        if cap and cap.reserved_capacity > 0:
            cap.reserved_capacity -= 1
            cap.last_updated = utcnow()
        req.reserved = False


def confirm_admission(db: Session, req: ReferralRequest):
    """Reserved unit -> occupied unit."""
    cap = get_capacity(db, req.selected_hospital_id, req.required_resource)
    if cap:
        if req.reserved and cap.reserved_capacity > 0:
            cap.reserved_capacity -= 1
        cap.occupied_capacity += 1
        cap.last_updated = utcnow()
    req.reserved = False
    req.admitted_at = utcnow()


def expire_old_reservations(db: Session) -> int:
    now = utcnow()
    stale = db.scalars(select(ReferralRequest).where(
        ReferralRequest.request_status == "accepted",
        ReferralRequest.expires_at != None, ReferralRequest.expires_at < now)).all()  # noqa: E711
    for r in stale:
        release(db, r)
        r.request_status = "expired"
        audit(db, None, r.selected_hospital_id, "reservation_expired", {"request_id": r.id})
    if stale:
        db.commit()
    return len(stale)
