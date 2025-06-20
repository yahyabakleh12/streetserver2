# api_client.py

import os
import json
import random
import shutil
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from network import send_request_with_retry
from db import SessionLocal
from models import Camera
from logger import logger

PARKONIC_BASE_URL = "https://dev.parkonic.com/api/street-parking/v2"


def park_out_request(token: str, parkout_time: str, spot_number: int, pole_id: int, trip_id: int):
    """
    Call the /park-out endpoint.
    """
    url = f"{PARKONIC_BASE_URL}/park-out"
    payload = {
        "token": token,
        "parkout_time": str(parkout_time),
        "spot_number": str(spot_number),
        "pole_id": pole_id,
        "trip_id": str(trip_id)
    }
    resp = send_request_with_retry(url, payload)
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except Exception:
            logger.error("[PARK-OUT] JSON decode failed", exc_info=True)
            resp = {}
    return resp


def park_in_request(
    token: str,
    parkin_time: str,
    plate_code: str,
    plate_number: str,
    emirates: str,
    conf: str,
    spot_number: int,
    pole_id: int,
    images: list[str]
):
    """
    Call the /park-in endpoint.
    """
    url = f"{PARKONIC_BASE_URL}/park-in"
    payload = {
        "token": token,
        "parkin_time": str(parkin_time),
        "plate_code": plate_code,
        "plate_number": plate_number,
        "emirates": emirates,
        "conf": conf,
        "spot_number": spot_number,
        "pole_id": pole_id,
        "images": images
    }
    # Only log non–image fields
    log_data = {k: v for k, v in payload.items() if k != "images"}
    logger.info("[PARK-IN] Sending payload: %s", log_data)
    resp = send_request_with_retry(url, payload)
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except Exception:
            logger.error("[PARK-IN] JSON decode failed", exc_info=True)
            resp = {}
    return resp


def get_trip_request(token: str, spot_number: int, pole_id: int):
    """
    Call the /get-trip endpoint.
    """
    url = f"{PARKONIC_BASE_URL}/get-trip"
    payload = {
        "token": token,
        "spot_number": str(spot_number),
        "pole_id": pole_id
    }
    logger.info("[GET-TRIP] Sending payload: %s", payload)
    resp = send_request_with_retry(url, payload)
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except Exception:
            logger.error("[GET-TRIP] JSON decode failed", exc_info=True)
            resp = {}
    return resp


def save_correction_files(
    camera_id: int,
    car_id: int,
    event_time: str,
    parking_id: int,
    plate_info: dict
) -> Optional[str]:
    """
    1) Fetch camera → pole → location info from the DB (via raw SQL).
    2) Copy relevant detection folders into a new correction folder.
    3) Build a JSON “correction” payload and write it to disk.
    4) Return the path of the saved JSON, or None if something goes wrong.
    
    Parameters:
      - camera_id:    ID of the camera that triggered OCR.
      - car_id:       A unique integer identifier for the detected car.
      - event_time:   Timestamp string (e.g. "2025-06-03 16:46:11").
      - parking_id:   The spot_number / parking index from the incoming payload.
      - plate_info:   A dict containing OCR‐derived data, e.g. {
                          "plate_code": "...",
                          "plate_number": "...",
                          "plate_city": "...",
                          "conf": 10,
                          "plate_image_path": "...",
                          "frame_image_path": "...",
                          "character_confidence": [...],
                          "message": "NP_"
                        }
    """
    # ──────────────────────────────────────────────────────────────────────────
    # 1) Query the DB for camera → pole → location in one SQL statement
    # ──────────────────────────────────────────────────────────────────────────
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT
                  l.id          AS location_id,
                  l.name        AS location_name,
                  p.id          AS pole_id,
                  p.code        AS pole_number,
                  COALESCE(p.server, '') AS server_ip,
                  c.p_ip        AS camera_ip
                FROM cameras     AS c
                JOIN poles       AS p ON c.pole_id = p.id
                JOIN locations   AS l ON p.location_id = l.id
                WHERE c.id = :cam_id
                LIMIT 1
                """
            ),
            {"cam_id": camera_id}
        ).fetchone()

        if row is None:
            raise ValueError(f"No camera/pole/location found for camera_id={camera_id}")

        location_id, location_name, pole_id_db, pole_number, server_ip, camera_ip = row

    except (SQLAlchemyError, ValueError) as e:
        logger.error("DB error fetching camera→pole→location", exc_info=True)
        db.rollback()
        return None

    finally:
        db.close()

    # ──────────────────────────────────────────────────────────────────────────
    # 2) Create a unique “Correction” directory under C://Watcher//Correction//
    # ──────────────────────────────────────────────────────────────────────────
    num_     = random.randint(10**18, 10**22)
    dir_name = f"{num_}_{pole_id_db}_{location_id}_0"
    base_dir = os.path.join("C://Watcher//Correction", dir_name)
    try:
        os.makedirs(base_dir, exist_ok=True)
    except Exception as e:
        logger.error("Failed to create base_dir for correction: %s", base_dir, exc_info=True)
        return None

    # ──────────────────────────────────────────────────────────────────────────
    # 3) Copy “detections/crop” and “recordings” folders for this car_id & parking_id
    # ──────────────────────────────────────────────────────────────────────────
    src_dirs = [
        f"detections/crop/car_{car_id}/full",
        f"detections/crop/car_{car_id}/crop",
        f"detections/crop/car_{car_id}/no_p",
        f"recordings/{parking_id}/{car_id}/input"
    ]
    video_ext = {".mp4"}

    for src in src_dirs:
        os.makedirs(src, exist_ok=True)
        try:
            for fname in os.listdir(src):
                ext = os.path.splitext(fname)[1].lower()
                if src.endswith("/input") and ext not in video_ext:
                    continue
                shutil.copy2(os.path.join(src, fname), base_dir)
        except Exception:
            # Skip missing folders or individual files that can’t be copied
            continue

    # ──────────────────────────────────────────────────────────────────────────
    # 4) Build `files_list` for the JSON “correction” payload
    # ──────────────────────────────────────────────────────────────────────────
    files_list = []
    try:
        for fname in os.listdir(base_dir):
            files_list.append(
                f"/ParkonicJLT/api/correction/Corrections_zip/{num_}_{pole_id_db}/{fname}"
            )
    except Exception as e:
        logger.error("Error listing files in base_dir: %s", base_dir, exc_info=True)

    # ──────────────────────────────────────────────────────────────────────────
    # 5) Construct the correction JSON
    # ──────────────────────────────────────────────────────────────────────────
    correction_data = {
        "correction": {
            "anpr": {
                "category":      plate_info.get("plate_code", ""),
                "text":          plate_info.get("plate_number", ""),
                "country":       plate_info.get("plate_city", ""),
                "country_code":  "0",
                "confidence":    str(plate_info.get("conf", "")),
                "plate_image":   plate_info.get("plate_image_path", ""),
                "frame_image":   plate_info.get("frame_image_path", ""),
                "camera_ip":     camera_ip,
                "message":       plate_info.get("message", "NP_")
            },
            "character_confidence": plate_info.get("character_confidence", []),
            "files_list":           files_list,
            "folder_name":          dir_name,
            "location":             location_name,
            "access_point_id":      pole_id_db,
            "spot_number":          str(parking_id),
            "entrance_Name":        pole_number,
            "transactionid":        str(num_),
            "event_datetime":       event_time,
            "Server_ip":            server_ip,
            "is_exit":              1,
            "location_id":          location_id
        }
    }

    # ──────────────────────────────────────────────────────────────────────────
    # 6) Write the JSON to disk
    # ──────────────────────────────────────────────────────────────────────────
    json_path = os.path.join(base_dir, f"{num_}_{pole_id_db}.json")
    try:
        with open(json_path, "w", encoding="utf-8") as jf:
            json.dump(correction_data, jf, indent=4)
    except Exception as e:
        logger.error("Failed to write correction JSON: %s", json_path, exc_info=True)
        return None

    # 7) Return the JSON path for logging or further upload
    return json_path
