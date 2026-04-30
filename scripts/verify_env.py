import os
import json
import yaml
import sys
from pathlib import Path
from dotenv import load_dotenv

def verify():
    print("--- Environment Verification ---")
    # Base path is now project root (one level up from scripts/)
    base_path = Path(__file__).parent.parent
    
    # 1. Check .env
    env_path = base_path / ".env"
    if env_path.exists():
        print(f"[OK] .env found at {env_path}")
        load_dotenv(env_path)
    else:
        print(f"[ERROR] .env NOT found at {env_path}")

    # 2. Check Resources
    pkg_res_path = base_path / "src" / "assessment" / "resources"
    resources = ["prompts.yaml", "schemas.json", "competencies.json"]
    for res in resources:
        res_path = pkg_res_path / res
        if res_path.exists():
            print(f"[OK] {res} found.")
        else:
            print(f"[ERROR] {res} NOT found at {res_path}")

    # Check root resources
    root_resources = ["Dockerfile", "docker-compose.yml", "README.md", ".gitignore"]
    for res in root_resources:
        if (base_path / res).exists():
            print(f"[OK] {res} found.")
        else:
            print(f"[WARNING] {res} NOT found.")

    # 3. Check Config Values
    try:
        sys.path.append(str(base_path / "src"))
        from assessment.config import INTERACTIVE_COURSES_PATH, DB_DSN
        print(f"[INFO] COURSES_PATH: {INTERACTIVE_COURSES_PATH}")
        print(f"[INFO] DB_DSN: {DB_DSN}")
    except ImportError as e:
        print(f"[ERROR] Could not import config.py: {e}")

    print("\n--- Verification Complete ---")
    print("Run `docker-compose up --build` to launch the POC stack.")

if __name__ == "__main__":
    verify()
