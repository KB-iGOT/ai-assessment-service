"""
How `generate_assessment` gathers its inputs before the LLM call: course
folders on disk, the competency-only KCM context, uploaded files, learning
objectives and the Bloom's settings. The LLM is faked; each test reads the
prompt it was sent and the course metadata returned.
"""

import json

import pytest

from assessment import generator as gen
from assessment.questions import BUCKET_MCQ

COUNTS = {"mcq": 1}


class PromptCapture:
    """Fake `call_llm`: records the prompt, returns one valid MCQ."""

    def __init__(self):
        self.prompts = []

    async def __call__(self, prompt, *, schema=None):
        self.prompts.append(prompt)
        body = {"blueprint": {"title": "bp"},
                "questions": {BUCKET_MCQ: [{"question_text": "Q", "blooms_level": "Remember"}]}}
        return json.dumps(body), {"total_token_count": 1}

    @property
    def prompt(self):
        return self.prompts[-1]


@pytest.fixture
def llm(monkeypatch):
    capture = PromptCapture()
    monkeypatch.setattr(gen, "call_llm", capture)
    return capture


@pytest.fixture
def courses(monkeypatch, tmp_path):
    """Course folders as the fetcher leaves them, under a temp INTERACTIVE_COURSES_PATH."""
    monkeypatch.setattr(gen, "INTERACTIVE_COURSES_PATH", str(tmp_path))

    def vtt(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\n{text}\n", encoding="utf-8")

    def pdf(path, text):
        import fitz

        path.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), text)
        doc.save(str(path))
        doc.close()

    c1 = tmp_path / "do_1"
    c1.mkdir()
    (c1 / "metadata.json").write_text(json.dumps({
        "name": "Course One", "identifier": "do_1",
        "instructions": ["Explain X", "Apply Y", "Explain X"],
    }), encoding="utf-8")
    vtt(c1 / "m1" / "english_subtitles.vtt", "Course one transcript")
    vtt(c1 / "m2" / "english_subtitles.vtt", "Course one transcript")   # duplicate content
    (c1 / "m3").mkdir()
    (c1 / "m3" / "english_subtitles.vtt").write_text("WEBVTT\n\n", encoding="utf-8")  # no text
    pdf(c1 / "m1" / "doc.pdf", "Course one handout")
    pdf(c1 / "m2" / "copy.pdf", "Course one handout")                     # duplicate content
    (c1 / "m2" / "broken.pdf").write_bytes(b"not a pdf")                  # unreadable

    c2 = tmp_path / "do_2"
    c2.mkdir()
    (c2 / "metadata.json").write_text(json.dumps({
        "name": "Course Two", "identifier": "do_2", "instructions": "Legacy objective",
    }), encoding="utf-8")
    vtt(c2 / "english_subtitles.vtt", "Course one transcript")            # duplicate across courses
    pdf(c2 / "two.pdf", "Course two handout")

    c3 = tmp_path / "do_3"
    c3.mkdir()
    (c3 / "metadata.json").write_text(json.dumps({"name": "Course Three", "instructions": "  "}),
                                      encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------
# Course folders
# --------------------------------------------------------------------------

async def test_course_content_is_read_once_and_deduplicated(llm, courses):
    meta, _, _ = await gen.generate_assessment(COUNTS, course_ids=["do_1", "do_2"], enable_blooms=False)

    transcripts, pdfs = llm.prompt.split("PDF CONTENT:", 1)
    assert transcripts.count("Course one transcript") == 1
    assert "--- SOURCE: do_1 / m1" in transcripts
    assert pdfs.count("Course one handout") == 1
    assert "Course two handout" in pdfs

    assert [c["name"] for c in meta["courses"]] == ["Course One", "Course Two"]
    assert meta["content_availability"] == {
        "do_1": {"has_vtt": True, "has_pdf": True},
        "do_2": {"has_vtt": False, "has_pdf": True},  # its only VTT was a duplicate
    }


async def test_learning_objectives_from_list_and_legacy_string_deduplicated(llm, courses):
    await gen.generate_assessment(COUNTS, course_ids=["do_1", "do_2", "do_3"], enable_blooms=False)
    assert "- Explain X\n- Apply Y\n- Legacy objective" in llm.prompt
    assert llm.prompt.count("- Explain X") == 1


async def test_no_learning_objectives_asks_the_model_to_synthesise_them(llm, courses):
    await gen.generate_assessment(COUNTS, course_ids=["do_3"], enable_blooms=False)
    assert "None explicitly provided. Synthesize appropriate Learning Objectives" in llm.prompt


async def test_missing_course_folder_uses_supplied_name_or_is_skipped(llm, courses):
    meta, _, _ = await gen.generate_assessment(
        COUNTS, course_ids=["do_missing", "do_1", "do_missing_too"],
        course_names=["Named Missing Course"], enable_blooms=False,
    )
    names = [c["name"] for c in meta["courses"]]
    assert names == ["Named Missing Course", "Course One"]
    assert meta["courses"][0]["identifier"] == "do_missing"
    assert "do_missing" not in meta["content_availability"]


async def test_deprecated_course_folder_argument_still_works(llm, courses):
    meta, _, _ = await gen.generate_assessment(COUNTS, course_folder=courses / "do_2", enable_blooms=False)
    assert [c["name"] for c in meta["courses"]] == ["Course Two"]


async def test_no_courses_and_no_competency_is_a_custom_upload(llm):
    meta, _, _ = await gen.generate_assessment(COUNTS, enable_blooms=False)
    assert meta["courses"][0]["code"] == "CUSTOM_UPLOAD"
    assert "TRANSCRIPTS:\nN/A" in llm.prompt


# --------------------------------------------------------------------------
# Competency-only assessments
# --------------------------------------------------------------------------

def _kcm_entry():
    return next(e for e in gen.KCM_DESCRIPTIONS_FILE if e.get("Area") and e.get("Label"))


async def test_competency_without_courses_injects_matching_kcm_entries(llm):
    entry = _kcm_entry()
    meta, _, _ = await gen.generate_assessment(
        COUNTS, assessment_type="competency", competency_area="Behavioural",
        competency_themes=f" {entry['Area'].upper()} , ", competency_sub_themes="Nope",
        enable_blooms=False,
    )
    assert "--- KCM COMPETENCY REFERENCE ---" in llm.prompt
    assert f"Sub-Theme: {entry['Label']}" in llm.prompt
    course = meta["courses"][0]
    assert course["code"] == "KCM_COMPETENCY"
    assert course["name"] == "Competency Assessment — Behavioural"
    assert f"Themes: {entry['Area'].upper()}, Sub-Themes: Nope" in course["description"]
    # The focus instruction lists the same parsed themes.
    assert f"Competency Themes: {entry['Area'].upper()}\n" in llm.prompt
    assert "ALL questions MUST map to one of the above sub-themes" in llm.prompt


async def test_competency_with_list_inputs_and_no_match_adds_no_kcm_section(llm):
    meta, _, _ = await gen.generate_assessment(
        COUNTS, assessment_type="competency", competency_area="Functional",
        competency_themes=["No such theme"], competency_sub_themes=None, enable_blooms=False,
    )
    assert "KCM COMPETENCY REFERENCE" not in llm.prompt
    assert meta["courses"][0]["description"].endswith("Themes: No such theme, Sub-Themes: ")


async def test_non_competency_assessment_gets_no_focus_instruction(llm):
    await gen.generate_assessment(COUNTS, assessment_type="final", competency_area="Behavioural",
                                  enable_blooms=False)
    assert "Not applicable for this assessment type." in llm.prompt


# --------------------------------------------------------------------------
# Uploads
# --------------------------------------------------------------------------

async def test_uploads_that_yield_no_text_are_skipped(llm, tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("   ", encoding="utf-8")
    sheet = tmp_path / "sheet.xlsx"
    sheet.write_bytes(b"x")
    meta, _, _ = await gen.generate_assessment(
        COUNTS, transcript_files=[empty], extra_files=[sheet], enable_blooms=False,
    )
    assert meta["content_availability"]["uploaded_files"] == {"has_vtt": False, "has_pdf": False}
    assert "UPLOADED" not in llm.prompt


async def test_uploads_are_added_after_course_content(llm, courses, tmp_path):
    talk = tmp_path / "talk.txt"
    talk.write_text("Uploaded talk", encoding="utf-8")
    await gen.generate_assessment(COUNTS, course_ids=["do_2"], transcript_files=[talk], enable_blooms=False)
    transcripts = llm.prompt.split("PDF CONTENT:", 1)[0]
    assert transcripts.index("--- SOURCE: do_2") < transcripts.index("--- UPLOADED TRANSCRIPT: talk.txt")


# --------------------------------------------------------------------------
# Bloom's settings
# --------------------------------------------------------------------------

async def test_blooms_disabled(llm):
    await gen.generate_assessment(COUNTS, enable_blooms=False)
    assert "Strictly Disabled - Do NOT force any specific Bloom's taxonomy mapping" in llm.prompt


@pytest.mark.parametrize("difficulty,expected", [
    ("Beginner", "Remember: 40%, Understand: 40%, Apply: 20%"),
    ("ADVANCED", "Apply: 20%, Analyze: 40%, Evaluate: 30%, Create: 10%"),
    (None, "Remember: 20%, Understand: 25%, Apply: 25%, Analyze: 20%, Evaluate: 10%"),
])
async def test_blooms_defaults_follow_difficulty(llm, difficulty, expected):
    await gen.generate_assessment(COUNTS, difficulty_level=difficulty)
    assert expected in llm.prompt


async def test_blooms_distribution_is_assigned_per_type(llm):
    await gen.generate_assessment({"mcq": 2, "truefalse": 1},
                                  blooms_distribution={"Remember": 50, "Apply": 50})
    first = llm.prompts[0]  # later prompts are top-ups for the short fake reply
    assert "Per-type Bloom's assignment (NON-NEGOTIABLE)" in first
    assert "Multiple Choice Questions (MCQ) (2 questions)" in first
    assert "True/False Questions (1 questions)" in first


async def test_course_folder_without_metadata_still_contributes_content(llm, courses):
    folder = courses / "do_bare"
    folder.mkdir()
    (folder / "english_subtitles.vtt").write_text("WEBVTT\n\nBare course talk\n", encoding="utf-8")
    meta, _, _ = await gen.generate_assessment(COUNTS, course_ids=["do_bare"], enable_blooms=False)
    assert meta["courses"] == []
    assert meta["content_availability"]["do_bare"] == {"has_vtt": True, "has_pdf": False}
    assert "Bare course talk" in llm.prompt


async def test_unreadable_course_file_is_skipped_not_fatal(llm, courses, monkeypatch):
    async def explode(path):
        raise OSError("disk error")

    monkeypatch.setattr(gen, "extract_vtt_text", explode)
    meta, _, _ = await gen.generate_assessment(COUNTS, course_ids=["do_1"], enable_blooms=False)
    assert meta["content_availability"]["do_1"] == {"has_vtt": False, "has_pdf": True}
    assert "Course one transcript" not in llm.prompt
