from pathlib import Path
import json

from eval_pipeline.utils import _read_json_file, _write_json_file
from extraction_chain import data_models, prompt_template
from extraction_chain.completion import chat_completion


def _task_payload_to_text(task: object) -> str:
    if not isinstance(task, dict):
        return ""
    return str(task.get("text") or "").strip()


def iterate_annotations(annotations: dict):
    for video in annotations.get("videos", []):
        if not isinstance(video, dict):
            continue

        video_path = video.get("videoPath") or video.get("videoLink")
        for note in video.get("annotations", []):
            if not isinstance(note, dict):
                continue
            cognitive_tasks = note.get("cognitiveTasks")
            if not isinstance(cognitive_tasks, dict):
                cognitive_tasks = {}

            yield {
                "video_path": video_path,
                "timestamp": note.get("timestampSec"),
                "characters": note.get("characters", []),
                "social_dimension": note.get("socialDimension"),
                "comprehension": cognitive_tasks.get("comprehension"),
                "reasoning": cognitive_tasks.get("reasoning"),
                "prediction": cognitive_tasks.get("prediction"),
            }


def generateQA(input_json_file, output_json_file, model="gpt-4o"):
    input_path = Path(input_json_file).expanduser()
    output_path = Path(output_json_file).expanduser()

    if not input_path.is_absolute():
        input_path = Path.cwd() / input_path
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path

    if not input_path.is_file():
        raise FileNotFoundError("Annotations file not found: {}".format(input_path))
    try:
        annotations = _read_json_file(input_path)
    except json.JSONDecodeError as exc:
        raise ValueError("Annotations file is not valid JSON: {}".format(exc)) from exc

    qa_pairs = {"QA_pairs": []}

    for note in iterate_annotations(annotations):
        comprehension = _task_payload_to_text(note.get("comprehension"))
        reasoning = _task_payload_to_text(note.get("reasoning"))
        prediction = _task_payload_to_text(note.get("prediction"))

        if not any([comprehension, reasoning, prediction]):
            continue

        try:
            response = chat_completion.chatgpt_api_chat(
                prompt=prompt_template.QA_GENERATION_PROMPT.format(
                    timestamp=note.get("timestamp"),
                    social_dimension=note.get("social_dimension"),
                    comprehension_annotations=comprehension,
                    reasoning_annotations=reasoning,
                    prediction_annotations=prediction,
                ),
                response_format=data_models.QAPairs,
                model=model,
            )
            if response is None:
                continue

            note_result = dict(note)
            for qa_pair in response.qa_pairs:
                task_name = qa_pair.cognitive_task.value
                note_result["{}_q".format(task_name)] = qa_pair.question
                note_result["{}_a".format(task_name)] = qa_pair.answer
            note_result["question_category"] = response.category.value
            qa_pairs["QA_pairs"].append(note_result)
        except Exception as exc:
            print("Failed to generate QA pair: {}".format(exc))

    _write_json_file(output_path, qa_pairs)

    print("All done. Saved {} QA pairs to {}.".format(len(qa_pairs["QA_pairs"]), output_path))
    return qa_pairs
