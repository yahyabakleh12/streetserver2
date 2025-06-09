# ocr_processor.py

import os
import shutil
import base64
import threading
import json
from datetime import datetime, timedelta

import numpy as np
from PIL import Image, ImageDraw

from camera_clip import request_camera_clip
from network import send_request_with_retry
from config import OCR_TOKEN, PARKONIC_API_TOKEN, YOLO_MODEL_PATH
from models import PlateLog, Ticket, ManualReview
from db import SessionLocal
from logger import logger
from utils import is_same_image

from ultralytics import YOLO

# Directories
PLATES_READ_DIR   = "plates/read"
PLATES_UNREAD_DIR = "plates/unread"
SPOT_LAST_DIR     = "spot_last"

os.makedirs(PLATES_READ_DIR,   exist_ok=True)
os.makedirs(PLATES_UNREAD_DIR, exist_ok=True)
os.makedirs(SPOT_LAST_DIR,      exist_ok=True)

# Load YOLO model (CPU)
plate_model = YOLO(YOLO_MODEL_PATH)


def process_plate_and_issue_ticket(
    payload: dict,
    park_folder: str,
    ts: str,
    camera_id: int,
    pole_id: int,
    spot_number: int,
    camera_ip: str,
    camera_user: str,
    camera_pass: str
):
    """
    1) Re-open saved snapshot, annotate & crop the parking region.
    2) Compare new main_crop vs. saved 'last' image for this spot; if same, skip.
    3) Otherwise, run YOLO→OCR, insert into plate_logs,
       and create Ticket (READ) or Ticket+ManualReview+clip thread (UNREAD),
       ensuring no duplicate open ticket per spot.
       Clip window: 8 seconds before to 8 seconds after trigger.
    """
    db_session = SessionLocal()
    try:
        # 1) Re-open snapshot and draw parking polygon
        snapshot_path = os.path.join(park_folder, f"snapshot_{ts}.jpg")
        if not os.path.isfile(snapshot_path):
            logger.error(f"Snapshot missing: {snapshot_path}")
            return

        img = Image.open(snapshot_path)
        draw = ImageDraw.Draw(img)

        coords = [
            (payload["coordinate_x1"], payload["coordinate_y1"]),
            (payload["coordinate_x2"], payload["coordinate_y2"]),
            (payload["coordinate_x3"], payload["coordinate_y3"]),
            (payload["coordinate_x4"], payload["coordinate_y4"])
        ]
        xs, ys = zip(*coords)
        left, right = min(xs), max(xs)
        top, bottom = min(ys), max(ys)

        annotated_path = os.path.join(park_folder, f"annotated_{ts}.jpg")
        draw.rectangle([left, top, right, bottom], outline="red", width=3)
        img.save(annotated_path)

        main_crop = img.crop((left, top, right, bottom))
        main_crop_path = os.path.join(
            park_folder,
            f"main_crop_{payload['parking_area']}_{ts}.jpg"
        )
        main_crop.save(main_crop_path)

        # 2) Feature-match vs. last image for this spot
        spot_key = f"spot_{camera_id}_{spot_number}.jpg"
        last_image_path = os.path.join(SPOT_LAST_DIR, spot_key)

        if os.path.isfile(last_image_path):
            try:
                same = is_same_image(
                    last_image_path,
                    main_crop_path,
                    min_match_count=50,
                    inlier_ratio_thresh=0.5
                )
                if same:
                    logger.debug(
                        "Spot %d camera %d: same car detected → skip OCR/ticket",
                        spot_number, camera_id
                    )
                    return
            except Exception:
                logger.error("Error in feature-matching", exc_info=True)

        # Overwrite last-seen image
        try:
            shutil.copy(main_crop_path, last_image_path)
        except Exception:
            logger.error("Failed to update last-seen image", exc_info=True)

        # 3) Run YOLO on main_crop to detect license plate
        arr = np.array(main_crop)
        results = plate_model(arr)

        plate_status = "UNREAD"
        plate_number = None
        plate_code   = None
        plate_city   = None
        conf_val     = None

        if results and results[0].boxes:
            x1p, y1p, x2p, y2p = results[0].boxes.xyxy[0].tolist()
            x1i, y1i, x2i, y2i = map(int, (x1p, y1p, x2p, y2p))
            plate_crop = main_crop.crop((x1i, y1i, x2i, y2i))

            tmp_candidate_path = os.path.join(park_folder, f"plate_candidate_{ts}.jpg")
            plate_crop.save(tmp_candidate_path)

            # 4) Base64-encode plate crop and send to OCR
            with open(tmp_candidate_path, "rb") as f:
                plate_bytes = f.read()
            plate_b64 = base64.b64encode(plate_bytes).decode("utf-8")

            ocr_payload  = {
                "token":  OCR_TOKEN,
                "base64": plate_b64,
                "pole_id": pole_id
            }
            ocr_response = send_request_with_retry(
                "https://parkonic.cloud/ParkonicJLT/anpr/engine/process",
                ocr_payload
            )

            logger.debug(f"Raw OCR response: {ocr_response!r}")

            # double json.loads
            intermediate = None
            if isinstance(ocr_response, str):
                try:
                    intermediate = json.loads(ocr_response)
                    logger.debug("After first json.loads: %s", type(intermediate).__name__)
                except Exception:
                    logger.error("First json.loads failed", exc_info=True)
                    plate_status = "UNREAD"
            else:
                logger.debug("OCR response not str → UNREAD")
                plate_status = "UNREAD"

            ocr_json = None
            if isinstance(intermediate, str):
                try:
                    ocr_json = json.loads(intermediate)
                    logger.debug("After second json.loads → dict")
                except Exception:
                    logger.error("Second json.loads failed", exc_info=True)
                    plate_status = "UNREAD"
            elif isinstance(intermediate, dict):
                ocr_json = intermediate
                logger.debug("OCR JSON is dict")
            else:
                logger.error("Unexpected OCR intermediate type: %s", type(intermediate).__name__)
                plate_status = "UNREAD"

            if isinstance(ocr_json, dict):
                try:
                    confidance_value = int(ocr_json.get("confidance", 0))
                    logger.debug("OCR confidance: %d", confidance_value)
                    if confidance_value >= 5:
                        plate_status = "READ"
                        plate_number = ocr_json.get("text", "")
                        plate_code   = ocr_json.get("category", "")
                        city_code    = ocr_json.get("cityName", "")
                        conf_val     = confidance_value

                        city_map = {
                            "AE-AZ": "Abu Dhabi",
                            "AE-DU": "Dubai",
                            "AE-SH": "Sharjah",
                            "AE-AJ": "Ajman",
                            "AE-RK": "RAK",
                            "AE-FU": "Fujairah",
                            "AE-UQ": "UAQ"
                        }
                        plate_city = city_map.get(city_code, "Unknown")
                    else:
                        logger.debug("Confidence %d < 5 → UNREAD", confidance_value)
                        plate_status = "UNREAD"
                except Exception:
                    logger.error("Failed to extract from ocr_json", exc_info=True)
                    plate_status = "UNREAD"

        # 5) Save final plate image
        os.makedirs(PLATES_READ_DIR,   exist_ok=True)
        os.makedirs(PLATES_UNREAD_DIR, exist_ok=True)

        final_plate_filename = f"{camera_id}_{ts}.jpg"
        dest_dir = PLATES_READ_DIR if plate_status == "READ" else PLATES_UNREAD_DIR
        final_plate_path = os.path.join(dest_dir, final_plate_filename)

        if "tmp_candidate_path" in locals() and os.path.exists(tmp_candidate_path):
            shutil.copy(tmp_candidate_path, final_plate_path)
        else:
            shutil.copy(main_crop_path, final_plate_path)

        # 6) Insert into plate_logs
        new_plate_log = PlateLog(
            camera_id    = camera_id,
            car_id       = payload.get("car_id"),
            plate_number = plate_number,
            plate_code   = plate_code,
            plate_city   = plate_city,
            confidence   = conf_val,
            image_path   = final_plate_path,
            status       = plate_status,
            attempt_ts   = datetime.utcnow()
        )
        db_session.add(new_plate_log)
        db_session.commit()
        logger.debug("Inserted into plate_logs: camera_id=%d, status=%s", camera_id, plate_status)

        # 7) If READ → create Ticket
        if plate_status == "READ":
            with open(final_plate_path, "rb") as f:
                img_bytes = f.read()
            plate_b64_list = [base64.b64encode(img_bytes).decode("utf-8")]

            from api_client import park_in_request
            try:
                ticket_resp = park_in_request(
                    token        = PARKONIC_API_TOKEN,
                    parkin_time  = payload["time"],
                    plate_code   = plate_code or "",
                    plate_number = plate_number or "",
                    emirates     = plate_city or "",
                    conf         = str(conf_val or 0),
                    spot_number  = spot_number,
                    pole_id      = pole_id,
                    images       = plate_b64_list
                )
                logger.debug("park_in_request returned: %s", ticket_resp)

                trip_id = ticket_resp.get("trip_id")
                # trip_id = 123098198234
                if trip_id is None:
                    logger.debug("No trip_id returned → skip ticket insert")
                else:
                    new_ticket = Ticket(
                        camera_id        = camera_id,
                        spot_number      = spot_number,
                        plate_number     = plate_number,
                        plate_code       = plate_code,
                        plate_city       = plate_city,
                        confidence       = conf_val,
                        entry_time       = datetime.fromisoformat(payload["time"]),
                        parkonic_trip_id = trip_id
                    )
                    logger.debug("About to insert new_ticket: %s", new_ticket)
                    db_session.add(new_ticket)
                    db_session.commit()
                    logger.debug("Inserted into tickets: id=%d", new_ticket.id)
            except Exception:
                logger.error("park_in_request failed", exc_info=True)

        # 8) If UNREAD → create Ticket + ManualReview + spawn clip thread,
        #    but only if no existing open ticket for this spot
        elif plate_status == "UNREAD":
            try:
                # a) Check if an open ticket exists (exit_time is NULL)
                existing_ticket = db_session.query(Ticket).filter_by(
                    camera_id   = camera_id,
                    spot_number = spot_number,
                    exit_time   = None
                ).first()

                if existing_ticket:
                    logger.debug(
                        "Spot %d on camera %d already has open ticket (id=%d) → skip new ticket/manual review",
                        spot_number, camera_id, existing_ticket.id
                    )
                    return

                # b) Insert into ManualReview
                new_review = ManualReview(
                    camera_id     = camera_id,
                    spot_number   = spot_number,
                    event_time    = datetime.fromisoformat(payload["time"]),
                    image_path    = final_plate_path,
                    review_status = "PENDING"
                )
                db_session.add(new_review)
                db_session.flush()  # assign new_review.id without committing
                review_id = new_review.id
                db_session.commit()
                logger.debug("Inserted into manual_reviews: id=%d", review_id)

                # c) INSERT a Ticket for UNREAD plate
                new_ticket = Ticket(
                    camera_id        = camera_id,
                    spot_number      = spot_number,
                    plate_number     = plate_number or "UNKNOWN",
                    plate_code       = plate_code or "",
                    plate_city       = plate_city or "",
                    confidence       = conf_val or 0,
                    entry_time       = datetime.fromisoformat(payload["time"]),
                    parkonic_trip_id = None
                )
                db_session.add(new_ticket)
                db_session.commit()
                logger.debug("Inserted UNREAD ticket into tickets: id=%d", new_ticket.id)

                # link manual review to the new ticket
                new_review.ticket_id = new_ticket.id
                db_session.commit()

                # d) Spawn thread to fetch camera clip for manual review
                def fetch_and_update_clip(rid: int, cam_ip: str, user: str, pwd: str, ev_time: datetime):
                    start_dt = ev_time - timedelta(seconds=8)
                    end_dt   = ev_time + timedelta(seconds=8)
                    clip_path = request_camera_clip(
                        camera_ip    = cam_ip,
                        username     = user,
                        password     = pwd,
                        start_dt     = start_dt,
                        end_dt       = end_dt,
                        segment_name = ev_time.strftime("%Y%m%d%H%M%S")
                    )
                    session_t = SessionLocal()
                    try:
                        if clip_path:
                            review_obj = session_t.query(ManualReview).get(rid)
                            if review_obj:
                                review_obj.clip_path = clip_path
                                session_t.commit()
                                logger.debug(
                                    "Updated manual_reviews.clip_path=%s for id=%d", clip_path, rid
                                )
                        else:
                            logger.error("Could not obtain clip for manual review id=%d", rid)
                    except Exception:
                        logger.error("Exception in fetch_and_update_clip thread", exc_info=True)
                        session_t.rollback()
                    finally:
                        session_t.close()

                thread = threading.Thread(
                    target=fetch_and_update_clip,
                    args=(review_id, camera_ip, camera_user, camera_pass, datetime.fromisoformat(payload["time"]))
                )
                thread.daemon = True
                thread.start()

            except Exception:
                logger.error("manual_reviews INSERT failed", exc_info=True)
                db_session.rollback()

    except Exception:
        logger.error("process_plate_and_issue_ticket exception", exc_info=True)
        db_session.rollback()
    finally:
        db_session.close()
