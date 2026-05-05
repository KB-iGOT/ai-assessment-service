import os
from pathlib import Path
from dotenv import load_dotenv

# Robustly find the .env file relative to this file's location
# Found in root
ROOT_DIR = Path(__file__).parent.parent.parent
ENV_PATH = ROOT_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()

# API Configuration
KARMAYOGI_API_KEY = os.getenv("KARMAYOGI_API_KEY", "")
SEARCH_API_URL = os.getenv("SEARCH_API_URL")
TRANSCODER_STATS_URL = os.getenv("TRANSCODER_STATS_URL")

# SSO / Auth Configuration
SSO_URL = os.getenv("SUNBIRD_SSO_URL")
SSO_REALM = os.getenv("SUNBIRD_SSO_REALM")
REQUIRED_ROLE = os.getenv("REQUIRED_ROLE", "AI_ASSESSMENT_CREATOR")

# Derived: JWKS endpoint built from SSO_URL + realm
JWKS_URL = f"{SSO_URL}realms/{SSO_REALM}/protocol/openid-connect/certs" if SSO_URL and SSO_REALM else None

# Database
DB_DSN = os.getenv("DB_DSN", "postgresql://myuser:mypassword@localhost:5432/karmayogi_db")

# Paths
# Store data in the root directory's interactive_courses_data folder (or custom path)
default_courses_path = os.path.join(ROOT_DIR, "interactive_courses_data")
INTERACTIVE_COURSES_PATH = os.getenv("INTERACTIVE_COURSES_PATH", default_courses_path)

# Google GenAI
GOOGLE_PROJECT_ID = os.getenv("GOOGLE_PROJECT_ID")
GOOGLE_LOCATION = os.getenv("GOOGLE_LOCATION", "us-central1")
GENAI_MODEL_NAME = os.getenv("GENAI_MODEL_NAME", "gemini-2.5-pro")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# Headers for Karmayogi API
API_HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'authorization': f'Bearer {KARMAYOGI_API_KEY}' if KARMAYOGI_API_KEY else '',
    'org': 'dopt',
    'rootorg': 'igot',
    'locale': 'en',
    # 'hostpath': 'portal.uat.karmayogibharat.net' # Optional based on JS
}

# Load Prompt Version
import yaml
PROMPTS_PATH = Path(__file__).parent / "resources" / "prompts.yaml"
try:
    with open(PROMPTS_PATH, "r") as f:
        _prompts = yaml.safe_load(f)
        PROMPT_VERSION = _prompts.get("version", "3.0")
except Exception as e:
    print(f"Warning: Could not load prompt version: {e}")
    PROMPT_VERSION = "Unknown"
