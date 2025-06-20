# main.py

import os
import io
import re
import json
import base64
from datetime import datetime, timedelta
import uuid
import asyncio
from queue import Queue
from concurrent.futures import ThreadPoolExecutor, Future
import threading

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, Depends
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import ClientDisconnect
from sqlalchemy import text, asc, desc, func
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from sqlalchemy.orm import joinedload
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext

from PIL import Image
from db import SessionLocal
from models import (
    Report,
    Ticket,
    Camera,
    Pole,
    Location,
    Spot,
    ManualReview,
    ClipRequest,
    Zone,
    User,
    Role,
    Permission,
)
from ocr_processor import process_plate_and_issue_ticket, spot_has_car
from camera_clip import (
    request_camera_clip,
    is_valid_mp4,
    fetch_camera_frame,
    fetch_exit_frame
)
from logger import logger
from utils import is_same_image

from config import API_POLE_ID, API_LOCATION_ID

from pydantic import BaseModel

app = FastAPI()

# Shared thread pool for blocking tasks
EXECUTOR = ThreadPoolExecutor(
    max_workers=int(os.environ.get("MAX_WORKERS", "4"))
)

async def run_in_executor(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, func, *args)

# 1. Determine which origins are allowed to access the API.
#    By default a couple of development IPs are whitelisted, but this can be
#    overridden via the ``CORS_ORIGINS`` environment variable.  Use ``*`` to
#    allow any origin, or provide a comma separated list of hosts.
cors_env = os.environ.get("CORS_ORIGINS")
if cors_env:
    origins = [o.strip() for o in cors_env.split(",")]
else:
    origins = [
        "http://localhost:5000",
        "http://192.168.1.220:5000",
        "http://10.0.8.2:5000",
    ]

# 2. Add the CORS middleware *before* you include any routers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # ⚙️ Allowed origins
    allow_credentials=True,  # ⚙️ Allow cookies, Authorization headers
    allow_methods=["*"],  # ⚙️ Allowed HTTP methods (GET, POST, ...)
    allow_headers=["*"],  # ⚙️ Allowed HTTP headers (Content-Type, Authorization, ...)
    expose_headers=["*"],  # (optional) headers you want JS to read
    max_age=3600,  # (optional) how long the results of a preflight request can be cached
)

# Directories for saving raw requests and snapshots
SNAPSHOTS_DIR = "snapshots"
RAW_REQUEST_DIR = os.path.join(SNAPSHOTS_DIR, "raw_request")
SPOT_LAST_DIR = "spot_last"  # where we keep the "last main_crop" per (camera, spot)
REPORTS_JSON_DIR = "reports_json"  # directory for storing JSON reports

os.makedirs(RAW_REQUEST_DIR, exist_ok=True)
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
os.makedirs(SPOT_LAST_DIR, exist_ok=True)
os.makedirs(REPORTS_JSON_DIR, exist_ok=True)

# ── Authentication setup ───────────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "changeme")
ALGORITHM = "HS256"
# Extend token validity to 24 hours so users stay logged in longer
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


class LocationCreate(BaseModel):
    name: str
    code: str
    portal_name: str
    portal_password: str
    ip_schema: str
    parkonic_api_token: str | None = None
    camera_user: str | None = None
    camera_pass: str | None = None


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
    parkonic_api_token: str | None = None
    camera_user: str | None = None
    camera_pass: str | None = None
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
    image_base64: str | None = None


class ReportUpdate(BaseModel):
    camera_id: int | None = None
    event: str | None = None
    report_type: str | None = None
    timestamp: datetime | None = None
    payload: dict | None = None


class ManualReviewUpdate(BaseModel):
    review_status: str | None = None


class ClipRequestCreate(BaseModel):
    camera_id: int
    start: datetime
    end: datetime


class UserCreate(BaseModel):
    username: str
    password: str
    role_ids: list[int] = []


class UserUpdate(BaseModel):
    username: str | None = None
    password: str | None = None
    role_ids: list[int] | None = None


class RoleCreate(BaseModel):
    name: str
    description: str | None = None
    permission_ids: list[int] = []


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    permission_ids: list[int] | None = None


class PermissionCreate(BaseModel):
    name: str
    description: str | None = None


class PermissionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class SpotCreate(BaseModel):
    camera_id: int
    spot_number: int
    bbox_x1: int
    bbox_y1: int
    bbox_x2: int
    bbox_y2: int


class SpotUpdate(BaseModel):
    spot_number: int | None = None
    bbox_x1: int | None = None
    bbox_y1: int | None = None
    bbox_x2: int | None = None
    bbox_y2: int | None = None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire, "jti": str(uuid.uuid4())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    db = SessionLocal()
    try:
        user = _retry_operation(
            lambda s: (
                s.query(User)
                .options(joinedload(User.roles).joinedload(Role.permissions))
                .filter(User.username == username)
                .first()
            ),
            db,
        )
    finally:
        db.close()
    if user is None:
        raise credentials_exception
    return user


def require_permission(permission_name: str):
    """Dependency that checks the current user has a given permission."""

    def dependency(current_user: User = Depends(get_current_user)):
        for role in current_user.roles:
            if any(p.name == permission_name for p in role.permissions):
                return current_user
        raise HTTPException(status_code=403, detail="Not enough permissions")

    return dependency


@app.post("/users")
def create_user(
    user: UserCreate,
    current_user: User = Depends(require_permission("manage_users")),
):
    db = SessionLocal()
    try:
        if _retry_operation(lambda s: s.query(User).filter(User.username == user.username).first(), db):
            raise HTTPException(status_code=400, detail="Username already exists")
        roles = []
        if user.role_ids:
            roles = _retry_operation(
                lambda s: s.query(Role).filter(Role.id.in_(user.role_ids)).all(),
                db,
            )
        new_user = User(username=user.username, hashed_password=get_password_hash(user.password))
        new_user.roles = roles
        db.add(new_user)
        _retry_commit(new_user, db)
        return {"id": new_user.id}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    finally:
        db.close()


@app.get("/users")
def list_users(current_user: User = Depends(require_permission("manage_users"))):
    db = SessionLocal()
    try:
        users = _retry_operation(
            lambda s: s.query(User).options(joinedload(User.roles)).all(),
            db,
        )
        return [{**_as_dict(u), "roles": [r.id for r in u.roles]} for u in users]
    finally:
        db.close()


@app.get("/users/{user_id}")
def get_user(user_id: int, current_user: User = Depends(require_permission("manage_users"))):
    db = SessionLocal()
    try:
        u = _retry_operation(
            lambda s: s.query(User).options(joinedload(User.roles)).get(user_id),
            db,
        )
        if u is None:
            raise HTTPException(status_code=404, detail="Not found")
        return {**_as_dict(u), "roles": [r.id for r in u.roles]}
    finally:
        db.close()


@app.put("/users/{user_id}")
def update_user(
    user_id: int,
    user: UserUpdate,
    current_user: User = Depends(require_permission("manage_users")),
):
    db = SessionLocal()
    try:
        obj = _retry_operation(
            lambda s: s.query(User).options(joinedload(User.roles)).get(user_id),
            db,
        )
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        if user.username is not None:
            obj.username = user.username
        if user.password is not None:
            obj.hashed_password = get_password_hash(user.password)
        if user.role_ids is not None:
            obj.roles = _retry_operation(
                lambda s: s.query(Role).filter(Role.id.in_(user.role_ids)).all(),
                db,
            )
        _retry_commit(obj, db)
        return {**_as_dict(obj), "roles": [r.id for r in obj.roles]}
    finally:
        db.close()


@app.delete("/users/{user_id}")
def delete_user(user_id: int, current_user: User = Depends(require_permission("manage_users"))):
    db = SessionLocal()
    try:
        obj = _retry_operation(lambda s: s.query(User).get(user_id), db)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        db.delete(obj)
        _retry_commit(obj, db)
        return {"status": "deleted"}
    finally:
        db.close()


@app.post("/roles")
def create_role(
    role: RoleCreate,
    current_user: User = Depends(require_permission("manage_roles")),
):
    db = SessionLocal()
    try:
        if _retry_operation(lambda s: s.query(Role).filter(Role.name == role.name).first(), db):
            raise HTTPException(status_code=400, detail="Role already exists")
        perms = []
        if role.permission_ids:
            perms = _retry_operation(
                lambda s: s.query(Permission).filter(Permission.id.in_(role.permission_ids)).all(),
                db,
            )
        new_role = Role(name=role.name, description=role.description)
        new_role.permissions = perms
        db.add(new_role)
        _retry_commit(new_role, db)
        return {"id": new_role.id}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    finally:
        db.close()


@app.get("/roles")
def list_roles(current_user: User = Depends(require_permission("manage_roles"))):
    db = SessionLocal()
    try:
        roles = _retry_operation(
            lambda s: s.query(Role).options(joinedload(Role.permissions)).all(),
            db,
        )
        return [{**_as_dict(r), "permissions": [p.id for p in r.permissions]} for r in roles]
    finally:
        db.close()


@app.get("/roles/{role_id}")
def get_role(role_id: int, current_user: User = Depends(require_permission("manage_roles"))):
    db = SessionLocal()
    try:
        role = _retry_operation(
            lambda s: s.query(Role).options(joinedload(Role.permissions)).get(role_id),
            db,
        )
        if role is None:
            raise HTTPException(status_code=404, detail="Not found")
        return {**_as_dict(role), "permissions": [p.id for p in role.permissions]}
    finally:
        db.close()


@app.put("/roles/{role_id}")
def update_role(
    role_id: int,
    role: RoleUpdate,
    current_user: User = Depends(require_permission("manage_roles")),
):
    db = SessionLocal()
    try:
        obj = _retry_operation(
            lambda s: s.query(Role).options(joinedload(Role.permissions)).get(role_id),
            db,
        )
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        if role.name is not None:
            obj.name = role.name
        if role.description is not None:
            obj.description = role.description
        if role.permission_ids is not None:
            obj.permissions = _retry_operation(
                lambda s: s.query(Permission).filter(Permission.id.in_(role.permission_ids)).all(),
                db,
            )
        _retry_commit(obj, db)
        return {**_as_dict(obj), "permissions": [p.id for p in obj.permissions]}
    finally:
        db.close()


@app.delete("/roles/{role_id}")
def delete_role(role_id: int, current_user: User = Depends(require_permission("manage_roles"))):
    db = SessionLocal()
    try:
        obj = _retry_operation(lambda s: s.query(Role).get(role_id), db)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        db.delete(obj)
        _retry_commit(obj, db)
        return {"status": "deleted"}
    finally:
        db.close()


@app.post("/permissions")
def create_permission(
    perm: PermissionCreate,
    current_user: User = Depends(require_permission("manage_permissions")),
):
    db = SessionLocal()
    try:
        if _retry_operation(lambda s: s.query(Permission).filter(Permission.name == perm.name).first(), db):
            raise HTTPException(status_code=400, detail="Permission already exists")
        new_perm = Permission(name=perm.name, description=perm.description)
        db.add(new_perm)
        _retry_commit(new_perm, db)
        return {"id": new_perm.id}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    finally:
        db.close()


@app.get("/permissions")
def list_permissions(current_user: User = Depends(require_permission("manage_permissions"))):
    db = SessionLocal()
    try:
        perms = _retry_operation(lambda s: s.query(Permission).all(), db)
        return [_as_dict(p) for p in perms]
    finally:
        db.close()


@app.get("/permissions/{perm_id}")
def get_permission(
    perm_id: int, current_user: User = Depends(require_permission("manage_permissions"))
):
    db = SessionLocal()
    try:
        perm = _retry_operation(lambda s: s.query(Permission).get(perm_id), db)
        if perm is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(perm)
    finally:
        db.close()


@app.put("/permissions/{perm_id}")
def update_permission(
    perm_id: int,
    perm: PermissionUpdate,
    current_user: User = Depends(require_permission("manage_permissions")),
):
    db = SessionLocal()
    try:
        obj = _retry_operation(lambda s: s.query(Permission).get(perm_id), db)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        for k, v in perm.dict(exclude_unset=True).items():
            setattr(obj, k, v)
        _retry_commit(obj, db)
        return _as_dict(obj)
    finally:
        db.close()


@app.delete("/permissions/{perm_id}")
def delete_permission(
    perm_id: int, current_user: User = Depends(require_permission("manage_permissions"))
):
    db = SessionLocal()
    try:
        obj = _retry_operation(lambda s: s.query(Permission).get(perm_id), db)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        db.delete(obj)
        _retry_commit(obj, db)
        return {"status": "deleted"}
    finally:
        db.close()


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


def _retry_operation(func, session):
    """Execute ``func(session)`` and retry once on ``OperationalError``.

    If the first attempt raises ``OperationalError``, the session is
    rolled back and closed, then the function is called again with a new
    fresh session.  Any return value from ``func`` is returned.
    """
    try:
        return func(session)
    except OperationalError:
        logger.warning(
            "Lost DB connection during operation; retrying once", exc_info=True
        )
        try:
            session.rollback()
        except Exception:
            pass
        try:
            session.close()
        except Exception:
            pass

        new_sess = SessionLocal()
        try:
            return func(new_sess)
        finally:
            new_sess.close()


def save_report_to_file(payload: dict, camera_id: int, spot_number: int, ts: str):
    """Write parking report payload to a JSON file under ``REPORTS_JSON_DIR``."""
    filename = os.path.join(
        REPORTS_JSON_DIR, f"report_cam{camera_id}_spot{spot_number}_{ts}.json"
    )
    try:
        with open(filename, "w") as f:
            json.dump(payload, f)
    except Exception:
        logger.error("Failed to write report JSON", exc_info=True)


def _process_plate_task(
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
    parkonic_api_token: str,
    rtsp_path: str = "/",
):
    """Run plate processing synchronously in the worker thread."""
    process_plate_and_issue_ticket(
        payload,
        park_folder,
        ts,
        camera_id,
        pole_id,
        api_pole_id,
        spot_number,
        camera_ip,
        camera_user,
        camera_pass,
        parkonic_api_token,
        rtsp_path,
    )


def _exit_flow(
    payload: dict,
    ts: str,
    camera_id: int,
    api_pole_id: int | None,
    spot_number: int,
    camera_ip: str,
    cam_user: str,
    cam_pass: str,
    parkonic_api_token: str,
):
    """Handle EXIT logic synchronously."""
    frame_bytes = None
    try:
        frame_bytes = fetch_exit_frame(
            camera_ip=camera_ip,
            username=cam_user,
            password=cam_pass,
            event_time=datetime.fromisoformat(payload["time"]),
        )
    except Exception:
        logger.error("Failed to fetch camera frame for EXIT check", exc_info=True)

    if frame_bytes is None:
        try:
            frame_bytes = base64.b64decode(payload["snapshot"])
        except Exception:
            frame_bytes = None

    if frame_bytes is not None:
        try:
            if spot_has_car(frame_bytes, camera_id=camera_id, spot_number=spot_number):
                logger.debug(
                    "EXIT report ignored - spot still occupied. Camera=%d, Spot=%d",
                    camera_id,
                    spot_number,
                )
                return JSONResponse(status_code=200, content={"message": "Spot still occupied"})
        except Exception:
            logger.error("Error checking spot occupancy", exc_info=True)

    try:
        raw_bytes = base64.b64decode(payload["snapshot"])
        pil_img = Image.open(io.BytesIO(raw_bytes))
    except Exception:
        pil_img = None
        logger.error("Failed to decode snapshot for EXIT check", exc_info=True)

    if pil_img:
        temp_path = os.path.join(SNAPSHOTS_DIR, f"temp_exit_{ts}.jpg")
        pil_img.save(temp_path)
        try:
            os.remove(temp_path)
        except Exception:
            pass

    db2 = SessionLocal()
    try:
        open_ticket = (
            db2.query(Ticket)
            .filter_by(camera_id=camera_id, spot_number=spot_number, exit_time=None)
            .order_by(Ticket.entry_time.desc())
            .first()
        )

        if open_ticket:
            open_ticket.exit_time = datetime.fromisoformat(payload["time"])
            _retry_commit(open_ticket, db2)
            logger.debug(
                "Closed ticket id=%d at %s camera %f spot %d",
                open_ticket.id,
                payload["time"],
                camera_id,
                spot_number,
            )

            if open_ticket.parkonic_trip_id is not None:
                try:
                    from api_client import park_out_request

                    park_out_request(
                        token=parkonic_api_token or "",
                        parkout_time=payload["time"],
                        spot_number=spot_number,
                        pole_id=api_pole_id,
                        trip_id=open_ticket.parkonic_trip_id,
                    )
                except Exception:
                    logger.error("park_out_request failed", exc_info=True)

            return JSONResponse(status_code=200, content={"message": "Exit recorded"})
        else:
            logger.debug(
                "No open ticket to close for camera=%d, spot=%d",
                camera_id,
                spot_number,
            )
            return JSONResponse(status_code=200, content={"message": "No open ticket to close"})

    except SQLAlchemyError as e:
        try:
            db2.rollback()
        except Exception:
            pass
        logger.error("Database error on EXIT", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error on exit: {e}")
    finally:
        db2.close()


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


@app.post("/token")
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    db = SessionLocal()
    try:
        user = _retry_operation(
            lambda s: (
                s.query(User)
                .options(joinedload(User.roles))
                .filter(User.username == form_data.username)
                .first()
            ),
            db,
        )
        if not user or not verify_password(form_data.password, user.hashed_password):
            raise HTTPException(status_code=401, detail="Incorrect username or password")
        role_names = [r.name for r in user.roles]
    finally:
        db.close()
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "roles": role_names},
        expires_delta=access_token_expires,
    )
    logger.debug(
        "Issued access token for user %s with roles %s", user.username, role_names
    )
    return {"access_token": access_token, "token_type": "bearer","roles": role_names  }


def _process_post_task(payload: dict, raw_body: bytes, ts: str):
    """Process a /post request synchronously."""
    raw_fn = os.path.join(RAW_REQUEST_DIR, f"raw_request_{ts}.json")
    try:
        with open(raw_fn, "wb") as f:
            f.write(raw_body)
    except Exception:
        logger.error("Failed to write raw request to disk", exc_info=True)

    required_fields = [
        "event",
        "device",
        "time",
        "report_type",
        "resolution_w",
        "resolution_y",
        "parking_area",
        "index_number",
        "occupancy",
        "duration",
        "coordinate_x1",
        "coordinate_y1",
        "coordinate_x2",
        "coordinate_y2",
        "coordinate_x3",
        "coordinate_y3",
        "coordinate_x4",
        "coordinate_y4",
        "vehicle_frame_x1",
        "vehicle_frame_y1",
        "vehicle_frame_x2",
        "vehicle_frame_y2",
        "snapshot",
    ]
    missing = [f for f in required_fields if payload.get(f) is None]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")

    m = re.match(r"^([A-Za-z]+)(\d+)$", payload["parking_area"])
    if not m:
        raise HTTPException(
            status_code=400,
            detail="Invalid parking_area format (expected letters+digits, e.g. 'NAD95')",
        )
    location_code = m.group(1)
    api_code = m.group(2)
    spot_number = payload["index_number"]

    rtsp_path = "/"
    try:
        db = SessionLocal()
        stmt = text(
            """
            SELECT
              c.id      AS camera_id,
              c.pole_id AS pole_id,

              c.p_ip    AS camera_ip,
              p.api_pole_id       AS api_pole_id,
              l.parkonic_api_token AS parkonic_api_token,
              l.camera_user        AS camera_user,
              l.camera_pass        AS camera_pass,
              l.parameters         AS location_params

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

        (
            camera_id,
            pole_id,
            camera_ip,
            api_pole_id,
            parkonic_api_token,
            cam_user,
            cam_pass,
            loc_params,
        ) = row

        rtsp_path = "/"
        if loc_params:
            try:
                if isinstance(loc_params, str):
                    loc_params = json.loads(loc_params)
                rtsp_path = loc_params.get("rtsp_path", "/") if isinstance(loc_params, dict) else "/"
            except Exception:
                rtsp_path = "/"

    except OperationalError:
        logger.warning("Lost DB connection during camera lookup; retrying once", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass

        db2 = SessionLocal()
        try:
            row2 = db2.execute(stmt, {"loc_code": location_code, "api_code": api_code}).fetchone()
            if row2 is None:
                raise HTTPException(status_code=400, detail="No camera found for that parking_area")

            (
                camera_id,
                pole_id,
                camera_ip,
                api_pole_id,
                parkonic_api_token,
                cam_user,
                cam_pass,
                loc_params,
            ) = row2

            rtsp_path = "/"
            if loc_params:
                try:
                    if isinstance(loc_params, str):
                        loc_params = json.loads(loc_params)
                    rtsp_path = loc_params.get("rtsp_path", "/") if isinstance(loc_params, dict) else "/"
                except Exception:
                    rtsp_path = "/"

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

    if payload["occupancy"] == 0:
        return _exit_flow(
            payload,
            ts,
            camera_id,
            api_pole_id,
            spot_number,
            camera_ip,
            cam_user,
            cam_pass,
            parkonic_api_token,
        )
    else:
        db2 = SessionLocal()
        try:
            existing_ticket = (
                db2.query(Ticket)
                .filter_by(camera_id=camera_id, spot_number=spot_number, exit_time=None)
                .order_by(Ticket.entry_time.desc())
                .first()
            )

            if existing_ticket:
                logger.debug(
                    "Spot %d on camera %d already occupied (ticket id=%d)",
                    spot_number,
                    camera_id,
                    existing_ticket.id,
                )
                return JSONResponse(status_code=200, content={"message": "Spot already occupied"})

            save_report_to_file(payload, camera_id, spot_number, ts)

        except SQLAlchemyError as sa_err:
            try:
                db2.rollback()
            except Exception:
                pass
            logger.error("Database error during entry handling", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Database error on entry: {sa_err}")
        finally:
            db2.close()

        park_folder = os.path.join(SNAPSHOTS_DIR, f"parking_cam{camera_id}_spot{spot_number}_{ts}")
        os.makedirs(park_folder, exist_ok=True)

        try:
            img_data = base64.b64decode(payload["snapshot"])
            snapshot_path = os.path.join(park_folder, f"snapshot_{ts}.jpg")
            with open(snapshot_path, "wb") as imgf:
                imgf.write(img_data)
        except Exception as e:
            logger.error("Failed to decode/save snapshot", exc_info=True)
            raise HTTPException(status_code=400, detail=f"Cannot decode snapshot: {e}")

        _process_plate_task(
            payload,
            park_folder,
            ts,
            camera_id,
            pole_id,
            api_pole_id,
            spot_number,
            camera_ip,
            cam_user,
            cam_pass,
            parkonic_api_token,
            rtsp_path,
        )

        return JSONResponse(status_code=200, content={"message": "Entry queued for processing"})


# Queue and worker thread for sequential processing of /post requests
POST_QUEUE: Queue[tuple] = Queue()

def _post_worker():
    while True:
        payload, raw_body, ts, fut = POST_QUEUE.get()
        try:
            result = _process_post_task(payload, raw_body, ts)
            fut.set_result(result)
        except Exception as e:
            fut.set_exception(e)
        finally:
            POST_QUEUE.task_done()

_post_thread = threading.Thread(target=_post_worker, daemon=True)
_post_thread.start()


@app.post("/post")
async def receive_parking_data(
    request: Request,
):
    """
    1) Save raw JSON to disk (catching ClientDisconnect).
    2) Validate required fields.
    3) Split parking_area into (location_code, api_code).
    4) Lookup camera_id, pole_id, camera_ip in DB (short‐lived session, with retry).
    5) If occupancy == 0 → EXIT: feature‐match vs. last‐saved crop → only close if truly gone.
    6) If occupancy == 1 → ENTRY: check for existing open ticket; then save report to JSON; save snapshot; queue OCR.
    """

    # ── 1) Read raw body & save to file ──
    try:
        raw_body = await request.body()
    except ClientDisconnect:
        logger.error("Client disconnected before sending body", exc_info=True)
        raise HTTPException(status_code=400, detail="Client disconnected before sending body")

    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    try:
        payload = await request.json()
    except Exception as e:
        logger.error("Failed to parse JSON payload", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    fut: Future = Future()
    POST_QUEUE.put((payload, raw_body, ts, fut))
    return await asyncio.wrap_future(fut)



@app.post("/locations")
def create_location(
    loc: LocationCreate,
    current_user: User = Depends(get_current_user),
):
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
def create_zone(
    zone: ZoneCreate,
    current_user: User = Depends(get_current_user),
):
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
def create_pole(
    pole: PoleCreate,
    current_user: User = Depends(get_current_user),
):
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
def create_camera(
    cam: CameraCreate,
    current_user: User = Depends(get_current_user),
):
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
def list_locations(current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        objs = db.query(Location).order_by(desc(Location.created_at)).all()
        return [_as_dict(o) for o in objs]
    finally:
        db.close()


@app.get("/locations/{loc_id}")
def get_location(loc_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        obj = db.query(Location).get(loc_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.put("/locations/{loc_id}")
def update_location(
    loc_id: int,
    loc: LocationUpdate,
    current_user: User = Depends(get_current_user),
):
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
def delete_location(loc_id: int, current_user: User = Depends(get_current_user)):
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
def list_zones(current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        objs = db.query(Zone).order_by(desc(Zone.id)).all()
        return [_as_dict(z) for z in objs]
    finally:
        db.close()


@app.get("/zones/{zone_id}")
def get_zone(zone_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        obj = db.query(Zone).get(zone_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.put("/zones/{zone_id}")
def update_zone(
    zone_id: int,
    zone: ZoneUpdate,
    current_user: User = Depends(get_current_user),
):
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
def delete_zone(zone_id: int, current_user: User = Depends(get_current_user)):
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
def list_poles(current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        objs = db.query(Pole).order_by(desc(Pole.id)).all()
        return [_as_dict(p) for p in objs]
    finally:
        db.close()


@app.get("/poles/{pole_id}")
def get_pole(pole_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        obj = db.query(Pole).get(pole_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.put("/poles/{pole_id}")
def update_pole(
    pole_id: int,
    pole: PoleUpdate,
    current_user: User = Depends(get_current_user),
):
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
def delete_pole(pole_id: int, current_user: User = Depends(get_current_user)):
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
def list_cameras(current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        objs = db.query(Camera).order_by(desc(Camera.id)).all()
        return [_as_dict(c) for c in objs]
    finally:
        db.close()


@app.get("/cameras/{cam_id}")
def get_camera(cam_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        obj = db.query(Camera).get(cam_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.get("/cameras/{cam_id}/clip")
def get_camera_clip(
    cam_id: int,
    start: str,
    end: str,
    current_user: User = Depends(get_current_user),
):
    """Fetch a video clip from a camera between ``start`` and ``end``."""

    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime format")

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="end must be after start")

    db = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT c.p_ip, l.camera_user, l.camera_pass
                FROM cameras c
                JOIN poles p ON c.pole_id = p.id
                JOIN locations l ON p.location_id = l.id
                WHERE c.id = :cam_id
                LIMIT 1
                """
            ),
            {"cam_id": cam_id},
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Camera not found")

        cam_ip, user, pwd = row

    finally:
        db.close()

    clip_path = request_camera_clip(
        camera_ip=cam_ip,
        username=user or "",
        password=pwd or "",
        start_dt=start_dt,
        end_dt=end_dt,
        segment_name=start_dt.strftime("%Y%m%d%H%M%S"),
        unique_tag=str(cam_id),
    )

    if not clip_path or not os.path.isfile(clip_path) or not is_valid_mp4(clip_path):
        raise HTTPException(status_code=500, detail="Failed to fetch clip")

    return FileResponse(clip_path)


@app.get("/cameras/{cam_id}/frame")
def get_camera_frame(
    cam_id: int,
    current_user: User = Depends(get_current_user),
):
    """Return a JPEG frame captured from the camera."""

    db = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT c.p_ip, l.camera_user, l.camera_pass, l.parameters
                FROM cameras c
                JOIN poles p ON c.pole_id = p.id
                JOIN locations l ON p.location_id = l.id
                WHERE c.id = :cam_id
                LIMIT 1
                """
            ),
            {"cam_id": cam_id},
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Camera not found")

        cam_ip, user, pwd, params = row
        rtsp_path = "/"
        if params:
            try:
                if isinstance(params, str):
                    params = json.loads(params)
                rtsp_path = params.get("rtsp_path", "/") if isinstance(params, dict) else "/"
            except Exception:
                rtsp_path = "/"
    finally:
        db.close()

    try:
        frame_bytes = fetch_camera_frame(cam_ip, user, pwd, rtsp_path=rtsp_path)
    except Exception:
        logger.error("Failed fetching camera frame", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch frame")

    return Response(content=frame_bytes, media_type="image/jpeg")


def _process_clip_request(req_id: int, cam_ip: str, user: str, pwd: str, start_dt: datetime, end_dt: datetime):
    """Background task to fetch clip and update ClipRequest row."""
    clip_path = request_camera_clip(
        camera_ip=cam_ip,
        username=user or "",
        password=pwd or "",
        start_dt=start_dt,
        end_dt=end_dt,
        segment_name=start_dt.strftime("%Y%m%d%H%M%S"),
        unique_tag=str(req_id),
    )
    session = SessionLocal()
    try:
        req = session.query(ClipRequest).get(req_id)
        if req:
            if clip_path and os.path.isfile(clip_path) and is_valid_mp4(clip_path):
                req.status = "COMPLETED"
                req.clip_path = clip_path
            else:
                req.status = "FAILED"
            session.commit()
    except Exception:
        logger.error("Failed updating clip request %d", req_id, exc_info=True)
        session.rollback()
    finally:
        session.close()

async def _process_clip_request_async(req_id: int, cam_ip: str, user: str, pwd: str, start_dt: datetime, end_dt: datetime):
    await run_in_executor(
        _process_clip_request,
        req_id,
        cam_ip,
        user,
        pwd,
        start_dt,
        end_dt,
    )


@app.post("/clip-requests")
def create_clip_request(
    data: ClipRequestCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """Create a camera clip request and process asynchronously."""

    if data.end <= data.start:
        raise HTTPException(status_code=400, detail="end must be after start")

    db = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT c.p_ip, l.camera_user, l.camera_pass
                FROM cameras c
                JOIN poles p ON c.pole_id = p.id
                JOIN locations l ON p.location_id = l.id
                WHERE c.id = :cam_id
                LIMIT 1
                """
            ),
            {"cam_id": data.camera_id},
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Camera not found")

        cam_ip, user, pwd = row

        req = ClipRequest(
            camera_id=data.camera_id,
            start_time=data.start,
            end_time=data.end,
            status="PENDING",
        )
        db.add(req)
        _retry_commit(req, db)
        background_tasks.add_task(
            _process_clip_request_async,
            req.id,
            cam_ip,
            user or "",
            pwd or "",
            data.start,
            data.end,
        )
        return {"id": req.id, "status": req.status}
    finally:
        db.close()


@app.get("/clip-requests")
def list_clip_requests(current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        objs = db.query(ClipRequest).order_by(desc(ClipRequest.created_at)).all()
        return [ _as_dict(o) for o in objs ]
    finally:
        db.close()


@app.delete("/clip-requests/{req_id}")
def delete_clip_request(req_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        obj = db.query(ClipRequest).get(req_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        path = obj.clip_path
        db.delete(obj)
        _retry_commit(obj, db)
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except Exception:
                logger.error("Failed deleting clip file %s", path, exc_info=True)
        return {"status": "deleted"}
    finally:
        db.close()


@app.put("/cameras/{cam_id}")
def update_camera(
    cam_id: int,
    cam: CameraUpdate,
    current_user: User = Depends(get_current_user),
):
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
def delete_camera(cam_id: int, current_user: User = Depends(get_current_user)):
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


@app.post("/spots")
def create_spot(
    spot: SpotCreate,
    current_user: User = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        if not db.query(Camera.id).filter(Camera.id == spot.camera_id).first():
            raise HTTPException(status_code=404, detail="Camera not found")
        new_obj = Spot(**spot.dict())
        db.add(new_obj)
        _retry_commit(new_obj, db)
        return {"id": new_obj.id}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    finally:
        db.close()


@app.get("/spots")
def list_spots(current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        objs = db.query(Spot).order_by(desc(Spot.id)).all()
        return [_as_dict(o) for o in objs]
    finally:
        db.close()


@app.get("/spots/{spot_id}")
def get_spot(spot_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        obj = db.query(Spot).get(spot_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.get("/cameras/{cam_id}/spots")
def list_camera_spots(cam_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        if not db.query(Camera.id).filter(Camera.id == cam_id).first():
            raise HTTPException(status_code=404, detail="Camera not found")
        objs = db.query(Spot).filter(Spot.camera_id == cam_id).order_by(asc(Spot.spot_number)).all()
        return [_as_dict(o) for o in objs]
    finally:
        db.close()


@app.get("/tickets")
def list_tickets(
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    sort_by: str = "id",
    sort_order: str = "desc",
    current_user: User = Depends(get_current_user),
):
    """Return paginated list of tickets with optional search and sorting."""

    db = SessionLocal()
    try:
        query = db.query(Ticket)

        if search:
            pattern = f"%{search}%"
            query = query.filter(Ticket.plate_number.like(pattern))

        sort_col = getattr(Ticket, sort_by, Ticket.id)
        order_fn = desc if sort_order.lower() == "desc" else asc
        query = query.order_by(order_fn(sort_col))

        total = query.count()
        results = query.offset((page - 1) * page_size).limit(page_size).all()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "data": [_as_dict(t) for t in results],
        }
    finally:
        db.close()


@app.post("/tickets")
def create_ticket(
    ticket: TicketUpdate,
    current_user: User = Depends(get_current_user),
):
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
def get_ticket(ticket_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        obj = db.query(Ticket).get(ticket_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.put("/tickets/{ticket_id}")
def update_ticket(
    ticket_id: int,
    ticket: TicketUpdate,
    current_user: User = Depends(get_current_user),
):
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
def delete_ticket(ticket_id: int, current_user: User = Depends(get_current_user)):
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
def list_reports(current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        objs = db.query(Report).order_by(desc(Report.created_at)).all()
        return [_as_dict(r) for r in objs]
    finally:
        db.close()


@app.post("/reports")
def create_report(
    report: ReportUpdate,
    current_user: User = Depends(get_current_user),
):
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
def get_report(report_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        obj = db.query(Report).get(report_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.put("/reports/{report_id}")
def update_report(
    report_id: int,
    report: ReportUpdate,
    current_user: User = Depends(get_current_user),
):
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
def delete_report(report_id: int, current_user: User = Depends(get_current_user)):
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
def list_manual_reviews(
    status: str = "PENDING",
    page: int = 1,
    page_size: int = 50,
    current_user: User = Depends(get_current_user),
):
    """Return paginated manual reviews filtered by status."""

    db = SessionLocal()
    try:
        query = db.query(ManualReview).filter_by(review_status=status)
        query = query.order_by(desc(ManualReview.created_at))

        total = query.count()
        reviews = query.offset((page - 1) * page_size).limit(page_size).all()

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

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "data": data,
        }
    finally:
        db.close()


@app.get("/manual-reviews/{review_id}")
def get_manual_review(
    review_id: int,
    current_user: User = Depends(get_current_user),
):
    """Return a single manual review by id."""
    db = SessionLocal()
    try:
        obj = db.query(ManualReview).get(review_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _as_dict(obj)
    finally:
        db.close()


@app.get("/manual-reviews/{review_id}/image")
def get_review_image(
    review_id: int,
    current_user: User = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        review = db.query(ManualReview).get(review_id)
        if review is None or not os.path.isfile(review.image_path):
            raise HTTPException(status_code=404, detail="Image not found")
        return FileResponse(review.image_path)
    finally:
        db.close()


@app.get("/manual-reviews/{review_id}/video")
def get_review_video(
    review_id: int,
    current_user: User = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        review = db.query(ManualReview).get(review_id)
        if review is None or not review.clip_path or not os.path.isfile(review.clip_path):
            raise HTTPException(status_code=404, detail="Clip not found")
        return FileResponse(review.clip_path)
    finally:
        db.close()


@app.post("/manual-reviews/{review_id}/correct")
def correct_manual_review(
    review_id: int,
    correction: ManualCorrection,
    current_user: User = Depends(get_current_user),
):
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
        ticket.plate_code = correction.plate_code
        ticket.plate_city = correction.plate_city
        ticket.confidence = correction.confidence
        _retry_commit(ticket, db)

        review.review_status = "RESOLVED"
        review.plate_status = "READ"
        _retry_commit(review, db)

        try:
            from api_client import park_in_request

            park_token = None
            try:
                park_token = ticket.camera.pole.location.parkonic_api_token
            except Exception:
                park_token = None

            if ticket.image_base64:
                images_list = [ticket.image_base64]
            else:
                images_list = []
                folder = os.path.join(SNAPSHOTS_DIR, review.snapshot_folder)
                try:
                    for fname in os.listdir(folder):
                        if fname.startswith("annotated_") or fname.startswith("main_crop_"):
                            with open(os.path.join(folder, fname), "rb") as f:
                                images_list.append(base64.b64encode(f.read()).decode("utf-8"))
                except Exception:
                    logger.error("Failed loading snapshot images for API", exc_info=True)

                if not images_list:
                    with open(review.image_path, "rb") as f:
                        images_list = [base64.b64encode(f.read()).decode("utf-8")]

            pole_api_id = (
                db.query(Pole.api_pole_id)
                .join(Camera, Camera.pole_id == Pole.id)
                .filter(Camera.id == review.camera_id)
                .scalar()
            )
            if pole_api_id is None:
                pole_api_id = CFG_POLE_ID

            park_in_request(
                token=park_token or "",
                parkin_time=str(ticket.entry_time),
                plate_code=correction.plate_code,
                plate_number=correction.plate_number,
                emirates=correction.plate_city,
                conf=str(correction.confidence),
                spot_number=ticket.spot_number,
                pole_id=pole_api_id,
                images=images_list,
            )
        except Exception:
            logger.error("park_in_request failed", exc_info=True)

        return {"status": "updated"}
    finally:
        db.close()


@app.post("/manual-reviews/{review_id}/dismiss")
def dismiss_manual_review(
    review_id: int,
    current_user: User = Depends(get_current_user),
):
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
def list_review_snapshots(
    review_id: int,
    current_user: User = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        review = db.query(ManualReview).get(review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        folder = os.path.join(SNAPSHOTS_DIR, review.snapshot_folder)
        if not os.path.isdir(folder):
            raise HTTPException(status_code=404, detail="Snapshot folder not found")
        files = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
        files.sort(key=lambda x: os.path.getmtime(os.path.join(folder, x)), reverse=True)
        return {"files": files}
    finally:
        db.close()


@app.get("/manual-reviews/{review_id}/snapshots/{filename}")
def get_review_snapshot(
    review_id: int,
    filename: str,
    current_user: User = Depends(get_current_user),
):
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


@app.get("/location-stats")
def location_stats(current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        locations = db.query(Location).all()
        data: list[dict] = []
        for loc in locations:
            loc_info = {
                "id": loc.id,
                "name": loc.name,
                "code": loc.code,
                "zone_count": db.query(func.count(Zone.id)).filter(Zone.location_id == loc.id).scalar() or 0,
                "zones": [],
            }
            zones = db.query(Zone).filter(Zone.location_id == loc.id).all()
            for zone in zones:
                zone_info = {
                    "id": zone.id,
                    "code": zone.code,
                    "pole_count": db.query(func.count(Pole.id)).filter(Pole.zone_id == zone.id).scalar() or 0,
                    "poles": [],
                }
                poles = db.query(Pole).filter(Pole.zone_id == zone.id).all()
                for pole in poles:
                    camera_count = db.query(func.count(Camera.id)).filter(Camera.pole_id == pole.id).scalar() or 0
                    ticket_count = (
                        db.query(func.count(Ticket.id))
                        .join(Camera, Ticket.camera_id == Camera.id)
                        .filter(Camera.pole_id == pole.id)
                        .scalar()
                        or 0
                    )
                    review_count = (
                        db.query(func.count(ManualReview.id))
                        .join(Camera, ManualReview.camera_id == Camera.id)
                        .filter(Camera.pole_id == pole.id)
                        .scalar()
                        or 0
                    )
                    pole_info = {
                        "id": pole.id,
                        "code": pole.code,
                        "camera_count": camera_count,
                        "ticket_count": ticket_count,
                        "manual_review_count": review_count,
                    }
                    zone_info["poles"].append(pole_info)
                loc_info["zones"].append(zone_info)
            data.append(loc_info)
        return {"data": data}
    finally:
        db.close()
