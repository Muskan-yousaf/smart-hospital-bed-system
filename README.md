# Smart Hospital Bed & Emergency Capacity System — Backend (FastAPI)

## Run (Windows / Linux / Mac)
```
python -m venv venv
venv\Scripts\activate          # Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000/docs  (Swagger UI — click "Authorize" and login).

## Demo logins (auto-created)
| Role | username / password |
|---|---|
| Admin | admin / admin123 |
| Hospital staff (Hospital 1,2,3) | staff1, staff2, staff3 / staff123 |
| Coordinator | coord1 / coord123 |
| Patient | patient1 / patient123 |

## Structure
| File | Purpose |
|---|---|
| app/main.py | Starts the app, creates DB, seeds data, background task expiring 15-min reservations |
| app/database.py | SQLite connection (hospital.db) |
| app/models.py | DB tables: User, Hospital, Capacity, ReferralRequest, AuditLog |
| app/schemas.py | Request/response validation (Pydantic) |
| app/security.py | Password hashing, JWT login, role checks |
| app/services.py | Smart matching, text classifier, reservation logic, audit |
| app/routes.py | All API endpoints |
| app/seed.py | Demo hospitals + users |

## Flow
Need -> /search/match -> POST /requests -> staff /review -> /respond (accept reserves 1 bed for 15 min)
-> coordinator /transfer -> staff /admit (reserved -> occupied) -> /analytics updated
