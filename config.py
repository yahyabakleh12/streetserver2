# config.py

# ─────────────────────────────────────────────────────────────────────────────
# Database URL (replace placeholders with real credentials/host)
# Environment variable `DATABASE_URL` overrides these defaults.
# ─────────────────────────────────────────────────────────────────────────────
import os

DB_USER     = "street"
DB_PASS     = "!#Street"
DB_HOST     = "127.0.0.1"
DB_NAME     = "parking_management"

DEFAULT_DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:3306/{DB_NAME}"

DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

# ─────────────────────────────────────────────────────────────────────────────
# API Tokens
# ─────────────────────────────────────────────────────────────────────────────
OCR_TOKEN = os.environ.get(
    "OCR_TOKEN",
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJTTlAiLCJpYXQiOjE3MDM0MTUxOTcsImV4cCI6MTczNDk1MTE5NywiY2xpIjoiQVBJIiwid2lkIjpudWxsfQ.EIup6X0h65BjBEUMmE3BHxolQjH18lrMaCxvfoJ0_Nw",
)
# Location specific configuration now stores the Parkonic API token and camera
# credentials.  The global constants previously defined here have been
# deprecated.

# ─────────────────────────────────────────────────────────────────────────────
# YOLO model path (on CPU)
# ─────────────────────────────────────────────────────────────────────────────
YOLO_MODEL_PATH = "models/car.pt"

# RealESRGAN model weights path
REAL_ESRGAN_MODEL_PATH = os.environ.get(
    "REAL_ESRGAN_MODEL_PATH",
    "weights/RealESRGAN_x4plus.pth",
)

API_POLE_ID = 586


API_LOCATION_ID = 213
