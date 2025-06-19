# config.py

# ─────────────────────────────────────────────────────────────────────────────
# Database connection string provided via the `DATABASE_URL` environment
# variable. No fallback credentials are stored in the repository.
# ─────────────────────────────────────────────────────────────────────────────
import os

# The application requires a database connection string in `DATABASE_URL`.
# No default credentials are provided.
DATABASE_URL = os.environ["DATABASE_URL"]

# ─────────────────────────────────────────────────────────────────────────────
# API Tokens
# ─────────────────────────────────────────────────────────────────────────────
# Token for the OCR service must be provided via the `OCR_TOKEN` environment
# variable.
OCR_TOKEN = os.environ["OCR_TOKEN"]
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
