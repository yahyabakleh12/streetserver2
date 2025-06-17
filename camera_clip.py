# camera_clip.py

import time
import uuid
import requests
import cv2
from imutils.video import VideoStream
from datetime import datetime, timedelta
from typing import Optional
from logger import logger
import os


def is_valid_mp4(path: str) -> bool:
    """Return True if the file at ``path`` can be opened and read as MP4."""
    if not os.path.isfile(path):
        return False
    try:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            cap.release()
            return False
        ret, _ = cap.read()
        cap.release()
        if not ret:
            os.remove(path)
            return False
        return True
    except Exception:
        logger.error("Failed validating MP4 %s", path, exc_info=True)
        try:
            os.remove(path)
        except Exception:
            pass
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
) -> Optional[str]:
    """
    Attempt up to 3 times (0, +5s, +5s) to fetch a 20 s MP4 from the camera.
    Uses a 30 second read timeout each attempt.
    Returns the saved filepath on success, or None on permanent failure.
    """
    base_params = {
        "dw":        "sd",
        "filename":  segment_name,
        "starttime": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "endtime":   end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "index":     0,
        "sid":       0,
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
        params = base_params | {"uuid": str(uuid.uuid4())}
        try:
            logger.debug(
                f"Attempt {attempt+1}/{max_retries+1}: requesting clip {out_name} from {camera_ip}"
            )
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


def fetch_camera_frame(camera_ip: str, username: str, password: str) -> bytes:
    """Return a JPEG snapshot from the camera using RTSP."""

    rtsp_url = f"rtsp://{username}:{password}@{camera_ip}:554/"
    stream = VideoStream(rtsp_url).start()
    try:
        frame = stream.read()
        if frame is None:
            raise RuntimeError("Failed to read frame from RTSP stream")
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("Failed to encode frame as JPEG")
        return buf.tobytes()
    finally:
        stream.stop()


def frame_from_video(path: str) -> bytes:
    """Return the first frame of a video file encoded as JPEG bytes."""

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        cap.release()
        raise RuntimeError(f"Failed to open video {path}")
    try:
        ret, frame = cap.read()
        if not ret:
            raise RuntimeError("Failed to read frame from video")
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("Failed to encode frame as JPEG")
        return buf.tobytes()
    finally:
        cap.release()


def fetch_exit_frame(
    camera_ip: str,
    username: str,
    password: str,
    event_time: datetime,
) -> bytes:
    """Return a JPEG frame from 1 second before ``event_time`` via clip download."""

    start_dt = event_time - timedelta(seconds=0)
    end_dt = start_dt + timedelta(seconds=5)

    clip_path = request_camera_clip(
        camera_ip=camera_ip,
        username=username,
        password=password,
        start_dt=start_dt,
        end_dt=end_dt,
        segment_name=start_dt.strftime("%Y%m%d%H%M%S"),
    )
    if not clip_path:
        raise RuntimeError("Failed to fetch exit clip")

    try:
        frame_bytes = frame_from_video(clip_path)
        return frame_bytes
    finally:
        try:
            os.remove(clip_path)
        except Exception:
            pass
