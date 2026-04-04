from pathlib import Path
import json

from eval_pipeline.utils import _read_json_file, _write_json_file
from extraction_chain import data_models, prompt_template, extraction_chain


def _build_qa_id(video_ref, entry_id, note_id, fallback_index: int) -> str:
    if isinstance(entry_id, int) and isinstance(note_id, int):
        return "{}:{}".format(entry_id, note_id)
    if isinstance(video_ref, int) and isinstance(entry_id, int):
        return "{}:{}:{}".format(video_ref, entry_id, fallback_index)
    return "note-{}".format(fallback_index)


def _iter_note_records(annotations: dict):
    if isinstance(annotations.get("qaSourceNotes"), list):
        for index, item in enumerate(annotations["qaSourceNotes"], start=1):
            if not isinstance(item, dict):
                continue
            note = item.get("note")
            if not isinstance(note, dict):
                note = {}
            yield {
                "qaId": _build_qa_id(item.get("videoRef"), item.get("entryId"), item.get("noteId"), index),
                "videoRef": item.get("videoRef"),
                "entryId": item.get("entryId"),
                "noteId": item.get("noteId"),
                "videoTitle": item.get("videoTitle"),
                "annotator": item.get("annotator"),
                "timestamp": item.get("timeSec"),
                "timecode": item.get("timecode"),
                "category": item.get("category") or note.get("category"),
                "comprehension": item.get("comprehension") or note.get("comprehension") or "",
                "reasoning": item.get("reasoning") or note.get("reasoning") or "",
                "prediction": item.get("prediction") or note.get("prediction") or "",
            }
        return

    videos = annotations.get("videos")
    if not isinstance(videos, list):
        raise ValueError("Annotations JSON must contain either 'qaSourceNotes' or 'videos'.")

    fallback_index = 0
    for video in videos:
        if not isinstance(video, dict):
            continue
        for entry in video.get("annotations", []):
            if not isinstance(entry, dict):
                continue
            annotation = entry.get("annotation")
            if not isinstance(annotation, dict):
                continue
            notes = annotation.get("notes")
            if not isinstance(notes, list):
                continue
            for note in notes:
                if not isinstance(note, dict):
                    continue
                fallback_index += 1
                yield {
                    "qaId": _build_qa_id(video.get("videoRef"), entry.get("id"), note.get("id"), fallback_index),
                    "videoRef": video.get("videoRef"),
                    "entryId": entry.get("id"),
                    "noteId": note.get("id"),
                    "videoTitle": video.get("videoTitle") or entry.get("videoTitle"),
                    "annotator": entry.get("annotator"),
                    "timestamp": note.get("timeSec"),
                    "timecode": note.get("timecode"),
                    "category": note.get("category"),
                    "comprehension": note.get("comprehension") or "",
                    "reasoning": note.get("reasoning") or "",
                    "prediction": note.get("prediction") or "",
                }


def generateQA(input_json_file, output_json_file, model="gpt-4o"):
    input_path = Path(input_json_file).expanduser()
    output_path = Path(output_json_file).expanduser()
    error_path = output_path.with_suffix(output_path.suffix + ".errors.json")

    if not input_path.is_file():
        raise FileNotFoundError("Annotations file not found: {}".format(input_path))

    try:
        annotations = _read_json_file(input_path)
    except json.JSONDecodeError as exc:
        raise ValueError("Annotations file is not valid JSON: {}".format(exc)) from exc

    qa_pairs = {}
    if output_path.exists():
        try:
            qa_pairs = _read_json_file(output_path)
        except json.JSONDecodeError as exc:
            raise ValueError("Existing QA output is not valid JSON: {}".format(exc)) from exc

    generated_count = 0
    skipped_count = 0
    failed_items = {}

    for record in _iter_note_records(annotations):
        qa_id = str(record["qaId"])
        label = "{} [{}]".format(record.get("videoTitle") or "Untitled video", qa_id)
        if qa_id in qa_pairs:
            print("QA pair for {} already exists. Skipping generation.".format(label))
            skipped_count += 1
            continue

        category = str(record.get("category") or "").strip()
        comprehension = str(record.get("comprehension") or "").strip()
        reasoning = str(record.get("reasoning") or "").strip()
        prediction = str(record.get("prediction") or "").strip()

        if not any([comprehension, reasoning, prediction]):
            failed_items[qa_id] = {
                "videoTitle": record.get("videoTitle"),
                "entryId": record.get("entryId"),
                "noteId": record.get("noteId"),
                "error": "All annotation text fields are empty.",
            }
            print("Skipping {} because comprehension/reasoning/prediction are empty.".format(label))
            continue

        print("Generating QA pair for {}...".format(label))
        try:
            qa_result = extraction_chain.extraction_chain(
                input={
                    "timestamp": record.get("timestamp"),
                    "social_dimension": category,
                    "comprehension_annotations": comprehension,
                    "reasoning_annotations": reasoning,
                    "prediction_annotations": prediction,
                },
                data_model=data_models.QAPairs,
                prompt_template=prompt_template.QA_GENERATION_PROMPT,
                reasoning_model=model,
            )
        except Exception as exc:
            failed_items[qa_id] = {
                "videoTitle": record.get("videoTitle"),
                "entryId": record.get("entryId"),
                "noteId": record.get("noteId"),
                "error": str(exc),
            }
            print("Failed to generate QA pair for {}: {}".format(label, exc))
            continue

        qa_pairs[qa_id] = {
            "qaId": qa_id,
            "videoRef": record.get("videoRef"),
            "entryId": record.get("entryId"),
            "noteId": record.get("noteId"),
            "videoTitle": record.get("videoTitle"),
            "annotator": record.get("annotator"),
            "timestamp": record.get("timestamp"),
            "timecode": record.get("timecode"),
            "sourceCategory": category,
            "questionCategory": qa_result.get("category"),
            "cognitiveTask": qa_result.get("cognitive_task"),
            "question": qa_result.get("question"),
            "answer": qa_result.get("answer"),
        }
        generated_count += 1
        _write_json_file(output_path, qa_pairs)

    if failed_items:
        _write_json_file(error_path, failed_items)

    print("All done. Saved {} QA pairs to {}.".format(len(qa_pairs), output_path))
    if failed_items:
        print("Encountered {} failed items. Details saved to {}.".format(len(failed_items), error_path))

    return {
        "generated": generated_count,
        "skipped": skipped_count,
        "failed": len(failed_items),
        "outputPath": str(output_path),
        "errorPath": str(error_path) if failed_items else None,
    }

