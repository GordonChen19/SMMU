from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any



from eval_pipeline import video_utils
from eval_pipeline.utils import _read_json_file, _write_json_file
from extraction_chain import data_models, prompt_template
from extraction_chain.completion import chat_completion, multimodal_completion


def _default_output_path(qa_path: Path) -> Path:
    return qa_path.with_suffix("").with_name(qa_path.stem + ".eval.json")


def _resolve_input_path(raw_path: Any, qa_path: Path) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise ValueError("Missing video_path in QA pair.")

    candidate = Path(text).expanduser()
    candidates = [candidate] if candidate.is_absolute() else [
        Path.cwd() / candidate,
        qa_path.parent / candidate,
    ]

    for path in candidates:
        if path.is_file():
            return path.resolve()

    raise FileNotFoundError("Video file not found for QA evaluation: {}".format(text))


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def model_completion(prompt, model, video_file):

    if "gemini" in model.lower():
        return multimodal_completion.gemini_api_multimodal(
            prompt=prompt,
            video_file=video_file,
            DataModel=data_models.ResponseOpenEnded,
            thinking_level="low",
            model=model,
        )
    
def evaluate_models(
    qa_json_file: str | Path,
    output_json_file: str | Path | None = None,
    candidate_model: str = "gemini-3.1-pro-preview",
) -> dict[str, Any]:
    qa_path = Path(qa_json_file).expanduser()
    if not qa_path.is_absolute():
        qa_path = Path.cwd() / qa_path
    if not qa_path.is_file():
        raise FileNotFoundError("QA pairs file not found: {}".format(qa_path))

    output_path = Path(output_json_file).expanduser() if output_json_file else _default_output_path(qa_path)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path

    try:
        qa_payload = _read_json_file(qa_path)
    except json.JSONDecodeError as exc:
        raise ValueError("QA pairs file is not valid JSON: {}".format(exc)) from exc

    qa_pairs = qa_payload.get("QA_pairs")
    if not isinstance(qa_pairs, list):
        raise ValueError("Expected QA_pairs to be a list in {}.".format(qa_path))

    export_payload: dict[str, Any] = {
        "sourceQaPath": str(qa_path),
        "candidateModel": candidate_model,
        "QA_pairs": [],
    }

    for qa_pair in qa_pairs:
        evaluated_pair = dict(qa_pair)
        if qa_pair.get("prediction_q") is not None:
            evaluated_pair["prediction_evaluation"] = {"status": "SKIPPED"}

        try:
            timestamp = qa_pair.get("timestamp")
            video_path = _resolve_input_path(qa_pair.get("video_path"), qa_path)
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
                clip_path = Path(handle.name)
            try:
                video_utils.trim_video(str(video_path), str(clip_path), end_time=timestamp)

                
                try:
                    candidate_payload = model_completion(
                        prompt = data_models.CANDIDATE_ANSWER_PROMPT.format(
                            timestamp=timestamp,
                            question=qa_pair.get("question"),
                        ),
                        model = candidate_model,
                        video_file = str(clip_path),
                    )
                    evaluated_pair["answer"] = candidate_payload
                    
                except Exception as exc:  # pragma: no cover
                    evaluated_pair["answer"] = {
                        "status": "FAILED",
                        "error": str(exc),
                    }

               
            finally:
                clip_path.unlink(missing_ok=True)
        except Exception as exc:  # pragma: no cover
            evaluated_pair["evaluation_error"] = str(exc)

        export_payload["QA_pairs"].append(evaluated_pair)
        
        _write_json_file(output_path, export_payload)

    return export_payload


############ The Following functions are deprecated in favour of MCQ format ############


# def _reasoning_claims_to_text(evidence_claims: list[dict[str, str]]) -> str:
#     if not evidence_claims:
#         return "(no evidence claims provided)"
#     return "\n".join(
#         "- [{}] {}".format(
#             str(item.get("timestamp") or "").strip() or "?",
#             str(item.get("claim") or "").strip(),
#         )
#         for item in evidence_claims
#     )


# def fact_checker(video_file: str | Path, claim: str, timestamp: Any, model: str) -> str:
#     response = multimodal_completion.gemini_api_multimodal(
#         prompt=prompt_template.EVIDENCE_CHECKER_PROMPT.format(
#             evidence=claim,
#             timestamp=timestamp,
#         ),
#         video_file=str(video_file),
#         DataModel=data_models.EvidenceCheck,
#         thinking_level="low",
#         model=model,
#     )
#     return _enum_value(response.fact_check)

# def _evaluate_comprehension(
#     *,
#     clip_path: Path,
#     timestamp: Any,
#     question_text: str,
#     ground_truth: str,
#     candidate_model: str,
#     judge_model: str,
#     max_follow_ups: int = 3,
# ) -> tuple[dict[str, Any], dict[str, Any]]:
#     initial_response = multimodal_completion.gemini_api_multimodal(
#         prompt=prompt_template.CANDIDATE_ANSWER_PROMPT.format(
#             timestamp=timestamp,
#             question=question_text,
#         ),
#         video_file=str(clip_path),
#         DataModel=data_models.ResponseOpenEnded,
#         thinking_level="low",
#         model=candidate_model,
#     )

#     conversation_history = "Model Response: {}\n".format(initial_response.answer)
#     follow_ups: list[dict[str, str]] = []
#     judge_response = multimodal_completion.gemini_api_multimodal(
#         prompt=prompt_template.ANSWER_ELICITATION_PROMPT.format(
#             question=question_text,
#             ground_truth=ground_truth,
#             conversation=conversation_history,
#         ),
#         video_file=str(clip_path),
#         DataModel=data_models.FolowUpQuestion,
#         thinking_level="low",
#         model=judge_model,
#     )

#     follow_up_count = 0
#     while (
#         _enum_value(judge_response.verdict) == "UNCLEAR"
#         and judge_response.question
#         and follow_up_count < max_follow_ups
#     ):
#         follow_up_question = str(judge_response.question)
#         follow_up_response = multimodal_completion.gemini_api_multimodal(
#             prompt=prompt_template.CANDIDATE_ANSWER_PROMPT.format(
#                 timestamp=timestamp,
#                 question=follow_up_question,
#             ),
#             video_file=str(clip_path),
#             DataModel=data_models.ResponseOpenEnded,
#             thinking_level="low",
#             model=candidate_model,
#         )
#         follow_ups.append(
#             {
#                 "question": follow_up_question,
#                 "answer": follow_up_response.answer,
#             }
#         )
#         conversation_history += "Follow-up Question: {}\n".format(follow_up_question)
#         conversation_history += "Model Response: {}\n".format(follow_up_response.answer)
#         judge_response = multimodal_completion.gemini_api_multimodal(
#             prompt=prompt_template.ANSWER_ELICITATION_PROMPT.format(
#                 question=question_text,
#                 ground_truth=ground_truth,
#                 conversation=conversation_history,
#             ),
#             video_file=str(clip_path),
#             DataModel=data_models.FolowUpQuestion,
#             thinking_level="low",
#             model=judge_model,
#         )
#         follow_up_count += 1

#     candidate_payload = {
#         "question": question_text,
#         "ground_truth": ground_truth,
#         "initial_answer": initial_response.answer,
#         "follow_ups": follow_ups,
#     }
#     evaluation_payload = {
#         "status": "EVALUATED",
#         "judge_model": judge_model,
#         "verdict": _enum_value(judge_response.verdict),
#         "follow_up_count": len(follow_ups),
#         "conversation_history": conversation_history,
#         "judge_response": _model_to_dict(judge_response),
#     }
#     return candidate_payload, evaluation_payload

# def _evaluate_reasoning(
#     *,
#     clip_path: Path,
#     source_video_path: Path,
#     timestamp: Any,
#     question_text: str,
#     ground_truth: str,
#     candidate_model: str,
#     llm_judge: str,
#     mllm_judge: str,
# ) -> tuple[dict[str, Any], dict[str, Any]]:
#     answer_response = multimodal_completion.gemini_api_multimodal(
#         prompt=prompt_template.CANDIDATE_ANSWER_PROMPT.format(
#             timestamp=timestamp,
#             question=question_text,
#         ),
#         video_file=str(clip_path),
#         DataModel=data_models.ResponseOpenEnded,
#         thinking_level="low",
#         model=candidate_model,
#     )

#     evidence_response = multimodal_completion.gemini_api_multimodal(
#         prompt=(
#             prompt_template.CANDIDATE_ANSWER_PROMPT.format(
#                 timestamp=timestamp,
#                 question=question_text,
#             )
#             + "\nList the timestamped evidence claims that support your answer."
#         ),
#         video_file=str(clip_path),
#         DataModel=data_models.ResponseStructured,
#         thinking_level="low",
#         model=candidate_model,
#     )

#     evidence_claims = [
#         {
#             "claim": claim.claim,
#             "timestamp": claim.timestamp,
#         }
#         for claim in evidence_response.evidence_claims
#     ]

#     fact_checks: list[dict[str, Any]] = []
#     all_evidence_correct = True
#     for claim in evidence_claims:
#         verdict = fact_checker(
#             video_file=source_video_path,
#             claim=claim["claim"],
#             timestamp=claim["timestamp"],
#             model=mllm_judge,
#         )
#         fact_checks.append(
#             {
#                 **claim,
#                 "verdict": verdict,
#             }
#         )
#         all_evidence_correct = all_evidence_correct and verdict == "CORRECT"

#     judge_response = None
#     judge_verdict = None
#     evidence_claims_text = _reasoning_claims_to_text(evidence_claims)
#     if all_evidence_correct:
#         judge_response = chat_completion.chatgpt_api_chat(
#             prompt="",
#             response_format=data_models.ReasoningCheck,
#             model=llm_judge,
#         )
#         judge_verdict = _enum_value(judge_response.verdict) if judge_response else None

#     candidate_payload = {
#         "question": question_text,
#         "ground_truth": ground_truth,
#         "answer": answer_response.answer,
#         "evidence_claims": evidence_claims,
#     }
#     evaluation_payload = {
#         "status": "EVALUATED",
#         "judge_model": llm_judge,
#         "fact_checker_model": mllm_judge,
#         "fact_checks": fact_checks,
#         "all_evidence_correct": all_evidence_correct,
#         "judge_verdict": judge_verdict,
#         "evidence_claims_text": evidence_claims_text,
#     }
#     return candidate_payload, evaluation_payload

