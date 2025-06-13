import os
import base64
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

TEST_DB = "sqlite:///./test.db"
os.environ["DATABASE_URL"] = TEST_DB

from db import Base, engine, SessionLocal
from main import app
from models import Location, Zone, Pole, Camera, Spot, Ticket


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)
    try:
        os.remove("test.db")
    except FileNotFoundError:
        pass


def setup_db(tmp_path):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    loc = Location(
        name="Loc",
        code="LOC",
        portal_name="u",
        portal_password="p",
        ip_schema="ip",
        parkonic_api_token="tok",
        camera_user="user",
        camera_pass="pass",
    )
    session.add(loc)
    session.commit()
    zone = Zone(code="Z1", location_id=loc.id)
    session.add(zone)
    session.commit()
    pole = Pole(zone_id=zone.id, code="P1", location_id=loc.id, api_pole_id=1)
    session.add(pole)
    session.commit()
    cam = Camera(pole_id=pole.id, api_code="123", p_ip="127.0.0.1")
    session.add(cam)
    session.commit()
    spot = Spot(camera_id=cam.id, spot_number=1, bbox_x1=0, bbox_y1=0, bbox_x2=10, bbox_y2=10)
    session.add(spot)
    session.commit()
    ticket = Ticket(
        camera_id=cam.id,
        spot_number=1,
        plate_number="AAA",
        plate_code="1",
        plate_city="DXB",
        confidence=90,
        entry_time=datetime.utcnow(),
    )
    session.add(ticket)
    session.commit()
    ticket_id = ticket.id
    session.close()

    from PIL import Image
    snap_path = tmp_path / "snap.jpg"
    Image.new("RGB", (20, 20)).save(snap_path)
    snap_b64 = base64.b64encode(snap_path.read_bytes()).decode()

    payload = {
        "event": "E",
        "device": "D",
        "time": datetime.utcnow().isoformat(),
        "report_type": "R",
        "resolution_w": 10,
        "resolution_y": 10,
        "parking_area": f"{loc.code}{cam.api_code}",
        "index_number": 1,
        "occupancy": 0,
        "duration": 1,
        "coordinate_x1": 0,
        "coordinate_y1": 0,
        "coordinate_x2": 1,
        "coordinate_y2": 0,
        "coordinate_x3": 1,
        "coordinate_y3": 1,
        "coordinate_x4": 0,
        "coordinate_y4": 1,
        "vehicle_frame_x1": 0,
        "vehicle_frame_y1": 0,
        "vehicle_frame_x2": 1,
        "vehicle_frame_y2": 1,
        "snapshot": snap_b64,
    }
    return payload, ticket_id


def test_exit_spot_still_occupied(client, tmp_path):
    payload, ticket_id = setup_db(tmp_path)
    with patch("main.fetch_camera_frame", return_value=b"img"), \
         patch("main.spot_has_car", return_value=True):
        resp = client.post("/post", json=payload)
    assert resp.status_code == 200
    assert resp.json()["message"] == "Spot still occupied"
    session = SessionLocal()
    t = session.query(Ticket).get(ticket_id)
    session.close()
    assert t.exit_time is None


def test_exit_closes_ticket(client, tmp_path):
    payload, ticket_id = setup_db(tmp_path)
    with patch("main.fetch_camera_frame", return_value=b"img"), \
         patch("main.spot_has_car", return_value=False):
        resp = client.post("/post", json=payload)
    assert resp.status_code == 200
    assert resp.json()["message"] == "Exit recorded"
    session = SessionLocal()
    t = session.query(Ticket).get(ticket_id)
    session.close()
    assert t.exit_time is not None
