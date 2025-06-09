# StreetServer2

StreetServer2 is a FastAPI application that receives parking reports from network cameras and logs them to a MySQL database. When a vehicle is detected occupying a spot, a snapshot is processed through a YOLO-based OCR pipeline to read the license plate. Tickets are then created in the database and optionally synchronized with the Parkonic API.

## Requirements

Python 3.10 or later is recommended. Install the required packages:

```bash
pip install fastapi uvicorn[standard] sqlalchemy pymysql mysql-connector-python \
    requests pillow numpy opencv-python ultralytics
```

## Configuration

Edit `config.py` and update the following values before running the server:

- `DB_USER`, `DB_PASS`, `DB_HOST`, `DB_NAME` – MySQL credentials used to build `DATABASE_URL`.
- `OCR_TOKEN` and `PARKONIC_API_TOKEN` – tokens for the OCR service and Parkonic API.
- `CAMERA_USER` and `CAMERA_PASS` – credentials used to fetch camera clips.
- `YOLO_MODEL_PATH` – path to the YOLO license plate model.

## Running the server

Make sure MySQL is running and the tables defined in `models.py` exist. Then start the service with:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

The API exposes a `/post` endpoint that accepts JSON payloads describing parking events.

### Listing tickets

Use the `/tickets` endpoint to retrieve issued tickets. It supports pagination
and basic searching by license plate number.

Query parameters:

- `page` – page number starting from 1 (default `1`)
- `page_size` – number of items per page (default `50`)
- `search` – partial plate number to filter by
- `sort_by` – field to sort on (`id`, `entry_time`, etc.)
- `sort_order` – `asc` or `desc` (default `desc`)

Example:

```bash
curl "http://localhost:8000/tickets?page=1&page_size=20&search=ABC&sort_by=entry_time"
```

### Listing manual reviews

Use the `/manual-reviews` endpoint to retrieve events that require human
verification. The endpoint supports simple pagination.

Query parameters:

- `status` – review status to filter by (`PENDING` or `RESOLVED`, default `PENDING`)
- `page` – page number starting from 1 (default `1`)
- `page_size` – number of items per page (default `50`)

Example:

```bash
curl "http://localhost:8000/manual-reviews?page=1&page_size=20"
```

## License

This project is released under the terms of the MIT License. See [LICENSE](LICENSE) for the full text.
