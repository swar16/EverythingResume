import streamlit as st
from uuid import uuid4
from graph import chatbot
st.title("Resume Tailor")

if "thread_id" not in st.session_state:
    st.session_state["thread_id"] = str(uuid4())

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
        st.session_state["profile_context"] = profile_context.strip()
        st.session_state["job_description"] = job_description.strip()
        result = chatbot.invoke(
            {
                "profile_context": st.session_state["profile_context"],
                "job_description": st.session_state["job_description"],
            },
            config={"configurable": {"thread_id": st.session_state["thread_id"]}},
        )
        st.write("Profile facts:", result.get("profile_facts", []))
        st.write("Job requirements:", result.get("job_requirements", []))
        st.success("Input received.")