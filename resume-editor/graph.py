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
    clarification_requirement_index: int
    skipped_requirement_indexes: list[int]
    clarification_history: list[dict]

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
    for _ in range(2):
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

    expected = set(range(len(requirements)))
    validation_error = None

    for _ in range(2):
        retry_instruction = (
            f"Your previous result was invalid: {validation_error}. "
            "Please correct it and return exactly one match for every requirement."
            if validation_error
            else ""
        )

        result = invoke_structured(
            matcher,
            [
                (
                    "system",
                    """Compare each job requirement with the profile facts.
Return exactly one match for every requirement, using each zero-based
requirement index exactly once.
Only use supporting fact indices that refer to the supplied profile facts.
Use 'missing' when the profile contains no supporting evidence.""",
                ),
                (
                    "human",
                    f"""Profile facts:
{facts}

Job requirements:
{requirements}

{retry_instruction}""",
                ),
            ],
            "matching requirements",
        )

        matches = [match.model_dump() for match in result.matches]
        seen = set()
        validation_error = None

        for match in matches:
            index = match["requirement_index"]
            if index not in expected:
                validation_error = f"invalid requirement index {index}"
                break
            if index in seen:
                validation_error = f"duplicate match for requirement index {index}"
                break
            seen.add(index)

            for fact_index in match["supporting_fact_indices"]:
                if not 0 <= fact_index < len(facts):
                    validation_error = f"invalid profile fact index {fact_index}"
                    break
            if validation_error:
                break

        if validation_error is None:
            missing = expected - seen
            if missing:
                validation_error = (
                    f"missing matches for requirement indexes {sorted(missing)}"
                )

        if validation_error is None:
            return {"requirement_matches": matches}

    raise ValueError(
        f"Model returned invalid requirement matches after 2 attempts: "
        f"{validation_error}"
    )


def find_unresolved_match(state: ResumeState):
    skipped = set(state.get("skipped_requirement_indexes", []))

    return next(
        (
            match
            for match in state.get("requirement_matches", [])
            if match["status"] in {"missing", "partially_supported"}
            and match["requirement_index"] not in skipped
        ),
        None,
    )

def ask_for_clarification(state: ResumeState) -> dict:
    gap = find_unresolved_match(state)

    if gap is None:
        raise ValueError("No unresolved requirement to clarify.")

    requirement_index = gap["requirement_index"]
    requirement = state["job_requirements"][requirement_index]["requirement"]

    question = (
        f"Your profile doesn't clearly support this requirement: "
        f"{requirement}. Can you share relevant experience or details? "
        "You can also reply 'not applicable'."
    )

    answer = interrupt({
        "question": question,
        "requirement_index": requirement_index,
    })

    return {
        "clarification_requirement_index": requirement_index,
        "clarification_question": question,
        "clarification_answer": answer,
    }

def incorporate_clarification(state: ResumeState) -> dict:
    requirement_index = state["clarification_requirement_index"]
    question = state["clarification_question"]
    answer = state["clarification_answer"].strip()

    history = state.get("clarification_history", []) + [
        {
            "requirement_index": requirement_index,
            "question": question,
            "answer": answer,
        }
    ]

    # No LLM call is needed for an explicit skip.
    if answer.casefold().rstrip(".!?") == "not applicable":
        skipped = state.get("skipped_requirement_indexes", [])

        return {
            "skipped_requirement_indexes": list(
                dict.fromkeys(skipped + [requirement_index])
            ),
            "clarification_history": history,
        }

    requirement = state["job_requirements"][requirement_index]["requirement"]

    result = invoke_structured(
        profile_extractor,
        [
            (
                "system",
                """
                Extract profile facts explicitly stated in the user's answer.
                The question and job requirement provide context only.
                Do not treat their wording as evidence about the user.
                Do not invent or infer achievements, skills, or metrics.
                Every fact must have a supporting quote from the user's answer.
                """,
            ),
            (
                "human",
                f"Job requirement:\n{requirement}\n\n"
                f"Clarification question:\n{question}\n\n"
                f"User answer:\n{answer}",
            ),
        ],
        "extracting clarification facts",
    )

    new_facts = [fact.model_dump() for fact in result.facts]

    

    return {
        "profile_facts": state.get("profile_facts", []) + new_facts,
        "clarification_history": history,
    }


def route_after_matching(state: ResumeState) -> str:
    if find_unresolved_match(state) is not None:
        return "clarify"

    return "done"

conn= sqlite3.connect(database='resume_bot.db',check_same_thread=False)
checkpointer = SqliteSaver(conn=conn)
graph = StateGraph(ResumeState)
graph.add_node('validate_inputs', validate_inputs)
graph.add_node('extract_profile_facts', extract_profile_facts)
graph.add_node('extract_job_requirements', extract_job_requirements)
graph.add_node('match_requirements', match_requirements)
graph.add_node("ask_for_clarification", ask_for_clarification)
graph.add_node("incorporate_clarification", incorporate_clarification)

graph.add_edge(START, 'validate_inputs')
graph.add_edge('validate_inputs', 'extract_profile_facts')
graph.add_edge('extract_profile_facts', 'extract_job_requirements')
graph.add_edge('extract_job_requirements', 'match_requirements')
graph.add_edge('ask_for_clarification', "incorporate_clarification")
graph.add_edge("incorporate_clarification", 'match_requirements')
graph.add_conditional_edges(
    "match_requirements",
    route_after_matching,
    {
        "clarify": "ask_for_clarification",
        "done": END,
    },
)




chatbot=graph.compile(checkpointer=checkpointer)

