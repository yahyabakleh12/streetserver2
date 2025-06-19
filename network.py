# network.py

import time
import requests
from logger import logger

def send_request_with_retry(url: str, payload: dict, max_retries: int = 2, backoff: float = 1.0) -> str:
    """
    POST ``payload`` to ``url`` with optional retries and exponential backoff.

    The function simply returns ``requests.Response.text``.  Callers are
    responsible for parsing the returned value (some endpoints return a JSON
    string while others may double encode the JSON).

    Raises any ``requests`` exception after the final retry.
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
