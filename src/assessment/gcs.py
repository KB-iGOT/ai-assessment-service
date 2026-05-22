import logging
from pathlib import Path
from google.cloud import storage
from google.oauth2 import service_account

from .config import GCS_CREDENTIALS, GCS_BUCKET_NAME

logger = logging.getLogger(__name__)

_client = None

def get_gcs_client() -> storage.Client:
    global _client
    if _client is None:
        if GCS_CREDENTIALS:
            creds = service_account.Credentials.from_service_account_file(GCS_CREDENTIALS)
            _client = storage.Client(credentials=creds)
        else:
            _client = storage.Client()
    return _client

def upload_to_gcs(local_path: Path, gcs_path: str) -> str:
    client = get_gcs_client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(str(local_path))
    logger.info(f"GCS upload: {local_path} → gs://{GCS_BUCKET_NAME}/{gcs_path}")
    return gcs_path

def download_from_gcs(gcs_path: str, local_path: Path):
    client = get_gcs_client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(gcs_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(str(local_path))
    logger.info(f"GCS download: gs://{GCS_BUCKET_NAME}/{gcs_path} → {local_path}")

def delete_from_gcs(gcs_path: str):
    try:
        client = get_gcs_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(gcs_path)
        blob.delete()
        logger.info(f"GCS delete: gs://{GCS_BUCKET_NAME}/{gcs_path}")
    except Exception as e:
        logger.warning(f"GCS delete failed for {gcs_path}: {e}")
