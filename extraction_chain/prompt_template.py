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
...
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
        "Reasoning": "Which of the following best describes what causes {character} to feel how they feel in this scene?",
        "Comprehension": "Which of the following correctly describes what {character} is feeling at this point in the video?",
        "Prediction": "Given how {character} is feeling at this point in the video, which of the following actions is {character} most likely to take next?"
    },
    "Intent": {
        "Reasoning": "Which of the following best explains why {character}'s intent in this scene is what it is?",
        "Comprehension": "Which of the following correctly describes what {character}'s intent is at this point in the video?",
        "Prediction": "Given {character}'s intent, which of the following outcomes is {character} most likely to pursue next?"
    }, 
    "Perspective/Belief":{
        "Reasoning": "Which of the following best describes why {character} has the perspective/belief that he does?",
        "Comprehension": "Which of the following correctly describes {character}'s perspective/belief at this point in the video?",
        "Prediction": "Given {character}'s perspective/belief, which of the following beliefs or actions is {character} most likely to have or take next?"
    },
    "Knowledge_State":{
        "Reasoning": "Which of the following best explains why {character} knows or does not know what they do in this scene?",
        "Comprehension": "Which of the following correctly describes what {character} knows or does not know at this point in the video?",
        "Prediction": "Given what {character} knows or does not know, which of the following beliefs or actions is {character} most likely to have or take next?"
    },
    "Communicative_Intent": {
        "Reasoning": "Which of the following best explains why {character} is communicating what they are in this scene?",
        "Comprehension": "Which of the following correctly describes what {character}'s communicative intent is at this point in the video?",
        "Prediction": "Given {character}'s communicative intent, what type of reaction is {character} most likely to elicit from others?"
    },
    "Relationship":{
        "Reasoning": "Which of the following best explains why {character1} has the relationship with {character2} that they do in this scene?",
        "Comprehension": "Which of the following correctly describes the relationship between {character1} and {character2} at this point in the video?",
        "Prediction": "Given the relationship between {character1} and {character2}, which of the following interactions is most likely to occur next between them?"
    },
    "Social_Norms":{
        "Reasoning": "Explain how {character} is adhering to or violating social norms in this scene.",
        "Comprehension": "Is {character} adhering or violating a social norm? If so, which social norm?",
        "Prediction": "Given the norm violation or conformity observed from {character}, which of the following social consequences is most likely to follow?"
    },
    "Cultural_Conventions":{
        "Reasoning": "Explain how {character} is adhering to or violating cultural conventions in this scene.",
        "Comprehension": "Is {character} adhering or violating a cultural convention? If so, which cultural convention?",
        "Prediction": "Given the convention violation or conformity observed from {character}, which of the following cultural consequences is most likely to follow?"
    },
    "Moral_Judgement":{
        "Reasoning": "Explain how {character}'s action is morally good or bad in this scene.",
        "Comprehension": "Is {character}'s action morally good, bad, or neutral? Explain what type of moral judgement it represents.",
        "Prediction": "Given the moral judgement that {character} makes, which of the following consequences is most likely to follow?"
    },
    "Role/Institutional_Knowledge":{
        "Reasoning": "Explain how {character} is adhering to or violating their role and institutional knowledge in this scene.",
        "Comprehension": "Is {character} adhering or violating their role and institutional knowledge? If so, which aspects?",
        "Prediction": "Given {character}'s role and institutional knowledge, which of the following consequences is most likely to follow?"
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

