from typing import List, Optional
from enum import Enum
from pydantic import BaseModel, Field

############# QA Generation #############

class QAPair(BaseModel):
    question: str = Field(..., description="The question to be answered.")
    answer: str = Field(..., description="The answer to the question.")


class AnswerChoice(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    G = "G"
    H = "H"

class ResponseMCQ(BaseModel):
    choice: List[AnswerChoice] = Field(..., description="The correct answer choice(s) to the question.")


class PlausibleAnswer(BaseModel):
    answer: List[str] = Field(..., description="A plausible answer choice to the question.")

################ The following data models are deprecated in favour of MCQ format ##############

class SocialCategory(str, Enum):
    emotion = "emotion" 
    relationship = "relationship"
    intent = "intent"
    perspective = "perspective"
    persona = "persona"
    social_norm = "social_norm"


class CognitiveTask(str, Enum):
    comprehension = "comprehension"
    prediction = "prediction"
    reasoning = "reasoning"

class QAPairs(BaseModel):
    qa_pairs: List[QAPair] = Field(..., description="A list of question-answer pairs generated from the video annotations.")
    category: SocialCategory = Field(..., description="The social category of the question.")

class VerdictEnum(str, Enum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    UNLCEAR = "UNCLEAR"

class FactCheckEnum(str, Enum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"

class ResponseOpenEnded(BaseModel):
    answer: str = Field(..., description="The answer to the question.")

class EvidenceClaim(BaseModel):
    claim: str = Field(..., description="A claim about what is seen/heard in the clip that supports the verdict.")
    timestamp: str = Field(..., description="The timestamp of the claim in the video.")

class ResponseStructured(BaseModel):
    evidence_claims: List[EvidenceClaim] = Field(..., description="A list of evidence claims that support the candidate's answer.")

class EvidenceCheck(BaseModel):
    fact_check: FactCheckEnum = Field(..., description="The factuality of the candidate's evidence claim.")

class ReasoningCheck(BaseModel):
    verdict: VerdictEnum = Field(..., description="The verdict of the evaluation.")
    explanation: Optional[str] = Field(..., description="An explanation justifying the verdict.")

class FolowUpQuestion(BaseModel):
    verdict: VerdictEnum = Field(..., description="The verdict of the evaluation.")
    question: Optional[str] = Field(..., description="A follow-up question to further probe the candidate's understanding.")
    fact_check: Optional[FactCheckEnum] = Field(..., description="Check the factuality of the candidate's evidence claim.")

