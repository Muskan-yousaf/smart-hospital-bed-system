"""Demo data. Run automatically on first start (when DB is empty)."""
from datetime import timedelta
from sqlalchemy import select
from .models import User, Hospital, Capacity, RESOURCE_TYPES, utcnow
from .security import hash_password

# Demo hospitals (fictional names, approximate coordinates around Hyderabad/Jamshoro, Sindh)
HOSPITALS = [
    ("Demo City Hospital A", "Hyderabad City", 25.3960, 68.3578, "emergency,cardiology,neurology,surgery,orthopedics",
     {"general": (100, 70), "emergency": (20, 12), "icu": (20, 17), "nicu": (10, 6), "ventilator": (12, 9),
      "operation_theatre": (6, 3), "isolation": (8, 2), "dialysis": (10, 5), "trauma": (8, 4), "ambulance": (6, 2)}),
    ("Demo Medical Centre B", "Latifabad, Hyderabad", 25.4200, 68.3400, "emergency,gynecology,surgery",
     {"general": (60, 40), "emergency": (10, 8), "icu": (10, 10), "nicu": (4, 2), "ventilator": (5, 5),
      "operation_theatre": (3, 1), "isolation": (4, 1), "dialysis": (4, 1), "trauma": (3, 1), "ambulance": (3, 1)}),
    ("Demo University Hospital C", "Jamshoro", 25.4300, 68.2800, "emergency,cardiology,nephrology,neonatology,surgery",
     {"general": (150, 100), "emergency": (30, 20), "icu": (25, 15), "nicu": (12, 7), "ventilator": (15, 8),
      "operation_theatre": (8, 4), "isolation": (10, 3), "dialysis": (15, 9), "trauma": (10, 4), "ambulance": (8, 3)}),
    ("Demo Community Hospital D", "Kotri", 25.3650, 68.3050, "general,gynecology",
     {"general": (40, 30), "emergency": (6, 3), "icu": (4, 2), "nicu": (0, 0), "ventilator": (2, 0),
      "operation_theatre": (2, 1), "isolation": (2, 0), "dialysis": (0, 0), "trauma": (2, 1), "ambulance": (2, 1)}),
]


def seed(db):
    if db.scalar(select(User)):
        return
    for name, loc, lat, lon, svc, caps in HOSPITALS:
        h = Hospital(name=name, location=loc, latitude=lat, longitude=lon, services=svc,
                     contact="000-0000000", verification_status="verified", emergency_available=True)
        for r in RESOURCE_TYPES:
            total, occ = caps[r]
            h.capacities.append(Capacity(resource_type=r, total_capacity=total, occupied_capacity=occ))
        db.add(h)
    # make Hospital D's data look outdated for the demo
    db.flush()
    for c in db.scalars(select(Capacity).where(Capacity.hospital_id == 4)).all():
        c.last_updated = utcnow() - timedelta(hours=5)

    db.add_all([
        User(username="admin", password_hash=hash_password("admin123"), full_name="System Admin", role="admin"),
        User(username="staff1", password_hash=hash_password("staff123"), full_name="Staff Hospital A", role="hospital_staff", hospital_id=1),
        User(username="staff2", password_hash=hash_password("staff123"), full_name="Staff Hospital B", role="hospital_staff", hospital_id=2),
        User(username="staff3", password_hash=hash_password("staff123"), full_name="Staff Hospital C", role="hospital_staff", hospital_id=3),
        User(username="coord1", password_hash=hash_password("coord123"), full_name="Ambulance Coordinator", role="coordinator"),
        User(username="patient1", password_hash=hash_password("patient123"), full_name="Demo Patient", role="patient"),
    ])
    db.commit()
