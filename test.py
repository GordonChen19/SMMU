
# Uncomment this block only when you need Whisper-based transcription utilities.
# from eval_pipeline import video_utils
# video_utils.mp4_to_mp3('extraction_chain/test_video.mov', 'extraction_chain/test_audio.wav')
# dialogue = video_utils.extract_dialogue('extraction_chain/test_audio.wav')
# print(dialogue)


# from completion import chat_completion, multimodal_completion, utils
# import data_models


# response = chat_completion.gemini_api_chat("Who was the 44th president of the US?",
#                                            data_models.dummy)

# response = chat_completion.chatgpt_api_chat("Who was the 44th president of the US?",
#                                            response_format=data_models.dummy)

# print(response)
# response = multimodal_completion.gemini_api_multimodal(
#     prompt = "What do you see in this video clip?",
#     video_file = "extraction_chain/test_video.mov",
#     DataModel = data_models.ResponseOpenEnded,
#     thinking_level="low"
# )


# from google.cloud import storage
# from google.oauth2 import service_account

# creds = service_account.Credentials.from_service_account_file(
#     "extraction_chain/gemini_key.json"
# )

# def upload_to_gcs(local_path, bucket_name, blob_name, credentials=None, project_id=None):
#     print(f"Using Project ID: {project_id}")
    
#     storage_client = storage.Client(project=project_id, credentials=credentials)
#     bucket = storage_client.bucket(bucket_name)
#     blob = bucket.blob(blob_name)
#     blob.upload_from_filename(local_path)
#     return f"gs://{bucket_name}/{blob_name}"


# upload_to_gcs(
#     local_path="extraction_chain/test_video.mov",
#     bucket_name="gordon-test-bucket",
#     blob_name="test_video.mov",
#     credentials=creds,
#     project_id="my-project-id"
# )

# from eval_pipeline.qa_gen import generateQA

# response = generateQA("shared_library/annotations.export.json",
#                       "shared_library/qa_output.export.json")

from eval_pipeline.eval import evaluate_models

evaluate_models(
        "shared_library/qa_output.export.json",
        output_json_file="shared_library/evaluation_results_2.export.json"
    )