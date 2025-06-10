# config.py

# ─────────────────────────────────────────────────────────────────────────────
# Database URL (replace placeholders with real credentials/host)
# Environment variable `DATABASE_URL` overrides these defaults.
# ─────────────────────────────────────────────────────────────────────────────
import os

DB_USER     = "street"
DB_PASS     = "Devstreet"
DB_HOST     = "127.0.0.1"
DB_NAME     = "parking_management"

DEFAULT_DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:3306/{DB_NAME}"

DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

# ─────────────────────────────────────────────────────────────────────────────
# API Tokens
# ─────────────────────────────────────────────────────────────────────────────
OCR_TOKEN         = os.environ.get(
    "OCR_TOKEN",
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJTTlAiLCJpYXQiOjE3MDM0MTUxOTcsImV4cCI6MTczNDk1MTE5NywiY2xpIjoiQVBJIiwid2lkIjpudWxsfQ.EIup6X0h65BjBEUMmE3BHxolQjH18lrMaCxvfoJ0_Nw",
)
PARKONIC_API_TOKEN = os.environ.get(
    "PARKONIC_API_TOKEN",
    "dBOs11IDXwseQCb3bLvHxNv0Gx4HLC21UQ",
)

# ─────────────────────────────────────────────────────────────────────────────
# Camera Credentials (if they’re the same for all cameras)
# ─────────────────────────────────────────────────────────────────────────────
CAMERA_USER = os.environ.get("CAMERA_USER", "admin")
CAMERA_PASS = os.environ.get("CAMERA_PASS", "72756642@NAHSP196")

# ─────────────────────────────────────────────────────────────────────────────
# YOLO model path (on CPU)
# ─────────────────────────────────────────────────────────────────────────────
YOLO_MODEL_PATH = "models/car.pt"


API_POLE_ID = 586

API_LOCATION_ID = 213