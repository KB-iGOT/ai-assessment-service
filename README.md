# Course Assessment Generation

An AI-powered, audit-ready assessment generation system built for the **Karmayogi government learning platform**. Powered by **Google Gemini 2.5 Pro**, FastAPI, and Streamlit, the system follows Senior Instructional Designer logic to produce blueprints and questions with full pedagogic reasoning.

---

## Features

- **Model**: Powered by `gemini-2.5-pro` via Google Vertex AI.
- **V2 Smart Architecture**: JWT Authentication, Clone-on-Request (instant results), and Private Workspaces.
- **Event-Driven**: Decoupled API (Producer) and Worker (Consumer) via Kafka. Publishes `ASSESSMENT_COMPLETED` events on job completion.
- **3 Assessment Types**: Practice (Reinforcement), Final (Certification), and Comprehensive (Cross-course).
- **5 Question Types**: MCQ, Fill-in-the-Blank (FTB), Match-the-Following (MTF), Multi-Choice, and True/False.
- **Multilingual**: Supports 10+ Indian languages (Hindi, Tamil, Telugu, Gujarati, Kannada, Malayalam, Marathi, Odia, Punjabi, Assamese, Bengali).
- **KCM Alignment**: Maps every question strictly to the **Karmayogi Competency Model** (Behavioural & Functional).
- **Explainable AI**: Every question includes alignment reasoning — Learning Objectives, KCM Competencies, and Bloom's Taxonomy justification.
- **Gemini Context Caching**: The 110 KCM competency definitions (~60k tokens) are cached in Gemini to reduce token costs on every request.
- **Multiple Export Formats**: JSON, CSV (V2 7-option schema), PDF (with Indian language rendering), and Word (DOCX).
- **Dual Logging**: Console and file logging (`logs/api.log` & `logs/worker.log`).
- **Auto Cleanup**: Scheduled daily job deletes records older than a configurable retention period.

---

## Architecture Overview

The system uses an event-driven, microservice architecture:

```
Streamlit UI (8501)
       │
       ▼
FastAPI API (8000)   ──── Kafka ────▶  Worker Service
       │                                     │
       ▼                                     ▼
  PostgreSQL ◀──────────────────────── Gemini 2.5 Pro
                                             │
                                      Karmayogi Platform
```

For the full architecture including sequence diagrams, database schema, and caching strategy, see [architecture.md](architecture.md).

---

## Project Structure

```
.
├── src/assessment/          # Core Python package
│   ├── api.py               # FastAPI app — all REST endpoints
│   ├── worker_service.py    # Kafka consumer — orchestrates generation
│   ├── generator.py         # LLM prompt engineering and response parsing
│   ├── fetcher.py           # Karmayogi API integration, PDF/VTT extraction
│   ├── db.py                # PostgreSQL async operations (asyncpg)
│   ├── auth.py              # JWT validation (JWKS from iGot Karmayogi)
│   ├── events.py            # Kafka producer/consumer setup
│   ├── exporters.py         # PDF and DOCX generation (WeasyPrint + python-docx)
│   ├── exporters_csv_v2.py  # CSV export with 7-option V2 schema
│   ├── cleanup.py           # APScheduler job for old record deletion
│   ├── config.py            # Environment variable loading
│   └── resources/
│       ├── competencies.json     # 110 KCM competencies index
│       ├── kcm_descriptions.json # Full KCM descriptions (~60k tokens, cached)
│       ├── schemas.json          # LLM JSON output schemas
│       ├── prompts.yaml          # LLM system and user prompts
│       └── fonts/               # Noto Sans fonts for Indian language PDF rendering
│
├── ui/
│   ├── app.py               # Streamlit interactive UI
│   ├── requirements.txt     # UI-specific dependencies
│   └── Dockerfile           # UI container image
│
├── scripts/
│   └── verify_env.py        # Pre-flight environment check script
│
├── Dockerfile               # API/Worker container image
├── DockerfileWorker         # Dedicated worker container image
├── docker-compose.yml       # Full stack orchestration
├── pyproject.toml           # Python project and dependency config
├── .env.example             # Environment variable template
├── architecture.md          # Detailed technical architecture document
└── DEPLOYMENT.md            # Server deployment guide
```

---

## Getting Started

### Prerequisites
- Python 3.11+
- Docker and Docker Compose
- Google Cloud Project with Vertex AI / GenAI API enabled
- Google Cloud Service Account credentials JSON file
- Karmayogi platform API key (JWT)

### Initial Setup
1. Clone this repository.
2. Copy `.env.example` to `.env` and fill in all required values.
3. Place your Google Cloud Service Account JSON file in the root as `credentials.json`.

### Method 1: Docker (Recommended)

Launch the full stack (PostgreSQL + Kafka + Zookeeper + API + Worker + UI) with one command:

```bash
docker-compose up --build
```

| Service | URL |
| :--- | :--- |
| API | http://localhost:8000 |
| Streamlit UI | http://localhost:8501 |
| Kafka | localhost:29092 |

### Method 2: Hybrid Run (Local Code + Docker Infrastructure)

Useful during development to run code with hot-reload while keeping infra in Docker.

**1. Start infrastructure:**
```bash
docker-compose up -d db kafka zookeeper
```

**2. Start API (Producer):**
```bash
export PYTHONPATH=$PYTHONPATH:$(pwd)/src
uv run uvicorn assessment.api:app --reload
```

**3. Start Worker (Consumer):**
```bash
export PYTHONPATH=$PYTHONPATH:$(pwd)/src
uv run python -m assessment.worker_service
```

**4. Start UI:**
```bash
uv run streamlit run ui/app.py
```

---

## Environment Variables

Copy `.env.example` to `.env` and configure:

| Variable | Description | Example |
| :--- | :--- | :--- |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:pass@db:5432/karmayogi_db` |
| `GOOGLE_PROJECT_ID` | GCP project ID | `my-gcp-project` |
| `GOOGLE_LOCATION` | Vertex AI region | `us-central1` |
| `GENAI_MODEL_NAME` | Gemini model name | `gemini-2.5-pro` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to credentials JSON | `/app/credentials.json` |
| `KARMAYOGI_API_KEY` | iGot Karmayogi JWT token | `eyJhbGci...` |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker address | `localhost:29092` |
| `KAFKA_TOPIC` | Kafka topic name | `assessment.lifecycle.events` |
| `MAX_CONCURRENCY` | Max parallel generation jobs | `50` |
| `CLEANUP_RETENTION_DAYS` | Days before auto-deletion | `7` |
| `DISABLE_AUTH_VERIFICATION` | Bypass JWT check (dev only) | `false` |

---

## API Reference (V2) — Recommended

The V2 API is the production-ready, event-driven iteration. It introduces **Authentication**, **Private Workspaces**, and instant **Cloning**.

- **Base URL**: `http://localhost:8000/ai-assessments/v2`
- **Authentication**: All V2 endpoints require a valid JWT via the `x-authenticated-user-token` header.

### 1. Generate Assessment (Async)
- **Endpoint**: `POST /ai-assessments/generate` — `multipart/form-data`
- **Description**: Starts an event-driven generation job, or instantly clones an existing one if the same parameters were previously requested.
- **Response**:
  - `202 Accepted` (`status: PENDING`) — New background job started.
  - `200 OK` (`status: COMPLETED`) — Cache/clone hit, instant result.

| Form Field | Type | Description |
| :--- | :--- | :--- |
| `course_ids` | String | Comma-separated course IDs (e.g. `do_1,do_2`) |
| `assessment_type` | Enum | `practice`, `final`, `comprehensive`, `standalone` |
| `difficulty` | Enum | `beginner`, `intermediate`, `advanced` |
| `language` | Enum | `english`, `hindi`, `tamil`, `telugu`, etc. |
| `total_questions` | Integer | Total number of questions to generate |
| `question_type_counts` | JSON String | Counts per type: `{"mcq": 5, "ftb": 5, "mtf": 5, "multichoice": 0, "truefalse": 0}` |
| `force` | Boolean String | `"true"` bypasses cache and forces a new LLM call |
| `enable_blooms` | Boolean String | `"true"` enforces Bloom's Taxonomy distribution |
| `blooms_config` | JSON String | Bloom's % per level — must sum to 100: `{"Remember": 20, "Understand": 20, "Apply": 20, "Analyze": 20, "Evaluate": 10, "Create": 10}` |
| `course_weightage` | JSON String | Comprehensive only — % per course ID, must sum to 100: `{"do_1": 60, "do_2": 40}` |
| `time_limit` | Integer | Duration in minutes — shifts cognitive level distribution |
| `files` | File | PDF or VTT files for standalone assessments |

### 2. Check Status
- **Endpoint**: `GET /ai-assessments/status/{job_id}`
- **Response**: Returns `status` (`PENDING`, `IN_PROGRESS`, `COMPLETED`, `FAILED`). When `COMPLETED`, includes the full `assessment_data` object.

### 3. Edit Assessment
- **Endpoint**: `PUT /ai-assessments/update/{job_id}`
- **Description**: Allows the owner to manually edit questions before finalising.
- **Body**: `{"assessment_data": { ... }}`

### 4. Fetch User History
- **Endpoint**: `GET /ai-assessments/history`
- **Description**: Returns all assessment jobs (any status) initiated by the authenticated user.
- **Response**: Array of `{job_id, status, created_at, updated_at, config, error_message}`.

### 5. Download Results
- **Endpoint**: `GET /ai-assessments/download/{job_id}?format=<format>`
- **Supported formats**: `csv`, `json`, `pdf`, `docx`
- **Ownership**: Only the user who generated the assessment can download it — others get `403 Forbidden`.
- **Invalid format**: Returns `400 Bad Request` with the list of supported formats.

| Format | Endpoint |
| :--- | :--- |
| CSV (7-option V2 schema) | `GET /ai-assessments/download/{job_id}?format=csv` |
| JSON | `GET /ai-assessments/download/{job_id}?format=json` |
| PDF | `GET /ai-assessments/download/{job_id}?format=pdf` |
| Word (DOCX) | `GET /ai-assessments/download/{job_id}?format=docx` |

> **UI Integration Note**: Do NOT use `<a href>` links with the token in the URL — this leaks the JWT in server logs and browser history. Use the fetch + Blob pattern instead:
>
> ```js
> async function downloadAssessment(jobId, format, token) {
>   const response = await fetch(
>     `/ai-assessments/v2/download/${jobId}?format=${format}`,
>     { headers: { 'x-authenticated-user-token': token } }
>   );
>   if (!response.ok) throw new Error(`Download failed: ${response.status}`);
>   const blob = await response.blob();
>   const url = URL.createObjectURL(blob);
>   const a = document.createElement('a');
>   a.href = url;
>   a.download = `${jobId}_assessment.${format}`;
>   a.click();
>   URL.revokeObjectURL(url);
> }
> ```

---

## Integration Workflow

Assessment generation is asynchronous (LLM latency + file processing). Follow this pattern:

### Step 1: Start Generation

```bash
POST /ai-assessments/v2/generate
```

**Response (new job):**
```json
{
  "message": "Generation started",
  "status": "PENDING",
  "job_id": "comprehensive_do_123_do_456"
}
```

**Response (cache/clone hit):**
```json
{
  "status": "COMPLETED",
  "job_id": "comprehensive_do_123_do_456",
  "assessment_data": { ... }
}
```

### Step 2: Poll for Status

Poll `GET /ai-assessments/v2/status/{job_id}` every 5–10 seconds:

| Status | Meaning | Suggested UI Action |
| :--- | :--- | :--- |
| `PENDING` | Queued, not yet started | Show "Queued" message |
| `IN_PROGRESS` | Worker is generating | Show spinner / progress bar |
| `COMPLETED` | Done — `assessment_data` available | Render questions |
| `FAILED` | Error occurred | Show `error_message` |

**In-progress response:**
```json
{ "status": "IN_PROGRESS", "job_id": "comprehensive_do_123_do_456" }
```

**Failed response:**
```json
{ "status": "FAILED", "job_id": "comprehensive_do_123_do_456", "error": "Reason..." }
```

### Step 3: Retrieve Results

When `COMPLETED`, the `/status` response includes `assessment_data` for direct rendering. Use the download endpoints to get files.

---

## Parsing the Response

The `assessment_data` object has two top-level keys:

### `blueprint`
Pedagogic audit trail and design rationale.

| Field | Description |
| :--- | :--- |
| `assessment_scope_summary` | Plain-text summary of covered topics |
| `smart_learning_objectives` | Array of discrete learning objectives |
| `unified_competency_map` | Functional and behavioural KCM areas covered |
| `blooms_taxonomy_mapping` | Bloom's level distribution in the assessment |
| `time_appropriateness_validation` | Validation note for the requested time limit |

### `questions`
Arrays keyed by question type: `Multiple Choice Question`, `FTB Question`, `MTF Question`, `True/False Question`.

Every question object includes:
- `course_name` — source course (important for comprehensive assessments)
- `answer_rationale` — `correct_answer_explanation`, `why_factor`, `logic_justification`
- `reasoning`:
  - `learning_objective_alignment` — verbatim match to a blueprint learning objective
  - `competency_alignment` — KCM area, theme, sub-theme, and domain
  - `blooms_level_justification`
  - `relevance_percentage` — 0–100 confidence score
- `blooms_level` — e.g. `Remember`, `Analyze`, `Create`

> **MTF Note**: Match-the-Following questions use `matching_context` instead of `question_text`.

---

## Developer Examples

### Example 1: Standard Assessment (Single Course)

```bash
curl --location 'http://localhost:8000/ai-assessments/v2/generate' \
--header 'x-authenticated-user-token: YOUR_JWT_TOKEN_HERE' \
--form 'course_ids="do_1144540583527301121908"' \
--form 'assessment_type="practice"' \
--form 'difficulty="intermediate"' \
--form 'language="english"' \
--form 'total_questions="10"' \
--form 'question_type_counts="{\"mcq\": 5, \"ftb\": 5, \"mtf\": 0, \"multichoice\": 0, \"truefalse\": 0}"' \
--form 'enable_blooms="true"' \
--form 'blooms_config="{\"Remember\": 20, \"Understand\": 30, \"Apply\": 30, \"Analyze\": 20, \"Evaluate\": 0, \"Create\": 0}"' \
--form 'force="false"'
```

### Example 2: Comprehensive Assessment (Cross-Course)

```bash
curl --location 'http://localhost:8000/ai-assessments/v2/generate' \
--header 'x-authenticated-user-token: YOUR_JWT_TOKEN_HERE' \
--form 'course_ids="do_courseA123,do_courseB456"' \
--form 'assessment_type="comprehensive"' \
--form 'difficulty="advanced"' \
--form 'language="english"' \
--form 'total_questions="20"' \
--form 'question_type_counts="{\"mcq\": 10, \"ftb\": 5, \"mtf\": 5, \"multichoice\": 0, \"truefalse\": 0}"' \
--form 'enable_blooms="false"' \
--form 'course_weightage="{\"do_courseA123\": 70, \"do_courseB456\": 30}"' \
--form 'force="false"'
```

### Example 3: Standalone Assessment (File Upload)

```bash
curl --location 'http://localhost:8000/ai-assessments/v2/generate' \
--header 'x-authenticated-user-token: YOUR_JWT_TOKEN_HERE' \
--form 'assessment_type="standalone"' \
--form 'difficulty="beginner"' \
--form 'language="english"' \
--form 'total_questions="5"' \
--form 'question_type_counts="{\"mcq\": 5, \"ftb\": 0, \"mtf\": 0, \"multichoice\": 0, \"truefalse\": 0}"' \
--form 'enable_blooms="false"' \
--form 'files=@"/path/to/document.pdf"' \
--form 'files=@"/path/to/transcript.vtt"'
```

### Example 4: Poll Job Status

```bash
curl --location 'http://localhost:8000/ai-assessments/v2/status/comprehensive_do_courseA123_do_courseB456' \
--header 'x-authenticated-user-token: YOUR_JWT_TOKEN_HERE'
```

### Example 5: Fetch User History

```bash
curl --location 'http://localhost:8000/ai-assessments/v2/history' \
--header 'x-authenticated-user-token: YOUR_JWT_TOKEN_HERE'
```

**Response (snippet):**
```json
[
  {
    "job_id": "comprehensive_do_courseA123_do_courseB456",
    "status": "COMPLETED",
    "created_at": "2026-03-10T11:45:00.000Z",
    "updated_at": "2026-03-10T11:46:15.000Z",
    "config": { "assessment_type": "comprehensive", "difficulty": "advanced", "total_questions": 20 },
    "error_message": null
  }
]
```

### Example 6: Download Results

```bash
# CSV
curl -H 'x-authenticated-user-token: YOUR_JWT' \
  'http://localhost:8000/ai-assessments/v2/download/{job_id}?format=csv' \
  --output assessment.csv

# JSON
curl -H 'x-authenticated-user-token: YOUR_JWT' \
  'http://localhost:8000/ai-assessments/v2/download/{job_id}?format=json' \
  --output assessment.json

# PDF
curl -H 'x-authenticated-user-token: YOUR_JWT' \
  'http://localhost:8000/ai-assessments/v2/download/{job_id}?format=pdf' \
  --output assessment.pdf

# Word (DOCX)
curl -H 'x-authenticated-user-token: YOUR_JWT' \
  'http://localhost:8000/ai-assessments/v2/download/{job_id}?format=docx' \
  --output assessment.docx
```

---

## Sample Assessment JSON Output

```json
{
  "blueprint": {
    "assessment_scope_summary": "Comprehensive assessment covering...",
    "smart_learning_objectives": [
      "Identify the core mandate and organisational structure of the Department.",
      "Describe the primary regulatory functions..."
    ],
    "unified_competency_map": {
      "functional": ["Financial Acumen", "Regulatory Compliance"],
      "behavioral": ["Accountability"]
    },
    "time_appropriateness_validation": "Validated for 30 minutes."
  },
  "questions": {
    "Multiple Choice Question": [
      {
        "course_name": "Introduction to Financial Services",
        "question_text": "What is the core mandate of the Department?",
        "options": [
          { "text": "Marketing" },
          { "text": "Financial Regulation" },
          { "text": "Human Resources" },
          { "text": "IT Support" }
        ],
        "correct_option_index": 1,
        "answer_rationale": {
          "correct_answer_explanation": "The department oversees financial regulations...",
          "why_factor": "Essential for compliance.",
          "logic_justification": "Drawn directly from chapter 2."
        },
        "reasoning": {
          "learning_objective_alignment": "Identify the core mandate and organisational structure of the Department.",
          "competency_alignment": {
            "kcm": {
              "competency_area": "Functional",
              "competency_theme": "Financial Acumen",
              "sub_theme": "Regulatory Compliance"
            },
            "domain": "Finance"
          },
          "blooms_level_justification": "Requires recalling foundational mandates.",
          "relevance_percentage": 95
        },
        "blooms_level": "Remember"
      }
    ]
  }
}
```

---

## Legacy V1 API

Still available at `/api/v1/...` for backward compatibility. Does not support cloning, editing, or user separation.

- `POST /api/v1/generate` — Start generation
- `GET /api/v1/status/{job_id}` — Poll status
- `GET /api/v1/download_csv/{job_id}` — Download CSV
- `GET /api/v1/download_json/{job_id}` — Download JSON
- `GET /api/v1/download_pdf/{job_id}` — Download PDF
- `GET /api/v1/download_docx/{job_id}` — Download DOCX

---

## Governance Rules

- **Anti-Hallucination**: Prompts are strictly anchored to extracted PDF/VTT content. The LLM cannot introduce external information.
- **Learning Objective Tagging**: Every question must map verbatim to one of the learning objectives parsed from the Karmayogi course metadata.
- **KCM Mapping**: All competency references must come from the authoritative 110-competency KCM dataset.
- **Context Caching**: KCM competency descriptions are cached in Gemini to reduce per-request token costs.
- **Multilingual Output**: All generated text (questions, options, reasoning) is produced in the user-selected language.
