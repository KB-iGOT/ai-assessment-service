import os
import shutil
import logging
import json
import pandas as pd
from pathlib import Path
from typing import List, Optional, Union
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form, HTTPException, APIRouter
from fastapi.responses import FileResponse, JSONResponse
from fastapi.openapi.utils import get_openapi
from contextlib import asynccontextmanager

from .config import INTERACTIVE_COURSES_PATH
from .db import init_db, create_job, update_job_status, get_assessment_status, save_assessment_result, find_job_by_prefix, create_completed_job, update_job_result
from .fetcher import fetch_course_data
from .generator import generate_assessment
from .generator import generate_assessment
from .exporters import generate_pdf, generate_docx
from .cleanup import start_cleanup_scheduler, stop_cleanup_scheduler
from .events import send_completion_event, stop_kafka_producer
from .exporters_csv_v2 import generate_csv_v2

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler() # Good for Docker/K8s
    ]
)
logger = logging.getLogger("assessment-api")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Assessment API...")
    try:
        await init_db()
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
    
    # Start Background Scheduler
    start_cleanup_scheduler()
    
    yield
    
    stop_cleanup_scheduler()
    await stop_kafka_producer()
    logger.info("Shutting down Assessment API...")

app = FastAPI(
    title="Course Assessment Generator API (v1.0)",
    description="Audit-ready assessment generation using Gemini 2.5 Pro",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/ai-assment-generation",
    servers=[{"url": "/ai-assment-generation", "description": "Default Server"}],
    docs_url="/docs",
    openapi_url="/openapi.json"
)

# Routers
# Routers
api_v1_router = APIRouter(prefix="/api/v1", tags=["API V1"])

@app.get("/", include_in_schema=False)
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/ai-assment-generation/docs")

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "assessment-generator"}

@api_v1_router.get("/status/{job_id}")
async def check_status(job_id: str):
    status = await get_assessment_status(job_id)
    if not status:
        return JSONResponse(status_code=404, content={"status": "NOT_FOUND"})
    return status

from enum import Enum
from typing import List, Optional, Dict

class AssessmentType(str, Enum):
    PRACTICE = "practice"
    FINAL = "final"
    COMPREHENSIVE = "comprehensive"
    STANDALONE = "standalone"

class Difficulty(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"

class Language(str, Enum):
    ENGLISH = "english"
    HINDI = "hindi"
    TAMIL = "tamil"
    TELUGU = "telugu"
    KANNADA = "kannada"
    MALAYALAM = "malayalam"
    MARATHI = "marathi"
    BENGALI = "bengali"
    GUJARATI = "gujarati"
    PUNJABI = "punjabi"
    ODIA = "odia"
    ASSAMESE = "assamese"

class QuestionType(str, Enum):
    MCQ = "mcq"
    FTB = "ftb"
    MTF = "mtf"
    MULTICHOICE = "multichoice"
    TRUE_FALSE = "truefalse"

@api_v1_router.post("/generate")
async def generate(
    background_tasks: BackgroundTasks,
    course_ids: Optional[List[str]] = Form(None, description="List of Course IDs (or comma-separated string)"),
    force: bool = Form(False),
    assessment_type: AssessmentType = Form(...),
    difficulty: Difficulty = Form(...),
    total_questions: int = Form(5),
    question_type_counts: str = Form(
        '{"mcq": 5, "ftb": 5, "mtf": 5, "multichoice": 5, "truefalse": 5}', 
        description='Default values: {"mcq": 5, "ftb": 5, "mtf": 5, "multichoice": 5, "truefalse": 5}'
    ),
    question_types: List[str] = Form(["mcq", "ftb", "mtf", "multichoice", "truefalse"], description="List of Question Types"),
    time_limit: Optional[int] = Form(None, description="Time limit in minutes"),
    topic_names: Optional[str] = Form("", description="Comma-separated topics"),
    language: Language = Form(Language.ENGLISH),
    blooms_config: Optional[str] = Form(
        '{"Remember": 20, "Understand": 30, "Apply": 30, "Analyze": 10, "Evaluate": 10, "Create": 0}',
        description="JSON string of Bloom's %"
    ),
    additional_instructions: Optional[str] = Form(""),
    files: Optional[List[Union[UploadFile, str]]] = File(None)
):
    # Robust File Handling (Workaround for Swagger UI defaults)
    valid_files = []
    if files:
        for f in files:
            # Check for invalid strings (Swagger UI "string" default)
            # Accept anything else (Starlette UploadFile, FastAPI UploadFile)
            if not isinstance(f, str):
                valid_files.append(f)
    
    files = valid_files

    # Sanitize optional string inputs (Swagger sometimes sends "string" or "")
    if topic_names in ["string", ""]: topic_names = None
    if blooms_config in ["string", ""]: blooms_config = None
    if additional_instructions in ["string", ""]: additional_instructions = None
    
    # Parse List Inputs (Support both List[str] and comma-separated string fallback)
    c_ids = []
    if course_ids:
        for item in course_ids:
            c_ids.extend([c.strip() for c in item.split(",") if c.strip()])
    
    # Validation: Must have Content
    if not c_ids and not valid_files:
        raise HTTPException(status_code=400, detail="Must provide either Course ID(s) or Uploaded Files.")

    q_types = []
    for item in question_types:
        q_types.extend([q.strip().lower() for q in item.split(",") if q.strip()])

    # Validate Question Types
    valid_types = {t.value for t in QuestionType}
    for qt in q_types:
        if qt not in valid_types:
             raise HTTPException(status_code=400, detail=f"Invalid question type: {qt}. Allowed: {valid_types}")

    question_type_counts: Dict[str, int] = json.loads(question_type_counts)
    for qtype, count in question_type_counts.items():
        if qtype not in valid_types:
            raise HTTPException(400, f"Unknown question type: {qtype}")
        if count <= 0:
            raise HTTPException(400, f"Invalid question count for {qtype}")


    t_names = [t.strip() for t in topic_names.split(",")] if topic_names else None
    
    # Parse Bloom's Config
    b_dist = None
    if blooms_config:
        try:
            b_dist = json.loads(blooms_config)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON for blooms_config")

    import hashlib
    # Generate Parameter Hash for Cache Invalidation
    param_list = [
        str(assessment_type),
        str(difficulty),
        str(total_questions),
        str(question_type_counts),
        str(sorted(question_types)), # Sort for consistency
        str(time_limit),
        str(topic_names),
        str(language),
        str(blooms_config),
        str(additional_instructions)
    ]
    if files:
         param_list.extend([f.filename for f in valid_files])

    param_str = "_".join(param_list)
    param_hash = hashlib.md5(param_str.encode()).hexdigest()[:8]

    # Composite Key for Caching (Sorted course IDs + Hash)
    if c_ids:
        sorted_ids = sorted(c_ids)
        base_id = f"comprehensive_{'_'.join(sorted_ids)}" if len(sorted_ids) > 1 else sorted_ids[0]
    else:
        base_id = "custom_upload"
        
    composite_id = f"{base_id}_{param_hash}"

    existing = await get_assessment_status(composite_id)
    if existing and existing['status'] == 'COMPLETED' and not force:
        return {"message": "Assessment already exists", "status": "COMPLETED", "job_id": composite_id}
    
    if existing and existing['status'] == 'IN_PROGRESS':
        return {"message": "Assessment generation in progress", "status": "IN_PROGRESS", "job_id": composite_id}

    await create_job(composite_id)
    
    saved_files = []
    if files:
        # Use first course ID for temp storage OR 'custom_uploads' folder if no course ID
        storage_folder_name = sorted_ids[0] if c_ids else "custom_uploads"
        temp_dir = Path(INTERACTIVE_COURSES_PATH) / storage_folder_name / "uploads"
        temp_dir.mkdir(parents=True, exist_ok=True)
        for file in files:
            file_path = temp_dir / file.filename
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            saved_files.append(file_path)
            logger.info(f"Successfully saved uploaded file: {file.filename} to {file_path}")

    background_tasks.add_task(
        process_course_task, 
        composite_id, 
        c_ids, 
        saved_files, 
        assessment_type, 
        difficulty, 
        total_questions, 
        question_type_counts,
        additional_instructions, 
        language,
        t_names,
        b_dist,
        q_types,
        time_limit
    )
    return {"message": "Generation started", "status": "PENDING", "job_id": composite_id}

async def process_course_task(
    job_id: str, 
    course_ids: List[str],
    extra_files: List[Path], 
    assessment_type: str, 
    difficulty: str, 
    total_questions: int, 
    question_type_counts: Dict[str, int],
    additional_instructions: Optional[str], 
    language: str,
    topic_names: Optional[List[str]],
    blooms_distribution: Optional[Dict[str, int]],
    question_types: List[str],
    time_limit: Optional[int]
):
    try:
        await update_job_status(job_id, "IN_PROGRESS")
        
        base_path = Path(INTERACTIVE_COURSES_PATH)
        
        # Fetch Data for ALL courses
        for cid in course_ids:
            success = await fetch_course_data(cid, base_path)
            if not success:
                logger.warning(f"Failed to fetch content for {cid}, proceeding with available data.")

        # 3. Generate Assessment
        metadata, assessment, usage = await generate_assessment(
            course_ids=course_ids,
            assessment_type=assessment_type, 
            difficulty_level=difficulty, 
            total_questions=total_questions,
            question_type_counts=question_type_counts,
            additional_instructions=additional_instructions,
            input_language=language,
            topic_names=topic_names,
            blooms_distribution=blooms_distribution,
            question_types=question_types,
            time_limit=time_limit,
            extra_files=extra_files
        )
        
        # 4. Save Result
        await save_assessment_result(job_id, metadata, assessment, usage)
        
        # 5. Send Kafka Event
        # We need to retrieve user_id. Since process_course_task is background, 
        # we might need to query it or pass it in. 
        # Optimization: We can pass user_id as an argument or fetch from DB status.
        # For now, let's fetch status again or just pass it to this function?
        # Updating process_course_task signature is cleaner.
        
        # NOTE: job_id here is actually the 'user_job_id' or 'composite_id'.
        # We can extract user_id if we passed it, OR fetch from DB.
        
        # Let's do a quick DB lookup to be safe/clean
        job_data = await get_assessment_status(job_id)
        u_id = job_data.get('user_id') if job_data else "unknown"
        
        await send_completion_event(job_id, u_id, "COMPLETED", {"course_ids": course_ids})
        
    except Exception as e:
        logger.exception(f"Job failed for {job_id}")
        await update_job_status(job_id, "FAILED", str(e))
        # Optional: Send FAILED event?
        # await send_completion_event(job_id, "unknown", "FAILED", {"error": str(e)})

@api_v1_router.get("/download_csv/{job_id}")
async def download_csv(job_id: str):
    data = await get_assessment_status(job_id)
    if not data or data['status'] != 'COMPLETED':
        raise HTTPException(status_code=404, detail="Assessment not ready or found")
    
    assessment_json = json.loads(data['assessment_data']) if isinstance(data['assessment_data'], str) else data['assessment_data']
    
    # Flatten logic for new structure
    rows = []
    questions_obj = assessment_json.get("questions", {})
    
    for q_type, q_list in questions_obj.items():
        for q in q_list:
            row = {
                "Question ID": q.get("question_id"),
                "Type": q_type,
                "Text": q.get("question_text", "N/A"),
                "Options/Pairs": json.dumps(q.get("options") or q.get("pairs") or "", ensure_ascii=False),
                "Correct Answer": (
                    ",".join(map(str, q.get("correct_option_index"))) if isinstance(q.get("correct_option_index"), list)
                    else q.get("correct_option_index") if q.get("correct_option_index") is not None 
                    else q.get("correct_answer")
                ),
                "Blooms Level": q.get("blooms_level"),
                "Difficulty": q.get("difficulty_level"),
                "Relevance %": q.get("relevance_percentage")
            }
            rows.append(row)
        
    df = pd.DataFrame(rows)
    csv_path = Path(INTERACTIVE_COURSES_PATH) / f"{job_id}_assessment.csv"
    df.to_csv(csv_path, index=False)
    
    return FileResponse(csv_path, filename=f"{job_id}_assessment.csv")

@api_v1_router.get("/download_json/{job_id}")
async def download_json(job_id: str):
    data = await get_assessment_status(job_id)
    if not data or data['status'] != 'COMPLETED':
        raise HTTPException(status_code=404, detail="Assessment not ready or found")
    
    assessment_json = json.loads(data['assessment_data']) if isinstance(data['assessment_data'], str) else data['assessment_data']
    
    json_path = Path(INTERACTIVE_COURSES_PATH) / f"{job_id}_assessment.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(assessment_json, f, indent=2, ensure_ascii=False)
        
    return FileResponse(json_path, filename=f"{job_id}_assessment.json", media_type='application/json')

@api_v1_router.get("/download_pdf/{job_id}")
async def download_pdf(job_id: str):
    data = await get_assessment_status(job_id)
    if not data or data['status'] != 'COMPLETED':
        raise HTTPException(status_code=404, detail="Assessment not ready or found")
    
    assessment_json = json.loads(data['assessment_data']) if isinstance(data['assessment_data'], str) else data['assessment_data']
    pdf_path = Path(INTERACTIVE_COURSES_PATH) / f"{job_id}_assessment.pdf"
    
    # Remove caching check to ensure font fixes are applied
    # if not pdf_path.exists():
    generate_pdf(assessment_json, pdf_path)
        
    return FileResponse(pdf_path, filename=f"{job_id}_assessment.pdf", media_type='application/pdf')

@api_v1_router.get("/download_docx/{job_id}")
async def download_docx(job_id: str):
    data = await get_assessment_status(job_id)
    if not data or data['status'] != 'COMPLETED':
        raise HTTPException(status_code=404, detail="Assessment not ready or found")
    
    assessment_json = json.loads(data['assessment_data']) if isinstance(data['assessment_data'], str) else data['assessment_data']
    docx_path = Path(INTERACTIVE_COURSES_PATH) / f"{job_id}_assessment.docx"
    
    if not docx_path.exists():
        generate_docx(assessment_json, docx_path)
        
    return FileResponse(docx_path, filename=f"{job_id}_assessment.docx", media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

# ==========================================
# API V2 Router (Authenticated & Optimized)
# ==========================================
from fastapi import Depends
from .auth import get_current_user

api_v2_router = APIRouter(prefix="/api/v2", tags=["API V2"])

@api_v2_router.post("/generate")
async def generate_v2(
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user), # AUTH REQUIREMENT
    course_ids: Optional[List[str]] = Form(None, description="List of Course IDs"),
    force: bool = Form(False),
    assessment_type: AssessmentType = Form(...),
    difficulty: Difficulty = Form(...),
    total_questions: int = Form(5),
    question_type_counts: str = Form(
        '{"mcq": 5, "ftb": 5, "mtf": 5, "multichoice": 5, "truefalse": 5}', 
        description='Default values: {"mcq": 5, "ftb": 5, "mtf": 5, "multichoice": 5, "truefalse": 5}'
    ),
    question_types: List[str] = Form(["mcq", "ftb", "mtf", "multichoice", "truefalse"], description="List of Question Types"),
    time_limit: Optional[int] = Form(None),
    topic_names: Optional[str] = Form(""),
    language: Language = Form(Language.ENGLISH),
    blooms_config: Optional[str] = Form(
        '{"Remember": 20, "Understand": 30, "Apply": 30, "Analyze": 10, "Evaluate": 10, "Create": 0}',
        description="JSON string of Bloom's %"
    ),
    additional_instructions: Optional[str] = Form(""),
    files: Optional[List[Union[UploadFile, str]]] = File(None)
):
    """
    V2 Generation Endpoint:
    1. Authenticated: Requires valid `x-auth-token` (User ID extraction).
    2. Private Instances: Every request gets a unique Job ID: `{Hash}_{UserID}`.
    3. Clone-on-Request: If a matching assessment exists (even from another user), it is instantly CLONED to this user's workspace.
    4. Async/Sync Hybrid: 
       - Returns 200 OK + JSON if cache hit/cloned.
       - Returns 202 Accepted if new generation started.
    """
    
    # --- 1. Validation & Logic Reuse (Same as V1) ---
    valid_files = []
    if files:
        for f in files:
            if not isinstance(f, str):
                valid_files.append(f)
    files = valid_files

    if topic_names in ["string", ""]: topic_names = None
    if blooms_config in ["string", ""]: blooms_config = None
    if additional_instructions in ["string", ""]: additional_instructions = None
    
    c_ids = []
    if course_ids:
        for item in course_ids:
            c_ids.extend([c.strip() for c in item.split(",") if c.strip()])
    
    if not c_ids and not valid_files:
        raise HTTPException(status_code=400, detail="Must provide either Course ID(s) or Uploaded Files.")

    q_types = []
    for item in question_types:
        q_types.extend([q.strip().lower() for q in item.split(",") if q.strip()])

    valid_types = {t.value for t in QuestionType}
    for qt in q_types:
        if qt not in valid_types:
             raise HTTPException(status_code=400, detail=f"Invalid question type: {qt}. Allowed: {valid_types}")

    q_counts: Dict[str, int] = json.loads(question_type_counts)
    for qtype, count in q_counts.items():
        if qtype not in valid_types:
            raise HTTPException(400, f"Unknown question type: {qtype}")

    t_names = [t.strip() for t in topic_names.split(",")] if topic_names else None
    
    b_dist = None
    if blooms_config:
        try:
            b_dist = json.loads(blooms_config)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON for blooms_config")

    # --- 2. Hashing (Same logic) ---
    import hashlib
    param_list = [
        str(assessment_type), str(difficulty), str(total_questions),
        str(q_counts), str(sorted(question_types)), str(time_limit),
        str(topic_names), str(language), str(blooms_config), str(additional_instructions)
    ]
    if files:
         param_list.extend([f.filename for f in valid_files])

    param_str = "_".join(param_list)
    param_hash = hashlib.md5(param_str.encode()).hexdigest()[:8]

    if c_ids:
        sorted_ids = sorted(c_ids)
        base_id = f"comprehensive_{'_'.join(sorted_ids)}" if len(sorted_ids) > 1 else sorted_ids[0]
    else:
        base_id = "custom_upload"
        
    composite_id = f"{base_id}_{param_hash}" # This is the "Shared Signature"
    
    # V2 Logic: User-Specific IDs to allow private editing
    # Format: {Shared_Signature}_{User_ID}
    # But we fallback to Shared_Signature lookup for cache hits
    
    user_job_id = f"{composite_id}_{user_id}"

    # --- 3. Check Status (V2 Logic: Clone or Generate) ---
    
    # A. Check if THIS user already has this job
    existing_own = await get_assessment_status(user_job_id)
    if existing_own and not force:
         status = existing_own['status']
         if status == 'COMPLETED':
             result = json.loads(existing_own['assessment_data']) if isinstance(existing_own['assessment_data'], str) else existing_own['assessment_data']
             return {
                "message": "Assessment retrieved from cache", 
                "status": "COMPLETED", 
                "job_id": user_job_id,
                "result": result 
            }
         elif status == 'IN_PROGRESS':
             return {"message": "Assessment generation in progress", "status": "IN_PROGRESS", "job_id": user_job_id}

    # B. If not found (or forced), check if a TEMPLATE exists (Shared Cache)
    if not force:
        template = await find_job_by_prefix(composite_id)
        if template:
            # CLONE IT!
            logger.info(f"Cloning template {template['course_id']} for user {user_id}")
            
            t_meta = json.loads(template['metadata']) if isinstance(template['metadata'], str) else template['metadata']
            t_data = json.loads(template['assessment_data']) if isinstance(template['assessment_data'], str) else template['assessment_data']
            t_usage = json.loads(template['token_usage']) if isinstance(template['token_usage'], str) else template['token_usage']
            
            await create_completed_job(user_job_id, user_id, t_meta, t_data, t_usage)
            
            return {
                "message": "Assessment cloned from cache", 
                "status": "COMPLETED", 
                "job_id": user_job_id,
                "result": t_data 
            }

    # --- 4. Start New Job ---
    # Store with User Specific ID
    await create_job(user_job_id, user_id=user_id)
    
    # Save files
    saved_files = []
    if files:
        storage_folder_name = sorted_ids[0] if c_ids else "custom_uploads"
        temp_dir = Path(INTERACTIVE_COURSES_PATH) / storage_folder_name / "uploads"
        temp_dir.mkdir(parents=True, exist_ok=True)
        for file in files:
            file_path = temp_dir / file.filename
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            saved_files.append(file_path)

    background_tasks.add_task(
        process_course_task, 
        user_job_id, c_ids, saved_files, assessment_type, difficulty, 
        total_questions, q_counts, additional_instructions, language,
        t_names, b_dist, q_types, time_limit
    )
    
    return {"message": "Generation started", "status": "PENDING", "job_id": user_job_id}

@api_v2_router.get("/status/{job_id}")
async def check_status_v2(job_id: str, user_id: str = Depends(get_current_user)):
    status = await get_assessment_status(job_id)
    if not status:
        return JSONResponse(status_code=404, content={"status": "NOT_FOUND"})
    
    # Strict Access Control
    # if status.get('user_id') and status.get('user_id') != user_id:
    #    raise HTTPException(403, "Access Denied")
        
    return status

from pydantic import BaseModel
class AssessmentUpdate(BaseModel):
    assessment_data: Dict

@api_v2_router.put("/assessment/{job_id}")
async def update_assessment(
    job_id: str, 
    payload: AssessmentUpdate, 
    user_id: str = Depends(get_current_user)
):
    """
    Updates the assessment result.
    Enforces that the user owns the assessment.
    """
    success = await update_job_result(job_id, user_id, payload.assessment_data)
    
    if not success:
        # Ambiguous: could be 404 Not Found or 403 Forbidden
        # But generally means "Resource invalid for this user"
        raise HTTPException(status_code=404, detail="Assessment not found or you do not have permission to edit it")
        
    return {"message": "Assessment updated successfully", "status": "COMPLETED", "job_id": job_id}

@api_v2_router.get("/download_csv/{job_id}")
async def download_csv_v2(job_id: str, user_id: str = Depends(get_current_user)):
    """
    V2 Export: Returns CSV in the specific 7-Option Column format.
    """
    data = await get_assessment_status(job_id)
    if not data or data['status'] != 'COMPLETED':
        raise HTTPException(status_code=404, detail="Assessment not ready or found")
        
    # Check Ownership? For downloads, maybe strictness is good.
    # if data.get('user_id') != user_id: raise HTTPException(403)
    
    assessment_json = json.loads(data['assessment_data']) if isinstance(data['assessment_data'], str) else data['assessment_data']
    csv_path = Path(INTERACTIVE_COURSES_PATH) / f"{job_id}_assessment_v2.csv"
    
    # Always generate fresh to ensure logic update
    generate_csv_v2(assessment_json, csv_path)
        
    return FileResponse(csv_path, filename=f"{job_id}_assessment_v2.csv", media_type='text/csv')


app.include_router(api_v1_router)
app.include_router(api_v2_router)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    # WORKAROUND: Force 'files' to be binary array in Docs
    # ----------------------------------------------------
    try:
        paths = openapi_schema.get("paths", {})
        for path, methods in paths.items():
            if path.endswith("/generate"):
                post = methods.get("post", {})
                content = post.get("requestBody", {},).get("content", {})
                multipart = content.get("multipart/form-data", {})
                schema = multipart.get("schema", {})
                
                # Check if schema is a reference
                if "$ref" in schema:
                    ref_name = schema["$ref"].split("/")[-1]
                    schema = openapi_schema.get("components", {}).get("schemas", {}).get(ref_name, {})
                
                properties = schema.get("properties", {})
                
                # Force File Picker Override
                properties["files"] = {
                    "type": "array",
                    "items": {"type": "string", "format": "binary"},
                    "title": "Files",
                    "description": "Upload Files"
                }
                logger.info(f"Forced 'files' schema override for path: {path}")
    except Exception as e:
        logger.warning(f"Failed to patch OpenAPI schema: {e}")

    app.openapi_schema = openapi_schema
    return openapi_schema

app.openapi = custom_openapi
