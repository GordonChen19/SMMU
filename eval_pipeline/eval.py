from __future__ import annotations

import random
import tempfile
from pathlib import Path


from eval_pipeline import video_utils
from eval_pipeline.utils import _read_json_file, _write_json_file
from extraction_chain import data_models, prompt_template
from extraction_chain.completion import multimodal_completion


TASK_ORDER = ("reasoning", "comprehension", "prediction")
CHOICE_LETTERS = [choice.value for choice in data_models.AnswerChoice]


def _score(correct, total):
    return correct / total if total else 0.0

def _build_labeled_choices(task):

    options = [[distractor,0] for distractor in task.get("distractors")] + [[task.get("correctAnswer"),1]]
    random.shuffle(options)

    labeled_choices = []
    for i, option in enumerate(options):
        labeled_choices.append({
            "letter": CHOICE_LETTERS[i],
            "text": option[0],
        })
        if option[1] == 1:
            correct_letter = CHOICE_LETTERS[i]
        
    return labeled_choices, correct_letter

def _evaluate_task(
    clipped_video_path,
    timestamp,
    task,
    candidate_model,
    client,
    cache
):
    labeled_choices, correct_letter = _build_labeled_choices(task)
    
    response = multimodal_completion.gemini_api_multimodal(
        prompt=prompt_template.CANDIDATE_ANSWER_PROMPT.format(
            timestamp=timestamp,
            question=str(task.get("question") or "").strip(),
            choices="\n".join("{}.".format(choice["letter"]) + " " + choice["text"] for choice in labeled_choices),
        ),
        video_file=str(clipped_video_path),
        DataModel=data_models.ResponseMCQ,
        thinking_level="low",
        model=candidate_model,
        client=client,
        cache=cache
    )

    predicted_letter = response["choice"] 
    is_correct = predicted_letter == correct_letter
    return {
        "question": task["question"],
        "choices": labeled_choices,
        "correctAnswer": task["correctAnswer"],
        "correctChoice": correct_letter,
        "predictedChoices": predicted_letter,
        "isCorrect": is_correct,
        "modelResponse": response,
    }


def evaluate_models(
    qa_json_file,
    output_json_file,
    candidate_model="gemini-3.1-pro-preview"
):
    
    qa_path = Path(qa_json_file).expanduser()
    if not qa_path.is_absolute():
        qa_path = Path.cwd() / qa_path

    output_path = Path(output_json_file).expanduser() 
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path

    qa_payload = _read_json_file(qa_path)
    videos = qa_payload.get("videos", [])

    total_questions = 0
    total_correct = 0
    cognitive_progression_correct = 0
    temporal_cognitive_progression_correct = 0
    task_totals = {task_name: 0 for task_name in TASK_ORDER}
    task_correct = {task_name: 0 for task_name in TASK_ORDER}
    evaluated_videos = []

    for video in videos:
        video_path = video.get("videoPath")
        
        temporal_chain_alive = True
        evaluated_notes = []
        
        for note in video.get("notes", []):
            timestamp = note.get("timestamp")
            note_tasks = note.get("tasks", {})
            cognitive_chain_alive = True
            evaluated_tasks = {}



            #Save clipped video as clip path
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
                clipped_video_path = Path(handle.name)

            clipped_video_path.unlink(missing_ok=True)
            video_utils.trim_video(str(video_path), str(clipped_video_path), end_time=float(timestamp))
                
            
            client, cache = multimodal_completion._upload_and_cache(
                video_file = clipped_video_path,
                model= candidate_model,
                video_display_name = "input_video"
            )
            

            for task_name in TASK_ORDER:
                task = note_tasks.get(task_name)

                #Create a cache for the video

                evaluated_task = _evaluate_task(
                    clipped_video_path=clipped_video_path,
                    timestamp=timestamp,
                    task=task,
                    candidate_model=candidate_model,
                    client=client,
                    cache=cache
                )

                raw_correct = bool(evaluated_task["isCorrect"])
                cognitive_credit = int(cognitive_chain_alive and raw_correct)
                temporal_credit = int(temporal_chain_alive and raw_correct)

                total_questions += 1
                total_correct += int(raw_correct)
                cognitive_progression_correct += cognitive_credit
                temporal_cognitive_progression_correct += temporal_credit
                task_totals[task_name] += 1
                task_correct[task_name] += int(raw_correct)

                evaluated_task["cognitiveProgressionCorrect"] = cognitive_credit
                evaluated_task["temporalCognitiveProgressionCorrect"] = temporal_credit
                evaluated_tasks[task_name] = evaluated_task

                if not raw_correct:
                    cognitive_chain_alive = False
                    temporal_chain_alive = False

            if evaluated_tasks:
                evaluated_notes.append(
                    {
                        "timestamp": timestamp,
                        "tasks": evaluated_tasks,
                    }
                )

        if evaluated_notes:
            video_question_total = sum(len(note["tasks"]) for note in evaluated_notes)
            video_correct_total = sum(
                int(task["isCorrect"])
                for note in evaluated_notes
                for task in note["tasks"].values()
            )
            video_cognitive_total = sum(
                int(task["cognitiveProgressionCorrect"])
                for note in evaluated_notes
                for task in note["tasks"].values()
            )
            video_temporal_total = sum(
                int(task["temporalCognitiveProgressionCorrect"])
                for note in evaluated_notes
                for task in note["tasks"].values()
            )
            evaluated_videos.append(
                {
                    "videoPath": video_path,
                    "notes": evaluated_notes,
                    "average_score": _score(video_correct_total, video_question_total),
                    "cognitive_progression_score": _score(video_cognitive_total, video_question_total),
                    "temporal_cognitive_progression_score": _score(video_temporal_total, video_question_total),
                }
            )

    export_payload = {
        "sourceQaPath": str(qa_path),
        "candidateModel": candidate_model,
        "temporal_cognitive_progression_score": _score(
            temporal_cognitive_progression_correct,
            total_questions,
        ),
        "cognitive_progression_score": _score(
            cognitive_progression_correct,
            total_questions,
        ),
        "average_comprehension_score": _score(
            task_correct["comprehension"],
            task_totals["comprehension"],
        ),
        "average_reasoning_score": _score(
            task_correct["reasoning"],
            task_totals["reasoning"],
        ),
        "average_prediction_score": _score(
            task_correct["prediction"],
            task_totals["prediction"],
        ),
        "average_score": _score(total_correct, total_questions),
        "videos": evaluated_videos,
    }

    _write_json_file(output_path, export_payload)
    return export_payload
