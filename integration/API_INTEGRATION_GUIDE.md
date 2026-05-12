# AI Assessment Service — API Integration Guide

This document covers all endpoints, request parameters, response formats, and end-to-end integration flows for the AI Assessment Generation Service.

## Postman Collections

Import the appropriate collection based on your access method:

| Collection | Access Method | Link |
|---|---|---|
| Kong (API / server-to-server) | Requires `x-authenticated-user-token` + Kong JWT | [postman_collection_kong.json](<GITHUB_LINK_TO_postman_collection_kong.json>) |
| UI Proxy (browser / frontend) | Requires session cookie only | [postman_collection_proxy.json](<GITHUB_LINK_TO_postman_collection_proxy.json>) |

---

## Base URLs

| Environment | Access Method | Base URL |
|---|---|---|
| UAT | Kong API Gateway | `https://portal.uat.karmayogibharat.net/api/ai/assessments/v1` |
| UAT | UI Proxy (cookie) | `https://portal.uat.karmayogibharat.net/apis/proxies/v8/ai/assessments/v1` |
| Local | Direct | `http://localhost:8000/ai-assessments/v1` |

---

## Authentication

### Kong (API / server-to-server)
Two headers are required:

| Header | Value |
|---|---|
| `x-authenticated-user-token` | Keycloak JWT obtained after user login |
| `Authorization` | `bearer <kong_jwt_credential>` |

### UI Proxy (browser / frontend)
Only a session cookie is required — no tokens:

| Header | Value |
|---|---|
| `cookie` | `connect.sid=<session_cookie>` (set automatically by the browser) |

The proxy reads the session and injects user identity on behalf of the caller. No token handling needed in frontend code.

---

## Endpoints

### 1. Generate Assessment

**`POST /generate`**

Submits an assessment generation request. Returns immediately with a `job_id`. Generation happens asynchronously in the background.

**Cache behaviour:**
- If this user has already generated the exact same assessment (same course + same parameters), returns `COMPLETED` instantly from cache.
- If another user generated the same assessment before, it is cloned instantly to this user and returns `COMPLETED`.
- Otherwise, queues a new generation job and returns `PENDING`.

#### Kong

```bash
curl --location 'https://portal.uat.karmayogibharat.net/api/ai/assessments/v1/generate' \
  --header 'x-authenticated-user-token: <keycloak_jwt>' \
  --header 'Authorization: bearer <kong_jwt_credential>' \
  --form 'course_ids="do_1144540583527301121908"' \
  --form 'assessment_type="practice"' \
  --form 'difficulty="intermediate"' \
  --form 'language="english"' \
  --form 'total_questions="10"' \
  --form 'question_type_counts="{\"mcq\":5,\"ftb\":5,\"mtf\":0,\"multichoice\":0,\"truefalse\":0}"' \
  --form 'enable_blooms="true"' \
  --form 'blooms_config="{\"Remember\":20,\"Understand\":30,\"Apply\":30,\"Analyze\":10,\"Evaluate\":10,\"Create\":0}"' \
  --form 'force="false"' \
  --form 'time_limit="0"'
```

#### UI Proxy

```bash
curl --location 'https://portal.uat.karmayogibharat.net/apis/proxies/v8/ai/assessments/v1/generate' \
  --header 'cookie: connect.sid=<session_cookie>' \
  --form 'course_ids="do_1144540583527301121908"' \
  --form 'assessment_type="practice"' \
  --form 'difficulty="intermediate"' \
  --form 'language="english"' \
  --form 'total_questions="10"' \
  --form 'question_type_counts="{\"mcq\":5,\"ftb\":5,\"mtf\":0,\"multichoice\":0,\"truefalse\":0}"' \
  --form 'enable_blooms="true"' \
  --form 'blooms_config="{\"Remember\":20,\"Understand\":30,\"Apply\":30,\"Analyze\":10,\"Evaluate\":10,\"Create\":0}"' \
  --form 'force="false"' \
  --form 'time_limit="0"'
```

#### Request Parameters

| Parameter | Type | Required | Accepted Values | Description |
|---|---|---|---|---|
| `course_ids` | string | Yes* | Any valid iGOT course ID | Comma-separated course IDs. `*`Required unless uploading files. |
| `assessment_type` | string | Yes | `practice`, `final`, `comprehensive`, `standalone` | Type of assessment to generate. `comprehensive` is used when multiple courses are combined. |
| `difficulty` | string | Yes | `beginner`, `intermediate`, `advanced` | Target difficulty level of questions. |
| `language` | string | Yes | `english`, `hindi`, `tamil`, `telugu`, `kannada`, `malayalam`, `marathi`, `bengali`, `gujarati`, `punjabi`, `odia`, `assamese` | Language for generated questions. |
| `total_questions` | integer | No | Any positive integer | Total number of questions to generate. Default: `5`. |
| `question_type_counts` | JSON string | No | Keys: `mcq`, `ftb`, `mtf`, `multichoice`, `truefalse` | Count per question type. Values must add up to (or be consistent with) `total_questions`. Example: `{"mcq":5,"ftb":5,"mtf":0,"multichoice":0,"truefalse":0}` |
| `enable_blooms` | boolean | No | `true`, `false` | Whether to apply Bloom's taxonomy distribution. Default: `true`. |
| `blooms_config` | JSON string | No | Keys: `Remember`, `Understand`, `Apply`, `Analyze`, `Evaluate`, `Create` | Percentage distribution across Bloom's levels. Values must sum to 100. Example: `{"Remember":20,"Understand":30,"Apply":30,"Analyze":10,"Evaluate":10,"Create":0}` |
| `time_limit` | integer | No | `0` or any positive integer | Time limit in minutes. `0` means no limit. |
| `topic_names` | string | No | Comma-separated topic names | Restrict question generation to specific topics within the course. Leave blank to use all topics. |
| `course_weightage` | JSON string | No | `{"<course_id>": <percent>, ...}` | Weightage per course when `assessment_type` is `comprehensive`. Values must sum to 100. Example: `{"do_1":60,"do_2":40}` |
| `additional_instructions` | string | No | Free text | Any extra instructions passed to the AI model (e.g. "focus on case studies", "avoid numerical questions"). |
| `force` | boolean | No | `true`, `false` | `true` bypasses cache and forces a fresh generation. Default: `false`. |

#### Question Types

| Type | Key | Description |
|---|---|---|
| Multiple Choice (Single) | `mcq` | One correct answer from 4 options |
| Fill in the Blank | `ftb` | Complete a missing word or phrase |
| Match the Following | `mtf` | Match items in two columns |
| Multiple Choice (Multi) | `multichoice` | More than one correct answer from options |
| True / False | `truefalse` | Binary true or false question |

#### Response — New Job (202 Accepted equivalent, returns 200)

```json
{
  "message": "Generation started (Queued)",
  "status": "PENDING",
  "job_id": "do_1144540583527301121908_7fa321bd_1e8b6826-3326-4175-b202-f5f5971f457a"
}
```

#### Response — Cache Hit or Clone

```json
{
  "message": "Assessment retrieved from cache",
  "status": "COMPLETED",
  "job_id": "do_1144540583527301121908_7fa321bd_1e8b6826-3326-4175-b202-f5f5971f457a",
  "result": { ... }
}
```

#### Response Fields

| Field | Description |
|---|---|
| `message` | Human-readable description of what happened |
| `status` | `PENDING` (queued), `IN_PROGRESS` (generating), `COMPLETED` (done), `FAILED` (error) |
| `job_id` | Unique identifier for this assessment. Format: `{course_id}_{param_hash}_{user_id}`. Store this — it is used in all subsequent calls. |
| `result` | Present only on cache hits. Contains the full assessment data (same as Status response when COMPLETED). |

---

### 2. Get Status

**`GET /status/{job_id}`**

Polls the status of a generation job. When `status` becomes `COMPLETED`, the `assessment_data` field contains the full generated assessment.

#### Kong

```bash
curl --location 'https://portal.uat.karmayogibharat.net/api/ai/assessments/v1/status/<job_id>' \
  --header 'x-authenticated-user-token: <keycloak_jwt>' \
  --header 'Authorization: bearer <kong_jwt_credential>'
```

#### UI Proxy

```bash
curl --location 'https://portal.uat.karmayogibharat.net/apis/proxies/v8/ai/assessments/v1/status/<job_id>' \
  --header 'cookie: connect.sid=<session_cookie>'
```

#### Response — In Progress

```json
{
  "job_id": "do_1144540583527301121908_7fa321bd_1e8b6826-3326-4175-b202-f5f5971f457a",
  "status": "IN_PROGRESS",
  "assessment_data": null,
  "error_message": null
}
```

#### Response — Completed

```json
{
  "job_id": "do_1144540583527301121908_7fa321bd_1e8b6826-3326-4175-b202-f5f5971f457a",
  "status": "COMPLETED",
  "assessment_data": {
    "title": "Course Assessment",
    "questions": [
      {
        "id": 1,
        "type": "mcq",
        "question": "What is the primary purpose of ...?",
        "options": ["Option A", "Option B", "Option C", "Option D"],
        "correct_answer": "Option A",
        "explanation": "Option A is correct because ...",
        "bloom_level": "Understand",
        "difficulty": "intermediate",
        "topic": "Introduction"
      }
    ]
  },
  "error_message": null
}
```

#### Response Fields

| Field | Description |
|---|---|
| `job_id` | The assessment job ID |
| `status` | `PENDING`, `IN_PROGRESS`, `COMPLETED`, or `FAILED` |
| `assessment_data` | `null` until complete. On completion, contains `title` and `questions` array. |
| `error_message` | `null` on success. Contains error details if `status` is `FAILED`. |

#### Question Object Fields

| Field | Description |
|---|---|
| `id` | Sequential question number |
| `type` | `mcq`, `ftb`, `mtf`, `multichoice`, or `truefalse` |
| `question` | The question text |
| `options` | List of answer choices (not present for `ftb`) |
| `correct_answer` | The correct answer or answer key |
| `explanation` | Explanation of why the answer is correct |
| `bloom_level` | Bloom's taxonomy level: `Remember`, `Understand`, `Apply`, `Analyze`, `Evaluate`, or `Create` |
| `difficulty` | `beginner`, `intermediate`, or `advanced` |
| `topic` | The topic within the course this question covers |

---

### 3. Get History

**`GET /history`**

Returns all assessments previously generated or cloned by the authenticated user, sorted by most recent first.

#### Kong

```bash
curl --location 'https://portal.uat.karmayogibharat.net/api/ai/assessments/v1/history' \
  --header 'x-authenticated-user-token: <keycloak_jwt>' \
  --header 'Authorization: bearer <kong_jwt_credential>'
```

#### UI Proxy

```bash
curl --location 'https://portal.uat.karmayogibharat.net/apis/proxies/v8/ai/assessments/v1/history' \
  --header 'cookie: connect.sid=<session_cookie>'
```

#### Sample Response

```json
[
  {
    "job_id": "do_113948972799877120197_7fa321bd_1e8b6826-3326-4175-b202-f5f5971f457a",
    "status": "COMPLETED",
    "created_at": "2026-05-08T17:04:31.793723",
    "updated_at": "2026-05-08T20:38:12.732428",
    "config": {
      "language": "english",
      "difficulty": "beginner",
      "time_limit": 0,
      "assessment_type": "practice",
      "total_questions": 5,
      "course_weightage": null,
      "question_type_counts": {
        "ftb": 5,
        "mcq": 5,
        "mtf": 0,
        "truefalse": 0,
        "multichoice": 0
      }
    },
    "error_message": null
  }
]
```

#### Response Fields

| Field | Description |
|---|---|
| `job_id` | Unique assessment ID. Use this to call Status or Download. |
| `status` | `PENDING`, `IN_PROGRESS`, `COMPLETED`, or `FAILED` |
| `created_at` | ISO 8601 timestamp when the job was first created |
| `updated_at` | ISO 8601 timestamp of the last status change |
| `config` | The parameters used when this assessment was generated (language, difficulty, types, etc.) |
| `error_message` | `null` on success. Error detail if `status` is `FAILED`. |

---

### 4. Download Assessment

**`GET /download/{job_id}?format={format}`**

Downloads a completed assessment in the requested file format. Only available when `status` is `COMPLETED`.

#### Kong

```bash
# CSV
curl --location 'https://portal.uat.karmayogibharat.net/api/ai/assessments/v1/download/<job_id>?format=csv' \
  --header 'x-authenticated-user-token: <keycloak_jwt>' \
  --header 'Authorization: bearer <kong_jwt_credential>' \
  --output assessment.csv

# JSON
curl ... ?format=json --output assessment.json

# PDF
curl ... ?format=pdf --output assessment.pdf

# DOCX
curl ... ?format=docx --output assessment.docx
```

#### UI Proxy

```bash
curl --location 'https://portal.uat.karmayogibharat.net/apis/proxies/v8/ai/assessments/v1/download/<job_id>?format=csv' \
  --header 'cookie: connect.sid=<session_cookie>' \
  --output assessment.csv
```

#### Query Parameters

| Parameter | Required | Accepted Values | Description |
|---|---|---|---|
| `format` | Yes | `csv`, `json`, `pdf`, `docx` | Output file format |

#### Response

Returns the file as a binary stream with the appropriate `Content-Type` header. The filename in `Content-Disposition` will be `<job_id>_assessment.<format>`.

---

## End-to-End Integration Flow

### Flow 1: Generate a New Assessment

This is the primary flow. The generation is asynchronous — you submit a job and poll until done.

```
1. POST /generate          → receive job_id + status (PENDING or COMPLETED)
                                        │
                    ┌───────────────────┴───────────────────┐
                    │ status == COMPLETED                    │ status == PENDING / IN_PROGRESS
                    │ (cache hit)                            │
                    ▼                                        ▼
           Use result directly               2. GET /status/{job_id}   ◄──┐
                                                      │                    │
                                         ┌────────────┴──────────┐        │
                                         │ COMPLETED              │ still  │
                                         ▼                        │ waiting├──┘
                                   Show assessment            wait 3–5s,
                                                              poll again
```

**Recommended polling interval:** 3–5 seconds. Most generations complete within 30–90 seconds depending on course size and question count.

**Implementation example (pseudocode):**

```js
// Step 1: Submit
const { job_id, status, result } = await POST('/generate', formData);

if (status === 'COMPLETED') {
  showAssessment(result);
  return;
}

// Step 2: Poll
let assessment = null;
while (!assessment) {
  await sleep(4000);
  const resp = await GET(`/status/${job_id}`);
  if (resp.status === 'COMPLETED') {
    assessment = resp.assessment_data;
  } else if (resp.status === 'FAILED') {
    showError(resp.error_message);
    return;
  }
}

showAssessment(assessment);
```

---

### Flow 2: Show Assessment History

Use this to show a user's previously generated assessments. Each history item has a `job_id` which can be used to re-fetch the full assessment data or download it.

```
1. GET /history                        → list of past jobs with job_id + config + status
2. User selects a COMPLETED job
3. GET /status/{job_id}                → full assessment_data for that job
   OR
   GET /download/{job_id}?format=json  → download as file
```

**Implementation example (pseudocode):**

```js
// Show history list
const history = await GET('/history');
renderHistoryList(history);  // show job_id, created_at, config, status

// On user click — load full assessment
const selected = history[i];
if (selected.status === 'COMPLETED') {
  const detail = await GET(`/status/${selected.job_id}`);
  showAssessment(detail.assessment_data);
}
```

---

## Status Reference

| Status | Meaning |
|---|---|
| `PENDING` | Job is queued, worker has not picked it up yet |
| `IN_PROGRESS` | Worker is actively generating questions |
| `COMPLETED` | Generation finished, `assessment_data` is available |
| `FAILED` | Generation failed, see `error_message` for details |

---

## Error Responses

| HTTP Code | Meaning |
|---|---|
| `400` | Bad request — invalid parameter (e.g. unknown question type, invalid JSON) |
| `401` | Missing or invalid authentication token / session |
| `403` | Authenticated but not authorized — either missing required role or trying to access another user's assessment |
| `404` | Job not found, or assessment not yet completed |
| `500` | Internal server error |

**Error response format:**

```json
{
  "detail": "Human-readable error message"
}
```

---

## Notes

- **Job ID format:** `{course_id}_{param_hash}_{user_id}`. The same user requesting the same course+config will always get the same job ID — cache is automatic.
- **Ownership:** All assessments are user-scoped. Status and Download will return `403` if called with a different user's credentials.
- **Force regeneration:** Pass `force=true` in the Generate request to bypass cache and generate fresh questions.
- **Multi-course assessments:** Pass multiple `course_ids` and set `assessment_type=comprehensive`. Use `course_weightage` to control the proportion of questions per course.
