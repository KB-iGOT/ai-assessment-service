# Assessment Generation Architecture

This document provides a technical overview of the AI-powered Assessment Generation system. It is designed for architectural review and covers system context, data flow, and key logic components.

## 1. System Context

The system operates as a microservice offering a REST API for generating audit-ready assessments from course content (PDFs, Videos/VTTs). It integrates with the Karmayogi Platform for content and Google Vertex AI for generation.

```mermaid
graph TD
    User["Learner / Admin"] -- "UI (Streamlit)" --> UI["Frontend Service"]
    UI -- "REST API" --> API["Assessment API (FastAPI)"]
    
    subgraph "Assessment Service (Docker)"
        API -- "Background Task" --> Worker["Async Worker"]
        Worker -- "Read/Write" --> DB[("PostgreSQL")]
        Worker -- "File I/O" --> FS["Shared Volume\n(interactive_courses_data)"]
    end
    
    Worker -- "Search Content" --> KB["Karmayogi Platform APIs"]
    Worker -- "Generate Content" --> Gemini["Google Vertex AI\n(Gemini 2.5 Pro)"]
    
    API -- "Download (PDF/DOCX)" --> Exporter["Exporter Engine\n(WeasyPrint)"]
```

## 2. End-to-End Request Flow (Sequence)

The generation process is asynchronous. The API acknowledges the request immediately, while the heavy lifting happens in the background.

```mermaid
sequenceDiagram
    participant User
    participant API as API Server
    participant Worker as Background Worker
    participant Ext as Karmayogi/Files
    participant LLM as Google GenAI
    participant DB as Database

    User->>API: POST /generate (Course IDs or Files)
    API->>API: Generate & Hash Params (Cache Key)
    API->>DB: Check Cache (Job ID)
    
    alt Cache Hit (Completed)
        DB-->>API: Return Existing Job ID
        API-->>User: 200 OK (Status: COMPLETED)
    else New Request
        API->>DB: Create Job (Status: PENDING)
        API->>Worker: Dispatch Task
        API-->>User: 200 OK (Status: PENDING, JobID: xyz)
        
        loop Polling Status
            User->>API: GET /status/xyz
            API->>DB: Check Status
            DB-->>API: IN_PROGRESS
            API-->>User: {"status": "IN_PROGRESS"}
        end
        
        par Background Processing
            Worker->>Worker: Update Status: IN_PROGRESS
            
            alt Comprehensive Mode
                Worker->>Ext: Fetch Metadata/PDFs/VTTs (Recursive)
            else Standalone Mode
                Worker->>Worker: Process Uploaded Files
            end
            
            Worker->>Worker: Deduplicate Content
            Worker->>Worker: Build Prompt (v3.5)
            Worker->>LLM: Generate Assessment (JSON)
            LLM-->>Worker: JSON Response
            Worker->>DB: Save Result & Status: COMPLETED
        end
        
        User->>API: GET /status/xyz
        API-->>User: {"status": "COMPLETED"}
        User->>API: GET /download_pdf/xyz
        API-->>User: Returns PDF File
    end
```

## 3. Core Logic: Generator & Prompting

The core intelligence resides in `src/assessment/generator.py` and `prompts.yaml`.

### 3.1 Content Aggregation Strategy
- **Recursive Fetching**: The system crawls the course hierarchy (deep-search) to find all leaf nodes.
- **Deduplication**: Content hashes (MD5) are used to prevent processing the same PDF or VTT twice (common in multi-language course structures).
- **Text Extraction**:
    - **PDF**: Uses `PyMuPDF` (fitz) for high-fidelity text extraction.
    - **Video**: Fetches `.vtt` subtitles via the Transcoder stats API.

### 3.2 Prompt Engineering (v3.5)
The prompt is dynamically constructed based on the `AssessmentType`:
- **Comprehensive**: Merges context from all course IDs. Logic forces cross-module questions.
- **Standalone**: STRICT scope limitation to provided files only.
- **Bloom's Taxonomy**: The prompt enforces a specific distribution (e.g., 20% Remember, 40% Analyze) to ensure pedagogical depth.

```mermaid
flowchart LR
    Input[Inputs] --> B{Assessment Type?}
    B -- Comprehensive --> C[Fetch All Courses]
    B -- Standalone --> S[Use Uploaded Files]
    
    C & S --> D[Content Processor]
    D --> E[Deduplication & Hash]
    E --> F[Prompt Builder]
    
    F --> G[System Prompt Template]
    G --> H{LLM Generation}
    H --> I[JSON Validation]
    I --> J[Output Assessment]
```

## 4. PDF Generation (WeasyPrint)

The system utilizes **WeasyPrint** for PDF generation to ensure robust rendering of Indian languages and complex scripts.

- **Approach**: HTML + CSS --> PDF.
- **Font Stack**: Noto Sans (Malayalam, Tamil, Devanagari, etc.) is embedded via `@font-face`.
- **Text Shaping**: Uses **Pango** (system library) for correct ligature rendering (unlike ReportLab's limited support).

## 5. Deployment View

Top-level deployment using Docker Compose.

- **API Container**:
    - Python 3.11 Slim
    - Dependencies: `fastapi`, `uvicorn`, `weasyprint`, `google-genai`.
    - System Libs: `libpango-1.0-0`, `libgobject-2.0-0` (for PDF generation).
- **UI Container**:
    - Streamlit (runs on port 8501).
    - Talks to API via internal Docker network (`http://api:8000`).
- **Storage**:
    - `/app/interactive_courses_data`: Shared volume for persistence.

## 6. Data Strategy: Caching & Reuse

The system implements a **Two-Layer Caching Strategy** to minimize external API calls and latency.

### Layer 1: Content Cache (File System)
*   **Goal**: Avoid re-downloading gigabytes of PDF/Video content.
*   **Mechanism**:
    *   Courses are stored in `interactive_courses_data/{course_id}`.
    *   **Logic**: Before fetching, the worker checks if `metadata.json` exists in the target folder.
    *   **Reuse**: If found, the download phase is **skipped entirely**, and the system reuses the local files.
    *   **Structure**:
        ```text
        /app/interactive_courses_data/
        ├── do_1139... (Course A)
        │   ├── metadata.json
        │   ├── module_1/
        │   │   ├── handout.pdf
        │   │   └── intro_video/
        │   │       └── en/transcript.vtt
        └── do_1140... (Course B)
        ```

### Layer 2: Result Cache (Database)
*   **Goal**: Return instant results for identical requests (same course + same parameters).
*   **Mechanism**:
    *   A **Composite Hash** is generated for every request:
        `Job_ID = {Sorted_Course_IDs}_{MD5(Params)}`
    *   Params included in hash: `difficulty`, `question_counts`, `prompt_version`, `blooms_distribution`, `inputs`.
    *   **Reuse**: If a job with this ID exists and is `COMPLETED`, the JSON payload is fetched directly from Postgres. No LLM call is made.

## 7. Database Design (PostgreSQL)

The system persists assessment states and results in a PostgreSQL database using the `asyncpg` driver.

> **Note**: This single table acts as both the **Progress Tracker** (polled by the UI) and the **Result Store**.

### Schema: `interactive_assessments`
| Column | Type | Description |
| :--- | :--- | :--- |
| `course_id` | `TEXT` | **Primary Key**. The Composite Job ID (Hash). |
| `status` | `TEXT` | `PENDING`, `IN_PROGRESS`, `COMPLETED`, `FAILED`. |
| `metadata` | `JSONB` | Input parameters used for generation (audit trail). |
| `assessment_data` | `JSONB` | The full generated assessment JSON structure (Result). |
| `token_usage` | `JSONB` | LLM token consumption stats (cost tracking). |
| `created_at` | `TIMESTAMP` | Record creation time. |
| `updated_at` | `TIMESTAMP` | Last status update time. |
| `error_message` | `TEXT` | Nullable. Error stack trace if failed. |

## 8. API Specification

### 8.1 Input Structure (`POST /generate`)
The API accepts `multipart/form-data` to handle both metadata and file uploads.
*   `course_ids`: List of Strings (Optional if standalone).
*   `assessment_type`: Enum (`final`, `practice`, `comprehensive`, `standalone`).
*   `files`: List of Binary Files (PDF/VTT).
*   `blooms_config`: JSON String (e.g., `{"Analyze": 40, "Apply": 30}`).
*   `question_type_counts`: JSON String (e.g., `{"mcq": 5, "ftb": 2}`).

### 8.2 Output Structure (JSON)
The generated assessment follows a strict schema enforced by the LLM.

```json
{
  "blueprint": {
    "assessment_scope_summary": "Summary of covered topics...",
    "courses_covered": ["Course A", "Course B"],
    "unified_competency_map": {
      "functional": ["Project Management", "Agile"],
      "behavioral": ["Teamwork"]
    },
    "smart_learning_objectives": ["..."],
    "blooms_taxonomy_mapping": {"Analyze": "40%", ...}
  },
  "questions": {
    "Multiple Choice Question": [
      {
        "question_id": "UUID-1",
        "question_text": "...",
        "options": [{"text": "A", "index": 0}, ...],
        "correct_option_index": 2,
        "reasoning": {
            "learning_objective_alignment": "...",
            "competency_alignment": { "kcm": { "competency_area": "..." } },
            "blooms_level_justification": "...",
            "relevance_percentage": 95
        }
      }
    ],
    "FTB Question": [...],
    "MTF Question": [...],
    "True/False Question": [...]
  }
}

### 8.3 Polling & Retrieval Endpoints
Since generation can take 30-60 seconds, the client must poll for completion.

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/api/v1/status/{job_id}` | `GET` | Returns `{"status": "IN_PROGRESS"}` or `COMPLETED`. |
| `/api/v1/download_json/{job_id}` | `GET` | Download raw JSON result (once COMPLETED). |
| `/api/v1/download_csv/{job_id}` | `GET` | Download as CSV (Excel compatible). |
| `/api/v1/download_pdf/{job_id}` | `GET` | Download formatted PDF (uses WeasyPrint). |
| `/api/v1/download_docx/{job_id}` | `GET` | Download Word Document. |
```
