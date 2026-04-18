from google.genai import types
from google.cloud import storage
from google.api_core.exceptions import Conflict

_VERTEX_SAFETY_SETTINGS = [
    types.SafetySetting(
        category="HARM_CATEGORY_HATE_SPEECH",
        threshold="BLOCK_NONE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_HARASSMENT",
        threshold="BLOCK_NONE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
        threshold="BLOCK_NONE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_DANGEROUS_CONTENT",
        threshold="BLOCK_NONE",
    ),
]

def _model_to_dict(model):
    if model is None:
        return None
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model

############ Buckets and GCS utilities ############
def upload_to_gcs(local_path, bucket_name, blob_name, credentials=None, project_id=None):

    storage_client = storage.Client(project=project_id, credentials=credentials)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path)
    return f"gs://{bucket_name}/{blob_name}"

def delete_from_gcs(bucket_name, blob_name, credentials=None, project_id=None):

    storage_client = storage.Client(project=project_id, credentials=credentials)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.delete()
    print(f"Deleted gs://{bucket_name}/{blob_name}")

def create_bucket(bucket_name, project_id, location="global", credentials=None):
    storage_client = storage.Client(project=project_id, credentials=credentials)
    bucket = storage_client.bucket(bucket_name)
    
    try:
        bucket = storage_client.create_bucket(bucket, location=location)
        print(f"Bucket gs://{bucket.name} created in {location}")
    except Conflict:
        print(f"Bucket gs://{bucket_name} already exists, skipping creation")
    
    return storage_client.bucket(bucket_name)