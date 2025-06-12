import os
from fastapi.testclient import TestClient
import pytest

TEST_DB = "sqlite:///./test.db"
os.environ["DATABASE_URL"] = TEST_DB

from db import Base, engine, SessionLocal
from main import app, get_password_hash
from models import Location, Zone, Pole, Camera, Spot, User

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

session = SessionLocal()
user = User(username="test", hashed_password=get_password_hash("secret"))
session.add(user)
session.commit()
session.close()

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        resp = c.post(
            "/token",
            data={"username": "test", "password": "secret"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token = resp.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c
    Base.metadata.drop_all(bind=engine)
    try:
        os.remove("test.db")
    except FileNotFoundError:
        pass

@pytest.fixture()
def sample_camera():
    session = SessionLocal()
    loc = Location(name="Loc", code="L1", portal_name="u", portal_password="p", ip_schema="ip")
    session.add(loc)
    session.commit()
    zone = Zone(code="Z1", location_id=loc.id)
    session.add(zone)
    session.commit()
    pole = Pole(zone_id=zone.id, code="P1", location_id=loc.id)
    session.add(pole)
    session.commit()
    cam = Camera(pole_id=pole.id, api_code="C1", p_ip="ip")
    session.add(cam)
    session.commit()
    cam_id = cam.id
    session.close()
    return cam_id


def test_create_and_list_spots(client, sample_camera):
    resp = client.post(
        "/spots",
        json={
            "camera_id": sample_camera,
            "spot_number": 1,
            "bbox_x1": 0,
            "bbox_y1": 0,
            "bbox_x2": 10,
            "bbox_y2": 10,
        },
    )
    assert resp.status_code == 200
    spot_id = resp.json()["id"]

    session = SessionLocal()
    assert session.query(Spot).get(spot_id) is not None
    session.close()

    resp = client.get(f"/cameras/{sample_camera}/spots")
    assert resp.status_code == 200
    spots = resp.json()
    assert len(spots) == 1
    assert spots[0]["id"] == spot_id

    resp = client.get("/spots")
    assert resp.status_code == 200
    assert any(s["id"] == spot_id for s in resp.json())


def test_create_spot_camera_not_found(client):
    resp = client.post(
        "/spots",
        json={
            "camera_id": 999,
            "spot_number": 1,
            "bbox_x1": 0,
            "bbox_y1": 0,
            "bbox_x2": 1,
            "bbox_y2": 1,
        },
    )
    assert resp.status_code == 404

