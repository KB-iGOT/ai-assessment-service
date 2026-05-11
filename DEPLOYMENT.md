# Docker Deployment Guide (POC Server)

This guide provides the minimal steps to deploy the Assessment Generation POC using Docker Compose.

## 1. Prerequisites
- **Docker** and **Docker Compose** installed on the server.
- **Port 8000** (API) and **Port 8501** (UI) must be open in the firewall.
- **System Libraries** (for PDF Generation): `libpango-1.0-0`, `libpangoft2-1.0-0`, `libjpeg62-turbo`, `libopenjp2-7` (Debian/Ubuntu).

## 2. Configuration
Create a `.env` file and a `credentials.json` file in the project root.

### .env file:
```bash
DATABASE_URL="postgresql://user:pass@db:5432/karmayogi_db"
GOOGLE_PROJECT_ID="your-project-id"
GOOGLE_LOCATION="us-central1"
GENAI_MODEL_NAME="gemini-2.5-pro"
KARMAYOGI_API_KEY="your-api-key"
```

### credentials.json:
Place your Google Cloud Service Account key file in the root as `credentials.json`.

## 3. Deployment Commands

```bash
# Build and start all services (DB, Kafka, Zookeeper, API, Worker, UI) in the background
docker-compose up -d --build
```

## 4. Verification
- **Swagger Documentation**: `http://<server-ip>:8000/docs` (Base redirect still works)
- **API v1 Status**: `http://<server-ip>:8000/ai-assessment-generation/v1/status/{course_id}`
- **Streamlit UI**: `http://<server-ip>:8501`
- **Health Check**: `http://<server-ip>:8000/health`

## 5. Persistence
The `postgres_data` and `interactive_courses_data` are mounted as volumes to ensure data persists across container restarts.
