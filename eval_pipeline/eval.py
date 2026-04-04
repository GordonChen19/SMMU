from pathlib import Path
import json

from eval_pipeline.utils import _read_json_file, _write_json_file
from extraction_chain import data_models, prompt_template, extraction_chain



def fack_checker(video_link, claim):
    return extraction_chain.extraction_chain(
        input=prompt_template.VERIFICATION_CHECKER_PROMPT + f"Video Link: {video_link}\nClaim: {claim}",
        data_model=data_models.VerificationCheck,
        prompt_template=prompt_template.VERIFICATION_CHECKER_PROMPT,
        reasoning_model="gemini-3.1-pro-preview"
    )['fact_check']


def evaluate_models(qa_json_file, candidate_model, llm_judge="gpt-4o", mllm_judge="gemini-3.1-pro-preview"):
    qa_path = Path(qa_json_file).expanduser()
    if not qa_path.is_file():
        raise FileNotFoundError("QA pairs file not found: {}".format(qa_path))

    try:
        qa_pairs = _read_json_file(qa_path)
    except json.JSONDecodeError as exc:
        raise ValueError("QA pairs file is not valid JSON: {}".format(exc)) from exc

    evaluation_results = {}
    for qa_id, qa_data in qa_pairs.items():
        question = qa_data.get("question")
        ground_truth = qa_data.get("answer")
        video_ref = qa_data.get("videoRef")
        timestamp = qa_data.get("timestamp")
        

        candidate_answer = extraction_chain.extraction_chain(
            input = f"At timestamp {timestamp}, {question}",
            data_model = data_models.

        )

        # Evaluate the candidate's answer using the LLM judge
        evaluation_result = extraction_chain.extraction_chain(
            input={
                "question": question,
                "ground_truth": ground_truth,
                "conversation": "",  # In a real implementation, this would include the conversation history
            },
            data_model=data_models.FolowUpQuestion,
            prompt_template=prompt_template.ANSWER_ELICITATION_PROMPT,
            reasoning_model=llm_judge,
        )

        # If the verdict is unclear, we may want to ask follow-up questions or perform fact-checking
        if evaluation_result.get("verdict") == data_models.VerdictEnum.UNCLEAR:
            follow_up_question = evaluation_result.get("question")
            print(f"Follow-up question for {qa_id}: {follow_up_question}")
            # Here you would typically get the candidate's response to the follow-up question and re-evaluate

        # Perform fact-checking on any evidence provided by the candidate
        if evaluation_result.get("fact_check") == data_models.FactCheckEnum.INCORRECT:
            print(f"Candidate's evidence for {qa_id} is factually incorrect based on video content.")

        evaluation_results[qa_id] = {
            "question": question,
            "ground_truth": ground_truth,
            "candidate_answer": candidate_answer,
            "evaluation_verdict": evaluation_result.get("verdict"),
            "follow_up_question": evaluation_result.get("question")
        }

    return evaluation_results

