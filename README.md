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

## License

This project is released under the terms of the MIT License. See [LICENSE](LICENSE) for the full text.
