
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
API_V1 = f"{API_BASE}/ai-assessments/v1"

st.set_page_config(page_title="Assessment Generator (Interactive)", layout="wide", page_icon="🧩")

# --- Sidebar: Auth & Config ---
st.sidebar.title("⚙️ Configuration")
auth_token = st.sidebar.text_input("Auth Token (JWT)", type="password", help="Enter your x-authenticated-user-token from iGot")

if not auth_token:
    st.sidebar.warning("⚠️ Auth Token is required for API")
    
force_new = st.sidebar.checkbox("Bypass Cache (Force New)", value=False)

# Headers helper
def get_headers():
    return {
        "x-authenticated-user-token": auth_token,
        "bg-bypass-cache": "true" if force_new else "false"
    }

st.title("🧩 Assessment Generator")
st.markdown("Test the full **Generate -> Clone -> Edit -> Event** lifecycle.")

# --- Tab Layout ---
tab_gen, tab_comp, tab_view, tab_history = st.tabs(["🚀 Generate / Clone", "📚 Comprehensive", "📝 View & Edit Result", "🗂️ History"])

# ==========================================
# TAB 1: GENERATE (Standard)
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
            assessment_type = st.selectbox("Assessment Type", ["practice", "final", "standalone"])
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
        
        st.markdown("#### Advanced Settings")
        adv1, adv2 = st.columns(2)
        with adv1:
            time_limit = st.number_input("Time Limit (Minutes)", min_value=0, value=0, help="0 means no limit. Influences cognitive depth of questions.")
            enable_blooms = st.checkbox("Enable Bloom's Taxonomy", value=True, help="If disabled, relies purely on Difficulty level")
            
            if enable_blooms:
                st.markdown("**Bloom's Distribution (%)**")
                b1, b2, b3 = st.columns(3)
                b_rem = b1.number_input("Remember", 0, 100, 20)
                b_und = b2.number_input("Understand", 0, 100, 30)
                b_app = b3.number_input("Apply", 0, 100, 30)
                
                b4, b5, b6 = st.columns(3)
                b_ana = b4.number_input("Analyze", 0, 100, 10)
                b_eva = b5.number_input("Evaluate", 0, 100, 10)
                b_cre = b6.number_input("Create", 0, 100, 0)
                
                blooms_total = b_rem + b_und + b_app + b_ana + b_eva + b_cre
                if blooms_total != 100:
                    st.warning(f"Total Bloom's percentage is {blooms_total}%. It should equal 100%.")
                
                blooms_config = {
                    "Remember": b_rem,
                    "Understand": b_und,
                    "Apply": b_app,
                    "Analyze": b_ana,
                    "Evaluate": b_eva,
                    "Create": b_cre
                }
            else:
                blooms_config = None
                
        with adv2:
            st.write("")
        
        uploaded_files = st.file_uploader("Upload Context (PDF/VTT)", accept_multiple_files=True)

    if st.button("Start Generation", type="primary"):
        if not auth_token:
            st.error("Please enter an Auth Token in the sidebar first.")
            st.stop()
            
        # Construct Payload
        course_weightage = None
        q_counts = {"mcq": mcq, "ftb": ftb, "mtf": mtf, "multichoice": multi, "truefalse": tf}
        
        payload = {
            'course_ids': course_ids_input,
            'force': 'true' if force_new else 'false', 
            'assessment_type': assessment_type,
            'difficulty': difficulty,
            'total_questions': sum(q_counts.values()),
            'question_type_counts': json.dumps(q_counts),
            'language': language,
            'enable_blooms': 'true' if enable_blooms else 'false'
        }
        
        if enable_blooms and blooms_config:
            payload['blooms_config'] = json.dumps(blooms_config)
        
        if course_weightage and course_weightage.strip():
            payload['course_weightage'] = course_weightage.strip()
        
        if time_limit > 0:
            payload['time_limit'] = time_limit
        
        files = []
        if uploaded_files:
            for f in uploaded_files:
                mime = "application/pdf" if f.name.endswith(".pdf") else "text/vtt"
                files.append(('files', (f.name, f.getvalue(), mime)))

        with st.spinner("Calling API..."):
            try:
                # Generate Call
                r = requests.post(f"{API_V1}/generate", data=payload, files=files, headers=get_headers())
                
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
# TAB 2: COMPREHENSIVE GENERATION
# ==========================================
with tab_comp:
    st.markdown("### 📚 Comprehensive Assessment Builder")
    st.info("Combine multiple courses with specific percentage weightages to generate a comprehensive cross-course assessment.")
    
    # Dynamic Course Inputs
    if "comp_courses" not in st.session_state:
        st.session_state.comp_courses = [{"id": "", "weight": 50}, {"id": "", "weight": 50}]
        
    st.markdown("#### Input Courses & Weights")
    
    course_data = []
    total_weight = 0
    for i, course in enumerate(st.session_state.comp_courses):
        col1, col2, col3 = st.columns([5, 2, 1])
        with col1:
            c_id = st.text_input(f"Course ID {i+1}", value=course["id"], key=f"cid_{i}")
        with col2:
            c_w = st.number_input(f"Weight (%)", min_value=1, max_value=100, value=course["weight"], key=f"cw_{i}")
        with col3:
            st.write("") # Spacing
            st.write("")
            if st.button("🗑️", key=f"del_{i}"):
                st.session_state.comp_courses.pop(i)
                st.rerun()
                
        course_data.append({"id": c_id, "weight": c_w})
        total_weight += c_w
        
    if st.button("➕ Add Another Course"):
        st.session_state.comp_courses.append({"id": "", "weight": 0})
        st.rerun()
        
    if total_weight != 100:
        st.warning(f"⚠️ Total weight is currently {total_weight}%. It should ideally sum to 100%.")
    else:
        st.success("✅ Total weight is exactly 100%!")
        
    # Standard Configs
    st.markdown("#### Configuration")
    ccol1, ccol2, ccol3 = st.columns(3)
    with ccol1:
        comp_diff = st.selectbox("Difficulty Level", ["beginner", "intermediate", "advanced"], index=1)
    with ccol2:
        comp_lang = st.selectbox("Output Language", ["english", "hindi", "bengali", "gujarati", "kannada", "malayalam", "marathi", "tamil", "telugu", "odia", "punjabi", "assamese"])
    with ccol3:
        comp_enable_blooms = st.checkbox("Enable Bloom's", value=True, key="comp_blooms")
        
    comp_blooms_config = None
    if comp_enable_blooms:
        st.markdown("**Bloom's Distribution (%)**")
        b1, b2, b3 = st.columns(3)
        cb_rem = b1.number_input("Remember", 0, 100, 20, key="cb_rem")
        cb_und = b2.number_input("Understand", 0, 100, 30, key="cb_und")
        cb_app = b3.number_input("Apply", 0, 100, 30, key="cb_app")
        
        b4, b5, b6 = st.columns(3)
        cb_ana = b4.number_input("Analyze", 0, 100, 10, key="cb_ana")
        cb_eva = b5.number_input("Evaluate", 0, 100, 10, key="cb_eva")
        cb_cre = b6.number_input("Create", 0, 100, 0, key="cb_cre")
        
        cb_total = cb_rem + cb_und + cb_app + cb_ana + cb_eva + cb_cre
        if cb_total != 100:
            st.warning(f"Total Bloom's percentage is {cb_total}%. It should equal 100%.")
            
        comp_blooms_config = {
            "Remember": cb_rem,
            "Understand": cb_und,
            "Apply": cb_app,
            "Analyze": cb_ana,
            "Evaluate": cb_eva,
            "Create": cb_cre
        }
        
    st.markdown("#### Question Counts")
    c1, c2, c3, c4, c5 = st.columns(5)
    cmcq = c1.number_input("MCQ", 0, 50, 10, key="cmcq")
    cftb = c2.number_input("FTB", 0, 50, 0, key="cftb")
    cmtf = c3.number_input("MTF", 0, 50, 0, key="cmtf")
    cmulti = c4.number_input("Multi-Choice", 0, 50, 0, key="cmulti")
    ctf  = c5.number_input("True/False", 0, 50, 0, key="ctf")
    
    total_q = cmcq + cftb + cmtf + cmulti + ctf
    
    if st.button("Generate Comprehensive", type="primary"):
        if not auth_token:
            st.error("Please enter an Auth Token in the sidebar first.")
            st.stop()
            
        valid_courses = [c for c in course_data if c["id"].strip()]
        if len(valid_courses) < 2:
            st.error("A comprehensive assessment requires at least 2 valid courses.")
            st.stop()
            
        c_ids = [c["id"].strip() for c in valid_courses]
        c_weights = {c["id"].strip(): c["weight"] for c in valid_courses}
        
        comp_q_counts = {"mcq": cmcq, "ftb": cftb, "mtf": cmtf, "multichoice": cmulti, "truefalse": ctf}
        comp_payload = {
            'course_ids': ",".join(c_ids),
            'force': 'true' if force_new else 'false', 
            'assessment_type': 'comprehensive',
            'difficulty': comp_diff,
            'total_questions': total_q,
            'question_type_counts': json.dumps(comp_q_counts),
            'language': comp_lang,
            'enable_blooms': 'true' if comp_enable_blooms else 'false',
            'course_weightage': json.dumps(c_weights)
        }
        
        if comp_enable_blooms and comp_blooms_config:
            comp_payload['blooms_config'] = json.dumps(comp_blooms_config)
        
        with st.spinner("Calling API (Comprehensive)..."):
            try:
                r = requests.post(f"{API_V1}/generate", data=comp_payload, headers=get_headers())
                if r.status_code in [200, 202]:
                    data = r.json()
                    st.session_state['current_job_id'] = data.get("job_id")
                    st.session_state['job_status'] = data.get("status")
                    
                    if r.status_code == 200:
                        st.success(f"⚡ Instant Result! (Cache Hit/Cloned). Job ID: {data.get('job_id')}")
                        st.balloons()
                    else:
                        st.info(f"⏳ Job Started (Async). Job ID: {data.get('job_id')}")
                        st.info("Go to 'View & Edit Result' tab to poll status.")
                else:
                    st.error(f"API Error ({r.status_code}): {r.text}")
            except Exception as e:
                st.error(f"Connection Failed: {e}")

# ==========================================
# TAB 3: VIEW & EDIT
# ==========================================
with tab_view:
    job_id = st.text_input("Job ID", value=st.session_state.get('current_job_id', ''))
    
    col_act1, col_act2 = st.columns([1, 4])
    with col_act1:
        if st.button("Check Status / Fetch"):
            if not job_id: st.warning("Enter Job ID"); st.stop()
            if not auth_token: st.error("Auth Token Required"); st.stop()
            
            try:
                # GET /ai-assessments/v1/status/{job_id} — returns status and result when COMPLETED
                r = requests.get(f"{API_V1}/status/{job_id}", headers=get_headers())
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
            # Downloads — token sent in header, not URL query param
            st.markdown("### 📥 Downloads")
            d1, d2, d3 = st.columns(3)
            for fmt, col, label, mime in [
                ("csv",  d1, "Download CSV",  "text/csv"),
                ("json", d2, "Download JSON", "application/json"),
                ("pdf",  d3, "Download PDF",  "application/pdf"),
            ]:
                try:
                    dl_resp = requests.get(
                        f"{API_V1}/download/{job_id}",
                        params={"format": fmt},
                        headers=get_headers(),
                    )
                    if dl_resp.status_code == 200:
                        col.download_button(label, dl_resp.content, file_name=f"assessment_{job_id}.{fmt}", mime=mime)
                    else:
                        col.error(f"{fmt.upper()} failed ({dl_resp.status_code})")
                except Exception as e:
                    col.error(f"{fmt.upper()} error: {e}")

            # EDITOR
            st.markdown("### ✏️ Interactive Editor")
            st.info("Edit questions below and click 'Save Changes' to update the backend.")
            
            # Parse Data
            raw_res = data.get("assessment_data", {})
            
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
                        
                        course_tag = f" [{q.get('course_name')}]" if q.get("course_name") else ""
                        title_text = f"Q{i+1}{course_tag}: {q.get('question_text', 'Match the following')[:50]}..."
                        
                        with st.expander(title_text):
                            cols = st.columns([5, 1])
                            
                            # Give a default title for MTF
                            if q_type == "MTF Question":
                                default_txt = q.get("matching_context", q.get("question_text", "Match the following items appropriately:"))
                            else:
                                default_txt = q.get("question_text", "")
                            
                            updated_text = cols[0].text_area(f"Edit Text/Context", default_txt, key=f"qtxt_{qk}")
                            
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
                            
                            # Display Rationale
                            rationale = q.get("answer_rationale", {})
                            if rationale:
                                with st.expander("Show Answer Rationale"):
                                    st.write(f"**Explanation:** {rationale.get('correct_answer_explanation', 'N/A')}")
                                    st.write(f"**Why Factor:** {rationale.get('why_factor', 'N/A')}")
                                    st.write(f"**Logic:** {rationale.get('logic_justification', 'N/A')}")

                            # Display Reasoning
                            reasoning = q.get("reasoning", {})
                            if reasoning:
                                with st.expander("Show Competency & Bloom's Reasoning"):
                                    st.write(f"**Relevance:** {q.get('relevance_percentage', 'N/A')}%")
                                    st.write(f"**Learning Objective:** {reasoning.get('learning_objective_alignment', 'N/A')}")
                                    st.write(f"**Bloom's Level:** {q.get('blooms_level', 'N/A')} ({reasoning.get('blooms_level_justification', 'N/A')})")
                                    st.write(f"**Rationale:** {reasoning.get('question_type_rationale', 'N/A')}")
                                    kcm = reasoning.get("competency_alignment", {}).get("kcm", {})
                                    st.write(f"**Competency:** {kcm.get('competency_area', 'N/A')} - {kcm.get('competency_theme', 'N/A')}")
                                    if kcm.get("competency_sub_theme"):
                                        st.write(f"**Sub-Theme:** {kcm.get('competency_sub_theme', 'N/A')}")

                            # Reconstruct object
                            updated_q = q.copy()
                            if q_type != "MTF Question":
                                updated_q['question_text'] = updated_text
                            else:
                                updated_q['matching_context'] = updated_text
                            new_q_list.append(updated_q)
                            
                    new_questions_data[q_type] = new_q_list
                
                if st.form_submit_button("💾 Save Changes (PUT /ai-assessments/v1/update/{job_id})"):
                    # Construct full payload
                    updated_assessment = raw_res.copy()
                    updated_assessment['questions'] = new_questions_data

                    update_payload = {"assessment_data": updated_assessment}

                    try:
                        r_upd = requests.put(
                            f"{API_V1}/update/{job_id}",
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

# ==========================================
# TAB 3: HISTORY
# ==========================================
with tab_history:
    st.markdown("### 🗂️ Your Assessment History")
    st.info("View previously generated tests. Ensure you have provided your Auth Token in the sidebar.")
    
    if st.button("🔄 Refresh History"):
        if not auth_token:
            st.warning("Enter your Auth Token to retrieve history.")
        else:
            with st.spinner("Fetching history..."):
                try:
                    r_hist = requests.get(f"{API_V1}/history", headers=get_headers())
                    if r_hist.status_code == 200:
                        st.session_state['history_data'] = r_hist.json()
                    else:
                        st.error(f"Failed to fetch history: {r_hist.text}")
                except Exception as e:
                    st.error(f"Error fetching history: {e}")

    history_items = st.session_state.get('history_data', [])
    
    if history_items:
        for idx, item in enumerate(history_items):
            job_id = item.get("job_id", "Unknown")
            status = item.get("status", "Unknown")
            updated = item.get("updated_at", "Unknown")
            config = item.get("config", {})
            course_names = item.get("course_names", [])
            course_ids_list = item.get("course_ids", [])
            course_label = ", ".join(course_names) if course_names else "Unknown Course"

            # Status badge logic
            status_emoji = "⏳"
            if status == "COMPLETED": status_emoji = "✅"
            elif status == "FAILED": status_emoji = "❌"
            elif status == "PENDING": status_emoji = "🕒"

            with st.expander(f"{status_emoji} {course_label} | Updated: {updated[:10]}", expanded=(idx == 0)):
                cols = st.columns([2, 1])

                with cols[0]:
                    st.write(f"- **Job ID:** `{job_id}`")
                    if course_names:
                        st.write(f"- **Course(s):** {', '.join(course_names)}")
                    if course_ids_list:
                        st.write(f"- **Course ID(s):** {', '.join(course_ids_list)}")
                    st.write(f"- **Created:** {item.get('created_at', 'N/A')[:19].replace('T', ' ')}")
                    st.write(f"- **Updated:** {updated[:19].replace('T', ' ')}")
                    st.markdown("**Configuration:**")
                    if config:
                        st.write(f"- **Type:** {config.get('assessment_type', 'N/A')}")
                        st.write(f"- **Difficulty:** {config.get('difficulty', 'N/A')}")
                        st.write(f"- **Language:** {config.get('language', 'N/A')}")
                        st.write(f"- **Total Questions:** {config.get('total_questions', 'N/A')}")
                        q_counts = config.get('question_type_counts', {})
                        if q_counts:
                            active = ", ".join(f"{k.upper()}:{v}" for k, v in q_counts.items() if v > 0)
                            st.write(f"- **Question Types:** {active}")
                        st.write(f"- **Time Limit:** {config.get('time_limit', 0) or 'No limit'}")
                        if config.get('course_weightage'):
                            st.write(f"- **Course Weightage:** `{config.get('course_weightage')}`")
                    else:
                        st.write("No configuration metadata available (Legacy Job).")
                
                with cols[1]:
                    st.markdown("**Actions:**")
                    if status == "COMPLETED":
                        if st.button("Load into Editor", key=f"load_{job_id}"):
                            st.session_state['current_job_id'] = job_id
                            st.success(f"Job {job_id} loaded! Switch to 'View & Edit Result' tab.")
                        
                        # Downloads — token sent in header, not URL query param
                        dl_col1, dl_col2 = st.columns(2)
                        for fmt, col, label, mime in [
                            ("csv", dl_col1, "CSV", "text/csv"),
                            ("pdf", dl_col2, "PDF", "application/pdf"),
                        ]:
                            try:
                                dl_r = requests.get(
                                    f"{API_V1}/download/{job_id}",
                                    params={"format": fmt},
                                    headers=get_headers(),
                                )
                                if dl_r.status_code == 200:
                                    col.download_button(f"Download {label}", dl_r.content, file_name=f"assessment_{job_id}.{fmt}", mime=mime, key=f"dl_{fmt}_{job_id}")
                                else:
                                    col.error(f"{label} failed ({dl_r.status_code})")
                            except Exception as e:
                                col.error(f"{label} error: {e}")
                    else:
                        st.write(f"*Job is {status}* ‒ wait for completion.")
    elif history_items == []:
         st.write("No history found for your account.")
