"""Synthetic, fictional candidate profile used for matching tests (no real personal data)."""
from ai.schemas.candidate_profile import (
    CandidateProfile,
    Certification,
    ContactInformation,
    Education,
    ProfessionalSummary,
    Project,
    TechnicalSkills,
    WorkExperience,
)


def synthetic_profile(**overrides) -> CandidateProfile:
    data = dict(
        resume_id="resume-test-1",
        profile_id="profile-test-1",
        review_status="reviewed",
        contact=ContactInformation(full_name="Jane Sample", email="jane.sample@example.com", location="Pune, India"),
        summary=ProfessionalSummary(
            professional_summary="Machine learning engineer building NLP and generative AI systems.",
            current_job_title="Senior ML Engineer",
            total_experience_years=7.4,  # reviewed value (fixed so tests do not drift with today's date)
        ),
        technical_skills=TechnicalSkills(
            programming_languages=["Python", "SQL"],
            machine_learning=["Scikit-learn", "XGBoost"],
            deep_learning=["PyTorch", "TensorFlow"],
            generative_ai=["Generative AI", "RAG", "LLMs"],
            llm_frameworks=["LangChain"],
            databases=["MongoDB", "PostgreSQL"],
            cloud_platforms=["Azure", "AWS"],
            developer_tools=["Docker", "Git"],
        ),
        work_experience=[
            WorkExperience(
                company_name="Acme Analytics", job_title="Senior ML Engineer", start_date="2022-01", is_current=True,
                responsibilities=["Built a RAG pipeline using LangChain and MongoDB for internal search.",
                                  "Deployed ML models to production with Docker and monitored their performance.",
                                  "Mentored junior engineers on model evaluation."],
                achievements=["Reduced inference latency by 35% by migrating models to ONNX."],
                technologies_used=["Python", "RAG", "LangChain", "MongoDB", "Docker"],
            ),
            WorkExperience(
                company_name="Beta Labs", job_title="Data Scientist", start_date="2019-06", end_date="2021-12",
                responsibilities=["Developed churn prediction models in Python and Scikit-learn.",
                                  "Analyzed customer data with SQL and presented insights to stakeholders."],
                achievements=["Improved model accuracy from 82% to 91%."],
                technologies_used=["Python", "Scikit-learn", "SQL"],
            ),
        ],
        projects=[
            Project(project_name="Resume Matcher", technologies=["Python", "FastAPI", "MongoDB"],
                    responsibilities=["Matches resumes to job descriptions using embeddings."],
                    achievements=["Processed 10,000 resumes with 94% accuracy."]),
            Project(project_name="Churn Dashboard", technologies=["Python", "Pandas", "Tableau"],
                    responsibilities=["Visualised churn drivers for business teams."]),
        ],
        education=[Education(degree="Bachelor of Technology", specialization="Computer Science",
                             institution="Sample Institute of Technology", start_year=2015, graduation_year=2019)],
        certifications=[Certification(certification_name="Azure AI Engineer Associate", issuing_organization="Microsoft")],
    )
    data.update(overrides)
    return CandidateProfile(**data)
