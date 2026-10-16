import streamlit as st
from uuid import uuid4
from graph import chatbot
from langgraph.types import Command

st.title("Resume Tailor")

if "thread_id" not in st.session_state:
    st.session_state["thread_id"] = str(uuid4())

def graph_config():
    return {"configurable": {"thread_id": st.session_state["thread_id"]}}


def store_graph_result(result):
    interrupts = result.get("__interrupt__", ())
    if interrupts:
        st.session_state["clarification_question"] = interrupts[0].value["question"]
        st.session_state["awaiting_clarification"] = True
    else:
        st.session_state["result"] = result
        st.session_state["awaiting_clarification"] = False


if not st.session_state.get("awaiting_clarification", False):
    with st.form("resume_input"):
        profile_context = st.text_area(
            "Paste your resume or profile details",
            height=250,
            placeholder="Paste your resume, achievements, projects, skills, and metrics here...",
        )

        job_description = st.text_area(
            "Paste the job description (optional)",
            height=200,
            placeholder="Paste the job description here...",
        )

        submitted = st.form_submit_button("Continue")

    if submitted:
        if not profile_context.strip():
            st.error("Please paste your resume or profile details.")
        else:
            # Start a fresh graph thread for each new resume/job submission.
            st.session_state["thread_id"] = str(uuid4())
            st.session_state.pop("result", None)
            st.session_state["profile_context"] = profile_context.strip()
            st.session_state["job_description"] = job_description.strip()
            try:
                result = chatbot.invoke(
                    {
                        "profile_context": st.session_state["profile_context"],
                        "job_description": st.session_state["job_description"],
                    },
                    config=graph_config(),
                )
                store_graph_result(result)
            except (RuntimeError, ValueError) as error:
                st.error(str(error))


if st.session_state.get("awaiting_clarification", False):
    st.info(st.session_state["clarification_question"])
    with st.form("clarification_form"):
        clarification_answer = st.text_area("Your answer", height=150)
        answer_submitted = st.form_submit_button("Submit answer")

    if answer_submitted:
        if not clarification_answer.strip():
            st.error("Please provide an answer or type 'not applicable'.")
        else:
            try:
                result = chatbot.invoke(
                    Command(resume=clarification_answer.strip()),
                    config=graph_config(),
                )
                store_graph_result(result)
                st.rerun()
            except (RuntimeError, ValueError) as error:
                st.error(str(error))


result = st.session_state.get("result")
if result and not st.session_state.get("awaiting_clarification", False):
    st.write("Profile facts:", result.get("profile_facts", []))
    st.write("Job requirements:", result.get("job_requirements", []))
    st.write("Requirement matches:", result.get("requirement_matches", []))
    st.write("Clarification answer:", result.get("clarification_answer", ""))
    st.success("Input received.")
