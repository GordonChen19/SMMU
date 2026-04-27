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
You are tasked with analyzing a video and answering a multiple-choice question about the end of a video clip. 

INPUTS YOU WILL RECEIVE:
1) A VIDEO for analysis
2) A QUESTION about about the very end of the video.

You are to answer the question to the best of your abilities. 

Question:
--------------
{question}
--------------

Choices
--------------
{choices}
--------------

Choose the single best answer and return only the selected letter in the schema.
"""

'''

GENERATE_PLAUSIBLE_ANSWERS_PROMPT = """
You are generating answer choices for a multiple-choice question.

You are given:
- A video (the question refers to the very end of the video)
- A question about the video
- Example answer choices (optional)

Your task:
Generate plausible answer options. In total, there should be 8 answer choices (including the example answer choices provided). 

If two example answer choices are given, you should generate the remaining 6 answer choices.

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

QUESTION_MAPPINGS = {
    "Emotion": {
        "Reasoning": "Which of the following best describes what causes {character} to  the following emotion: {truth}?",
        "Comprehension": "Which of the following correctly describes what {character} is feeling at this point in the video?",
        "Prediction": "Given how {character} is feeling at this point in the video, which of the following actions is {character} most likely to take next?"
    },
    "Persona": {
        "Reasoning": "Which of the following best explains why {character}'s persona in this scene is {truth}?",
        "Comprehension": "Which of the following correctly describes {character}'s persona at this point in the video?",
        "Prediction": "Given {character}'s persona in this scene, which of the following actions is {character} most likely to take next?"
    },
    "Intent": {
        "Reasoning": "Which of the following best explains why {character}'s intent in this scene is the following: {truth}?",
        "Comprehension": "Which of the following correctly describes what {character}'s intent is at this point in the video?",
        "Prediction": "Given {character}'s intent, which of the following outcomes is {character} most likely to pursue next?"
    }, 
    "Perspective/Belief":{
        "Reasoning": "Which of the following best describes why {character} has the following perspective/belief: {truth}?",
        "Comprehension": "Which of the following correctly describes {character}'s perspective/belief at this point in the video?",
        "Prediction": "Given {character}'s perspective/belief, which of the following beliefs or actions is {character} most likely to have or take next?"
    },
    "Knowledge_State":{
        "Reasoning": "Which of the following best explains why {character} has the following knowledge state: {truth}?",
        "Comprehension": "Which of the following correctly describes what {character} knows or does not know at this point in the video?",
        "Prediction": "Given what {character} knows or does not know, which of the following beliefs or actions is {character} most likely to have or take next?"
    },
    "Communicative_Intent": {
        "Reasoning": "Which of the following correctly describes why {character} he communicates in the following way: {truth}?",
        "Comprehension": "Which of the following best explains what {character} is trying to communicate in this scene?",
        "Prediction": "Given {character}'s communicative intent, what type of reaction is {character} most likely to elicit from others?"
    },
    "Relationship":{
        "Reasoning": "Which of the following best explains why {character1} has the following relationship with {character2}: {truth}?",
        "Comprehension": "Which of the following correctly describes the relationship between {character1} and {character2} at this point in the video?",
        "Prediction": "Given the relationship between {character1} and {character2}, which of the following interactions is most likely to occur next between them?"
    },
    "Social_Norms":{
        "Reasoning": "Explain how {character} is adhering to or violating an unspoken rule in this scene.",
        "Comprehension": "Is {character} adhering or violating an unspoken rule?",
        "Prediction": "Given the norm violation or conformity observed from {character}, which of the following consequences is most likely to follow?"
    },
    "Cultural_Conventions":{
        "Reasoning": "Explain how {character} is adhering to or violating an unspoken rule in this scene.",
        "Comprehension": "Is {character} adhering or violating an unspoken rule?",
        "Prediction": "Given the norm violation or conformity observed from {character}, which of the following consequences is most likely to follow?"
    },
    "Moral_Judgement":{
        "Reasoning": "Explain why {character}'s action is morally good or bad in this scene.",
        "Comprehension": "Is {character}'s action morally good, bad, or neutral?",
        "Prediction": "Given the moral judgement that {character} makes, which of the following consequences is most likely to follow?"
    },
    "Role/Institutional_Knowledge":{
        "Reasoning": "Explain why {character} is adhering to or violating an unspoken rule in this scene.",
        "Comprehension": "Is {character} adhering or violating an unspoken rule?",
        "Prediction": "Given the norm violation or conformity observed from {character}, which of the following consequences is most likely to follow?"
    },

}



# World Knowledge
#              - Social Norms
#              - Cultural Conventions
#              - Role Expectations
#              - Moral Judgement
#              - Institutional Knowledge

# Mental States
#              - Emotions
#              - Intent
#              - Perspective
#              - Knowledge State


# Others States
#              - Relationships
#              - Communicative Intent
