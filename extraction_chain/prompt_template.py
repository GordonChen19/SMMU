QA_GENERATION_PROMPT = '''
You are an expert at rewriting multiple-choice questions for clarity and evaluation quality.

You are given a Multiple Choice Question (MCQ) with answer choices. Your task is to rewrite the question and choices while preserving their original meaning.

Requirements:
1. Improve clarity, grammar, and fluency. Correct any typos or awkward phrasing.
2. Do NOT change the semantic meaning of the question or any answer choice.
3. Ensure the question is precise, unambiguous, and self-contained.
4. Keep all answer choices grammatically parallel and similar in length and style.
5. Avoid introducing new information or removing necessary context.

Output format:
- Rewritten Question:
- A.
- B.
- C.
- D.
'''

CANDIDATE_ANSWER_PROMPT = '''
You are tasked with analyzing a video. 

INPUTS YOU WILL RECEIVE:
1) A VIDEO for analysis
2) A QUESTION about the social dynamics in the video at that moment.

You are to answer the question to the best of your abilities. 

Question:
--------------
{question}
--------------

'''

GENERATE_PLAUSIBLE_ANSWERS_PROMPT = """
You are generating answer choices for a multiple-choice question.

You are given:
- A video (the question refers to the very end of the video)
- A question about the video
- Example answer choices

Your task:
Generate plausible answer options. In total, there should be 10 answer choices (including the example answer choices provided). 

If two example answer choices are given, you should generate the remaining 8 answer choices.

The generated answer choices should be different from the example answer choices, but still plausible given the video context and question.

Requirements:
- All answers must be plausible given the video context and question.
- Each answer must be meaningfully distinct (no paraphrases or minor wording changes).
- Avoid obviously incorrect, irrelevant, or contradictory answers.
- Keep answers concise (one short sentence or phrase).

Question:
--------------
{question}
--------------

Example Answer Choices:
--------------
{example_answers}
--------------
"""
################# The Prompts below have been deprecated (All the questions are now open-ended) ##################

OLD_QA_GEN_PROMPT = '''

You are an expert at generating social intelligence assessment questions from video annotations.

Given a set of human annotations describing social interactions in a video, generate question-answer pairs to evaluate a candidate's social intelligence.

Task Categories
Each question must belong to exactly one cognitive category and one social category:

Cognitive Categories:
- Comprehension**: Tests understanding of what is currently happening in the video.
- **Prediction**: Tests ability to anticipate what will happen next based on social cues.
- **Reasoning**: Tests ability to explain *why* something is happening or will happen, with evidence-based justifications. 

**Social Categories:** Emotion, Perspective, Intent, Persona, Relationship, Social Norms

---

Question Generation Rules

**Comprehension** questions ask about an observable social state at a given moment.
> *Example:* "How does Jack appear to be feeling emotionally at this moment?"

**Prediction** questions ask the candidate to forecast a character's next action or state.
> *Example:* "What do you think Jack will do next at the party?"

**Reasoning** questions ask the candidate to justify a social observation or prediction. They must be grounded in a prior question:
- If a **Comprehension** question exists → the Reasoning question should reference the candidate's answer to it.
  > *Example:* "Based on your observation that Jack seems sad, why do you think he feels that way? Provide timestamped evidence from the video to support your reasoning."
- If no Comprehension question exists → the Reasoning question should reference the candidate's answer to the Prediction question.
  > *Example:* "Based on your prediction that Jack will leave the party early, what do you think is motivating him? Provide timestamped evidence from the video to support your reasoning."

Your output must strictly follow the JSON schema provided below.

Annotations:
--------------
Timestamp: {timestamp},
Social Dimension: {social_dimension},
Comprehension Annotations: {comprehension_annotations},
Reasoning Annotations: {reasoning_annotations},
Prediction Annotations: {prediction_annotations},
--------------
'''


ANSWER_ELICITATION_PROMPT = '''
You are an evaluation judge. 
You are given the following inputs:

1. A question about a video at a particular timestamp.
2. The ground-truth answer to the question based on human annotations.
3. Previous conversation history between the candidate and the questioner, which may include the candidate’s answers to previous questions.

Your task is to evaluate whether the candidate's answer to the question is correct, incorrect, or unclear based on the conversation history and the ground-truth answer.
If the candidate's answer is correct so far, but the candidate has not yet provided enough information compared to the ground-truth answer, you may ask a follow-up question to indirectly probe for more details.
The follow-up question should be designed to elicit information that distinguishes the candidate's current answer from the hidden gold interpretation.

RULES:
- Accept paraphrases and synonymous labels if they imply the same core state/event.
- If the candidate's answer is less specific than the golden interpretation, than further probing may be necessary.
- Once there is sufficient information to indicate the candidate understands/misunderstands the scenario, there is no need for further probing and return 'correct' or 'incorrect'.
- If it is not yet clear whether the candidate's interpretation is correct, score as "unclear" and continue the conversation with another probe.
- If the candidate makes a claim about the video to prove a similar point to the golden interpretation, fact-check the claim against the video and use the result to inform your next question or final evaluation.

Your output must strictly follow the JSON schema provided below.

Question:
--------------
{question}
--------------

Ground Truth Answer:
--------------
{ground_truth}
--------------

Conversation History:
--------------
{conversation}
--------------

'''

EVIDENCE_CHECKER_PROMPT = '''
You are a FACTUALITY CHECKER for evidence grounding in videos.

INPUTS YOU WILL RECEIVE:
1) A VIDEO for analysis
2) A timestamp corresponding to the moment of interest in the video. 
3) An EVIDENCE CLAIM made by a candidate model (The claim is an observable cue at the timestamp specified). The evidence may be an action, object, dialogue, facial expression, gesture, posture, sound, or scene detail.

TASK:
Verify whether the claim is observable/audible in the provided clip at the particular timestamp. 

DEFINITIONS:
- CORRECT: clearly visible/audible in the clip.
- INCORRECT: not visible/audible in the clip.

RULES:
- Only verify what is directly observable/audible in the clip. No speculation about intent/emotion.
- If the claim contains evidence that is occluded, off-camera or audio that is not intelligible, mark it as INCORRECT.
- If a claim contains multiple atomic facts, evaluate each atom; the overall claim is CORRECT only if all atoms are CORRECT. As long as one atom is INCORRECT, the overall claim is INCORRECT.
- Do not “help” the claim by reinterpreting it into something weaker. Evaluate it as written.
- If the claim mentions an object/action that is not shown in the clip or misidentifies an object, mark INCORRECT.
- If the timestamp/window is too far from the claim, mark INCORRECT. Otherwise, if the timestamp is roughly aligned with the claim, mark CORRECT.

Your output must strictly follow the JSON schema provided below.


Claim:
--------------
{evidence}
--------------

Timestamp:
--------------
{timestamp}
--------------
'''

REASONING_CHECKER_PROMPT = '''
You are a REASONING CHECKER for evidence-based video question answering.

INPUTS YOU WILL RECEIVE:
1) A QUESTION about a video.
2) A GROUND TRUTH ANSWER to the question.
3) A set of EVIDENCE CLAIMS gathered from the video (each claim has already been verified as factually observable).
4) A CANDIDATE ANSWER produced by a model.

TASK:
Evaluate whether the provided evidence claims are sufficient to logically support the candidate answer to the question, and whether the candidate answer is consistent with the ground truth.

DEFINITIONS:
- SUPPORTED: The evidence claims collectively provide a clear and logical basis for arriving at the candidate answer, and the candidate answer is consistent with the ground truth.
- UNSUPPORTED: The evidence claims are insufficient, irrelevant, or do not logically connect to the candidate answer.
- CONTRADICTED: The candidate answer is directly contradicted by the ground truth, regardless of the evidence provided.

RULES:
- Treat all provided evidence claims as verified facts. Do not question their observability.
- Evaluate whether the reasoning chain from evidence → candidate answer is logically sound.
- The evidence does not need to be exhaustive — it only needs to be sufficient to reasonably support the candidate answer.
- If the evidence only partially supports the candidate answer, mark as UNSUPPORTED.
- Do not award credit for correct answers that are not grounded in the provided evidence (i.e., lucky guesses).
- Do not penalise the candidate answer for missing evidence that is irrelevant to the question.
- Evaluate the candidate answer against the ground truth semantically, not lexically.

Your output must strictly follow the JSON schema provided below.

Question:
--------------
{question}
--------------

Ground Truth Answer:
--------------
{ground_truth}
--------------

Evidence Claims:
--------------
{evidence_claims}
--------------

'''


