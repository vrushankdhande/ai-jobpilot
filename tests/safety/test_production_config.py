"""Static validation of the deployment files and production configuration.

IMPORTANT: Docker is NOT installed on the machine where this project was developed, so these tests CANNOT prove that `docker compose build/up` works. They prove that the
files are well-formed and follow the security rules (no secrets, non-root, private database/API, health checks, restart policy). See docs/DEPLOYMENT.md for the commands to run the
real build on a machine with Docker.
"""
import re
from pathlib import Path

import pytest
import yaml

from config.settings import PROJECT_ROOT, Settings

ROOT = PROJECT_ROOT
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
SERVICES = COMPOSE["services"]


# ============================================================================================ compose structure
def test_the_three_services_exist_with_health_checks_and_restart_policies():
    assert set(SERVICES) == {"mongo", "backend", "web"}
    for name, svc in SERVICES.items():
        assert svc["restart"] == "unless-stopped", name
        assert svc["healthcheck"]["test"] and svc["healthcheck"]["interval"] and svc["healthcheck"]["retries"], name
        assert svc["logging"]["options"]["max-size"] and svc["logging"]["options"]["max-file"], name           # logs cannot fill the disk
    assert SERVICES["backend"]["depends_on"]["mongo"]["condition"] == "service_healthy"
    assert SERVICES["web"]["depends_on"]["backend"]["condition"] == "service_healthy"


def test_mongodb_and_the_api_are_not_published_and_the_dashboard_only_on_localhost():
    assert "ports" not in SERVICES["mongo"] and "ports" not in SERVICES["backend"]                            # reachable inside the compose network only
    assert SERVICES["mongo"]["expose"] == ["27017"] and SERVICES["backend"]["expose"] == ["8000"]
    assert SERVICES["web"]["ports"] == ["127.0.0.1:8501:8501"]
    assert "--auth" in SERVICES["mongo"]["command"]                                                            # MongoDB requires authentication


def test_persistent_data_lives_in_named_volumes():
    assert {"mongo_data", "jobpilot_storage", "jobpilot_logs"} <= set(COMPOSE["volumes"])
    assert "mongo_data:/data/db" in SERVICES["mongo"]["volumes"]
    assert any(v.startswith("jobpilot_storage:/app/storage") for v in SERVICES["backend"]["volumes"])
    assert any(v.startswith("jobpilot_logs:/app/logs") for v in SERVICES["backend"]["volumes"])


def test_the_containers_are_hardened():
    for name in ("backend", "web"):
        assert "no-new-privileges:true" in SERVICES[name]["security_opt"] and SERVICES[name]["cap_drop"] == ["ALL"], name
    assert "no-new-privileges:true" in SERVICES["mongo"]["security_opt"]


def test_no_secret_value_is_written_in_the_compose_file():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert not re.search(r"sk-[A-Za-z0-9_\-]{8,}", text)
    for key in ("MONGO_ROOT_PASSWORD", "ADMIN_TOKEN", "SECRET_KEY", "ANTHROPIC_API_KEY"):
        for m in re.finditer(rf"(?<![\{{$\w]){key}[\"']?\s*[:=]\s*(\S+)", text):                       # an assignment, not the name inside ${{NAME:?...}}
            assert m.group(1).startswith(("${", "$${")), f"{key} has a literal value: {m.group(1)}"
    # the values the application cannot run safely without are REQUIRED (compose stops with a message if they are missing)
    env = COMPOSE["x-app-env"]
    for key in ("ADMIN_TOKEN", "SECRET_KEY", "MONGODB_URI"):
        assert ":?" in env[key], key
    assert ":?" in SERVICES["mongo"]["environment"]["MONGO_INITDB_ROOT_PASSWORD"]


def test_production_defaults_are_safe():
    env = COMPOSE["x-app-env"]
    assert env["APP_ENV"] == "production" and env["DEBUG"] == "false"
    assert env["APPLICATION_AUTOMATION_ENABLED"].endswith(":-false}") and env["SCHEDULER_AUTOSTART"].endswith(":-false}") and env["NOTIFICATIONS_ENABLED"].endswith(":-false}")
    assert "authSource=admin" in env["MONGODB_URI"] and "@mongo:27017" in env["MONGODB_URI"]
    assert "BACKEND_BASE_URL" not in env                                                                       # only the web service needs to find the API
    assert "ALLOW_LOCAL_TEST_FORMS" not in env                                                                # the test-form switch cannot be enabled by compose
    web_env = SERVICES["web"]["environment"]
    assert web_env["BACKEND_BASE_URL"] == "http://backend:8000" and ":?" in web_env["ADMIN_TOKEN"]            # the web server (not the browser) holds the admin token
    assert SERVICES["web"]["read_only"] is True


def test_the_web_service_is_its_own_node_image_and_the_backend_has_no_streamlit():
    assert SERVICES["web"]["build"]["context"] == "./web" and SERVICES["web"]["image"] == "ai-jobpilot-web:latest"
    assert SERVICES["backend"]["image"] == "ai-jobpilot:latest"
    assert "streamlit" not in (ROOT / "docker-compose.yml").read_text(encoding="utf-8").lower()
    assert "frontend" not in DOCKERFILE.lower().replace("web/dockerfile", "")


def test_the_web_dockerfile_is_non_root_multistage_and_secret_free():
    web = (ROOT / "web" / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM node:22-alpine AS build" in web and "FROM node:22-alpine AS runtime" in web
    assert re.search(r"^USER node$", web, re.M) and "HEALTHCHECK" in web
    assert "npm ci --omit=dev" in web and "package-lock.json" in web                                          # exactly the locked production dependencies
    assert not re.search(r"ENV\s+\w*(KEY|TOKEN|PASSWORD|SECRET)\w*\s*=", web, re.I) and "ARG ADMIN" not in web
    assert not re.search(r"COPY\s+(--\S+\s+)*\.\s+\.", web)                                                    # no "COPY . ."
    ignore = (ROOT / "web" / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert {"node_modules", "dist", ".env"} <= set(ignore)


# ============================================================================================ Dockerfile
def test_the_dockerfile_runs_as_a_non_root_user_with_a_health_check():
    assert re.search(r"^USER app$", DOCKERFILE, re.M) and "useradd --system" in DOCKERFILE and "uid 10001" in DOCKERFILE
    assert "HEALTHCHECK" in DOCKERFILE and "/health" in DOCKERFILE
    assert DOCKERFILE.index("USER app") < DOCKERFILE.index("CMD")


def test_the_dockerfile_never_copies_secrets_or_the_whole_context():
    copies = [l for l in DOCKERFILE.splitlines() if l.strip().startswith("COPY")]
    assert copies and not any(re.search(r"COPY\s+(--\S+\s+)*\.\s+\.", l) for l in copies)                     # no "COPY . ."
    assert not any(".env" in l for l in copies) and not any("tests" in l.split() for l in copies)
    assert "ARG" not in "".join(re.findall(r"ARG\s+\w*(?:KEY|TOKEN|PASSWORD|SECRET)\w*", DOCKERFILE))
    assert not re.search(r"ENV\s+\w*(KEY|TOKEN|PASSWORD|SECRET)\w*\s*=", DOCKERFILE, re.I)


def test_the_dockerfile_installs_exactly_the_locked_dependencies_without_dev_tools():
    assert "uv sync --frozen --no-dev" in DOCKERFILE and "uv.lock" in DOCKERFILE
    assert re.search(r'uv==\d+\.\d+\.\d+', DOCKERFILE)                                                        # the installer itself is pinned


def test_every_runtime_package_is_copied_into_the_image():
    packages = {p.parent.name for p in ROOT.glob("*/__init__.py")} - {"tests", "src"}
    copied = set(re.findall(r"COPY\s+--chown=app:app\s+(\w+)\s+\./", DOCKERFILE))
    assert packages - {"frontend"} <= copied | {"scripts"}, f"missing from the image: {sorted(packages - copied)}"
    assert {"backend", "config", "database", "engines", "integrations", "scheduler", "notifications", "security", "browser", "ai"} <= copied


def test_the_browser_is_optional_and_off_by_default():
    assert "ARG INSTALL_BROWSER=false" in DOCKERFILE and "playwright install" in DOCKERFILE
    assert COMPOSE["services"]["backend"]["build"]["args"]["INSTALL_BROWSER"].endswith(":-false}")


def test_the_build_context_excludes_secrets_data_and_tests():
    for needed in (".env", ".env.*", ".git", ".venv", "storage/*", "logs/*", "tests", "*.cred"):
        assert needed in DOCKERIGNORE, needed
    assert "!.env.example" in DOCKERIGNORE


# ============================================================================================ environment documentation
def test_every_setting_is_documented_in_env_example():
    documented = {l.split("=")[0].strip() for l in ENV_EXAMPLE.splitlines() if "=" in l and not l.lstrip().startswith("#")}
    missing = {n.upper() for n in Settings.model_fields} - documented
    assert not missing, f"undocumented settings: {sorted(missing)}"


def test_env_example_contains_no_real_secret_and_marks_the_required_ones():
    values = {l.split("=", 1)[0]: l.split("=", 1)[1].strip() for l in ENV_EXAMPLE.splitlines() if "=" in l and not l.lstrip().startswith("#")}
    for key in ("ANTHROPIC_API_KEY", "ADMIN_TOKEN", "SECRET_KEY", "MONGO_ROOT_PASSWORD"):
        assert values[key] == "", key                                                                          # no default/placeholder secrets that someone might keep
    assert "REQUIRED when APP_ENV=production" in ENV_EXAMPLE
    for key in ("MONGO_ROOT_USER", "MONGO_ROOT_PASSWORD", "INSTALL_BROWSER", "SECRET_KEY", "ADMIN_TOKEN"):
        assert key in values
    assert values["APPLICATION_AUTOMATION_ENABLED"] == "false" and values["ALLOW_LOCAL_TEST_FORMS"] == "false" and values["SCHEDULER_AUTOSTART"] == "false" and values["NOTIFICATIONS_ENABLED"] == "false"


def test_compose_variables_are_all_documented():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    used = set(re.findall(r"(?<!\$)\$\{(\w+)[:\-?}]", text))                                                   # "$${X}" is resolved inside the container, not by compose
    documented = {l.split("=")[0].strip() for l in ENV_EXAMPLE.splitlines() if "=" in l and not l.lstrip().startswith("#")}
    assert used <= documented, f"compose uses undocumented variables: {sorted(used - documented)}"


def test_git_ignores_every_local_secret_and_data_path():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for needed in (".env", "storage/notifications/*", "storage/browser_sessions/*", "storage/resumes/*", "storage/generated/*", "logs/*.log"):
        assert needed in ignore, needed
    assert "backups" in ignore or "backups/" in ignore


def test_the_lock_file_pins_timezone_data_for_slim_images():
    assert 'name = "tzdata"' in (ROOT / "uv.lock").read_text(encoding="utf-8")                                 # zoneinfo (Asia/Kolkata) works on python:3.11-slim
