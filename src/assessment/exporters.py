import html
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from annotated_types import doc
from docx import Document
from docx.shared import Pt, Inches

# WeasyPrint Import
from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration

from .questions import (
    BUCKET_ACTION_PLANNING,
    BUCKET_OPEN_TEXT,
    BUCKET_RANKING,
    BUCKET_SCENARIO_MCQ,
    BUCKET_SCENARIO_MULTISELECT,
    BUCKET_SHORT_ANSWER,
    RUBRIC_ANCHOR_LEVELS,
    RUBRIC_BUCKETS,
    SCENARIO_BUCKETS,
    answers_by_single_option,
    iter_questions_in_order,
)

logger = logging.getLogger(__name__)

PROVENANCE_LABELS = {
    "ai_generated": "AI-generated",
    "ai_assisted": "AI-assisted (edited by a reviewer)",
    "human_authored": "Human-authored",
}

# Suppress noisy logs from pdf generation libraries
# WeasyPrint and FontTools can be very verbose, especially during font subsetting
# The user specifically requested only error logs.
for logger_name in ["weasyprint", "fontTools", "fontTools.subset", "fontTools.ttLib", "pydyf"]:
    logging.getLogger(logger_name).setLevel(logging.ERROR)

RESOURCE_DIR = Path(__file__).parent / "resources" / "fonts"

DISPLAY_LABELS = {
    "Multiple Choice Question": "Single selection MCQs",
    "Multi-Choice Question": "Multiple selection MCQs",
    BUCKET_SCENARIO_MCQ: "Scenario-based MCQ",
    BUCKET_SCENARIO_MULTISELECT: "Scenario-based multi select",
    BUCKET_OPEN_TEXT: "Open text",
    BUCKET_RANKING: "Situational judgment ranking",
    BUCKET_ACTION_PLANNING: "Action and decision planning",
    BUCKET_SHORT_ANSWER: "Scenario-based short answer",
}

ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# python-docx paragraph style for bulleted lines.
LIST_BULLET = "List Bullet"

CLOSE_DIV = "</div>"


# --------------------------------------------------------------------------
# Scenario questions — shared by the PDF and DOCX exports
# --------------------------------------------------------------------------

def _scenario_parts(q: Dict[str, Any]) -> Dict[str, Any]:
    scenario = q.get("scenario") if isinstance(q.get("scenario"), dict) else {}
    return {
        "title": str(scenario.get("title") or ""),
        "description": str(scenario.get("description") or ""),
        "constraints": [str(c) for c in scenario.get("constraints") or [] if str(c).strip()],
    }


def _option_letter_by_index(options: List[Dict[str, Any]]) -> Dict[int, str]:
    """Option `index` value -> the letter it is displayed under."""
    out: Dict[int, str] = {}
    for i, option in enumerate(options):
        try:
            out[int(option["index"]) if option.get("index") is not None else i] = ALPHA[i]
        except (TypeError, ValueError, IndexError):
            continue
    return out


def _scenario_answer_lines(bucket: str, q: Dict[str, Any]) -> List[str]:
    """The answer key of a scenario question, as label: value lines."""
    options = q.get("options") or []
    letters = _option_letter_by_index(options)

    def letters_for(values: Any) -> List[str]:
        if values is None:
            values = []
        elif not isinstance(values, list):
            values = [values]
        out = []
        for value in values:
            try:
                out.append(letters.get(int(value), "?"))
            except (TypeError, ValueError):
                out.append("?")
        return out

    if bucket == BUCKET_SCENARIO_MCQ:
        return [f"Correct Answer: {', '.join(letters_for(q.get('correct_option_index'))) or 'N/A'}"]
    if bucket == BUCKET_SCENARIO_MULTISELECT:
        return [f"Correct Options: {', '.join(letters_for(q.get('correct_option_index'))) or 'N/A'}"]
    if bucket == BUCKET_RANKING:
        order = " > ".join(letters_for(q.get("correct_ranking"))) or "N/A"
        return [f"Correct Ranking (most to least effective): {order}"]
    if bucket == BUCKET_OPEN_TEXT:
        return [f"Reference Answer: {q.get('reference_answer') or 'N/A'}"]
    if bucket == BUCKET_SHORT_ANSWER:
        return [f"Expected Answer: {q.get('expected_answer') or 'N/A'}"]
    return []


def _scenario_answer_list(bucket: str, q: Dict[str, Any]) -> Optional[tuple]:
    """(heading, items) for the bulleted part of a written answer key, if any."""
    if bucket == BUCKET_ACTION_PLANNING and q.get("expected_components"):
        return "Expected Components", [str(c) for c in q["expected_components"]]
    return None


def _rubric_rows(q: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in q.get("rubric") or [] if isinstance(c, dict)]


def _scenario_question_html(number: int, bucket: str, q: Dict[str, Any]) -> str:
    e = lambda value: html.escape(str(value if value is not None else ""))  # noqa: E731
    parts = _scenario_parts(q)
    out = [
        "<div class='question-block'>",
        f"<div class='type-label'>{e(DISPLAY_LABELS.get(bucket, bucket))}</div>",
        "<div class='scenario-box'>",
        f"<b>Scenario: {e(parts['title'])}</b><br/>{e(parts['description'])}",
    ]
    if parts["constraints"]:
        out.append("<br/><i>Constraints:</i><ul>"
                   + "".join(f"<li>{e(c)}</li>" for c in parts["constraints"]) + "</ul>")
    out.append(CLOSE_DIV)
    out.append(f"<div class='question-text'>Q{number}: {e(q.get('question_text'))}</div>")

    options = q.get("options") or []
    if options:
        out.append("<ul class='options-list'>" + "".join(
            f"<li>{ALPHA[i]}. {e(o.get('text'))}</li>" for i, o in enumerate(options[:len(ALPHA)])
        ) + "</ul>")
    for line in _scenario_answer_lines(bucket, q):
        out.append(f"<div class='correct'>{e(line)}</div>")
    answer_list = _scenario_answer_list(bucket, q)
    if answer_list:
        heading, items = answer_list
        out.append(f"<div><b>{e(heading)}:</b><ul>"
                   + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul></div>")

    rubric = _rubric_rows(q) if bucket in RUBRIC_BUCKETS else []
    if rubric:
        header = "".join(f"<th>{level}</th>" for level in RUBRIC_ANCHOR_LEVELS)
        rows = []
        for c in rubric:
            anchors = c.get("score_anchors") if isinstance(c.get("score_anchors"), dict) else {}
            evidence = "<br/>".join(f"• {e(x)}" for x in c.get("evidence_indicators") or [])
            rows.append(
                f"<tr><td><b>{e(c.get('criterion'))}</b><br/>{e(c.get('description'))}"
                + f"</td><td>{e(c.get('weight_percent'))}%</td>"
                + "".join(f"<td>{e(anchors.get(level))}</td>" for level in RUBRIC_ANCHOR_LEVELS)
                + f"<td>{evidence}</td></tr>"
            )
        out.append("<div class='rubric'><b>Rubric</b><table class='rubric-table'>"
                   f"<tr><th>Criterion</th><th>Weight</th>{header}<th>Evidence</th></tr>"
                   + "".join(rows) + "</table></div>")

    ar = q.get("answer_rationale") if isinstance(q.get("answer_rationale"), dict) else {}
    provenance = PROVENANCE_LABELS.get(q.get("provenance"), q.get("provenance") or "N/A")
    box = [
        f"<b>Rationale:</b> {e(ar.get('correct_answer_explanation') or 'N/A')}",
        f"<b>Scenario Domain:</b> {e(q.get('scenario_domain') or 'N/A')}",
        f"<b>Provenance:</b> {e(provenance)}",
    ]
    out.append("<div class='reasoning-box'>" + "<br/>".join(box) + CLOSE_DIV)
    out.append(CLOSE_DIV)
    return "\n".join(out)


def _add_scenario_question_docx(doc, number: int, bucket: str, q: Dict[str, Any]) -> None:
    parts = _scenario_parts(q)

    doc.add_paragraph().add_run(DISPLAY_LABELS.get(bucket, bucket)).italic = True
    doc.add_paragraph().add_run(f"Scenario: {parts['title']}").bold = True
    doc.add_paragraph(parts["description"])
    if parts["constraints"]:
        doc.add_paragraph().add_run("Constraints:").italic = True
        for constraint in parts["constraints"]:
            doc.add_paragraph(constraint, style=LIST_BULLET)

    doc.add_paragraph().add_run(f"{number}. {q.get('question_text') or 'N/A'}").bold = True

    for i, option in enumerate((q.get("options") or [])[:len(ALPHA)]):
        doc.add_paragraph(f"{ALPHA[i]}. {option.get('text', '')}", style=LIST_BULLET)
    for line in _scenario_answer_lines(bucket, q):
        doc.add_paragraph().add_run(line).bold = True
    answer_list = _scenario_answer_list(bucket, q)
    if answer_list:
        heading, items = answer_list
        doc.add_paragraph().add_run(f"{heading}:").bold = True
        for item in items:
            doc.add_paragraph(item, style=LIST_BULLET)

    rubric = _rubric_rows(q) if bucket in RUBRIC_BUCKETS else []
    if rubric:
        doc.add_paragraph().add_run("Rubric").bold = True
        headers = ["Criterion", "Weight", *RUBRIC_ANCHOR_LEVELS, "Evidence"]
        table = doc.add_table(rows=1, cols=len(headers))
        table.style = 'Table Grid'
        for cell, text in zip(table.rows[0].cells, headers):
            cell.text = text
        for c in rubric:
            anchors = c.get("score_anchors") if isinstance(c.get("score_anchors"), dict) else {}
            evidence = "\n".join(f"• {x}" for x in c.get("evidence_indicators") or [])
            criterion = f"{c.get('criterion', '')}\n{c.get('description', '')}".strip()
            cells = table.add_row().cells
            values = [criterion, f"{c.get('weight_percent', '')}%",
                      *[str(anchors.get(level) or "") for level in RUBRIC_ANCHOR_LEVELS],
                      evidence]
            for cell, text in zip(cells, values):
                cell.text = text

    ar = q.get("answer_rationale") if isinstance(q.get("answer_rationale"), dict) else {}
    doc.add_paragraph().add_run(
        f"Rationale: {ar.get('correct_answer_explanation') or 'N/A'}").italic = True
    doc.add_paragraph(f"Scenario Domain: {q.get('scenario_domain') or 'N/A'}")
    doc.add_paragraph(
        f"Provenance: {PROVENANCE_LABELS.get(q.get('provenance'), q.get('provenance') or 'N/A')}"
    )
    doc.add_paragraph()


def get_css_font_faces() -> str:
    """Generates CSS @font-face rules for all available Noto fonts."""
    font_map = {
        "NotoSansDevanagari-Regular.ttf": "NotoSansDevanagari",
        "NotoSansTamil-Regular.ttf": "NotoSansTamil",
        "NotoSansTelugu-Regular.ttf": "NotoSansTelugu",
        "NotoSansKannada-Regular.ttf": "NotoSansKannada",
        "NotoSansMalayalam-Regular.ttf": "NotoSansMalayalam",
        "NotoSansBengali-Regular.ttf": "NotoSansBengali",
        "NotoSansGujarati-Regular.ttf": "NotoSansGujarati",
        "NotoSansGurmukhi-Regular.ttf": "NotoSansGurmukhi"
    }
    
    DISPLAY_LABELS = {
        "Multiple Choice Question": "Single selection MCQs",
        "Multi-Choice Question": "Multiple selection MCQs",
    }
    
    css = []
    for filename, font_family in font_map.items():
        font_path = RESOURCE_DIR / filename
        if font_path.exists():
            # WeasyPrint needs file:// URI for local files or absolute paths
            css.append(f"""
            @font-face {{
                font-family: '{font_family}';
                src: url('file://{font_path.absolute()}');
            }}""")
            
    return "\n".join(css)

def generate_html_content(assessment_data: dict) -> str:
    """Constructs the HTML report string."""
    blueprint = assessment_data.get("blueprint", {})

    # Audit Data
    prompt_ver = blueprint.get("prompt_version", "N/A")
    api_ver = blueprint.get("api_version", "N/A")
    scope = blueprint.get('assessment_scope_summary', 'N/A')
    
    # CSS
    font_faces = get_css_font_faces()
    # Pango Stack: Put specific fonts first, then sans-serif fallback
    font_stack = "'NotoSansMalayalam', 'NotoSansDevanagari', 'NotoSansTamil', 'NotoSansTelugu', 'NotoSansKannada', 'NotoSansBengali', 'NotoSansGujarati', 'NotoSansGurmukhi', sans-serif"

    html_parts = ["""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            %s
            body {
                font-family: %s;
                font-size: 11pt;
                line-height: 1.5;
                color: #333;
                margin: 40px;
            }
            h1 { font-size: 24pt; color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }
            h2 { font-size: 18pt; color: #34495e; margin-top: 30px; }
            h3 { font-size: 14pt; color: #7f8c8d; }
            .audit-table { width: 100%%; border-collapse: collapse; margin-bottom: 20px; }
            .audit-table th, .audit-table td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            .audit-table th { background-color: #f2f2f2; }
            .question-block { margin-bottom: 25px; page-break-inside: avoid; }
            .question-text { font-weight: bold; font-size: 12pt; margin-bottom: 8px; }
            .options-list { list-style-type: none; padding-left: 20px; }
            .options-list li { margin-bottom: 4px; }
            .reasoning-box { background-color: #f8f9fa; border-left: 4px solid #3498db; padding: 10px; margin-top: 10px; font-size: 10pt; }
            .correct { color: #27ae60; font-weight: bold; }
            .type-label { font-size: 9pt; color: #7f8c8d; text-transform: uppercase; letter-spacing: 0.5px; }
            .type-summary { font-size: 10pt; color: #555; }
            .scenario-box { background-color: #fdf8ec; border-left: 4px solid #e0a800; padding: 10px; margin-bottom: 8px; }
            .rubric-table { width: 100%%; table-layout: fixed; border-collapse: collapse; margin-top: 6px; font-size: 8.5pt; }
            .rubric-table th, .rubric-table td { border: 1px solid #ddd; padding: 4px; vertical-align: top; text-align: left;
                                                 box-sizing: border-box; overflow-wrap: anywhere; word-break: break-word; }
            .rubric-table th:nth-child(1) { width: 18%%; }
            .rubric-table th:nth-child(2) { width: 8%%; }
            .rubric-table th:nth-child(n+3) { width: 14%%; }
            .rubric-table th:nth-child(7) { width: 18%%; }
            .rubric-table th { background-color: #f2f2f2; }
        </style>
    </head>
    <body>
        <h1>Course Assessment Report</h1>
        
        <p><b>Assessment Scope:</b> %s</p>
        
        <h2>Questions & Reasoning</h2>
    """ % (font_faces, font_stack, scope)]

    # Questions follow the assessment's authoritative sequence.
    # Rendered as one ordered list rather than grouped under per-type headings,
    # so the numbering here matches the CSV, JSON and DOCX exports. The
    # type is shown as a label on each question instead.
    type_counts = {}
    for bucket, _ in iter_questions_in_order(assessment_data):
        type_counts[bucket] = type_counts.get(bucket, 0) + 1
    if type_counts:
        summary = ", ".join(
            f"{DISPLAY_LABELS.get(bucket, bucket)}: {count}"
            for bucket, count in type_counts.items()
        )
        html_parts.append(f"<p class='type-summary'><b>Question types:</b> {summary}</p>")

    for q_counter, (q_type, q) in enumerate(iter_questions_in_order(assessment_data), start=1):
        if q_type in SCENARIO_BUCKETS:
            html_parts.append(_scenario_question_html(q_counter, q_type, q))
            continue

        if q_type == "MTF Question":
            default_q_txt = q.get("matching_context", "Match the following items appropriately:")
        else:
            default_q_txt = "N/A"

        q_txt = q.get("question_text", default_q_txt)
        display_label = DISPLAY_LABELS.get(q_type, q_type)
        q_html = f"""
        <div class="question-block">
            <div class="type-label">{display_label}</div>
            <div class="question-text">Q{q_counter}: {q_txt}</div>
            <div style="font-size: 0.85em; color: #666; margin-bottom: 8px;"><i>Source Course: {q.get('course_name', 'N/A')}</i></div>
        """

        # Options / Body
        ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

        # MCQ and option FTB render alike; a legacy FTB falls through to `Answer:`.
        if answers_by_single_option(q_type, q):
            options = q.get("options", [])
            opts_html = "".join([f"<li>{ALPHA[i]}. {o.get('text', '')}</li>" for i, o in enumerate(options)])
            correct_idx = q.get('correct_option_index')
            correct_set = {int(correct_idx)} if correct_idx is not None else set()
            correct_labels = [
                ALPHA[i] for i, o in enumerate(options)
                if (int(o["index"]) if o.get("index") is not None else i) in correct_set
            ]
            q_html += f"<ul class='options-list'>{opts_html}</ul>"
            q_html += f"<div class='correct'>Correct Answer: {correct_labels[0] if correct_labels else 'N/A'}</div>"

        elif q_type == "MTF Question":
            pairs_html = "".join([f"<li>{ALPHA[i]}. {p.get('left')} &rarr; {p.get('right')}</li>" for i, p in enumerate(q.get("pairs", []))])
            q_html += f"<ul class='options-list'>{pairs_html}</ul>"

        elif q_type == "Multi-Choice Question":
            options = q.get("options", [])
            opts_html = "".join([f"<li>{ALPHA[i]}. {o.get('text', '')}</li>" for i, o in enumerate(options)])
            corr = q.get('correct_option_index')
            correct_set = {int(x) for x in corr} if isinstance(corr, list) else ({int(corr)} if corr is not None else set())
            correct_labels = [
                ALPHA[i] for i, o in enumerate(options)
                if (int(o["index"]) if o.get("index") is not None else i) in correct_set
            ]
            q_html += f"<ul class='options-list'>{opts_html}</ul>"
            q_html += f"<div class='correct'>Correct Options: {', '.join(correct_labels) if correct_labels else 'N/A'}</div>"

        elif q_type == "True/False Question":
            q_html += "<ul class='options-list'><li>A. True</li><li>B. False</li></ul>"
            q_html += f"<div class='correct'>Correct Answer: {q.get('correct_answer')}</div>"

        else:
            q_html += f"<div class='correct'>Answer: {q.get('correct_answer')}</div>"

        # Reasoning Box
        rs = q.get("reasoning", {})
        ar = q.get("answer_rationale", {})
        kcm = rs.get("competency_alignment", {}).get("kcm", {})
        provenance = PROVENANCE_LABELS.get(q.get("provenance"), q.get("provenance") or "N/A")
        q_html += f"""
            <div class="reasoning-box">
                <b>Rationale:</b> {ar.get('correct_answer_explanation', 'N/A')}<br/>
                <b>Bloom's Level:</b> {q.get('blooms_level', 'N/A')} ({rs.get('blooms_level_justification', 'N/A')})<br/>
                <b>Learning Objective:</b> {rs.get('learning_objective_alignment', 'N/A')}<br/>
                <b>Competency:</b> {kcm.get('competency_area', 'N/A')} - {kcm.get('competency_theme', 'N/A')}<br/>
                <b>Relevance:</b> {q.get('relevance_percentage', 'N/A')}%<br/>
                <b>Provenance:</b> {provenance}
            </div>
        </div>
        """
        html_parts.append(q_html)

    html_parts.append("</body></html>")
    return "\n".join(html_parts)

def generate_pdf(assessment_data: dict, output_path: Path):
    """Generates a PDF report using WeasyPrint (HTML-to-PDF)."""
    try:
        html_content = generate_html_content(assessment_data)
        font_config = FontConfiguration()
        HTML(string=html_content).write_pdf(target=str(output_path), font_config=font_config)
        logger.info(f"Generated PDF with WeasyPrint: {output_path}")
    except Exception as e:
        logger.error(f"WeasyPrint PDF Generation Failed: {e}")
        # Fallback? No, fail explicitly is better than bad boxes.
        raise


def generate_docx(assessment_data: dict, output_path: Path):
    """Generates a DOCX report from the assessment JSON data."""
    doc = Document()

    blueprint = assessment_data.get("blueprint", {})
    doc.add_paragraph(f"Assessment Scope: {blueprint.get('assessment_scope_summary', 'N/A')}")
    
    doc.add_heading('Questions & Reasoning', level=1)

    # One ordered list in the assessment's authoritative sequence, matching the
    # PDF, CSV and JSON exports.
    for position, (q_type, q) in enumerate(iter_questions_in_order(assessment_data), start=1):
        if q_type in SCENARIO_BUCKETS:
            _add_scenario_question_docx(doc, position, q_type, q)
            continue

        if q_type == "MTF Question":
            default_q_txt = q.get("matching_context", "Match the following items appropriately:")
        else:
            default_q_txt = "N/A"

        label_para = doc.add_paragraph()
        label_para.add_run(DISPLAY_LABELS.get(q_type, q_type)).italic = True

        q_para = doc.add_paragraph()
        q_para.add_run(f"{position}. {q.get('question_text', default_q_txt)}").bold = True

        # Course Tag
        c_para = doc.add_paragraph()
        c_para.add_run(f"Source Course: {q.get('course_name', 'N/A')}").italic = True

        ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

        # MCQ and option FTB render alike; a legacy FTB falls through to `Answer:`.
        if answers_by_single_option(q_type, q):
            options = q.get("options", [])
            for i, opt in enumerate(options):
                doc.add_paragraph(f"{ALPHA[i]}. {opt.get('text', '')}", style=LIST_BULLET)
            correct_idx = q.get('correct_option_index')
            correct_set = {int(correct_idx)} if correct_idx is not None else set()
            correct_labels = [
                ALPHA[i] for i, o in enumerate(options)
                if (int(o["index"]) if o.get("index") is not None else i) in correct_set
            ]
            p = doc.add_paragraph()
            p.add_run(f"Correct Answer: {correct_labels[0] if correct_labels else 'N/A'}").bold = True

        elif q_type == "MTF Question":
            for i, p_item in enumerate(q.get("pairs", [])):
                doc.add_paragraph(f"{ALPHA[i]}. {p_item.get('left')} -> {p_item.get('right')}", style=LIST_BULLET)

        elif q_type == "Multi-Choice Question":
            options = q.get("options", [])
            for i, opt in enumerate(options):
                doc.add_paragraph(f"{ALPHA[i]}. {opt.get('text', '')}", style=LIST_BULLET)
            corr = q.get('correct_option_index')
            correct_set = {int(x) for x in corr} if isinstance(corr, list) else ({int(corr)} if corr is not None else set())
            correct_labels = [
                ALPHA[i] for i, o in enumerate(options)
                if (int(o["index"]) if o.get("index") is not None else i) in correct_set
            ]
            p = doc.add_paragraph()
            p.add_run(f"Correct Options: {', '.join(correct_labels) if correct_labels else 'N/A'}").bold = True

        elif q_type == "True/False Question":
            doc.add_paragraph("A. True", style=LIST_BULLET)
            doc.add_paragraph("B. False", style=LIST_BULLET)
            p = doc.add_paragraph()
            p.add_run(f"Correct Answer: {q.get('correct_answer')}").bold = True

        else:
            p = doc.add_paragraph()
            p.add_run(f"Answer: {q.get('correct_answer')}").bold = True

        # Reasoning
        reasoning = q.get("reasoning", {})
        ar = q.get("answer_rationale", {})
        kcm = reasoning.get("competency_alignment", {}).get("kcm", {})

        r_para = doc.add_paragraph()
        r_para.add_run(f"Rationale: {ar.get('correct_answer_explanation', 'N/A')}").italic = True

        blooms_para = doc.add_paragraph()
        blooms_para.add_run(f"Bloom's: {q.get('blooms_level', 'N/A')} ({reasoning.get('blooms_level_justification', 'N/A')}) | Relevance: {q.get('relevance_percentage', 'N/A')}%")

        lo_para = doc.add_paragraph()
        lo_para.add_run(f"Learning Objective: {reasoning.get('learning_objective_alignment', 'N/A')}")

        comp_para = doc.add_paragraph()
        comp_para.add_run(f"Competency: {kcm.get('competency_area', 'N/A')} - {kcm.get('competency_theme', 'N/A')}")

        prov_para = doc.add_paragraph()
        prov_para.add_run(
            f"Provenance: {PROVENANCE_LABELS.get(q.get('provenance'), q.get('provenance') or 'N/A')}"
        )

        doc.add_paragraph()

    doc.save(str(output_path))
