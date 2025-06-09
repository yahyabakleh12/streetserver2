# StreetServer2

StreetServer2 is a FastAPI application that receives parking reports from network cameras and logs them to a MySQL database. When a vehicle is detected occupying a spot, a snapshot is processed through a YOLO-based OCR pipeline to read the license plate. Tickets are then created in the database and optionally synchronized with the Parkonic API.

## Requirements

Python 3.10 or later is recommended. Install the required packages using `requirements.txt`:

```bash
pip install -r requirements.txt
```

## Configuration

The application reads configuration from environment variables. Values defined
in `config.py` act as defaults if the variables are not provided.

Set the following variables as needed before running the server:

- `DATABASE_URL` – MySQL connection string. If unset it is built from `DB_USER`,
  `DB_PASS`, `DB_HOST`, and `DB_NAME` in `config.py`.
- `OCR_TOKEN` and `PARKONIC_API_TOKEN` – tokens for the OCR service and Parkonic API.
- `CAMERA_USER` and `CAMERA_PASS` – credentials used to fetch camera clips.
- `YOLO_MODEL_PATH` – path to the YOLO license plate model (can also be changed in
  `config.py`).

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

### Retrieving a manual review

Fetch details for a specific review by ID using `/manual-reviews/{id}`.

```bash
curl "http://localhost:8000/manual-reviews/123"
```

### Correcting a manual review

Submit updated plate information when a review has been manually verified using
`/manual-reviews/{review_id}/correct`.

Required JSON fields:

- `plate_number` – license plate number
- `plate_code` – plate code
- `plate_city` – issuing city
- `confidence` – confidence value as an integer

Example:

```bash
curl -X POST http://localhost:8000/manual-reviews/123/correct \
  -H "Content-Type: application/json" \
  -d '{"plate_number": "ABC123", "plate_code": "12", "plate_city": "DXB", "confidence": 95}'
```

### Dismissing a manual review

To dismiss a review without changing the ticket, use
`/manual-reviews/{review_id}/dismiss`.

```bash
curl -X POST http://localhost:8000/manual-reviews/123/dismiss
```

## License

This project is released under the terms of the MIT License. See [LICENSE](LICENSE) for the full text.
