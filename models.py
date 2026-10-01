from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


RESOURCE_TYPES = ["general", "emergency", "icu", "nicu", "ventilator",
                  "operation_theatre", "isolation", "dialysis", "trauma", "ambulance"]

# request status flow:
# searching -> request_sent -> hospital_reviewing -> accepted -> patient_transferred -> admitted
# others: rejected, cancelled, expired, no_capacity


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    full_name: Mapped[str] = mapped_column(String(100), default="")
    role: Mapped[str] = mapped_column(String(20))  # patient | coordinator | hospital_staff | admin
    hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Hospital(Base):
    __tablename__ = "hospitals"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    location: Mapped[str] = mapped_column(String(200), default="")
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    contact: Mapped[str] = mapped_column(String(50), default="")
    services: Mapped[str] = mapped_column(Text, default="")  # comma separated specialist services
    verification_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|verified|rejected
    emergency_available: Mapped[bool] = mapped_column(Boolean, default=True)
    account_status: Mapped[str] = mapped_column(String(20), default="active")  # active|suspended
    capacities: Mapped[list["Capacity"]] = relationship(back_populates="hospital", cascade="all, delete-orphan")

    @property
    def service_list(self) -> list[str]:
        return [s.strip().lower() for s in self.services.split(",") if s.strip()]


class Capacity(Base):
    __tablename__ = "capacities"
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    resource_type: Mapped[str] = mapped_column(String(30))
    total_capacity: Mapped[int] = mapped_column(Integer, default=0)
    occupied_capacity: Mapped[int] = mapped_column(Integer, default=0)
    reserved_capacity: Mapped[int] = mapped_column(Integer, default=0)      # temporary reservations
    unavailable_capacity: Mapped[int] = mapped_column(Integer, default=0)   # temporarily unavailable
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    hospital: Mapped[Hospital] = relationship(back_populates="capacities")

    @property
    def available_capacity(self) -> int:
        return max(0, self.total_capacity - self.occupied_capacity
                   - self.reserved_capacity - self.unavailable_capacity)


class ReferralRequest(Base):
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_reference: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    required_resource: Mapped[str] = mapped_column(String(30))
    required_service: Mapped[str | None] = mapped_column(String(100), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    urgency: Mapped[str] = mapped_column(String(10), default="medium")  # low|medium|high|critical
    selected_hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id"), nullable=True)
    request_status: Mapped[str] = mapped_column(String(30), default="searching")
    reserved: Mapped[bool] = mapped_column(Boolean, default=False)  # is a capacity unit currently held?
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    admitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    response_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hospital_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    details: Mapped[str] = mapped_column(Text, default="")
    suspicious: Mapped[bool] = mapped_column(Boolean, default=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
