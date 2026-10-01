from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

Urgency = Literal["low", "medium", "high", "critical"]


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6)
    full_name: str = ""
    role: Literal["patient", "coordinator"] = "patient"


class AdminCreateUserIn(BaseModel):
    username: str
    password: str = Field(min_length=6)
    full_name: str = ""
    role: Literal["patient", "coordinator", "hospital_staff", "admin"]
    hospital_id: int | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    full_name: str
    role: str
    hospital_id: int | None
    is_active: bool


class CapacityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    resource_type: str
    total_capacity: int
    occupied_capacity: int
    reserved_capacity: int
    unavailable_capacity: int
    available_capacity: int
    last_updated: datetime
    outdated: bool = False


class CapacityUpdateIn(BaseModel):
    total_capacity: int | None = Field(None, ge=0)
    occupied_capacity: int | None = Field(None, ge=0)
    unavailable_capacity: int | None = Field(None, ge=0)


class HospitalIn(BaseModel):
    name: str
    location: str = ""
    latitude: float
    longitude: float
    contact: str = ""
    services: str = ""
    emergency_available: bool = True


class HospitalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    location: str
    latitude: float
    longitude: float
    contact: str
    services: str
    verification_status: str
    emergency_available: bool
    account_status: str
    capacities: list[CapacityOut] = []


class MatchIn(BaseModel):
    required_resources: list[str] = Field(min_length=1, description="e.g. ['icu','ventilator']")
    required_service: str | None = None
    latitude: float
    longitude: float
    urgency: Urgency = "medium"
    max_distance_km: float = 50


class RequestIn(BaseModel):
    patient_reference: str
    description: str = ""
    required_resource: str
    required_service: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    urgency: Urgency = "medium"
    hospital_id: int


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_reference: str
    description: str
    required_resource: str
    required_service: str | None
    urgency: str
    selected_hospital_id: int | None
    request_status: str
    reserved: bool
    note: str
    created_at: datetime
    accepted_at: datetime | None
    expires_at: datetime | None
    admitted_at: datetime | None
    response_seconds: float | None


class RespondIn(BaseModel):
    accept: bool
    note: str = ""


class ClassifyIn(BaseModel):
    description: str
