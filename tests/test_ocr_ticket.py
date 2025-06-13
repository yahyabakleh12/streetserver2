import os
import json
from datetime import datetime
from unittest.mock import patch

import pytest

TEST_DB = "sqlite:///./test.db"
os.environ["DATABASE_URL"] = TEST_DB

from db import Base, engine, SessionLocal
from models import Location, Zone, Pole, Camera, Ticket
from ocr_processor import process_plate_and_issue_ticket

# Setup database
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

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
cam = Camera(pole_id=pole.id, api_code="C1", p_ip="1")
session.add(cam)
session.commit()
camera_id = cam.id
session.close()

def test_process_plate_with_json_ticket(tmp_path):
    snapshot = tmp_path / "snapshot_test.jpg"
    from PIL import Image
    Image.new("RGB", (20, 20)).save(snapshot)

    payload = {
        "coordinate_x1": 0,
        "coordinate_y1": 0,
        "coordinate_x2": 10,
        "coordinate_y2": 0,
        "coordinate_x3": 10,
        "coordinate_y3": 10,
        "coordinate_x4": 0,
        "coordinate_y4": 10,
        "parking_area": 1,
        "time": "2025-01-01T00:00:00",
        "car_id": 1,
    }

    class DummyModel:
        def __call__(self, arr):
            class Box:
                xyxy = [[0, 0, 5, 5]]
                def __bool__(self):
                    return True
            class Res:
                def __init__(self):
                    self.boxes = Box()
            return [Res()]

    ocr_resp = json.dumps({"confidance": 10, "text": "ABC", "category": "1", "cityName": "AE-DU"})
    ocr_wrapped = json.dumps(ocr_resp)

    with patch("ocr_processor.plate_model", DummyModel()), \
         patch("ocr_processor.is_same_image", return_value=False), \
         patch("ocr_processor.send_request_with_retry", return_value=ocr_wrapped), \
         patch("api_client.park_in_request", return_value={"trip_id": 1}) as mock_park:
        process_plate_and_issue_ticket(
            payload=payload,
            park_folder=str(tmp_path),
            ts="test",
            camera_id=camera_id,
            pole_id=1,
            api_pole_id=2,
            spot_number=1,
            camera_ip="ip",
            camera_user="user",
            camera_pass="pass",
            parkonic_api_token="token",
        )

    session = SessionLocal()
    ticket = session.query(Ticket).first()
    session.close()
    assert ticket is not None
    assert mock_park.call_args.kwargs["images"] == [ticket.image_base64]


def test_process_plate_retry_frame(tmp_path):
    snapshot = tmp_path / "snapshot_test.jpg"
    from PIL import Image
    Image.new("RGB", (20, 20)).save(snapshot)

    payload = {
        "coordinate_x1": 0,
        "coordinate_y1": 0,
        "coordinate_x2": 10,
        "coordinate_y2": 0,
        "coordinate_x3": 10,
        "coordinate_y3": 10,
        "coordinate_x4": 0,
        "coordinate_y4": 10,
        "parking_area": 1,
        "time": "2025-01-01T00:00:00",
        "car_id": 1,
    }

    class DummyModel:
        def __call__(self, arr):
            class Box:
                xyxy = [[0, 0, 5, 5]]

                def __bool__(self):
                    return True

            class Res:
                def __init__(self):
                    self.boxes = Box()

            return [Res()]

    unread = json.dumps({"confidance": 0})
    read = json.dumps(json.dumps({"confidance": 10, "text": "XYZ", "category": "1", "cityName": "AE-DU"}))

    with patch("ocr_processor.plate_model", DummyModel()), \
         patch("ocr_processor.is_same_image", return_value=False), \
         patch("ocr_processor.send_request_with_retry", side_effect=[unread, read]), \
         patch("ocr_processor.fetch_camera_frame", return_value=snapshot.read_bytes()), \
         patch("api_client.park_in_request", return_value={"trip_id": 2}):
        process_plate_and_issue_ticket(
            payload=payload,
            park_folder=str(tmp_path),
            ts="test",
            camera_id=camera_id,
            pole_id=1,
            api_pole_id=2,
            spot_number=1,
            camera_ip="ip",
            camera_user="user",
            camera_pass="pass",
            parkonic_api_token="token",
        )

    session = SessionLocal()
    ticket = session.query(Ticket).first()
    session.close()
    assert ticket.plate_number == "XYZ"
