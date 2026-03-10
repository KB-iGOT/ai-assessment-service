# Course Assessment Generation POC

An advanced, audit-ready assessment generation system powered by **Google Gemini 2.5 Pro**, FastAPI, and Streamlit. This POC follows Senior Instructional Designer logic to generate blueprints and questions with detailed pedagogic reasoning.

## Features
- **Model**: Powered by `gemini-2.5-pro`.
- **V2 Smart Architecture**: Authentication, Clone-on-Request (Instant Results), and Private Instances.
- **Decoupled Worker**: Event-Driven Architecture splitting API (Producer) and Worker (Consumer) via Kafka.
- **Event-Driven**: Publishes `ASSESSMENT_COMPLETED` events and consumes `ASSESSMENT_REQUESTED`.
- **3 Assessment Types**: Practice (Reinforcement), Final (Certification), and Comprehensive (Cross-course).
- **5 Question Types**: MCQ, Fill-in-the-Blank (FTB), Match-the-Following (MTF), Multi-Choice, and True/False.
- **Multilingual Support**: Supports 10+ Indian languages (Hindi, Tamil, Telugu, etc.).
- **KCM Alignment**: Maps questions strictly to the **Karmayogi Competency Model** (Behavioral/Functional).
- **Explainable-AI**: Every question comes with alignment reasoning (Learning Objectives, KCM Competencies, Bloom's Justification).
- **Dual Logging**: Console and native file logging (`logs/api.log` & `logs/worker.log`).
- **Exportable Results**: Download assessments in structured JSON or flattened CSV (V2 Schema) formats.

---

## Getting Started

### Prerequisites
- Python 3.10+
- [Optional] Docker & Docker Compose
- Google Cloud Project with Vertex AI / GenAI API enabled.
- Karmayogi API Key.

### Initial Setup
1. Clone this repository to your local machine.
2. Create a `.env` file in the root directory (see `DEPLOYMENT.md`).
3. Place your Google Application Credentials JSON file in the root as `credentials.json`.

### Method 1: Docker (Recommended)
Launch the entire stack (Database + API + UI + Kafka) with one command:
```bash
docker-compose up --build
```
> **Note**: If `docker-compose` is missing, use the local binary `./docker-compose`.

- **API**: http://localhost:8000
- **UI**: http://localhost:8501
- **Kafka**: localhost:29092

### Method 2: Hybrid Run (Local API + Docker Infra)
1. **Start Infra (Kafka, Zookeeper, DB)**:
   ```bash
   ./docker-compose up -d db kafka zookeeper
   ```
2. **Start API (Producer)**:
   ```bash
   export PYTHONPATH=$PYTHONPATH:$(pwd)/src
   uv run uvicorn assessment.api:app --reload
   ```
3. **Start Worker (Consumer)**:
   ```bash
   export PYTHONPATH=$PYTHONPATH:$(pwd)/src
   uv run python -m assessment.worker_service
   ```
4. **Start the UI**:
   ```bash
   export PYTHONPATH=$PYTHONPATH:$(pwd)/src
   uv run streamlit run ui/app.py
   ```

---

## 📚 API Reference (v2.0) - **Recommended**

The V2 API is the robust, event-driven iteration of the assessment generator. It introduces **Authentication**, **Private Worksapces**, and Instant **Cloning**.

- **Base URL**: `http://localhost:8000/ai-assment-generation/api/v2`
- **Authentication**: All V2 endpoints require authentication.
  - **Primary**: Pass the JWT in the `x-auth-token` header.
  - **Fallback (Downloads)**: For browser-based file downloads where headers cannot be modified, pass the token as a URL query parameter: `?token=<jwt>`

### 1. Generate Assessment (Async)
- **Endpoint**: `POST /generate` (Multipart/Form-Data)
- **Description**: Starts an event-driven generation job or instantly clones an existing one. Requires multipart form-data so that files or text fields can be uploaded.
- **Key Parameters (Form Data)**:
  - `course_ids` (String): Comma-separated list of IDs of courses to process (e.g. `do_1,do_2`).
  - `assessment_type` (Enum): `practice`, `final`, `comprehensive`, `standalone`.
  - `difficulty` (Enum): `beginner`, `intermediate`, `advanced`.
  - `language` (Enum): `english`, `hindi`, `tamil`, `telugu`, etc.
  - `total_questions` (Integer): The total number of questions to generate.
  - `question_type_counts` (JSON String): A JSON dict specifying exact counts per type. E.g., `{"mcq": 5, "ftb": 5, "mtf": 5, "multichoice": 0, "truefalse": 0}`.
  - `force` (Boolean String): `"true"` or `"false"`. If `"true"`, bypasses the cache and forces a new LLM generation (costs tokens).
  - `enable_blooms` (Boolean String): `"true"` or `"false"`. Toggles whether Bloom's Taxonomy cognitive levels should be enforced.
  - `blooms_config` (JSON String, Optional): If `enable_blooms` is true, this dictates the exact percentage distribution. Keys must map exactly to: `{"Remember": 20, "Understand": 20, "Apply": 20, "Analyze": 20, "Evaluate": 10, "Create": 10}`. Must sum to 100%.
  - `course_weightage` (JSON String, Optional): ONLY for `comprehensive` assessments, define percentage weights per course ID. E.g., `{"do_course1": 60, "do_course2": 40}`. Must sum to 100%.
  - `time_limit` (Integer, Optional): Duration in minutes. Short duration shifts questions to recall/understand, long duration shifts to analyze/apply.
  - `files` (File Upload, Optional): PDF or VTT files for context injection.
- **Response**: 
  - `202 Accepted` (Status: `PENDING`) -> A new background worker job has started.
  - `200 OK` (Status: `COMPLETED`) -> Cache hit. Instantly cloned into the user's workspace.

### 2. Check Status
- **Endpoint**: `GET /status/{job_id}`
- **Description**: Poll for job progress.
- **Response**: Returns status (`PENDING`, `IN_PROGRESS`, `COMPLETED`, `FAILED`). When `COMPLETED`, the response includes the full `assessment_data` object.

### 3. Update / Edit Assessment
- **Endpoint**: `PUT /assessment/{job_id}`
- **Description**: Allows the owner to manually edit the generated questions before finalizing.
- **Body**: `{"assessment_data": { ... }}`

### 4. Fetch User History
- **Endpoint**: `GET /history`
- **Description**: Returns a clean listing of all previous assessments initiated by the authenticated user.
- **Response**: Array of objects containing `job_id`, `status`, `created_at`, `updated_at`, and `config` metadata.

### 5. Download Results
All download endpoints return the actual generated file. They support the `?token=` query parameter.
- **V2 Output Schema (CSV)**: `GET /download_csv/{job_id}` (Returns the robust 7-Option format)
- **Standard JSON**: `GET /download_json/{job_id}`
- **PDF Export**: `GET /download_pdf/{job_id}`
- **Word (DOCX) Export**: `GET /download_docx/{job_id}`

---

### Legacy V1 API
- Still available at `/api/v1/...` for backward compatibility.
- Does not support cloning, editing, or user separation.

---

## UI Integration Guide (Custom Frontends)

### v3.2 Update: Strict Validation
The API now enforces strict Enum values for `assessment_type`, `difficulty`, and `language`. Do not send free text (e.g., sending "Hard" instead of "Advanced" will fail).

### Multi-Course (Comprehensive) Logic
For `Comprehensive` assessments:
1. Pass multiple IDs in `course_ids` (e.g. `do_1,do_2`).
2. The system generates a deterministic `job_id` (e.g., `comprehensive_do_1_do_2`).
3. If you call `/generate` again with the same IDs, it will return the **existing** job immediately unless `force=true`.

| Status | Suggested UI Action |
| :--- | :--- |
| `PENDING` | Show "Queued" or "Initializing" message. |
| `IN_PROGRESS` | Show "Analyzing Content & Generating Assessment..." (Spinner). |
| `COMPLETED` | Hide loader, parse `assessment_data`, and render. |
| `FAILED` | Show error message from the `content` field. |

### 3. Parsing the Response
The `assessment_data` field contains a JSON string (or object) with two main branches:

#### **`blueprint`**
Use this to display the "Audit" or "Design rationale" to the user.
- Fields: `assessment_scope_summary`, `smart_learning_objectives` (Array of Strings), `unified_competency_map`, `time_appropriateness_validation`.

#### **`questions`**
Contains arrays for each generated question type (e.g. `Multiple Choice Question`, `FTB Question`, `MTF Question`, `True/False Question`).
- **Course Tagging**: Every question includes a `course_name` property designating the source course (visible as `QuestionTagging` in CSV exports, especially utilized in `comprehensive` assessments).
- **Answer Rationale**: Every question has an `answer_rationale` object containing:
  - `correct_answer_explanation`
  - `why_factor`
  - `logic_justification`
- **Reasoning**: Every question has a `reasoning` object containing:
  - `learning_objective_alignment`: **[NEW]** An exact string match to one of the Learning Objectives found in the course metadata. Frontends should display this in their reasoning/explanation UI blocks.
  - `competency_alignment`: Nested object with `kcm` (area, theme, sub_theme) and `domain`.
  - `blooms_level_justification`
  - `relevance_percentage`: 0-100 score.
- **MTF Note**: `MTF Question` objects use `matching_context` instead of `question_text`.

### 4. Direct Downloads (V2)
All V2 download links require you to either send the `x-auth-token` header or append `?token=<jwt>` to the URL for native browser downloading. 
- `http://api-url/ai-assment-generation/api/v2/download_csv/{job_id}?token=<jwt>` (CSV)
- `http://api-url/ai-assment-generation/api/v2/download_json/{job_id}?token=<jwt>` (JSON)
- `http://api-url/ai-assment-generation/api/v2/download_pdf/{job_id}?token=<jwt>` (PDF)
- `http://api-url/ai-assment-generation/api/v2/download_docx/{job_id}?token=<jwt>` (Word)

---

## Core Governance Rules
- Hallucination is strictly avoided by anchoring prompts to extracted transcripts and PDFs.
- **Learning Objective Tagging**: The LLM parses HTML `instructions` from the Karmayogi API into discrete Learning Objectives, and is strictly prompted to tag every single question with the exact verbatim string of the objective it answers.
- **KCM Mapping**: All competencies must be sourced from the authoritative KCM Dataset.
- **Context Caching (Scale)**: The system automatically provisions a Gemini Context Cache for the 110 complete Behavioral and Functional KCM Competencies (including detailed descriptors and levels). This allows the LLM to process and map exact behavioral indicators against questions with near-zero latency overhead and dramatically reduced token costs per request.
- All output text (objectives, questions, reasoning) is generated in the selected language.

## 🔄 Integration Workflow (Async Handling)

Since assessment generation is a long-running process (LLM latency + file processing), the API is designed to be **Asynchronous**.

### Step 1: Start Generation
Call the `POST /generate` endpoint.
- **Request**: Form data with course IDs and config.
- **Response**: Immediate returns with a `job_id`.
```json
{
  "message": "Generation started",
  "status": "PENDING",
  "job_id": "comprehensive_do_123_do_456"
}
```

### Step 2: Poll for Status
Use the `job_id` to poll the status every 5-10 seconds.
**GET** `{{BASE_URL}}/api/v1/status/{job_id}`

**Response Examples:**
- **In Progress**:
  ```json
  { "status": "IN_PROGRESS", "job_id": "comprehensive_do_123_do_456" }
  ```
  _UI Action: Show a loading spinner or progress bar._

- **Completed**:
  ```json
  { 
    "status": "COMPLETED", 
    "job_id": "comprehensive_do_123_do_456", 
    "assessment_data": { 
      "blueprint": {...}, 
      "questions": {...} 
    }
  }
  ```
  _UI Action: Stop polling. Render content from `assessment_data`._

- **Failed**:
  ```json
  { "status": "FAILED", "error": "Reason..." }
  ```
  _UI Action: Show error message._

### Step 3: Retrieve Results
Once status is `COMPLETED`, the response from `GET /status/{job_id}` will contain the full `assessment_data` JSON. This can be used to **display** the questions in the frontend immediately.

- **For Display**: Use the `assessment_data` field from the `/status` response.
- **For Download (CSV)**: Call `GET {{BASE_URL}}/api/v1/download_csv/{job_id}` to get the CSV file.
- **For Download (JSON)**: Call `GET {{BASE_URL}}/api/v1/download_json/{job_id}` to get the JSON file.

## 📚 API Reference (v1.0)

Base URL: `http://localhost:8000/ai-assment-generation`

### 1. Health Check
- **Endpoint**: `GET /health`
- **Description**: Verify service availability.
- **Response**: `{"status": "healthy", ...}`

### 2. Generate Assessment
- **Endpoint**: `POST /api/v1/generate` (Multipart/Form-Data)
- **Description**: Start an async generation job.
- **Key Parameters**:
  - `course_ids` (List[str]): IDs of courses to process.
  - `assessment_type` (Enum): `practice`, `final`, `comprehensive`.
  - `time_limit` (int): Duration in minutes.
  - `blooms_config` (JSON str): Optional Bloom's % map.
- **Response**: `{"status": "PENDING", "job_id": "comprehensive_do_123..."}`

### 3. Check Status
- **Endpoint**: `GET /api/v1/status/{job_id}`
- **Description**: Poll for job progress.
- **Response**: Returns status (`IN_PROGRESS`, `COMPLETED`, `FAILED`).
- **Note**: When `COMPLETED`, the JSON response includes the full `assessment_data` object, which can be used to render the results UI directly.

### 4. Download Results
- **Endpoint (CSV)**: `GET /api/v1/download_csv/{job_id}`
- **Endpoint (JSON)**: `GET /api/v1/download_json/{job_id}`
- **Endpoint (PDF)**: `GET /api/v1/download_pdf/{job_id}`
- **Endpoint (DOCX)**: `GET /api/v1/download_docx/{job_id}`
- **Description**: Download the assessment as a file. Only available when status is `COMPLETED`.

---

## 💻 Developer Integration Examples

To help frontend UI developers rapidly integrate the V2 APIs, here are ready-to-use cURL commands and a sample of the JSON structure that will be returned upon completion.

### Example 1: Standard Assessment (Course specific)
A standard request targeting a single course (e.g., `do_1144540583527301121908`), with 10 total questions and custom Bloom's distribution.
```bash
curl --location 'http://localhost:8000/ai-assment-generation/api/v2/generate' \\
--header 'x-auth-token: YOUR_JWT_TOKEN_HERE' \\
--form 'course_ids="do_1144540583527301121908"' \\
--form 'assessment_type="practice"' \\
--form 'difficulty="intermediate"' \\
--form 'language="english"' \\
--form 'total_questions="10"' \\
--form 'question_type_counts="{\\"mcq\\": 5, \\"ftb\\": 5, \\"mtf\\": 0, \\"multichoice\\": 0, \\"truefalse\\": 0}"' \\
--form 'enable_blooms="true"' \\
--form 'blooms_config="{\\"Remember\\": 20, \\"Understand\\": 30, \\"Apply\\": 30, \\"Analyze\\": 20, \\"Evaluate\\": 0, \\"Create\\": 0}"' \\
--form 'force="false"'
```

### Example 2: Comprehensive Assessment (Cross-Course)
A complex request merging two courses together. Notice how `course_ids` contains both, and the `course_weightage` JSON dictates that 70% of the questions should come from Course A, and 30% from Course B.
```bash
curl --location 'http://localhost:8000/ai-assment-generation/api/v2/generate' \\
--header 'x-auth-token: YOUR_JWT_TOKEN_HERE' \\
--form 'course_ids="do_courseA123,do_courseB456"' \\
--form 'assessment_type="comprehensive"' \\
--form 'difficulty="advanced"' \\
--form 'language="english"' \\
--form 'total_questions="20"' \\
--form 'question_type_counts="{\\"mcq\\": 10, \\"ftb\\": 5, \\"mtf\\": 5, \\"multichoice\\": 0, \\"truefalse\\": 0}"' \\
--form 'enable_blooms="false"' \\
--form 'course_weightage="{\\"do_courseA123\\": 70, \\"do_courseB456\\": 30}"' \\
--form 'force="false"'
```

### 📄 Expected JSON Output (Snippet)
Once you poll `/api/v2/status/{job_id}` and it hits `COMPLETED`, the `assessment_data` field will contain this structure. You map these fields directly to your frontend UI. Note the `course_name` field indicating where the question was sourced, and the unique `learning_objective_alignment`.

```json
{
  "blueprint": {
    "assessment_scope_summary": "Comprehensive assessment covering...",
    "smart_learning_objectives": [
      "Identify the core mandate and organizational structure of the Department.",
      "Describe the primary functions..."
    ],
    "unified_competency_map": {},
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
        "correct_option_index": 2,
        "answer_rationale": {
          "correct_answer_explanation": "The department strictly oversees regulations...",
          "why_factor": "Essential for compliance.",
          "logic_justification": "Drawn directly from chapter 2."
        },
        "reasoning": {
          "learning_objective_alignment": "Identify the core mandate and organizational structure of the Department.",
          "competency_alignment": {
            "kcm": {
              "competency_area": "Functional",
              "competency_theme": "Financial Acumen",
              "sub_theme": "Regulatory Compliance"
            },
            "domain": "Finance"
          },
          "blooms_level_justification": "Requires recalling foundational mandates.",
          "question_type_rationale": "MCQ is best to select a single mandate among distractors."
        },
        "blooms_level": "Remember",
        "relevance_percentage": 95
      }
    ]
  }
}
```
