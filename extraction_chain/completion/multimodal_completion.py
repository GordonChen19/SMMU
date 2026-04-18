import json
import time
from google import genai
from google.genai import types
from google.oauth2 import service_account
from pydantic import ValidationError

from extraction_chain.completion.utils import _VERTEX_SAFETY_SETTINGS, _model_to_dict, upload_to_gcs, delete_from_gcs, create_bucket

def delete_cached_content(client, cache):
    client.caches.delete(cache.name)
    print(f'Cache {cache.name} deleted.')


def _thinking_config_from_level(thinking_level):
    if isinstance(thinking_level, int):
        return types.ThinkingConfig(thinking_budget=thinking_level)

    budget_by_level = {
        "low": 0,
        "medium": 256,
        "high": -1,
    }
    normalized = str(thinking_level or "").strip().lower()
    return types.ThinkingConfig(
        thinking_budget=budget_by_level.get(normalized, -1)
    )


def _parse_structured_response(response, DataModel):
    parsed_response = response.parsed
    if isinstance(parsed_response, DataModel):
        return parsed_response
    if parsed_response is not None:
        return DataModel.model_validate(parsed_response)
    return DataModel.model_validate_json(response.text)


def gemini_api_multimodal(
    prompt,
    video_file,
    DataModel,
    model="gemini-3.1-pro-preview",
    thinking_level="high",
    client_file_path = "extraction_chain/gemini_key.json",
    video_display_name = "input_video",
    ttl="300s"
):
    
    #Setting up the client
    with open(client_file_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    project_id = payload.get("project_id")

    location = "global"

    credentials = service_account.Credentials.from_service_account_file(
        client_file_path,
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
        
    )
    client = genai.Client(
        vertexai=True,
        project=project_id,
        location=location,
        credentials=credentials,
    )

    #Create a bucket if it doesn't exist already

    bucket_name = "gordon-benchmark-bucket"
    create_bucket(
            bucket_name=bucket_name,
            project_id="a-data-processing-chengordon",
            location="US", 
            credentials=credentials
        )

    # Reuse the same service-account credentials for Cloud Storage uploads.
    gcs_uri = upload_to_gcs(
        video_file,
        bucket_name,
        "test.mp4",
        credentials=credentials,
        project_id=project_id,
    )

    video_part = types.Part.from_uri(file_uri=gcs_uri, mime_type="video/mp4")

    # Create a cache with a 5 minute TTL (300 seconds)
    cache = client.caches.create(
        model=model,
        config=types.CreateCachedContentConfig(
            display_name=video_display_name, # used to identify the cache
            system_instruction=(
                'You are an expert video analyzer, and your job is to answer '
                'the user\'s query based on the video file you have access to.'
            ),
            contents=[video_part],
            ttl=ttl,
        )
    )

    retry_suffix = (
        "\nReturn only a complete JSON object matching the response schema. "
        "Do not include markdown, prose, or partial output."
    )
    prompts = [prompt, "{}{}".format(prompt, retry_suffix)]
    last_error = None
    result = None

    for attempt_index, current_prompt in enumerate(prompts):
        response = client.models.generate_content(
            model = model,
            contents= current_prompt,
            config=types.GenerateContentConfig(cached_content=cache.name, 
                                               response_mime_type= "application/json",
                                               response_schema=DataModel,
                                               thinking_config=_thinking_config_from_level(thinking_level),
                                               temperature=0.0 if attempt_index > 0 else 0.7,
                                               top_p=0.95,
                                               top_k=40,
                                               max_output_tokens=2048 if attempt_index > 0 else 1024,
                                               safety_settings=_VERTEX_SAFETY_SETTINGS),
        )
        try:
            response = _parse_structured_response(response, DataModel)
            break
        except ValidationError as exc:
            last_error = exc

    if response is None:
        raise last_error

    response = _model_to_dict(response)
    print(response)
    return response
