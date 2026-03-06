import os
import json
import hashlib
import asyncio
import logging
import time
import yaml
import fitz  # PyMuPDF
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List
from google import genai
from google.genai import types
from google.genai.errors import APIError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import (
    DB_DSN,
    GOOGLE_PROJECT_ID, GOOGLE_LOCATION, GENAI_MODEL_NAME, 
    GOOGLE_APPLICATION_CREDENTIALS, PROMPT_VERSION
)

logger = logging.getLogger(__name__)

# Initialize GenAI Client
if GOOGLE_APPLICATION_CREDENTIALS:
    client = genai.Client(
        project=GOOGLE_PROJECT_ID,
        location=GOOGLE_LOCATION,
        vertexai=True
    )
else:
    client = None
    logger.warning("GOOGLE_APPLICATION_CREDENTIALS not set.")

# Load Resources
# Everything is now in the resources/ directory relative to this file
PACKAGE_DIR = Path(__file__).parent
RESOURCE_DIR = PACKAGE_DIR / "resources"

def load_yaml(filename):
    path = RESOURCE_DIR / filename
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

def load_json(filename):
    path = RESOURCE_DIR / filename
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

ASSESSMENT_PROMPTS = load_yaml('prompts.yaml')
ASSESSMENT_SCHEMA_FILE = load_json('schemas.json')
ASSESSMENT_SCHEMA = ASSESSMENT_SCHEMA_FILE.get('full_schema', {})
KCM_DATASET = load_json('competencies.json')

async def generate_assessment(
    question_type_counts: Dict[str, int],
    course_folder: Optional[Path] = None, # Deprecated in v3.2, kept for backward compat
    assessment_type: str = "final", 
    difficulty_level: str = "Intermediate", 
    total_questions: int = 5,
    time_to_complete: Optional[str] = None,
    additional_instructions: Optional[str] = None,
    input_language: str = "English",
    course_ids: List[str] = None,
    topic_names: Optional[List[str]] = None,
    blooms_distribution: Optional[Dict[str, int]] = None,
    enable_blooms: bool = True,
    course_weightage: Optional[str] = None,
    time_limit: Optional[int] = None,
    extra_files: Optional[List[Path]] = None
) -> Tuple[Dict, Dict, Dict]:
    """
    Generates assessment for one or multiple courses.
    Returns (aggregated_metadata, assessment_json, usage_metadata)
    """
    # Normalize inputs
    if not course_ids and course_folder:
        course_ids = [course_folder.name]
    
    # Normalize inputs
    if not course_ids and course_folder:
        course_ids = [course_folder.name]
    
    # if not course_ids:
    #     raise ValueError("No course_ids provided.")

    # Create Deterministic Composite Key for Caching (Sorted IDs)
    if course_ids:
        sorted_ids = sorted(course_ids)
        composite_id = f"comprehensive_{'_'.join(sorted_ids)}" if len(sorted_ids) > 1 else sorted_ids[0]
    else:
        composite_id = "custom_content_generation"
    
    # Base Path for Interactive Courses (should probably be passed in, but using config implied path for now)
    base_path = Path("/app/interactive_courses_data") 
    
    logger.info(f"Generating assessment for {composite_id} (Type: {assessment_type})")

    # 1. Aggregate Content from All Courses
    aggregated_metadata = {"courses": []}
    combined_transcript = []
    combined_pdfs = []
    
    # Deduplication Set (to prevent double-handling of leaf vs root downloads)
    seen_content_hashes = set()

    if course_ids:
        for cid in course_ids:
            c_path = base_path / cid
            if not c_path.exists():
                logger.warning(f"Course folder {cid} not found, skipping.")
                continue
                
            # Metadata
            meta_path = c_path / "metadata.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
                aggregated_metadata["courses"].append(meta)
                
            # Transcript (Recursive - find all english_subtitles.vtt in subfolders)
            for vtt_path in c_path.rglob("english_subtitles.vtt"):
                 try:
                     text = await extract_vtt_text(vtt_path)
                     if not text: continue
                     
                     # Deduplication Check
                     text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
                     if text_hash in seen_content_hashes:
                         logger.info(f"Skipping duplicate VTT content: {vtt_path.name}")
                         continue
                     seen_content_hashes.add(text_hash)
                     
                     rel_path = vtt_path.relative_to(c_path)
                     combined_transcript.append(f"--- SOURCE: {cid} / {rel_path} ---\n{text}")
                 except Exception as e:
                     logger.warning(f"Failed to read VTT {vtt_path}: {e}")
                
            # PDFs (Recursive - find all PDFs in subfolders)
            for pdf_file in c_path.rglob("*.pdf"):
                 # Avoid reading the same file if multiple symlinks or structure exists
                 try:
                    text = await extract_pdf_text(pdf_file)
                    if not text: continue
    
                    # Deduplication Check
                    text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
                    if text_hash in seen_content_hashes:
                        logger.info(f"Skipping duplicate PDF content: {pdf_file.name}")
                        continue
                    seen_content_hashes.add(text_hash)
    
                    rel_path = pdf_file.relative_to(c_path)
                    combined_pdfs.append(f"--- SOURCE: {cid} / {rel_path} ---\n{text}")
                 except Exception as e:
                     logger.warning(f"Failed to read PDF {pdf_file}: {e}")
    else:
        # Dummy Metadata for Custom Uploads
        aggregated_metadata["courses"].append({
             "name": "User Uploaded Content", 
             "code": "CUSTOM_UPLOAD", 
             "description": "Assessment generated from user provided files (PDF/VTT)."
         })

    # Process Extra Uploaded Files (from API)
    if extra_files:
        for fpath in extra_files:
            if fpath.suffix.lower() == '.pdf':
                text = await extract_pdf_text(fpath)
                combined_pdfs.append(f"--- UPLOADED FILE: {fpath.name} ---\n{text}")
            elif fpath.suffix.lower() == '.vtt':
                text = await extract_vtt_text(fpath)
                combined_transcript.append(f"--- UPLOADED FILE: {fpath.name} ---\n{text}")

    final_transcript_str = "\n\n".join(combined_transcript) if combined_transcript else "N/A"
    final_pdf_str = "\n\n".join(combined_pdfs) if combined_pdfs else "N/A"
    
    # 2. Format Bloom's Distribution
    if not enable_blooms:
        blooms_str = "Strictly Disabled - Do NOT force any specific Bloom's taxonomy mapping. Rely entirely on the requested difficulty level."
    elif not blooms_distribution:
        # Dynamic defaults based on Difficulty Level (PRD Requirement)
        if difficulty_level.lower() == "beginner":
            blooms_str = "Remember: 40%, Understand: 40%, Apply: 20%"
        elif difficulty_level.lower() == "intermediate":
            blooms_str = "Remember: 20%, Understand: 30%, Apply: 30%, Analyze: 20%"
        elif difficulty_level.lower() == "advanced":
             blooms_str = "Apply: 20%, Analyze: 40%, Evaluate: 30%, Create: 10%"
        else:
            blooms_str = "Remember: 20%, Understand: 25%, Apply: 25%, Analyze: 20%, Evaluate: 10%"
    else:
        # User defined
        blooms_str = ", ".join([f"{k}: {v}%" for k,v in blooms_distribution.items()])

    # 3. Format Topics
    topics_str = ", ".join(topic_names) if topic_names else "None specific (Cover all modules)"

    # 4. Format Course Weightage
    course_weightage_instruction = "Distribute questions roughly equally across courses, anchored by their content depth."
    if course_weightage:
        try:
            weights_dict = json.loads(course_weightage) if isinstance(course_weightage, str) else course_weightage
            instruction_list = [f"{cid}: {weight}%" for cid, weight in weights_dict.items()]
            course_weightage_instruction = "Distribute the generated questions STRICTLY according to the following percentages:\n" + "\n".join(instruction_list)
        except Exception as e:
            logger.warning(f"Failed to parse course weightage '{course_weightage}' - falling back to equal distribution. Error: {e}")

    # 5. Build Prompt
    prompt = build_prompt(
        question_type_counts=question_type_counts,
        course_context=json.dumps(aggregated_metadata, indent=2),
        transcript=final_transcript_str,
        pdf_snippets=final_pdf_str,
        assessment_type=assessment_type,
        difficulty_level=difficulty_level,
        total_questions=total_questions,
        time_to_complete=str(time_limit) + " minutes" if time_limit else None,
        additional_instructions=additional_instructions,
        input_language=input_language,
        topic_names=topics_str,
        blooms_distribution=blooms_str,
        course_weightage_instruction=course_weightage_instruction
    )
    
    # 6. Call LLM
    response_text, usage = await call_llm(prompt)
    
    try:
        result_json = json.loads(response_text)
        return aggregated_metadata, result_json, usage
    except json.JSONDecodeError:
        logger.error("Failed to parse LLM response as JSON")
        raise ValueError("LLM response was not valid JSON")

def build_prompt(
    question_type_counts:Dict[str, int],
    course_context: str, 
    transcript: str, 
    pdf_snippets: str,
    assessment_type: str,
    difficulty_level: str,
    total_questions: int,
    time_to_complete: Optional[str],
    additional_instructions: Optional[str],
    input_language: str,
    topic_names: str,
    blooms_distribution: str,
    course_weightage_instruction: str
) -> str:
    prompt_template = ASSESSMENT_PROMPTS.get('system_prompt_template', '')
    
    # Placeholder Replacement
    prompt = prompt_template.replace("{course_context}", course_context)
    prompt = prompt.replace("{content_context}", f"TRANSCRIPTS:\n{transcript}\n\nPDF CONTENT:\n{pdf_snippets}")
    prompt = prompt.replace("{additional_instructions}", additional_instructions or "None provided")
    prompt = prompt.replace("{input_language}", input_language or "English")
    prompt = prompt.replace("{kcm_dataset}", json.dumps(KCM_DATASET, indent=2))
    
    prompt = prompt.replace("{assessment_type}", assessment_type or "comprehensive")
    prompt = prompt.replace("{difficulty_level}", difficulty_level or "Medium")
    prompt = prompt.replace("{total_questions_x3}", str(total_questions))
    prompt = prompt.replace("{time_to_complete}", time_to_complete or "Not provided (use standard pacing)")
    prompt = prompt.replace("{course_weightage_instruction}", course_weightage_instruction)

    # v3.3 Specifics (Question Types)
    if not question_type_counts:
        raise ValueError("question_type_counts cannot be empty.")
        
    q_instructions = ""
    if question_type_counts.get('mcq', 0) > 0:
        count = question_type_counts['mcq']
        q_instructions += f"\n     - {count} Multiple Choice Questions (MCQs)"
    else:
        q_instructions += "\n     - 0 Multiple Choice Questions (MCQs) [DO NOT GENERATE]"

    if question_type_counts.get('ftb', 0) > 0:
        count = question_type_counts['ftb']
        q_instructions += f"\n     - {count} Fill in the Blank Questions (FTBs)"
    else:
        q_instructions += "\n     - 0 Fill in the Blank Questions (FTBs) [DO NOT GENERATE]"

    if question_type_counts.get('mtf', 0) > 0:
        count = question_type_counts['mtf']
        q_instructions += f"\n     - {count} Match the Following Questions (MTFs)"
    else:
        q_instructions += "\n     - 0 Match the Following Questions (MTFs) [DO NOT GENERATE]"

    if question_type_counts.get('multichoice', 0) > 0:
        count = question_type_counts['multichoice']
        q_instructions += f"\n     - {count} Multi-Choice Questions"
    else:
        q_instructions += "\n     - 0 Multi-Choice Questions [DO NOT GENERATE]"
        
    if question_type_counts.get('truefalse', 0) > 0:
        count = question_type_counts['truefalse']
        q_instructions += f"\n     - {count} True/False Questions"
    else:
        q_instructions += "\n     - 0 True/False Questions [DO NOT GENERATE]"

    prompt = prompt.replace("{question_type_instructions}", q_instructions)
    logger.info(f"Q Counts: {question_type_counts} | Inst: {q_instructions}")


    # v3.2 Specifics
    prompt = prompt.replace("{topic_names}", topic_names)
    prompt = prompt.replace("{blooms_distribution}", blooms_distribution)
    
    prompt = prompt.replace("{p_version}", PROMPT_VERSION)
    prompt = prompt.replace("{a_version}", "api/v1")

    return prompt

@retry(retry=retry_if_exception_type((Exception, APIError)), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def call_llm(prompt: str) -> Tuple[str, Dict[str, Any]]:
    if not client:
        raise RuntimeError("GenAI client is not initialized.")
        
    logger.info("Calling GenAI model: %s", GENAI_MODEL_NAME)
    
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=ASSESSMENT_SCHEMA, # Use the correct Assessment Schema
        temperature=0.1,
    )

    contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]

    response = await client.aio.models.generate_content(
        model=GENAI_MODEL_NAME,
        contents=contents,
        config=config
    )
    
    llm_usage = {}
    if response.usage_metadata:
        llm_usage = response.usage_metadata.to_json_dict()

    if not response.text:
        raise RuntimeError("LLM returned an empty response text.")
    
    return response.text, llm_usage

async def extract_vtt_text(vtt_path: Path) -> str:
    def _read_and_clean():
        text_lines = []
        try:
            raw = vtt_path.read_text(encoding='utf-8')
        except Exception:
            raw = vtt_path.read_text(encoding='latin-1')
            
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.upper().startswith('WEBVTT') or '-->' in line or line.isdigit():
                continue
            text_lines.append(line)
        return '\n'.join(text_lines)

    return await asyncio.to_thread(_read_and_clean)

def extract_pdf_text_sync(pdf_path: Path) -> str:
    text_parts = []
    try:
        doc = fitz.open(str(pdf_path))
        for page in doc:
            page_text = page.get_text().strip()
            if page_text:
                text_parts.append(page_text)
        doc.close()
    except Exception as e:
        logger.exception('PDF extraction failed for %s: %s', pdf_path, e)
    return '\n\n'.join(text_parts)

async def extract_pdf_text(pdf_path: Path) -> str:
    return await asyncio.get_running_loop().run_in_executor(None, extract_pdf_text_sync, pdf_path)
