# main.py

import os
import io
import re
import json
import base64
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import ClientDisconnect
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError, OperationalError

from PIL import Image
from db import SessionLocal
from models import (
    Report,
    Ticket,
    Camera,
    Pole,
    Location,
    ManualReview,
    Zone
)
from ocr_processor import process_plate_and_issue_ticket
from logger import logger
from utils import is_same_image
from config import CAMERA_USER, CAMERA_PASS
from pydantic import BaseModel

app = FastAPI()

# 1. List the origins your frontend will be served from.
#    You can use ["*"] to allow all, but in production it's safer to list exact URLs.
origins = [
    "http://localhost:5173",
    
]

# 2. Add the CORS middleware *before* you include any routers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,          # ⚙️ Allowed origins
    allow_credentials=True,         # ⚙️ Allow cookies, Authorization headers
    allow_methods=["*"],            # ⚙️ Allowed HTTP methods (GET, POST, ...)
    allow_headers=["*"],            # ⚙️ Allowed HTTP headers (Content-Type, Authorization, ...)
    expose_headers=["*"],           # (optional) headers you want JS to read
    max_age=3600,                   # (optional) how long the results of a preflight request can be cached
)

# Directories for saving raw requests and snapshots
SNAPSHOTS_DIR   = "snapshots"
RAW_REQUEST_DIR = os.path.join(SNAPSHOTS_DIR, "raw_request")
SPOT_LAST_DIR   = "spot_last"      # where we keep the "last main_crop" per (camera, spot)

os.makedirs(RAW_REQUEST_DIR, exist_ok=True)
os.makedirs(SNAPSHOTS_DIR,   exist_ok=True)
os.makedirs(SPOT_LAST_DIR,   exist_ok=True)


def _retry_commit(obj, session):
    """
    Try to session.commit() on obj; if commit fails due to lost connection,
    rollback/close and retry once with a fresh session.
    """
    try:
        session.commit()
    except OperationalError:
        logger.warning("Lost DB connection during commit; retrying once", exc_info=True)
        try:
            session.rollback()
        except:
            pass
        try:
            session.close()
        except:
            pass

        new_sess = SessionLocal()
        try:
            new_sess.add(obj)
            new_sess.commit()
        finally:
            new_sess.close()


def _as_dict(model_obj):
    """Return a dict of column values for a SQLAlchemy model instance.

    Handles ``bytes``/``bytearray`` fields by converting them to a hex string so
    that FastAPI's ``jsonable_encoder`` won't attempt to decode them as UTF-8,
    which would raise ``UnicodeDecodeError``.
    """

    result = {}
    for c in model_obj.__table__.columns:
        value = getattr(model_obj, c.name)
        if isinstance(value, (bytes, bytearray, memoryview)):
            try:
                value = value.decode("utf-8")
            except Exception:
                value = value.hex()
        result[c.name] = value
    return result


class LocationCreate(BaseModel):
    name: str
    code: str
    portal_name: str
    portal_password: str
    ip_schema: str


class PoleCreate(BaseModel):
    zone_id: int
    code: str
    location_id: int
    number_of_cameras: int | None = 0
    server: str | None = None
    router: str | None = None
    router_ip: str | None = None
    router_vpn_ip: str | None = None


class CameraCreate(BaseModel):
    pole_id: int
    api_code: str
    p_ip: str
    number_of_parking: int | None = 0
    vpn_ip: str | None = None


class ManualCorrection(BaseModel):
    plate_number: str
    plate_code: str
    plate_city: str
    confidence: int


class ZoneCreate(BaseModel):
    code: str
    location_id: int
    parameters: dict | None = None


class LocationUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    portal_name: str | None = None
    portal_password: str | None = None
    ip_schema: str | None = None
    parameters: dict | None = None


class PoleUpdate(BaseModel):
    zone_id: int | None = None
    code: str | None = None
    location_id: int | None = None
    number_of_cameras: int | None = None
    server: str | None = None
    router: str | None = None
    router_ip: str | None = None
    router_vpn_ip: str | None = None
    location_coordinates: str | None = None


class CameraUpdate(BaseModel):
    pole_id: int | None = None
    api_code: str | None = None
    p_ip: str | None = None
    number_of_parking: int | None = None
    vpn_ip: str | None = None


class ZoneUpdate(BaseModel):
    code: str | None = None
    location_id: int | None = None
    parameters: dict | None = None


class TicketUpdate(BaseModel):
    camera_id: int | None = None
    spot_number: int | None = None
    plate_number: str | None = None
    plate_code: str | None = None
    plate_city: str | None = None
    confidence: int | None = None
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    parkonic_trip_id: int | None = None


class ReportUpdate(BaseModel):
    camera_id: int | None = None
    event: str | None = None
    report_type: str | None = None
    timestamp: datetime | None = None
    payload: dict | None = None


class ManualReviewUpdate(BaseModel):
    review_status: str | None = None


@app.post("/post")
async def receive_parking_data(request: Request, background_tasks: BackgroundTasks):
    """
    1) Save raw JSON to disk (catching ClientDisconnect).
    2) Validate required fields.
    3) Split parking_area into (location_code, api_code).
    4) Lookup camera_id, pole_id, camera_ip in DB (short‐lived session, with retry).
    5) If occupancy == 0 → EXIT: feature‐match vs. last‐saved crop → only close if truly gone.
    6) If occupancy == 1 → ENTRY: check for existing open ticket; then insert report; save snapshot; queue OCR.
    """

    # ── 1) Read raw body & save to file ──
    try:
        raw_body = await request.body()
    except ClientDisconnect:
        logger.error("Client disconnected before sending body", exc_info=True)
        raise HTTPException(status_code=400, detail="Client disconnected before sending body")

    ts     = datetime.now().strftime("%Y%m%d%H%M%S")
    raw_fn = os.path.join(RAW_REQUEST_DIR, f"raw_request_{ts}.json")
    try:
        with open(raw_fn, "wb") as f:
            f.write(raw_body)
    except Exception:
        logger.error("Failed to write raw request to disk", exc_info=True)

    # ── 2) Parse JSON payload ──
    try:
        payload = await request.json()
    except Exception as e:
        logger.error("Failed to parse JSON payload", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    # ── 2a) Ensure required fields are present ──
    required_fields = [
        "event", "device", "time", "report_type",
        "resolution_w", "resolution_y", "parking_area",
        "index_number", "occupancy", "duration",
        "coordinate_x1", "coordinate_y1",
        "coordinate_x2", "coordinate_y2",
        "coordinate_x3", "coordinate_y3",
        "coordinate_x4", "coordinate_y4",
        "vehicle_frame_x1", "vehicle_frame_y1",
        "vehicle_frame_x2", "vehicle_frame_y2",
        "snapshot"
    ]
    missing = [f for f in required_fields if payload.get(f) is None]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing fields: {', '.join(missing)}"
        )

    # ── 3) Split parking_area → letters+digits ──
    m = re.match(r"^([A-Za-z]+)(\d+)$", payload["parking_area"])
    if not m:
        raise HTTPException(
            status_code=400,
            detail="Invalid parking_area format (expected letters+digits, e.g. 'NAD95')"
        )
    location_code = m.group(1)   # e.g. "NAD"
    api_code      = m.group(2)   # e.g. "95"
    spot_number   = payload["index_number"]

    # ── 4) Lookup camera_id, pole_id, camera_ip ──
    try:
        db = SessionLocal()
        stmt = text(
            """
            SELECT
              c.id      AS camera_id,
              c.pole_id AS pole_id,
              c.p_ip    AS camera_ip
            FROM cameras AS c
            JOIN poles     AS p ON c.pole_id   = p.id
            JOIN zones     AS z ON p.zone_id    = z.id
            JOIN locations AS l ON p.location_id = l.id
            WHERE l.code    = :loc_code
              AND c.api_code = :api_code
            LIMIT 1
            """
        )
        row = db.execute(stmt, {"loc_code": location_code, "api_code": api_code}).fetchone()
        db.close()

        if row is None:
            raise HTTPException(status_code=400, detail="No camera found for that parking_area")

        camera_id, pole_id, camera_ip = row

    except OperationalError:
        # Retry once on lost connection
        logger.warning("Lost DB connection during camera lookup; retrying once", exc_info=True)
        try:
            db.rollback()
        except:
            pass
        try:
            db.close()
        except:
            pass

        db2 = SessionLocal()
        try:
            row2 = db2.execute(stmt, {"loc_code": location_code, "api_code": api_code}).fetchone()
            if row2 is None:
                raise HTTPException(status_code=400, detail="No camera found for that parking_area")
            camera_id, pole_id, camera_ip = row2
        except SQLAlchemyError as final_err:
            db2.rollback()
            db2.close()
            logger.error("Final DB failure during camera lookup", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Database lookup failed: {final_err}")
        finally:
            db2.close()

    except SQLAlchemyError as sa_err:
        logger.error("Database error while looking up camera", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {sa_err}")

    # ── 5) If occupancy == 0 → attempt to “EXIT” ──
    if payload["occupancy"] == 0:
        # 5a) Decode snapshot and crop to the parking polygon (for feature‐matching)
        try:
            raw_bytes = base64.b64decode(payload["snapshot"])
            pil_img   = Image.open(io.BytesIO(raw_bytes))
        except Exception:
            pil_img = None
            logger.error("Failed to decode snapshot for EXIT check", exc_info=True)

        if pil_img:
            coords = [
                (payload["coordinate_x1"], payload["coordinate_y1"]),
                (payload["coordinate_x2"], payload["coordinate_y2"]),
                (payload["coordinate_x3"], payload["coordinate_y3"]),
                (payload["coordinate_x4"], payload["coordinate_y4"])
            ]
            xs, ys = zip(*coords)
            left, right = min(xs), max(xs)
            top, bottom = min(ys), max(ys)

            current_crop = pil_img.crop((left, top, right, bottom))
            temp_crop_path = os.path.join(SNAPSHOTS_DIR, f"temp_crop_exit_{ts}.jpg")
            current_crop.save(temp_crop_path)

            # Compare against last‐seen “main_crop” for this (camera, spot)
            last_image_path = os.path.join(SPOT_LAST_DIR, f"spot_{camera_id}_{spot_number}.jpg")
            if os.path.isfile(last_image_path):
                try:
                    same = is_same_image(
                        last_image_path,
                        temp_crop_path,
                        min_match_count=50,
                        inlier_ratio_thresh=0.5
                    )
                    if same:
                        # The car is still there: ignore this phantom “0”
                        os.remove(temp_crop_path)
                        logger.debug(
                            "EXIT report ignored (false‐clear). Camera=%d, Spot=%d",
                            camera_id, spot_number
                        )
                        return JSONResponse(status_code=200, content={"message": "False‐clear; skip EXIT"})
                except Exception:
                    logger.error("Error in feature-match during EXIT check", exc_info=True)

            try:
                os.remove(temp_crop_path)
            except:
                pass

        # 5b) Proceed to close any open ticket
        db2 = SessionLocal()
        try:
            open_ticket = db2.query(Ticket).filter_by(
                camera_id   = camera_id,
                spot_number = spot_number,
                exit_time   = None
            ).order_by(Ticket.entry_time.desc()).first()

            if open_ticket:
                open_ticket.exit_time = datetime.fromisoformat(payload["time"])
                _retry_commit(open_ticket, db2)
                logger.debug(
                    "Closed ticket id=%d at %s",
                    open_ticket.id, payload["time"]
                )
                return JSONResponse(status_code=200, content={"message": "Exit recorded"})
            else:
                logger.debug(
                    "No open ticket to close for camera=%d, spot=%d",
                    camera_id, spot_number
                )
                return JSONResponse(status_code=200, content={"message": "No open ticket to close"})

        except SQLAlchemyError as e:
            try:
                db2.rollback()
            except:
                pass
            logger.error("Database error on EXIT", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Database error on exit: {e}")
        finally:
            db2.close()

    # ── 6) If occupancy == 1 → “ENTRY” ──
    else:
        db2 = SessionLocal()
        try:
            # 6a) Ensure no open ticket already exists
            existing_ticket = db2.query(Ticket).filter_by(
                camera_id   = camera_id,
                spot_number = spot_number,
                exit_time   = None
            ).order_by(Ticket.entry_time.desc()).first()

            if existing_ticket:
                logger.debug(
                    "Spot %d on camera %d already occupied (ticket id=%d)",
                    spot_number, camera_id, existing_ticket.id
                )
                return JSONResponse(status_code=200, content={"message": "Spot already occupied"})

            # 6b) Insert into reports table
            new_report = Report(
                camera_id  = camera_id,
                event       = payload["event"],
                report_type = payload["report_type"],
                timestamp   = datetime.fromisoformat(payload["time"]),
                payload     = payload
            )
            db2.add(new_report)
            _retry_commit(new_report, db2)

        except SQLAlchemyError as sa_err:
            try:
                db2.rollback()
            except:
                pass
            logger.error("Database error on report insert", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Database error on report insert: {sa_err}")
        finally:
            db2.close()

        # 6c) Save the snapshot image locally (for OCR thread)
        park_folder = os.path.join(
            SNAPSHOTS_DIR,
            f"parking_cam{camera_id}_spot{spot_number}_{ts}"
        )
        os.makedirs(park_folder, exist_ok=True)

        try:
            img_data      = base64.b64decode(payload["snapshot"])
            snapshot_path = os.path.join(park_folder, f"snapshot_{ts}.jpg")
            with open(snapshot_path, "wb") as imgf:
                imgf.write(img_data)
        except Exception as e:
            logger.error("Failed to decode/save snapshot", exc_info=True)
            raise HTTPException(status_code=400, detail=f"Cannot decode snapshot: {e}")

        # 6d) Enqueue OCR/ticket logic in the background
        background_tasks.add_task(
            process_plate_and_issue_ticket,
            payload,
            park_folder,
            ts,
            camera_id,
            pole_id,
            spot_number,
            camera_ip,
            CAMERA_USER,
            CAMERA_PASS
        )

        return JSONResponse(status_code=200, content={"message": "Entry queued for processing"})


@app.post("/locations")
def create_location(loc: LocationCreate):
    db = SessionLocal()
    try:
        new_obj = Location(**loc.dict())
        db.add(new_obj)
        _retry_commit(new_obj, db)
        return {"id": new_obj.id}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    finally:
        db.close()


@app.post("/zones")
def create_zone(zone: ZoneCreate):
    db = SessionLocal()
    try:
        new_obj = Zone(**zone.dict())
        db.add(new_obj)
        _retry_commit(new_obj, db)
        return {"id": new_obj.id}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    finally:
        db.close()


@app.post("/poles")
def create_pole(pole: PoleCreate):
    db = SessionLocal()
    try:
        new_obj = Pole(**pole.dict())
        db.add(new_obj)
        _retry_commit(new_obj, db)
        return {"id": new_obj.id}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    finally:
        db.close()


@app.post("/cameras")
def create_camera(cam: CameraCreate):
    db = SessionLocal()
    try:
        new_obj = Camera(**cam.dict())
        db.add(new_obj)
        _retry_commit(new_obj, db)
        return {"id": new_obj.id}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    finally:
        db.close()


@app.get("/locations")
def list_locations():
    db = SessionLocal()
    try:
        objs = db.query(Location).all()
        return [_as_dict(o) for o in objs]
    finally:
        db.close()


@app.get("/locations/{loc_id}")
def get_location(loc_id: int):
    db = SessionLocal()
    try:
        obj = db.query(Location).get(loc_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.put("/locations/{loc_id}")
def update_location(loc_id: int, loc: LocationUpdate):
    db = SessionLocal()
    try:
        obj = db.query(Location).get(loc_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        for k, v in loc.dict(exclude_unset=True).items():
            setattr(obj, k, v)
        _retry_commit(obj, db)
        return _as_dict(obj)
    finally:
        db.close()


@app.delete("/locations/{loc_id}")
def delete_location(loc_id: int):
    db = SessionLocal()
    try:
        obj = db.query(Location).get(loc_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        db.delete(obj)
        _retry_commit(obj, db)
        return {"status": "deleted"}
    finally:
        db.close()


@app.get("/zones")
def list_zones():
    db = SessionLocal()
    try:
        return [_as_dict(z) for z in db.query(Zone).all()]
    finally:
        db.close()


@app.get("/zones/{zone_id}")
def get_zone(zone_id: int):
    db = SessionLocal()
    try:
        obj = db.query(Zone).get(zone_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.put("/zones/{zone_id}")
def update_zone(zone_id: int, zone: ZoneUpdate):
    db = SessionLocal()
    try:
        obj = db.query(Zone).get(zone_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        for k, v in zone.dict(exclude_unset=True).items():
            setattr(obj, k, v)
        _retry_commit(obj, db)
        return _as_dict(obj)
    finally:
        db.close()


@app.delete("/zones/{zone_id}")
def delete_zone(zone_id: int):
    db = SessionLocal()
    try:
        obj = db.query(Zone).get(zone_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        db.delete(obj)
        _retry_commit(obj, db)
        return {"status": "deleted"}
    finally:
        db.close()


@app.get("/poles")
def list_poles():
    db = SessionLocal()
    try:
        return [_as_dict(p) for p in db.query(Pole).all()]
    finally:
        db.close()


@app.get("/poles/{pole_id}")
def get_pole(pole_id: int):
    db = SessionLocal()
    try:
        obj = db.query(Pole).get(pole_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.put("/poles/{pole_id}")
def update_pole(pole_id: int, pole: PoleUpdate):
    db = SessionLocal()
    try:
        obj = db.query(Pole).get(pole_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        for k, v in pole.dict(exclude_unset=True).items():
            setattr(obj, k, v)
        _retry_commit(obj, db)
        return _as_dict(obj)
    finally:
        db.close()


@app.delete("/poles/{pole_id}")
def delete_pole(pole_id: int):
    db = SessionLocal()
    try:
        obj = db.query(Pole).get(pole_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        db.delete(obj)
        _retry_commit(obj, db)
        return {"status": "deleted"}
    finally:
        db.close()


@app.get("/cameras")
def list_cameras():
    db = SessionLocal()
    try:
        return [_as_dict(c) for c in db.query(Camera).all()]
    finally:
        db.close()


@app.get("/cameras/{cam_id}")
def get_camera(cam_id: int):
    db = SessionLocal()
    try:
        obj = db.query(Camera).get(cam_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.put("/cameras/{cam_id}")
def update_camera(cam_id: int, cam: CameraUpdate):
    db = SessionLocal()
    try:
        obj = db.query(Camera).get(cam_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        for k, v in cam.dict(exclude_unset=True).items():
            setattr(obj, k, v)
        _retry_commit(obj, db)
        return _as_dict(obj)
    finally:
        db.close()


@app.delete("/cameras/{cam_id}")
def delete_camera(cam_id: int):
    db = SessionLocal()
    try:
        obj = db.query(Camera).get(cam_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        db.delete(obj)
        _retry_commit(obj, db)
        return {"status": "deleted"}
    finally:
        db.close()


@app.get("/tickets")
def list_tickets():
    db = SessionLocal()
    try:
        return [_as_dict(t) for t in db.query(Ticket).all()]
    finally:
        db.close()


@app.post("/tickets")
def create_ticket(ticket: TicketUpdate):
    db = SessionLocal()
    try:
        new_obj = Ticket(**ticket.dict(exclude_unset=True))
        db.add(new_obj)
        _retry_commit(new_obj, db)
        return {"id": new_obj.id}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    finally:
        db.close()


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int):
    db = SessionLocal()
    try:
        obj = db.query(Ticket).get(ticket_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.put("/tickets/{ticket_id}")
def update_ticket(ticket_id: int, ticket: TicketUpdate):
    db = SessionLocal()
    try:
        obj = db.query(Ticket).get(ticket_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        for k, v in ticket.dict(exclude_unset=True).items():
            setattr(obj, k, v)
        _retry_commit(obj, db)
        return _as_dict(obj)
    finally:
        db.close()


@app.delete("/tickets/{ticket_id}")
def delete_ticket(ticket_id: int):
    db = SessionLocal()
    try:
        obj = db.query(Ticket).get(ticket_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        db.delete(obj)
        _retry_commit(obj, db)
        return {"status": "deleted"}
    finally:
        db.close()


@app.get("/reports")
def list_reports():
    db = SessionLocal()
    try:
        return [_as_dict(r) for r in db.query(Report).all()]
    finally:
        db.close()


@app.post("/reports")
def create_report(report: ReportUpdate):
    db = SessionLocal()
    try:
        new_obj = Report(**report.dict(exclude_unset=True))
        db.add(new_obj)
        _retry_commit(new_obj, db)
        return {"id": new_obj.id}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    finally:
        db.close()


@app.get("/reports/{report_id}")
def get_report(report_id: int):
    db = SessionLocal()
    try:
        obj = db.query(Report).get(report_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.put("/reports/{report_id}")
def update_report(report_id: int, report: ReportUpdate):
    db = SessionLocal()
    try:
        obj = db.query(Report).get(report_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        for k, v in report.dict(exclude_unset=True).items():
            setattr(obj, k, v)
        _retry_commit(obj, db)
        return _as_dict(obj)
    finally:
        db.close()


@app.delete("/reports/{report_id}")
def delete_report(report_id: int):
    db = SessionLocal()
    try:
        obj = db.query(Report).get(report_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        db.delete(obj)
        _retry_commit(obj, db)
        return {"status": "deleted"}
    finally:
        db.close()


@app.get("/manual-reviews")
def list_manual_reviews(status: str = "PENDING"):
    db = SessionLocal()
    try:
        reviews = db.query(ManualReview).filter_by(review_status=status).all()
        data = [
            {
                "id": r.id,
                "camera_id": r.camera_id,
                "spot_number": r.spot_number,
                "event_time": r.event_time.isoformat(),
                "image_path": r.image_path,
                "clip_path": r.clip_path,
                "plate_status": r.plate_status,
            }
            for r in reviews
        ]
        return data
    finally:
        db.close()


@app.get("/manual-reviews/{review_id}/image")
def get_review_image(review_id: int):
    db = SessionLocal()
    try:
        review = db.query(ManualReview).get(review_id)
        if review is None or not os.path.isfile(review.image_path):
            raise HTTPException(status_code=404, detail="Image not found")
        return FileResponse(review.image_path)
    finally:
        db.close()


@app.get("/manual-reviews/{review_id}/video")
def get_review_video(review_id: int):
    db = SessionLocal()
    try:
        review = db.query(ManualReview).get(review_id)
        if review is None or not review.clip_path or not os.path.isfile(review.clip_path):
            raise HTTPException(status_code=404, detail="Clip not found")
        return FileResponse(review.clip_path)
    finally:
        db.close()


@app.post("/manual-reviews/{review_id}/correct")
def correct_manual_review(review_id: int, correction: ManualCorrection):
    db = SessionLocal()
    try:
        review = db.query(ManualReview).get(review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        if review.ticket_id is None:
            raise HTTPException(status_code=400, detail="No associated ticket")

        ticket = db.query(Ticket).get(review.ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")

        ticket.plate_number = correction.plate_number
        ticket.plate_code   = correction.plate_code
        ticket.plate_city   = correction.plate_city
        ticket.confidence   = correction.confidence
        _retry_commit(ticket, db)

        review.review_status = "RESOLVED"
        review.plate_status  = "READ"
        _retry_commit(review, db)

        try:
            from api_client import park_in_request
            from config import PARKONIC_API_TOKEN
            with open(review.image_path, "rb") as f:
                b64_img = base64.b64encode(f.read()).decode("utf-8")
            park_in_request(
                token=PARKONIC_API_TOKEN,
                parkin_time=str(ticket.entry_time),
                plate_code=correction.plate_code,
                plate_number=correction.plate_number,
                emirates=correction.plate_city,
                conf=str(correction.confidence),
                spot_number=ticket.spot_number,
                pole_id=ticket.camera.pole_id,
                images=[b64_img]
            )
        except Exception:
            logger.error("park_in_request failed", exc_info=True)

        return {"status": "updated"}
    finally:
        db.close()


@app.post("/manual-reviews/{review_id}/dismiss")
def dismiss_manual_review(review_id: int):
    db = SessionLocal()
    try:
        review = db.query(ManualReview).get(review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")

        if review.ticket_id:
            ticket = db.query(Ticket).get(review.ticket_id)
            if ticket and ticket.exit_time is None:
                ticket.exit_time = ticket.entry_time
                _retry_commit(ticket, db)

        review.review_status = "RESOLVED"
        _retry_commit(review, db)
        return {"status": "dismissed"}
    finally:
        db.close()


@app.get("/manual-reviews/{review_id}/snapshots")
def list_review_snapshots(review_id: int):
    db = SessionLocal()
    try:
        review = db.query(ManualReview).get(review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        folder = os.path.join(SNAPSHOTS_DIR, review.snapshot_folder)
        if not os.path.isdir(folder):
            raise HTTPException(status_code=404, detail="Snapshot folder not found")
        files = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
        return {"files": files}
    finally:
        db.close()


@app.get("/manual-reviews/{review_id}/snapshots/{filename}")
def get_review_snapshot(review_id: int, filename: str):
    db = SessionLocal()
    try:
        review = db.query(ManualReview).get(review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        folder = os.path.join(SNAPSHOTS_DIR, review.snapshot_folder)
        path = os.path.join(folder, filename)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(path)
    finally:
        db.close()
