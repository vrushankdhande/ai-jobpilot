"""30 synthetic job descriptions with hand-written expectations (all fictional).

Expectations were written from the JD text and the synthetic candidate in ``candidate.py`` BEFORE the
engine was run on them. They describe what a careful human reviewer would expect under the documented
policies (mandatory skills must match exactly; related skills earn partial credit only; unknown is not
pass). ``expected_range`` is deliberately wide: it is a sanity band, not a reference score.

Synthetic candidate in one line: senior ML engineer, 7.4 yrs, Pune; Python, SQL, Scikit-learn, XGBoost,
PyTorch, TensorFlow, Generative AI, RAG, LLMs, LangChain, MongoDB, PostgreSQL, Azure, AWS, Docker, Git
(+ "ML" mentioned in experience text, FastAPI/Pandas/Tableau in projects); B.Tech CS; Azure AI Engineer cert.
NOT in the profile: Deep Learning, NLP, Computer Vision, OpenCV, Spark, Airflow, ETL, Kubernetes, MLflow,
Terraform, Linux, CI/CD, Java, C++, Kafka.
Test preferences (see conftest): locations [Pune], remote OK, not open to relocation, full-time,
min salary 2,000,000 INR, authorised to work in India.
"""

STRONG = ["Excellent Match", "Strong Match", "Good Match"]
WEAK = ["Moderate Match", "Low Match"]
ANY = ["Excellent Match", "Strong Match", "Good Match", "Moderate Match", "Low Match"]

JDS = [
    dict(
        id="JD01", scenario="Excellent technical match", title="Senior Machine Learning Engineer - RAG Platform",
        company="Northwind AI", location="Pune, India",
        text="""Senior Machine Learning Engineer - RAG Platform
Northwind AI builds enterprise search products.
Location: Pune, India (Hybrid)

Responsibilities:
- Design and deploy retrieval-augmented generation pipelines for enterprise search.
- Build and evaluate machine learning models and put them into production.
- Monitor model performance and mentor junior engineers.

Requirements:
- 5+ years of experience in machine learning engineering.
- Strong Python skills and experience with PyTorch.
- Hands-on experience with RAG pipelines and LangChain.
- Experience with MongoDB.
- Bachelor's degree in Computer Science or a related field.

Nice to have:
- Experience with Docker and Azure.
Full-time.""",
        mandatory=["Machine Learning", "Python", "PyTorch", "Retrieval-Augmented Generation", "LangChain", "MongoDB"],
        preferred=["Docker", "Azure"], eligibility="PASS", classes=STRONG, score_range=(72, 100),
        why="Every mandatory skill is in the profile, 7.4 yrs > 5, degree and city match."),
    dict(
        id="JD02", scenario="Strong AI/ML match + cloud examples", title="AI Engineer (Remote)", company="Skyline Labs",
        text="""AI Engineer (Remote)
Requirements:
- 4+ years of professional experience building machine learning systems.
- Proficient in Python and Scikit-learn.
- Experience with containerization using Docker.
- Experience with cloud platforms such as AWS, Azure or GCP.
Preferred:
- Familiarity with Kubernetes and MLflow.
Responsibilities:
- Train, evaluate and deploy machine learning models.
- Collaborate with product teams to ship AI features.
Fully remote position. Full-time.""",
        mandatory=["Machine Learning", "Python", "Scikit-learn", "Docker"], preferred=["Kubernetes", "MLflow"],
        eligibility="PASS", classes=STRONG, score_range=(72, 100),
        why="Cloud platforms are only EXAMPLES (not requirements); Kubernetes/MLflow are preferred; remote role."),
    dict(
        id="JD03", scenario="Strong Generative AI match (normalisation: Gen AI, LLM, RAG pipeline)", title="Generative AI Developer",
        company="Pixel Foundry",
        text="""Generative AI Developer
About us: we are a startup. We love Docker and Kubernetes.
Requirements:
- 3+ years of Python development experience.
- Hands-on experience with Gen AI applications using LLMs.
- Experience building RAG pipeline solutions with LangChain.
Nice to have:
- Fine tuning of open-source models.
- Experience with vector DB such as Pinecone or FAISS.
Responsibilities:
- Build and ship generative AI features for customers.""",
        mandatory=["Python", "Generative AI", "Large Language Models", "Retrieval-Augmented Generation", "LangChain"],
        preferred=["Fine-tuning", "Vector Database"], eligibility="PASS", classes=STRONG, score_range=(72, 100),
        why="'About us' skills (Docker/K8s) must be ignored; Pinecone/FAISS are examples of the preferred vector DB."),
    dict(
        id="JD04", scenario="Strong Data Science match", title="Data Scientist", company="Insight Metrics", location="Pune, India",
        text="""Data Scientist
Requirements:
- 3-5 years of experience in data science.
- Strong Python and SQL skills.
- Experience with Scikit-learn and machine learning model development.
Nice to have:
- XGBoost, Tableau.
Responsibilities:
- Analyze customer data and build predictive models.
- Present insights to business stakeholders.
Location: Pune, India""",
        mandatory=["Python", "SQL", "Scikit-learn", "Machine Learning"], preferred=["XGBoost", "Tableau"],
        eligibility="PASS", classes=STRONG, score_range=(72, 100),
        why="Candidate has every mandatory and preferred skill; '3-5 years' is a target range, not a ceiling."),
    dict(
        id="JD05", scenario="Python-heavy role", title="Python Backend Developer", company="CloudNest", location="Pune, India",
        text="""Python Backend Developer
Requirements:
- 3+ years of experience with Python.
- Experience with FastAPI.
- Working knowledge of MongoDB and Git.
Nice to have:
- Docker, PostgreSQL.
Responsibilities:
- Develop and maintain REST services.
- Write unit tests and documentation.
Location: Pune, India (Hybrid)""",
        mandatory=["Python", "FastAPI", "MongoDB", "Git"], preferred=["Docker", "PostgreSQL"],
        eligibility="PASS", classes=STRONG, score_range=(70, 100), why="All skills present (FastAPI from a project)."),
    dict(
        id="JD06", scenario="Machine learning role (compact skills list)", title="Machine Learning Engineer", company="Orbit Data",
        text="""Machine Learning Engineer
Required skills: Python, Scikit-learn, XGBoost, SQL
Preferred skills: Docker, AWS
Experience: 2+ years in machine learning
Location: Remote""",
        mandatory=["Machine Learning", "Python", "Scikit-learn", "XGBoost", "SQL"], preferred=["Docker", "AWS"],
        eligibility="PASS", classes=STRONG, score_range=(70, 100), why="Inline 'Required skills:' / 'Preferred skills:' headings."),
    dict(
        id="JD07", scenario="Deep learning role (related-not-equivalent skills)", title="Deep Learning Engineer", company="Neuron Works",
        text="""Deep Learning Engineer
Requirements:
- 4+ years of experience in deep learning.
- Proficiency in Python and PyTorch or TensorFlow.
- Solid understanding of neural networks and model optimization.
Nice to have: CUDA, Docker
Location: Pune""",
        mandatory=["Deep Learning", "Python", "PyTorch", "TensorFlow"], preferred=["Docker"],
        eligibility="PASS", failed=[], classes=ANY, score_range=(55, 100),
        why="'Deep Learning' is not listed in the profile, but PyTorch/TensorFlow imply it (documented credit 0.7). Scoring v1.1 policy: a documented "
            "near-equivalent (credit >= eligibility.mandatory_skill_equivalent_credit) meets a mandatory skill; loose relatives (TensorFlow for PyTorch, 0.5) do not. "
            "(v1.0 expected FAIL here.) 'PyTorch or TensorFlow' is an any-of group."),
    dict(
        id="JD08", scenario="NLP role", title="NLP Engineer", company="Lexi Labs", location="Pune",
        text="""NLP Engineer
Requirements:
- 3+ years of experience in natural language processing.
- Python and spaCy or Hugging Face.
- Experience with text classification and named entity recognition.
Preferred: BERT fine-tuning.
Location: Pune""",
        mandatory=["Natural Language Processing", "Python", "spaCy", "Hugging Face"], preferred=["Fine-tuning"],
        eligibility="FAIL", failed=["mandatory_skills"], classes=ANY, score_range=(35, 85),
        why="NLP, spaCy and Hugging Face are absent from the profile."),
    dict(
        id="JD09", scenario="RAG and LLM role (alternate wording)", title="LLM Application Engineer", company="Prompt Labs",
        text="""LLM Application Engineer
You will build large language model applications for customers.
What you'll need:
- 3+ years of Python.
- Experience with Retrieval Augmented Generation (RAG).
- Experience with LangChain or LlamaIndex.
- Familiarity with MongoDB.
Good to have:
- AWS or Azure""",
        mandatory=["Large Language Models", "Python", "Retrieval-Augmented Generation", "LangChain", "LlamaIndex"],
        preferred=["MongoDB", "AWS", "Azure"], eligibility="PASS", classes=STRONG, score_range=(70, 100),
        why="'Familiarity' is a weak cue so MongoDB is preferred; LangChain/LlamaIndex is an any-of group."),
    dict(
        id="JD10", scenario="Computer vision role", title="Computer Vision Engineer", company="Pixel Eye",
        text="""Computer Vision Engineer
Requirements:
- 3+ years of experience in computer vision.
- Strong Python and OpenCV skills.
- Experience training CNN models with PyTorch.
Nice to have: ONNX
Location: Pune""",
        mandatory=["Computer Vision", "Python", "OpenCV", "CNN", "PyTorch"], preferred=[],
        eligibility="FAIL", failed=["mandatory_skills"], classes=ANY, score_range=(35, 85),
        why="Computer Vision and OpenCV are not in the profile."),
    dict(
        id="JD11", scenario="Data engineering role", title="Data Engineer", company="Pipeline Co",
        text="""Data Engineer
Requirements:
- 4+ years of experience building ETL pipelines.
- Strong SQL and Python.
- Experience with Apache Spark and Airflow.
- Experience with AWS.
Nice to have: Kafka, Snowflake
Location: Pune (Hybrid)""",
        mandatory=["ETL", "SQL", "Python", "Spark", "Airflow", "AWS"], preferred=["Kafka", "Snowflake"],
        eligibility="FAIL", failed=["mandatory_skills"], classes=ANY, score_range=(30, 80),
        why="ETL, Spark and Airflow are missing."),
    dict(
        id="JD12", scenario="Backend engineering role (any-of groups)", title="Backend Engineer", company="Stackly", location="Pune, India",
        text="""Backend Engineer
Requirements:
- 3+ years of backend development experience.
- Experience with Python and FastAPI, Flask or Django.
- Experience with PostgreSQL or MongoDB.
- Experience with Docker and Git.
Nice to have: Redis, Kubernetes
Location: Pune, India""",
        mandatory=["Python", "FastAPI", "Flask", "Django", "PostgreSQL", "MongoDB", "Docker", "Git"],
        preferred=["Redis", "Kubernetes"], eligibility="PASS", classes=STRONG, score_range=(65, 100),
        why="Python is required on its own; only 'FastAPI, Flask or Django' and 'PostgreSQL or MongoDB' are any-of groups.",
        groups=[["Python"], ["FastAPI", "Flask", "Django"], ["PostgreSQL", "MongoDB"], ["Docker"], ["Git"]]),
    dict(
        id="JD13", scenario="Cloud engineering role", title="Cloud Engineer", company="Nimbus", location="Pune",
        text="""Cloud Engineer
Requirements:
- 5+ years of experience in cloud infrastructure.
- Expertise in AWS and Terraform.
- Experience with Kubernetes and Linux.
- Python scripting.
Nice to have: Jenkins, Azure
Location: Pune""",
        mandatory=["AWS", "Terraform", "Kubernetes", "Linux", "Python"], preferred=["Jenkins", "Azure"],
        eligibility="FAIL", failed=["mandatory_skills"], classes=ANY, score_range=(30, 80),
        why="Terraform, Kubernetes (only Docker, a related skill) and Linux are missing."),
    dict(
        id="JD14", scenario="MLOps role", title="MLOps Engineer", company="Pipeline Labs",
        text="""MLOps Engineer
Requirements:
- 3+ years of experience deploying machine learning models.
- Experience with Docker and CI/CD.
- Experience with MLflow and Kubernetes.
- Strong Python skills.
Nice to have: Airflow, AWS
Location: Remote""",
        mandatory=["MLOps", "Machine Learning", "Docker", "CI/CD", "MLflow", "Kubernetes", "Python"], preferred=["Airflow", "AWS"],
        eligibility="FAIL", failed=["mandatory_skills"], classes=ANY, score_range=(30, 85),
        why="MLOps, CI/CD and MLflow are missing; Docker only partially covers Kubernetes/CI-CD."),
    dict(
        id="JD15", scenario="Data analyst role (over-qualified candidate)", title="Data Analyst", company="Retail Insights", location="Pune, India",
        text="""Data Analyst
Requirements:
- 2+ years of experience in an analytics role.
- Strong SQL skills and Python.
- Experience with Tableau or Power BI.
Nice to have: Excel
Responsibilities:
- Build dashboards and reports for business teams.
- Analyze data to find trends.
Location: Pune, India""",
        mandatory=["SQL", "Python", "Tableau", "Power BI"], preferred=["Excel"], eligibility="PASS", classes=STRONG + ["Moderate Match"],
        score_range=(60, 100), why="Candidate is more senior than the role (small seniority discount, no rejection)."),
    dict(
        id="JD16", scenario="Entry-level role", title="Junior Data Scientist (Entry Level)", company="Fresh Start Analytics",
        text="""Junior Data Scientist (Entry Level)
0-1 years of experience. Freshers welcome.
Requirements:
- Python and SQL.
- Basic knowledge of machine learning and Scikit-learn.
Nice to have: Tableau
Location: Pune
Full-time""",
        mandatory=["Python", "SQL", "Machine Learning", "Scikit-learn"], preferred=["Tableau"], eligibility="PASS",
        classes=STRONG + ["Moderate Match"], score_range=(60, 100),
        why="'0-1 years' is not a ceiling; over-qualification is not a rejection."),
    dict(
        id="JD17", scenario="Senior-level role", title="Senior Data Scientist", company="Apex Analytics",
        text="""Senior Data Scientist
Requirements:
- 6+ years of experience in data science and machine learning.
- Expert-level Python, SQL and Scikit-learn.
- Proven experience leading projects and mentoring.
Nice to have: PyTorch
Location: Pune (On-site)""",
        mandatory=["Machine Learning", "Python", "SQL", "Scikit-learn"], preferred=["PyTorch"], eligibility="PASS",
        classes=STRONG, score_range=(72, 100), why="7.4 yrs >= 6; senior vs senior; on-site in the candidate's city."),
    dict(
        id="JD18", scenario="Experience requirement mismatch", title="Staff Machine Learning Engineer", company="Titan AI",
        text="""Staff Machine Learning Engineer
Requirements:
- 12+ years of professional experience.
- Python, PyTorch and Scikit-learn.
- Experience designing large-scale ML platforms.
Location: Pune""",
        mandatory=["Machine Learning", "Python", "PyTorch", "Scikit-learn"], preferred=[], eligibility="FAIL",
        failed=["min_experience"], classes=ANY, score_range=(55, 100),
        why="7.4 years < 12 required. The numeric score can still be high; eligibility must still FAIL."),
    dict(
        id="JD19", scenario="Mandatory skill mismatch (different stack)", title="Java Backend Engineer", company="Enterprise Corp",
        text="""Java Backend Engineer
Requirements:
- 4+ years of experience with Java and Spring Boot.
- Experience with Kafka and microservices.
- Knowledge of SQL.
Nice to have: Python, Docker
Location: Pune""",
        mandatory=["Java", "Spring Boot", "Kafka", "Microservices", "SQL"], preferred=["Python", "Docker"], eligibility="FAIL",
        failed=["mandatory_skills"], classes=WEAK, score_range=(15, 65),
        why="Java, Spring Boot, Kafka and Microservices are missing; Python is only preferred here."),
    dict(
        id="JD20", scenario="Preferred skill mismatch only", title="Machine Learning Engineer", company="Quantum Insights",
        text="""Machine Learning Engineer
Requirements:
- 3+ years of experience in machine learning.
- Python and Scikit-learn.
Nice to have:
- Kubernetes
- Apache Spark
- Kafka
- MLflow
Location: Pune""",
        mandatory=["Machine Learning", "Python", "Scikit-learn"], preferred=["Kubernetes", "Spark", "Kafka", "MLflow"],
        eligibility="PASS", classes=STRONG, score_range=(70, 100),
        why="Missing preferred skills lower the score but must never cause rejection.", missing_preferred_includes=["Spark", "Kafka", "MLflow"]),
    dict(
        id="JD21", scenario="Education requirement mismatch", title="Research Scientist - Machine Learning", company="Deep Think Labs",
        text="""Research Scientist - Machine Learning
Requirements:
- PhD in Computer Science, Machine Learning or a related field.
- 3+ years of research experience.
- Python and PyTorch.
Location: Pune""",
        mandatory=["Machine Learning", "Python", "PyTorch"], preferred=[], eligibility="FAIL", failed=["education"],
        classes=ANY, score_range=(55, 100), why="A PhD is mandatory; the candidate holds a bachelor's degree."),
    dict(
        id="JD22", scenario="Certification requirement", title="Cloud AI Engineer", company="Cert Corp",
        text="""Cloud AI Engineer
Requirements:
- AWS Certified Machine Learning Specialty certification is required.
- 3+ years of experience with Python and AWS.
- Experience with machine learning models.
Location: Pune""",
        mandatory=["AWS", "Python", "Machine Learning"], preferred=[], eligibility="FAIL", failed=["certifications"],
        classes=ANY, score_range=(55, 100), why="The candidate holds an Azure certification, not the required AWS one."),
    dict(
        id="JD23", scenario="Remote position", title="Remote Machine Learning Engineer", company="Anywhere Inc",
        text="""Remote Machine Learning Engineer
Requirements:
- 3+ years of experience with Python and machine learning.
Nice to have: Docker
This is a fully remote position. Full-time.""",
        mandatory=["Machine Learning", "Python"], preferred=["Docker"], eligibility="PASS", classes=STRONG + ["Moderate Match"],
        score_range=(55, 100), why="Remote: location is not a restriction. Short JD: some categories are UNKNOWN."),
    dict(
        id="JD24", scenario="Hybrid position", title="ML Engineer", company="Hybrid Works",
        text="""ML Engineer
Requirements:
- 3+ years of experience in ML.
- Python, SQL.
Location: Pune, India - Hybrid (3 days in office)""",
        mandatory=["Machine Learning", "Python", "SQL"], preferred=[], eligibility="PASS", classes=STRONG + ["Moderate Match"],
        score_range=(55, 100), why="Hybrid in the candidate's own city."),
    dict(
        id="JD25", scenario="On-site position", title="Machine Learning Engineer (On-site)", company="Onsite Ltd",
        text="""Machine Learning Engineer (On-site)
Requirements:
- 4+ years of experience in machine learning.
- Python, PyTorch.
Location: Pune, India
This is an on-site role.""",
        mandatory=["Machine Learning", "Python", "PyTorch"], preferred=[], eligibility="PASS", classes=STRONG + ["Moderate Match"],
        score_range=(55, 100), why="On-site in the candidate's own city."),
    dict(
        id="JD26", scenario="Location mismatch", title="Machine Learning Engineer", company="Far Away Co",
        text="""Machine Learning Engineer
Location: Bengaluru, India (On-site only)
Requirements:
- 3+ years of experience in machine learning.
- Python and Scikit-learn.""",
        mandatory=["Machine Learning", "Python", "Scikit-learn"], preferred=[], eligibility="FAIL", failed=["location"],
        classes=ANY, score_range=(55, 100), why="On-site in another city and the candidate is not open to relocation."),
    dict(
        id="JD27", scenario="Salary requirement mismatch", title="Machine Learning Engineer", company="Budget Co",
        text="""Machine Learning Engineer
Salary: INR 8-12 LPA
Requirements:
- 2+ years of experience in machine learning.
- Python, Scikit-learn.
Location: Pune""",
        mandatory=["Machine Learning", "Python", "Scikit-learn"], preferred=[], eligibility="PASS", classes=STRONG + ["Moderate Match"],
        score_range=(55, 100), soft_fail=["salary"],
        why="Below the candidate's expected minimum salary: a preference mismatch (warning), not a hard rejection."),
    dict(
        id="JD28", scenario="Incomplete job description", title="ML Engineer", company=None,
        text="We need an ML engineer with Python.",
        mandatory=["Machine Learning"], preferred=[], eligibility="PASS", classes=ANY, score_range=(30, 100),
        max_coverage=85, expect_warning=True,
        why="One sentence: only the title skill is mandatory; coverage must be visibly reduced and a warning given."),
    dict(
        id="JD29", scenario="Ambiguous job description", title=None, company=None,
        text=("We are looking for a rockstar ninja developer to join our family! You should be passionate and love to code. "
              "Python, Java, maybe some Go. Experience with cloud tech like AWS or Azure is a plus. 2-10 years of experience. "
              "Salary competitive. Come build the future with us!"),
        mandatory=[], preferred=[], eligibility="PASS", classes=ANY, score_range=(0, 100), max_coverage=85, expect_warning=True,
        why="Nothing is explicitly required: skills are only 'mentioned' or examples, so none may be mandatory."),
    dict(
        id="JD30", scenario="Unrelated technical role", title="Embedded Firmware Engineer", company="Chipset Corp",
        text="""Embedded Firmware Engineer
Requirements:
- 5+ years of experience in embedded systems.
- Expert C++ and C.
- Experience with RTOS and hardware debugging.
Nice to have: Python scripting
Location: Pune""",
        mandatory=["C++", "C"], preferred=["Python"], eligibility="FAIL", failed=["mandatory_skills"], classes=WEAK,
        score_range=(10, 65), why="C/C++ firmware work; the profile is ML/Python."),
]


def input_for(jd: dict) -> dict:
    """kwargs for JobInput."""
    return {"job_description": jd["text"], "job_title": jd.get("title"), "company_name": jd.get("company"),
            "location": jd.get("location")}
