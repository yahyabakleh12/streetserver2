import os
import base64
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

# Set up test database before importing app
TEST_DB = "sqlite:///./test.db"
os.environ["DATABASE_URL"] = TEST_DB

from db import Base, engine, SessionLocal
from main import app
from models import Location, Zone, Pole, Camera, Ticket, ManualReview, User
from main import get_password_hash

# Create tables
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
    # Clean up DB file after tests
    Base.metadata.drop_all(bind=engine)
    try:
        os.remove("test.db")
    except FileNotFoundError:
        pass

@pytest.fixture()
def sample_review(tmp_path):
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
    cam = Camera(pole_id=pole.id, api_code="C1", p_ip="127.0.0.1")
    session.add(cam)
    session.commit()
    ticket = Ticket(
        camera_id=cam.id,
        spot_number=1,
        plate_number="OLD",
        plate_code="1",
        plate_city="DXB",
        confidence=50,
        entry_time=datetime.utcnow(),
    )
    session.add(ticket)
    session.commit()
    ticket.image_base64 = base64.b64encode(b"imgdata").decode()
    session.commit()
    img_path = tmp_path / "img.jpg"
    img_path.write_bytes(b"data")
    review = ManualReview(
        camera_id=cam.id,
        spot_number=1,
        event_time=datetime.utcnow(),
        image_path=str(img_path),
        clip_path=None,
        ticket_id=ticket.id,
        plate_status="UNREAD",
        plate_image="plate.jpg",
        snapshot_folder="folder",
        review_status="PENDING",
    )
    session.add(review)
    session.commit()
    session.close()
    return review.id, ticket.id

from unittest.mock import patch


def test_correct_manual_review_success(client, sample_review):
    review_id, ticket_id = sample_review
    payload = {
        "plate_number": "NEW123",
        "plate_code": "90",
        "plate_city": "DXB",
        "confidence": 99,
    }
    with patch("api_client.park_in_request", return_value=None):
        response = client.post(f"/manual-reviews/{review_id}/correct", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "updated"}

    session = SessionLocal()
    ticket = session.query(Ticket).get(ticket_id)
    review = session.query(ManualReview).get(review_id)
    assert ticket.plate_number == "NEW123"
    assert ticket.plate_code == "90"
    assert ticket.plate_city == "DXB"
    assert ticket.confidence == 99
    assert review.review_status == "RESOLVED"
    assert review.plate_status == "READ"
    session.close()


def test_correct_manual_review_uses_ticket_image(client, sample_review):
    review_id, ticket_id = sample_review
    session = SessionLocal()
    ticket = session.query(Ticket).get(ticket_id)
    img_b64 = ticket.image_base64
    session.close()

    payload = {
        "plate_number": "IMG",
        "plate_code": "99",
        "plate_city": "DXB",
        "confidence": 70,
    }
    with patch("api_client.park_in_request", return_value=None) as mock_park:
        resp = client.post(f"/manual-reviews/{review_id}/correct", json=payload)
    assert resp.status_code == 200
    assert mock_park.call_args.kwargs["images"] == [img_b64]


def test_correct_manual_review_not_found(client):
    payload = {
        "plate_number": "X",
        "plate_code": "1",
        "plate_city": "DXB",
        "confidence": 80,
    }
    with patch("api_client.park_in_request", return_value=None):
        response = client.post("/manual-reviews/999/correct", json=payload)
    assert response.status_code == 404
    assert response.json()["detail"] == "Review not found"


def test_correct_manual_review_validation_error(client, sample_review):
    review_id, _ = sample_review
    payload = {
        "plate_number": "NEW",
        # Missing plate_code
        "plate_city": "DXB",
        "confidence": 80,
    }
    response = client.post(f"/manual-reviews/{review_id}/correct", json=payload)
    assert response.status_code == 422
