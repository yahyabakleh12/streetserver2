# camera_clip.py

import time
import uuid
import requests
from datetime import datetime
from logger import logger
import os


def is_valid_mp4(path: str) -> bool:
    """Return True if the file at ``path`` looks like a valid MP4."""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as f:
            header = f.read(8)
        return len(header) >= 8 and header[4:8] == b"ftyp"
    except Exception:
        logger.error("Failed validating MP4 %s", path, exc_info=True)
        return False

VIDEO_CLIPS_DIR = "video_clips"
os.makedirs(VIDEO_CLIPS_DIR, exist_ok=True)


def request_camera_clip(
    camera_ip: str,
    username: str,
    password: str,
    start_dt: datetime,
    end_dt: datetime,
    segment_name: str,
    unique_tag: str | None = None,
) -> str:
    """
    Attempt up to 3 times (0, +5s, +5s) to fetch a 20 s MP4 from the camera.
    Uses a 30 second read timeout each attempt.
    Returns the saved filepath on success, or None on permanent failure.
    """
    params = {
        "dw":        "sd",
        "filename":  segment_name,
        "starttime": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "endtime":   end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "index":     0,
        "sid":       0,
        "uuid":      str(uuid.uuid4()),
    }
    out_name = (
        f"{VIDEO_CLIPS_DIR}/clip_"
        f"{start_dt.strftime('%Y%m%d_%H%M%S')}_{end_dt.strftime('%H%M%S')}"
    )
    if unique_tag:
        out_name += f"_{unique_tag}"
    out_name += ".mp4"
    url = f"http://{camera_ip}/dataloader.cgi"

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            logger.debug(f"Attempt {attempt+1}/{max_retries+1}: requesting clip {out_name} from {camera_ip}")
            with requests.get(
                url,
                params=params,
                auth=(username, password),
                stream=True,
                timeout=30,
            ) as r:
                r.raise_for_status()
                with open(out_name, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            if not is_valid_mp4(out_name):
                os.remove(out_name)
                raise ValueError("Invalid clip downloaded")

            logger.debug(f"Successfully saved clip to {out_name}")
            return out_name

        except Exception as e:
            logger.error(f"Clip fetch attempt {attempt+1} failed: {e}", exc_info=True)
            if attempt < max_retries:
                time.sleep(5)
            else:
                logger.error(f"All {max_retries+1} attempts to fetch clip failed.")
                return None
