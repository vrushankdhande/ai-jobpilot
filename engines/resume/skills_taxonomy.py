"""Skill dictionary used to *recognise and categorise* skills found in a resume.

This list is a lookup table only. A skill is added to a profile only when it is
actually present in the candidate's resume text.
"""
import re

# category -> {canonical name: [aliases]}
TAXONOMY: dict[str, dict[str, list[str]]] = {
    "programming_languages": {
        "Python": [], "SQL": [], "Java": [], "JavaScript": ["js"], "TypeScript": ["ts"],
        "C++": [], "C#": [], "C": [], "R": [], "Go": ["golang"], "Rust": [], "Scala": [],
        "Kotlin": [], "Bash": ["shell scripting"], "MATLAB": [], "HTML": [], "CSS": [],
        "PHP": [], "Ruby": [], "Swift": [],
    },
    "machine_learning": {
        "Machine Learning": ["ml"], "Scikit-learn": ["sklearn", "scikit learn"], "XGBoost": [],
        "LightGBM": [], "Statistics": ["statistical analysis"], "Regression": [],
        "Classification": [], "Clustering": [], "Time Series": ["time series forecasting"],
        "NLP": ["natural language processing"], "Computer Vision": [],
        "Feature Engineering": [], "Recommendation Systems": ["recommender systems"],
        "Predictive Modeling": ["predictive modelling"],
    },
    "deep_learning": {
        "Deep Learning": [], "TensorFlow": [], "PyTorch": [], "Keras": [], "CNN": [],
        "RNN": [], "LSTM": [], "Transformers": [], "BERT": [], "ONNX": [],
    },
    "generative_ai": {
        "Generative AI": ["genai", "gen ai"], "RAG": ["retrieval augmented generation",
        "retrieval-augmented generation"], "LLMs": ["llm", "large language models"],
        "Prompt Engineering": [], "Fine-tuning": ["fine tuning", "finetuning"],
        "Embeddings": [], "Vector Search": [],
    },
    "llm_frameworks": {
        "LangChain": [], "LlamaIndex": [], "LangGraph": [], "Hugging Face": ["huggingface"],
        "OpenAI API": [], "Haystack": [], "CrewAI": [], "AutoGen": [],
    },
    "libraries": {
        "Pandas": [], "NumPy": [], "SciPy": [], "Matplotlib": [], "Seaborn": [], "Plotly": [],
        "FastAPI": [], "Flask": [], "Django": [], "Streamlit": [], "Pydantic": [],
        "OpenCV": [], "NLTK": [], "spaCy": [], "PySpark": [], "SQLAlchemy": [],
        "Beautiful Soup": ["beautifulsoup", "bs4"], "Selenium": [], "Playwright": [],
        "Pytest": [], "React": ["reactjs", "react.js"], "Node.js": ["nodejs", "node"],
    },
    "databases": {
        "MongoDB": ["mongo"], "PostgreSQL": ["postgres"], "MySQL": [], "SQLite": [],
        "Redis": [], "Elasticsearch": [], "Pinecone": [], "FAISS": [], "ChromaDB": ["chroma"],
        "Snowflake": [], "Oracle": [], "SQL Server": ["mssql"], "Cassandra": [],
        "DynamoDB": [], "Neo4j": [], "Qdrant": [], "Weaviate": [],
    },
    "cloud_platforms": {
        "Azure": ["microsoft azure"], "AWS": ["amazon web services"],
        "GCP": ["google cloud", "google cloud platform"], "Databricks": [],
        "Azure ML": [], "SageMaker": ["aws sagemaker"], "Vertex AI": [], "Heroku": [],
    },
    "developer_tools": {
        "Docker": [], "Git": [], "GitHub": [], "GitLab": [], "Kubernetes": ["k8s"],
        "Jenkins": [], "Linux": [], "VS Code": ["vscode"], "Jupyter": ["jupyter notebook"],
        "Postman": [], "Jira": [], "CI/CD": ["cicd"], "MLflow": [], "Airflow": [],
        "DVC": [], "Terraform": [], "Power BI": ["powerbi"], "Tableau": [], "Excel": [],
    },
}

# Label words in a "Label: a, b, c" skills line -> category.
CATEGORY_LABELS: list[tuple[str, str]] = [
    ("programming", "programming_languages"), ("languages", "programming_languages"),
    ("generative", "generative_ai"), ("genai", "generative_ai"),
    ("llm", "llm_frameworks"), ("deep learning", "deep_learning"),
    ("machine learning", "machine_learning"), ("ml", "machine_learning"),
    ("librar", "libraries"), ("framework", "libraries"),
    ("database", "databases"), ("cloud", "cloud_platforms"),
    ("tool", "developer_tools"), ("devops", "developer_tools"),
]

_AMBIGUOUS = {"c", "r", "go", "js", "ts", "ml", "node"}

# Spoken (human) languages are not technical skills ("Languages: Python, SQL" is; "Spoken Languages: English (Native)" is not).
SPOKEN_LABEL_RE = re.compile(r"\b(?:spoken|human|natural|foreign)\s+languages?\b|\blanguages?\s+(?:known|proficiency|spoken)\b|\blanguage\s+skills\b", re.IGNORECASE)
_SPOKEN = {
    "english", "hindi", "marathi", "german", "french", "spanish", "gujarati", "tamil", "telugu", "kannada", "malayalam", "bengali", "punjabi",
    "urdu", "odia", "oriya", "assamese", "italian", "portuguese", "russian", "japanese", "korean", "mandarin", "chinese", "cantonese", "arabic",
    "dutch", "swedish", "turkish", "polish", "hebrew", "persian", "farsi", "sanskrit", "nepali", "sindhi", "konkani",
}


def is_spoken_language(item: str) -> bool:
    """'English (Native)', 'German - Fluent', 'French' -> True. Programming names such as 'R' or 'Go' are never matched."""
    base = re.sub(r"\([^)]*\)", "", item)
    base = re.split(r"\s*[-\u2013\u2014:]\s*", base)[0].strip().lower()
    return base in _SPOKEN


def normalize_skill(text: str) -> str:
    text = re.sub(r"\([^)]*\)", "", text)  # drop "(Advanced)"
    text = re.sub(r"\s+", " ", text).strip().strip(".,;:").lower()
    return text


# normalized alias -> (category, canonical)
LOOKUP: dict[str, tuple[str, str]] = {}
for _cat, _skills in TAXONOMY.items():
    for _canon, _aliases in _skills.items():
        for _name in [_canon, *_aliases]:
            LOOKUP.setdefault(normalize_skill(_name), (_cat, _canon))


def category_for_label(label: str) -> str | None:
    label = label.lower()
    for key, cat in CATEGORY_LABELS:
        if key in label:
            return cat
    return None


def _build_scan_regex() -> re.Pattern:
    names = sorted(
        (n for n in LOOKUP if len(n) > 2 and n not in _AMBIGUOUS), key=len, reverse=True
    )
    body = "|".join(re.escape(n) for n in names)
    return re.compile(rf"(?<![\w+#.\-])({body})(?![\w+#]|-\w)", re.IGNORECASE)


_SCAN_RE = _build_scan_regex()


def find_skills_in_text(text: str) -> list[tuple[str, str, str]]:
    """Return [(category, canonical, text_as_written)] for explicit mentions."""
    found, seen = [], set()
    for m in _SCAN_RE.finditer(text):
        written = m.group(1)
        cat, canon = LOOKUP[normalize_skill(written)]
        if canon not in seen:
            seen.add(canon)
            found.append((cat, canon, written))
    return found
