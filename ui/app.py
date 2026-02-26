
import streamlit as st
import requests
import time
import pandas as pd
import json
import os
from dotenv import load_dotenv

# Load local .env if present
load_dotenv()

# --- Configuration ---
# Default to localhost for local testing
API_BASE = os.getenv("API_URL", "http://localhost:8000")
API_V2 = f"{API_BASE}/api/v2"

st.set_page_config(page_title="Assessment Generator V2 (Interactive)", layout="wide", page_icon="🧩")

# --- Sidebar: Auth & Config ---
st.sidebar.title("⚙️ Configuration")
auth_token = st.sidebar.text_input("Auth Token (JWT)", type="password", help="Enter your x-auth-token from iGot")

if not auth_token:
    st.sidebar.warning("⚠️ Auth Token is required for V2 API")
    
force_new = st.sidebar.checkbox("Bypass Cache (Force New)", value=False)

# Headers helper
def get_headers():
    return {
        "x-auth-token": auth_token,
        "bg-bypass-cache": "true" if force_new else "false"
    }

st.title("🧩 Assessment Generator V2")
st.markdown("Test the full **Generate -> Clone -> Edit -> Event** lifecycle.")

# --- Tab Layout ---
tab_gen, tab_view = st.tabs(["🚀 Generate / Clone", "📝 View & Edit Result"])

# ==========================================
# TAB 1: GENERATE
# ==========================================
with tab_gen:
    col_input, col_mode = st.columns([3, 1])
    with col_mode:
        use_custom = st.checkbox("Upload Only Mode", help="Generate from uploaded files without a Course ID")

    with col_input:
        if use_custom:
            st.info("Upload-only mode active. Please upload files below.")
            course_id = ""
            course_ids_input = ""
        else:
            course_ids_input = st.text_input("Course IDs (comma-separated)", placeholder="do_114297785654214656137, do_123...")

    # Config Form
    with st.expander("Detailed Configuration", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            assessment_type = st.selectbox("Assessment Type", ["practice", "final", "comprehensive", "standalone"])
        with col2:
            difficulty = st.selectbox("Difficulty", ["beginner", "intermediate", "advanced"], index=1)
        with col3:
            language = st.selectbox(
                "Language", 
                ["english", "hindi", "bengali", "gujarati", "kannada", "malayalam", "marathi", "tamil", "telugu", "odia", "punjabi", "assamese"]
            )
            
        st.markdown("#### Question Counts")
        c1, c2, c3, c4, c5 = st.columns(5)
        mcq = c1.number_input("MCQ", 0, 20, 5)
        ftb = c2.number_input("FTB", 0, 20, 5)
        mtf = c3.number_input("MTF", 0, 20, 5)
        multi = c4.number_input("Multi-Choice", 0, 20, 0)
        tf  = c5.number_input("True/False", 0, 20, 0)
        
        uploaded_files = st.file_uploader("Upload Context (PDF/VTT)", accept_multiple_files=True)

    if st.button("Start Generation (V2)", type="primary"):
        if not auth_token:
            st.error("Please enter an Auth Token in the sidebar first.")
            st.stop()
            
        # Construct Payload
        q_counts = {"mcq": mcq, "ftb": ftb, "mtf": mtf, "multichoice": multi, "truefalse": tf}
        q_types = [k for k,v in q_counts.items() if v > 0]
        
        payload = {
            'course_ids': course_ids_input,
            'force': 'true' if force_new else 'false', 
            'assessment_type': assessment_type,
            'difficulty': difficulty,
            'total_questions': sum(q_counts.values()),
            'question_type_counts': json.dumps(q_counts),
            'question_types': ",".join(q_types),
            'language': language
        }
        
        files = []
        if uploaded_files:
            for f in uploaded_files:
                mime = "application/pdf" if f.name.endswith(".pdf") else "text/vtt"
                files.append(('files', (f.name, f.getvalue(), mime)))

        with st.spinner("Calling V2 API..."):
            try:
                # V2 Generate Call
                r = requests.post(f"{API_V2}/generate", data=payload, files=files, headers=get_headers())
                
                if r.status_code in [200, 202]:
                    data = r.json()
                    st.session_state['current_job_id'] = data.get("job_id")
                    st.session_state['job_status'] = data.get("status")
                    
                    if r.status_code == 200:
                        st.success(f"⚡ Instant Result! (Cache Hit/Cloned). Job ID: {data.get('job_id')}")
                        st.balloons()
                    else: # 202
                        st.info(f"⏳ Job Started (Async). Job ID: {data.get('job_id')}")
                        st.info("Go to 'View & Edit Result' tab to poll status.")
                else:
                    st.error(f"API Error ({r.status_code}): {r.text}")
                    
            except Exception as e:
                st.error(f"Connection Failed: {e}")

# ==========================================
# TAB 2: VIEW & EDIT
# ==========================================
with tab_view:
    job_id = st.text_input("Job ID", value=st.session_state.get('current_job_id', ''))
    
    col_act1, col_act2 = st.columns([1, 4])
    with col_act1:
        if st.button("Check Status / Fetch"):
            if not job_id: st.warning("Enter Job ID"); st.stop()
            if not auth_token: st.error("Auth Token Required"); st.stop()
            
            try:
                # V2 Status Check (Uses V2 GET typically, effectively same as V1 but we should use api/v2 prefix if exists? 
                # Doc says Polling is V1 compatible. But api/v2/status exists? 
                # Actually code implemented api_v2_router.get("/status/{job_id}")
                r = requests.get(f"{API_V2}/status/{job_id}", headers=get_headers())
                if r.status_code == 200:
                    st.session_state['fetch_data'] = r.json()
                    st.success("Fetched!")
                else:
                    st.error(f"Error ({r.status_code}): {r.text}")
            except Exception as e:
                st.error(f"Conn Error: {e}")

    # Display Data
    data = st.session_state.get('fetch_data', {})
    if data:
        status = data.get("status")
        st.metric("Status", status)
        
        if status == "COMPLETED":
            # Downloads
            st.markdown("### 📥 Downloads")
            d1, d2, d3 = st.columns(3)
            # V2 CSV Link
            d1.markdown(f"[**Download CSV (V2)**]({API_V2}/download_csv/{job_id})")
            d2.markdown(f"[Download JSON]({API_BASE}/api/v1/download_json/{job_id})")
            d3.markdown(f"[Download PDF]({API_BASE}/api/v1/download_pdf/{job_id})")

            # EDITOR
            st.markdown("### ✏️ Interactive Editor")
            st.info("Edit questions below and click 'Save Changes' to update the backend.")
            
            # Parse Data
            raw_res = data.get("assessment_data", {})
            if isinstance(raw_res, str): raw_res = json.loads(raw_res)
            
            # We need to preserve the structure to save it back
            # Flatten for editing? 
            questions = raw_res.get("questions", {})
            
            # Form for editing
            with st.form("edit_assessment_form"):
                new_questions_data = {}
                
                for q_type, q_list in questions.items():
                    st.markdown(f"**{q_type}**")
                    new_q_list = []
                    for i, q in enumerate(q_list):
                        qk = f"{q_type}_{i}"
                        with st.expander(f"Q{i+1}: {q.get('question_text', 'Match the following')[:50]}..."):
                            cols = st.columns([5, 1])
                            
                            # Give a default title for MTF
                            default_txt = q.get("question_text", "Match the following items appropriately:") if q_type == "MTF Question" else q.get("question_text", "")
                            updated_text = cols[0].text_area(f"Edit Question Text", default_txt, key=f"qtxt_{qk}")
                            
                            # Restore Display of Options & Answers
                            if q_type == "Multiple Choice Question":
                                for opt in q.get("options", []):
                                    st.write(f"- {opt.get('text', '')}")
                                st.info(f"Answer: Option {q.get('correct_option_index')}")
                            elif q_type == "Multi-Choice Question":
                                for opt in q.get("options", []):
                                    st.write(f"- {opt.get('text', '')}")
                                st.info(f"Correct Options: {q.get('correct_option_index')}")
                            elif q_type == "MTF Question":
                                for p in q.get("pairs", []):
                                    st.write(f"- {p.get('left')} → {p.get('right')}")
                            else:
                                st.info(f"Answer: {q.get('correct_answer')}")
                            
                            # Reconstruct object
                            updated_q = q.copy()
                            if q_type != "MTF Question":
                                updated_q['question_text'] = updated_text
                            new_q_list.append(updated_q)
                            
                    new_questions_data[q_type] = new_q_list
                
                if st.form_submit_button("💾 Save Changes (PUT /api/v2)"):
                    # Construct full payload
                    updated_assessment = raw_res.copy()
                    updated_assessment['questions'] = new_questions_data
                    
                    update_payload = {"assessment_data": updated_assessment}
                    
                    try:
                        r_upd = requests.put(
                            f"{API_V2}/assessment/{job_id}", 
                            json=update_payload, 
                            headers=get_headers()
                        )
                        if r_upd.status_code == 200:
                            st.success("Saved successfully!")
                            st.session_state['fetch_data']['assessment_data'] = updated_assessment # Local update
                            st.rerun()
                        else:
                            st.error(f"Save Failed: {r_upd.text}")
                    except Exception as e:
                        st.error(f"Update Error: {e}")
