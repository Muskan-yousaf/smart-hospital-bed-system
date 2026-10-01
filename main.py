import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine, SessionLocal
from . import models  # noqa: F401  (register tables)
from .routes import auth_r, hosp_r, search_r, req_r, ana_r, admin_r
from .seed import seed
from .services import expire_old_reservations


async def expiry_loop():
    """Every 30s: expired 15-minute reservations release their capacity."""
    while True:
        await asyncio.sleep(30)
        with SessionLocal() as db:
            expire_old_reservations(db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    task = asyncio.create_task(expiry_loop())
    yield
    task.cancel()


app = FastAPI(title="Smart Hospital Bed & Emergency Capacity System", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for r in (auth_r, hosp_r, search_r, req_r, ana_r, admin_r):
    app.include_router(r)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "docs": "/docs"}
