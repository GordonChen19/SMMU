import json
import os
from dotenv import load_dotenv


from google import genai
from google.genai import types
from google.oauth2 import service_account
from extraction_chain.completion.utils import _VERTEX_SAFETY_SETTINGS, _model_to_dict

from openai import AzureOpenAI


load_dotenv()
CHAT_API_KEY = os.getenv("OPENAI_API_KEY")

openai_client = AzureOpenAI(api_key=CHAT_API_KEY, api_version="2024-02-01", azure_endpoint="https://yak-foundry-a2dcki.openai.azure.com/") if CHAT_API_KEY else None

def chatgpt_api_chat(prompt, response_format=None, model="gpt-4o", role="user"):
    client = openai_client
    messages = [{"role": role, "content": prompt}]

   
    response = client.chat.completions.parse(
        model=model,
        messages=messages,
        response_format=response_format,
    )
    try:
        response = _model_to_dict(response.choices[0].message.parsed)
        print(response)
        return response
    except Exception as e:
        print("Error parsing chat completion response:", str(e))
        return None


def gemini_api_chat(
    prompt,
    DataModel,
    client_file_path="extraction_chain/gemini_key.json",
    model="gemini-3.1-pro-preview"
):
    
    with open(client_file_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    project_id = payload.get("project_id")

    location = "global"

    credentials = service_account.Credentials.from_service_account_file(
        client_file_path
    )

    client = genai.Client(
        vertexai=True,
        project=project_id,
        location=location,
        credentials=credentials,
    )

    response = client.models.generate_content(
        model = model,
        contents= prompt,
        config=types.GenerateContentConfig(
                                           response_mime_type= "application/json",
                                           response_schema=DataModel,
                                           temperature=0.7,
                                           top_p=0.95,
                                           top_k=40,
                                           max_output_tokens=1024,
                                           safety_settings=_VERTEX_SAFETY_SETTINGS),
    )


    response = DataModel.model_validate_json(response.text)
    response = _model_to_dict(response)
    print(response)
    return response

