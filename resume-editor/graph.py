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
from schema import MatchingResult, ProfileExtraction, ProfileFact, JobExtraction, JobRequirement
from langgraph.types import interrupt

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
    requirement_matches: list[dict]
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
    include_raw=True,
)

job_extractor = model.with_structured_output(
    JobExtraction,
    method="function_calling",
    include_raw=True,
)

matcher = model.with_structured_output(
    MatchingResult,
    method="function_calling",
    include_raw=True,
)


def invoke_structured(extractor, messages, operation):
    for attempt in range(2):
        result = extractor.invoke(messages)
        parsed = result.get("parsed") if isinstance(result, dict) else result
        if parsed is not None:
            return parsed

    raise RuntimeError(
        f"The model returned no structured output while {operation}. "
        "Try submitting again or use a model with function-calling support."
    )

def extract_profile_facts(state: ResumeState) -> dict:
    result = invoke_structured(profile_extractor, [
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
    ], "extracting profile facts")

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

    result = invoke_structured(job_extractor, [
        (
            "system",
            """Extract the requirements explicitly stated in the job description.
Do not add requirements based on assumptions.
Include an exact supporting quote for every requirement."""
        ),
        ("human", f"Job description:\n{job_description}"),
    ], "extracting job requirements")

    return {
        "job_requirements": [
            requirement.model_dump()
            for requirement in result.requirements
        ]
    }

def match_requirements(state: ResumeState) -> dict:
    facts = state.get("profile_facts", [])
    requirements = state.get("job_requirements", [])

    if not requirements:
        return {"requirement_matches": []}

    result = invoke_structured(matcher, [
        (
            "system",
            """Compare each job requirement with the profile facts.
Return one match per requirement, using its zero-based index.
Only use supporting fact indices that refer to the supplied profile facts.
Use 'missing' when the profile contains no supporting evidence."""
        ),
        (
            "human",
            f"""Profile facts:
{facts}

Job requirements:
{requirements}"""
        ),
    ], "matching requirements")

    matches = [match.model_dump() for match in result.matches]

    # Reject references to facts or requirements that don't exist.
    for match in matches:
        if not 0 <= match["requirement_index"] < len(requirements):
            raise ValueError("Model returned an invalid requirement index")

        for fact_index in match["supporting_fact_indices"]:
            if not 0 <= fact_index < len(facts):
                raise ValueError("Model returned an invalid profile fact index")

    return {"requirement_matches": matches}

def ask_for_clarification(state: ResumeState) -> dict:
    matches = state.get("requirement_matches", [])
    requirements = state.get("job_requirements", [])

    gap = next(
        (
            match for match in matches
            if match["status"] in {"missing", "partially_supported"}
        ),
        None,
    )

    if gap is None:
        return {}

    requirement = requirements[gap["requirement_index"]]["requirement"]

    question = (
        f"Your profile doesn't provide clear evidence for this job requirement: "
        f"{requirement}. Is this experience missing from your profile, or can you "
        "share relevant details? You can also reply 'not applicable'."
    )

    answer = interrupt({"question": question})

    return {
        "clarification_question": question,
        "clarification_answer": answer,
    }



conn= sqlite3.connect(database='resume_bot.db',check_same_thread=False)
checkpointer = SqliteSaver(conn=conn)
graph = StateGraph(ResumeState)
graph.add_node('validate_inputs', validate_inputs)
graph.add_node('extract_profile_facts', extract_profile_facts)
graph.add_node('extract_job_requirements', extract_job_requirements)
graph.add_node('match_requirements', match_requirements)
graph.add_node('ask_for_clarification', ask_for_clarification)

graph.add_edge(START, 'validate_inputs')
graph.add_edge('validate_inputs', 'extract_profile_facts')
graph.add_edge('extract_profile_facts', 'extract_job_requirements')
graph.add_edge('extract_job_requirements', 'match_requirements')
graph.add_edge('match_requirements', 'ask_for_clarification')
graph.add_edge('ask_for_clarification', END)




chatbot=graph.compile(checkpointer=checkpointer)

