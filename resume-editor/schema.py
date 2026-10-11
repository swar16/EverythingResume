from pydantic import BaseModel, Field
from typing import Literal

class ProfileFact(BaseModel):
    category: str = Field(description="For example: project, skill, education, or experience")
    fact: str = Field(description="One fact stated in the user's profile")
    evidence_quote: str = Field(description="Exact text from the profile that supports this fact")


class ProfileExtraction(BaseModel):
    facts: list[ProfileFact]

class JobRequirement(BaseModel):
    requirement: str = Field(description="One requirement stated in the job description")
    category: str = Field(description="For example: skill, experience, responsibility, or qualification")
    evidence_quote: str = Field(description="Exact text from the job description supporting this requirement")


class JobExtraction(BaseModel):
    requirements: list[JobRequirement]

class RequirementMatch(BaseModel):
    requirement_index: int
    status: Literal["supported", "partially_supported", "missing"]
    supporting_fact_indices: list[int]
    explanation: str


class MatchingResult(BaseModel):
    matches: list[RequirementMatch]