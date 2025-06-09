# models.py

from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Enum,
    DateTime,
    JSON,
    ForeignKey
)
from sqlalchemy.orm import relationship
from db import Base


class Location(Base):
    __tablename__ = "locations"
    id             = Column(Integer, primary_key=True, index=True)
    name           = Column(String(100), nullable=False)
    code           = Column(String(50),  nullable=False, unique=True)
    portal_name    = Column(String(100), nullable=False)
    portal_password= Column(String(100), nullable=False)
    ip_schema      = Column(String(100), nullable=False)
    parameters     = Column(JSON, nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)

    poles  = relationship("Pole", back_populates="location")
    zones  = relationship("Zone", back_populates="location")


class Zone(Base):
    __tablename__ = "zones"
    id          = Column(Integer, primary_key=True, index=True)
    code        = Column(String(50), nullable=False)
    parameters  = Column(JSON, nullable=True)
    location_id = Column(Integer, ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)

    location = relationship("Location", back_populates="zones")
    poles    = relationship("Pole", back_populates="zone")


class Pole(Base):
    __tablename__ = "poles"
    id                = Column(Integer, primary_key=True, index=True)
    zone_id           = Column(Integer, ForeignKey("zones.id", ondelete="CASCADE"), nullable=False)
    code              = Column(String(50), nullable=False)
    location_id       = Column(Integer, ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)
    number_of_cameras = Column(Integer, default=0)
    server            = Column(String(100), nullable=True)
    router            = Column(String(100), nullable=True)
    router_ip         = Column(String(45), nullable=True)
    router_vpn_ip     = Column(String(45), nullable=True)
    location_coordinates = Column(String(255), nullable=True)

    cameras  = relationship("Camera", back_populates="pole")
    location = relationship("Location", back_populates="poles")
    zone     = relationship("Zone", back_populates="poles")


class Camera(Base):
    __tablename__ = "cameras"
    id                = Column(Integer, primary_key=True, index=True)
    pole_id           = Column(Integer, ForeignKey("poles.id", ondelete="CASCADE"), nullable=False)
    api_code          = Column(String(100), nullable=False)
    p_ip              = Column(String(45),  nullable=False)
    number_of_parking = Column(Integer, default=0)
    vpn_ip            = Column(String(45),  nullable=True)

    # Relationships (optional)
    reports          = relationship("Report", back_populates="camera")
    plate_logs       = relationship("PlateLog", back_populates="camera")
    tickets          = relationship("Ticket", back_populates="camera")
    manual_reviews   = relationship("ManualReview", back_populates="camera")
    pole             = relationship("Pole", back_populates="cameras")


class Report(Base):
    __tablename__ = "reports"
    id         = Column(Integer, primary_key=True, index=True)
    camera_id  = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    event       = Column(String(100),   nullable=False)
    report_type = Column(String(50),    nullable=False)
    timestamp   = Column(DateTime,      nullable=False)
    payload     = Column(JSON,          nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow)

    camera    = relationship("Camera", back_populates="reports")


class PlateLog(Base):
    __tablename__ = "plate_logs"
    id           = Column(Integer, primary_key=True, index=True)
    camera_id    = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    car_id       = Column(String(50), nullable=True)
    plate_number = Column(String(20), nullable=True)
    plate_code   = Column(String(10), nullable=True)
    plate_city   = Column(String(50), nullable=True)
    confidence   = Column(Integer, nullable=True)
    image_path   = Column(String(255), nullable=False)
    status       = Column(Enum("READ", "UNREAD", name="plate_status"), nullable=False, default="UNREAD")
    attempt_ts   = Column(DateTime, default=datetime.utcnow)

    camera    = relationship("Camera", back_populates="plate_logs")


class Ticket(Base):
    __tablename__ = "tickets"
    id               = Column(Integer, primary_key=True, index=True)
    camera_id        = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    spot_number      = Column(Integer,    nullable=False)
    plate_number     = Column(String(20), nullable=False)
    plate_code       = Column(String(10), nullable=True)
    plate_city       = Column(String(50), nullable=True)
    confidence       = Column(Integer,    nullable=True)
    entry_time       = Column(DateTime,   nullable=False)
    exit_time        = Column(DateTime,   nullable=True)
    parkonic_trip_id = Column(Integer,    nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)

    camera    = relationship("Camera", back_populates="tickets")


class ManualReview(Base):
    __tablename__ = "manual_reviews"
    id             = Column(Integer, primary_key=True, index=True)
    camera_id      = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    spot_number    = Column(Integer,    nullable=False)
    event_time     = Column(DateTime,   nullable=False)
    image_path     = Column(String(255), nullable=False)
    clip_path      = Column(String(255), nullable=True)
    ticket_id      = Column(Integer, ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    plate_status   = Column(Enum("READ", "UNREAD", name="manual_plate_status"), nullable=False)
    plate_image    = Column(String(255), nullable=False)
    snapshot_folder = Column(String(255), nullable=False)
    review_status  = Column(Enum("PENDING", "RESOLVED", name="review_status"), nullable=False, default="PENDING")
    created_at     = Column(DateTime, default=datetime.utcnow)

    camera    = relationship("Camera", back_populates="manual_reviews")
    ticket    = relationship("Ticket")


