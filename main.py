

from eval_pipeline.qa_gen import generateQA
from eval_pipeline.eval import evaluate_models
from argparse import ArgumentParser

if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--annotations_path", help="Path to the annotations file")
    parser.add_argument("--qa_path", help="Path to the generated QA pairs file")
    parser.add_argument("--qa_gen_model", default="gpt-4o", help="Name of the model to use for QA generation")
    parser.add_argument("--candidate_model", help="Name of the candidate model to evaluate")
    parser.add_argument("--llm_judge", default="gpt-4o", help="Name of the LLM judge to use")
    parser.add_argument("--mllm_judge", default="gemini-3.1-pro-preview", help="Name of the MLLM judge to use")
    args = parser.parse_args()
    if not args.annotations_path or not args.qa_path:
        parser.error("Missing required arguments: --annotations_path and --qa_path are required.")

    response = generateQA(args.annotations_path, args.qa_path, model=args.qa_gen_model)

    if args.candidate_model:
        evaluate_models(
            args.qa_path,
            candidate_model=args.candidate_model,
            llm_judge=args.llm_judge,
            mllm_judge=args.mllm_judge,
        )
    else:
        print("QA generation completed. Candidate model evaluation skipped since --candidate_model was not provided.")
        


# For running QA generation
# python3 main.py --annotations_path shared_library/annotations.export.json --qa_path shared_library/qa_output.export.json --qa_gen_model gpt-4o
