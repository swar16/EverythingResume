from langgraph.graph import StateGraph, START, END
from typing import TypedDict,Annotated
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.message import add_messages
import os
import sqlite3
load_dotenv()  
from schema import ProfileExtraction, ProfileFact, JobExtraction, JobRequirement

model = ChatOpenAI(
    model="cohere/north-mini-code:free",
    api_key=os.getenv("OPENROUTER_KEY"),
    base_url="https://openrouter.ai/api/v1",
)

class ResumeState(TypedDict, total=False):
    profile_context: str
    job_description: str

    profile_facts: list[dict]
    job_requirements: list[dict]
    proposed_bullets: list[dict]
    verification_issues: list[str]

    clarification_question: str
    clarification_answer: str

    review_feedback: str
    approved: bool

    revised_resume_latex: str


def validate_inputs(state: ResumeState) -> dict:
    profile_context = state.get("profile_context", "").strip()
    if not profile_context:
        raise ValueError("Profile context is required")

    return {
        "profile_context": profile_context,
        "job_description": state.get("job_description", "").strip(),
    }

profile_extractor = model.with_structured_output(
    ProfileExtraction,
    method="function_calling",
)

job_extractor = model.with_structured_output(
    JobExtraction,
    method="function_calling",
)

def extract_profile_facts(state: ResumeState) -> dict:
    result = profile_extractor.invoke([
        (
            "system",
            """Extract facts explicitly stated in the user's profile.
            Do not infer or invent information. Include a supporting quote
            from the profile for every fact."""
        ),
        (
            "human",
            f"Profile:\n{state['profile_context']}",
        ),
    ])

    return {
        "profile_facts": [
            fact.model_dump()
            for fact in result.facts
        ]
    }

def extract_job_requirements(state: ResumeState) -> dict:
    job_description = state.get("job_description", "").strip()

    if not job_description:
        return {"job_requirements": []}

    result = job_extractor.invoke([
        (
            "system",
            """Extract the requirements explicitly stated in the job description.
Do not add requirements based on assumptions.
Include an exact supporting quote for every requirement."""
        ),
        ("human", f"Job description:\n{job_description}"),
    ])

    return {
        "job_requirements": [
            requirement.model_dump()
            for requirement in result.requirements
        ]
    }
conn= sqlite3.connect(database='resume_bot.db',check_same_thread=False)
checkpointer = SqliteSaver(conn=conn)
graph = StateGraph(ResumeState)
graph.add_node('validate_inputs', validate_inputs)
graph.add_node('extract_profile_facts', extract_profile_facts)
graph.add_node('extract_job_requirements', extract_job_requirements)
graph.add_edge(START, 'validate_inputs')
graph.add_edge('validate_inputs', 'extract_profile_facts')
graph.add_edge('extract_profile_facts', 'extract_job_requirements')
graph.add_edge('extract_job_requirements', END)




chatbot=graph.compile(checkpointer=checkpointer)

