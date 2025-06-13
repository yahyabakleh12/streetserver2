# ocr_processor.py

import os
import shutil
import base64
import json
import io
from datetime import datetime, timedelta

import numpy as np
from PIL import Image, ImageDraw

from camera_clip import request_camera_clip, fetch_camera_frame
from network import send_request_with_retry

from config import OCR_TOKEN, YOLO_MODEL_PATH

from models import PlateLog, Ticket, ManualReview, Spot
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


def spot_has_car(image: Image.Image | bytes, camera_id: int, spot_number: int) -> bool:
    """Return True if the cropped spot contains a car based on YOLO detection."""
    if isinstance(image, bytes):
        img = Image.open(io.BytesIO(image))
    else:
        img = image

    db = SessionLocal()
    try:
        spot = (
            db.query(Spot)
            .filter_by(camera_id=camera_id, spot_number=spot_number)
            .first()
        )
    finally:
        db.close()

    if spot is None:
        return False

    left, top, right, bottom = (
        spot.bbox_x1,
        spot.bbox_y1,
        spot.bbox_x2,
        spot.bbox_y2,
    )
    crop = img.crop((left, top, right, bottom))
    arr = np.array(crop)
    results = plate_model(arr)
    if results and results[0].boxes:
        return True
        # classes = results[0].boxes.cls
        # try:
        #     cls_list = classes.tolist()
        # except Exception:
        #     cls_list = list(classes)
        # return 2 in cls_list
    return False


def process_plate_and_issue_ticket(
    payload: dict,
    park_folder: str,
    ts: str,
    camera_id: int,
    pole_id: int,
    api_pole_id: int | None,
    spot_number: int,
    camera_ip: str,
    camera_user: str,
    camera_pass: str,
    parkonic_api_token: str
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

        spot = (
            db_session.query(Spot)
            .filter_by(camera_id=camera_id, spot_number=spot_number)
            .first()
        )
        if spot is None:
            logger.error(
                "Spot %d on camera %d not found in DB", spot_number, camera_id
            )
            return

        left, top, right, bottom = (
            spot.bbox_x1,
            spot.bbox_y1,
            spot.bbox_x2,
            spot.bbox_y2,
        )

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

        # if os.path.isfile(last_image_path):
        #     try:
        #         same = is_same_image(
        #             last_image_path,
        #             snapshot_path,
        #             camera_id=camera_id,
        #             spot_number=spot_number,
        #             min_match_count=50,
        #             inlier_ratio_thresh=0.5,
        #         )
        #         if same:
        #             logger.debug(
        #                 "Spot %d camera %d: same car detected → skip OCR/ticket",
        #                 spot_number, camera_id
        #             )
        #             return
        #     except Exception:
        #         logger.error("Error in feature-matching", exc_info=True)

        # Overwrite last-seen image with the full snapshot
        try:
            shutil.copy(snapshot_path, last_image_path)
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

        # Fallback: capture a fresh frame and retry detection/OCR if unread
        if plate_status == "UNREAD":
            try:
                frame_bytes = fetch_camera_frame(camera_ip, camera_user or "", camera_pass or "")
                retry_snapshot = os.path.join(park_folder, f"retry_snapshot_{ts}.jpg")
                with open(retry_snapshot, "wb") as f:
                    f.write(frame_bytes)
                img = Image.open(retry_snapshot)
                draw = ImageDraw.Draw(img)
                draw.rectangle([left, top, right, bottom], outline="red", width=3)
                annotated_path = os.path.join(park_folder, f"annotated_retry_{ts}.jpg")
                img.save(annotated_path)
                main_crop = img.crop((left, top, right, bottom))
                main_crop_path = os.path.join(park_folder, f"main_crop_retry_{ts}.jpg")
                main_crop.save(main_crop_path)

                arr = np.array(main_crop)
                results = plate_model(arr)
                if results and results[0].boxes:
                    x1p, y1p, x2p, y2p = results[0].boxes.xyxy[0].tolist()
                    x1i, y1i, x2i, y2i = map(int, (x1p, y1p, x2p, y2p))
                    plate_crop = main_crop.crop((x1i, y1i, x2i, y2i))

                    tmp_candidate_path = os.path.join(park_folder, f"plate_candidate_retry_{ts}.jpg")
                    plate_crop.save(tmp_candidate_path)

                    with open(tmp_candidate_path, "rb") as f:
                        plate_bytes = f.read()
                    plate_b64 = base64.b64encode(plate_bytes).decode("utf-8")
                    ocr_payload = {
                        "token": OCR_TOKEN,
                        "base64": plate_b64,
                        "pole_id": pole_id,
                    }
                    ocr_response = send_request_with_retry(
                        "https://parkonic.cloud/ParkonicJLT/anpr/engine/process",
                        ocr_payload,
                    )

                    logger.debug(f"Retry OCR response: {ocr_response!r}")

                    intermediate = None
                    if isinstance(ocr_response, str):
                        try:
                            intermediate = json.loads(ocr_response)
                        except Exception:
                            logger.error("First json.loads failed on retry", exc_info=True)
                            intermediate = None
                    if isinstance(intermediate, str):
                        try:
                            ocr_json = json.loads(intermediate)
                        except Exception:
                            logger.error("Second json.loads failed on retry", exc_info=True)
                            ocr_json = None
                    elif isinstance(intermediate, dict):
                        ocr_json = intermediate
                    else:
                        ocr_json = None

                    if isinstance(ocr_json, dict):
                        try:
                            confidance_value = int(ocr_json.get("confidance", 0))
                            if confidance_value >= 5:
                                plate_status = "READ"
                                plate_number = ocr_json.get("text", "")
                                plate_code = ocr_json.get("category", "")
                                city_code = ocr_json.get("cityName", "")
                                conf_val = confidance_value
                                city_map = {
                                    "AE-AZ": "Abu Dhabi",
                                    "AE-DU": "Dubai",
                                    "AE-SH": "Sharjah",
                                    "AE-AJ": "Ajman",
                                    "AE-RK": "RAK",
                                    "AE-FU": "Fujairah",
                                    "AE-UQ": "UAQ",
                                }
                                plate_city = city_map.get(city_code, "Unknown")
                        except Exception:
                            logger.error("Failed to extract from retry ocr_json", exc_info=True)
            except Exception:
                logger.error("Retry capture or OCR failed", exc_info=True)

        # 5) Save final plate image
        os.makedirs(PLATES_READ_DIR,   exist_ok=True)
        os.makedirs(PLATES_UNREAD_DIR, exist_ok=True)

        micro = datetime.utcnow().strftime('%f')
        final_plate_filename = f"{camera_id}_{ts}_{micro}.jpg"
        dest_dir = PLATES_READ_DIR if plate_status == "READ" else PLATES_UNREAD_DIR
        final_plate_path = os.path.join(dest_dir, final_plate_filename)

        if "tmp_candidate_path" in locals() and os.path.exists(tmp_candidate_path):
            shutil.copy(tmp_candidate_path, final_plate_path)
        else:
            shutil.copy(main_crop_path, final_plate_path)

        ticket_image_b64 = None
        try:
            with open(final_plate_path, "rb") as f:
                ticket_image_b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            logger.error("Failed to read final plate image for ticket", exc_info=True)

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

        # 6b) Insert an entry in manual_reviews to keep track of the processed
        # plate image and snapshot directory for debugging.
        new_review_tx = ManualReview(
            camera_id       = camera_id,
            spot_number     = spot_number,
            event_time      = datetime.fromisoformat(payload["time"]),
            image_path      = final_plate_path,
            plate_status    = plate_status,
            plate_image     = final_plate_filename,
            snapshot_folder = os.path.basename(park_folder),
            review_status   = "RESOLVED" if plate_status == "READ" else "PENDING"
        )
        db_session.add(new_review_tx)
        db_session.flush()
        review_id = new_review_tx.id
        db_session.commit()

        # 7) If READ → create Ticket
        if plate_status == "READ":
            if ticket_image_b64:
                img_list = [ticket_image_b64]
            else:
                img_list = []
                try:
                    with open(annotated_path, "rb") as f:
                        img_list.append(base64.b64encode(f.read()).decode("utf-8"))
                except Exception:
                    logger.error("Failed to read annotated image for API", exc_info=True)
                try:
                    with open(main_crop_path, "rb") as f:
                        img_list.append(base64.b64encode(f.read()).decode("utf-8"))
                except Exception:
                    logger.error("Failed to read cropped image for API", exc_info=True)
                if not img_list:
                    with open(final_plate_path, "rb") as f:
                        img_list = [base64.b64encode(f.read()).decode("utf-8")]

            from api_client import park_in_request
            try:
                ticket_resp = park_in_request(
                    token        = parkonic_api_token,
                    parkin_time  = payload["time"],
                    plate_code   = plate_code or "",
                    plate_number = plate_number or "",
                    emirates     = plate_city or "",
                    conf         = str(conf_val or 0),
                    spot_number  = spot_number,
                    pole_id      = api_pole_id,
                    images       = img_list
                )
                logger.debug("park_in_request returned: %s", ticket_resp)

                if isinstance(ticket_resp, str):
                    try:
                        ticket_resp = json.loads(ticket_resp)
                    except Exception:
                        logger.error("Failed to parse ticket_resp", exc_info=True)
                        ticket_resp = {}

                trip_id = ticket_resp.get("trip_id") if isinstance(ticket_resp, dict) else None
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
                        parkonic_trip_id = trip_id,
                        image_base64     = ticket_image_b64
                    )
                    logger.debug("About to insert new_ticket: %s", new_ticket)
                    db_session.add(new_ticket)
                    db_session.commit()
                    logger.debug("Inserted into tickets: id=%d", new_ticket.id)
                    # associate manual review record with the ticket
                    new_review_tx.ticket_id = new_ticket.id
                    db_session.commit()
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

                # The manual review entry was already created above as
                # ``new_review_tx`` with review_status=PENDING. Reuse it here.
                logger.debug("Using existing manual_review id=%d for UNREAD plate", review_id)

                # c) INSERT a Ticket for UNREAD plate
                new_ticket = Ticket(
                    camera_id        = camera_id,
                    spot_number      = spot_number,
                    plate_number     = plate_number or "UNKNOWN",
                    plate_code       = plate_code or "",
                    plate_city       = plate_city or "",
                    confidence       = conf_val or 0,
                    entry_time       = datetime.fromisoformat(payload["time"]),
                    parkonic_trip_id = None,
                    image_base64     = ticket_image_b64
                )
                db_session.add(new_ticket)
                db_session.commit()
                logger.debug("Inserted UNREAD ticket into tickets: id=%d", new_ticket.id)

                # link manual review to the new ticket
                new_review_tx.ticket_id = new_ticket.id
                db_session.commit()

                # d) Fetch camera clip for manual review in the current background task
                def fetch_and_update_clip(rid: int, cam_ip: str, user: str, pwd: str, ev_time: datetime):
                    start_dt = ev_time - timedelta(seconds=15)
                    end_dt   = ev_time + timedelta(seconds=5)
                    clip_path = request_camera_clip(
                        camera_ip    = cam_ip,
                        username     = user,
                        password     = pwd,
                        start_dt     = start_dt,
                        end_dt       = end_dt,
                        segment_name = ev_time.strftime("%Y%m%d%H%M%S"),
                        unique_tag   = str(rid),
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
                        logger.error("Exception in fetch_and_update_clip", exc_info=True)
                        session_t.rollback()
                    finally:
                        session_t.close()

                fetch_and_update_clip(
                    review_id,
                    camera_ip,
                    camera_user,
                    camera_pass,
                    datetime.fromisoformat(payload["time"]),
                )

            except Exception:
                logger.error("manual_reviews INSERT failed", exc_info=True)
                db_session.rollback()

    except Exception:
        logger.error("process_plate_and_issue_ticket exception", exc_info=True)
        db_session.rollback()
    finally:
        db_session.close()
