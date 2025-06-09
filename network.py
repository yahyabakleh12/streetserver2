# network.py

import time
import requests
from logger import logger

def send_request_with_retry(url: str, payload: dict, max_retries: int = 2, backoff: float = 1.0) -> dict:
    """
    POST `payload` to `url` with up to max_retries (exponential backoff).
    Returns the JSON response (or raises after final failure).
    """
    for attempt in range(max_retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=10)
            r.raise_for_status()
            return r.text  # some endpoints return a quoted JSON-string
        except Exception as e:
            logger.error("send_request_with_retry attempt %d failed: %s", attempt + 1, e, exc_info=True)
            if attempt < max_retries:
                time.sleep(backoff * (2 ** attempt))
            else:
                raise
