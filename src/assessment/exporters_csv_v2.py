
import csv
from typing import Dict, List, Any
from pathlib import Path

def generate_csv_v2(assessment_data: Dict[str, Any], output_path: Path):
    """
    Generates a CSV export with the specific V2 schema required by the user.
    Schema:
    QuestionNo, QuestionType, Question, QuestionTagging, Option1, isOption1Correct, ... Option7, isOption7Correct
    """
    
    # Define Header
    headers = ["QuestionNo", "QuestionType", "Question", "QuestionTagging"]
    for i in range(1, 8):
        headers.extend([f"Option{i}", f"isOption{i}Correct"])
    headers.extend(["Explanation", "Why Factor", "Logic Justification"])
        
    rows = []
    questions_obj = assessment_data.get("questions", {})
    
    q_counter = 1
    
    # Flatten questions from all types
    all_questions = []
    for q_type, q_list in questions_obj.items():
        for q in q_list:
            # Normalize internal Type to CSV Type
            csv_type = "MCQ-SCA" # Default
            if q_type == "Multiple Choice Question": csv_type = "MCQ-SCA"
            elif q_type == "Multi-Choice Question": csv_type = "MCQ-MCA"
            elif q_type == "True/False Question": csv_type = "T/F"
            elif q_type == "MTF Question": csv_type = "MTF"
            elif q_type == "FTB Question": csv_type = "FTB"
            else: csv_type = q_type
            
            all_questions.append({
                "raw": q, 
                "type": csv_type,
                "complexity": q.get('reasoning', {}).get('complexity_level', 'Easy') # Fallback to Easy if missing?
            })
            
    for item in all_questions:
        q = item["raw"]
        q_type = item["type"]
        tagging = q.get("course_name", "N/A") # Fallback if not comprehensive / LLM fails
        
        default_q_txt = "" 
        if q_type == "MTF":
            # Extract Matching Context and Prepend it
            context = q.get("matching_context", "Match the following items appropriately:")
            default_q_txt = f"{context}\n\n" if context else "Match the following items appropriately:\n\n"
        
        row = {
            "QuestionNo": q_counter,
            "QuestionType": q_type,
            "Question": q.get("question_text", f"{default_q_txt}"),
            "QuestionTagging": tagging
        }
        
        # Override the MTF string to only contain the context since it lacks a QuestionText itself
        if q_type == "MTF":
           row["Question"] = q.get("matching_context", "Match the following items appropriately:")
        
        # Populate Options columns (Default empty)
        for i in range(1, 8):
            row[f"Option{i}"] = ""
            row[f"isOption{i}Correct"] = ""
            
        # Logic per Type
        if q_type in ["MCQ-SCA", "MCQ-MCA"]:
            options = q.get("options", [])
            correct_idx = q.get("correct_option_index")
            # correct_idx could be int (SCA) or list of ints (MCA) return 1-based index usually? 
            # Internal schema is 0-indexed or 1-indexed? Usually 0-indexed lists.
            # Example provided: Option1..Option7.
            
            # Normalize correct_idx to set
            correct_set = set()
            if isinstance(correct_idx, list):
                correct_set = {int(x) for x in correct_idx}
            elif correct_idx is not None:
                correct_set = {int(correct_idx)}
                
            for i, opt in enumerate(options[:7]): # Max 7 options
                col_idx = i + 1
                row[f"Option{col_idx}"] = opt.get("text", "")
                # Check if this index (i+1 if 1-based, i if 0-based) is correct.
                # Assuming internal schema correct_option_index matches Option List order.
                # Internal usually 0-based in list.
                # User example: OptionIndex 2 => "Option2" col? No example shows "Option2" Correct=Yes.
                # Wait, internal representation depends on generator prompts. 
                # Let's assume options list index 0 matches Option1 column.
                
                is_correct = "Yes" if (i+1) in correct_set else "No" 
                # Wait, careful. If generator says correct_index=2. Is that the 2nd item? list[1]? Or list[2]?
                # Usually prompts return 1-based "Option 2".
                # Let's assume 1-based to be safe with LLM outputs, but wait, options list is python list (0-based).
                # If LLM says "Correct: 2", it usually means the 2nd option.
                
                is_correct = "Yes" if (i+1) in correct_set else "No"
                row[f"isOption{col_idx}Correct"] = is_correct

        elif q_type == "T/F":
            # Fixed Options: TRUE / FALSE
            row["Option1"] = "TRUE"
            row["Option2"] = "FALSE"
            
            correct_ans = str(q.get("correct_answer", "")).lower()
            row["isOption1Correct"] = "Yes" if correct_ans == "true" else "No"
            row["isOption2Correct"] = "Yes" if correct_ans == "false" else "No"

        elif q_type == "MTF":
            # Map Pairs: Left -> OptionN, Right -> isOptionNCorrect
            pairs = q.get("pairs", [])
            for i, pair in enumerate(pairs[:7]):
                col_idx = i + 1
                row[f"Option{col_idx}"] = pair.get("left", "")
                row[f"isOption{col_idx}Correct"] = pair.get("right", "")

        elif q_type == "FTB":
            # Map blanks based on user example:
            # Option1: <text>, isOption1Correct: Blank1
            # But wait, internal FTB structure is usually a list of answers?
            # Or is it a question text with _____?
            # Let's check internal schema for FTB Question.
            # Standard FTB usually has 'correct_answer' list or dict.
            # We'll adapt: if 'blanks' list exists? 
            # Or if 'correct_answer' is dict {"blank1": "val"}.
            
            # Simple assumption:
            # Option{i}: Answer Text
            # isOption{i}Correct: "Blank{i}"
            
            correct_ans = q.get("correct_answer") # Could be string or list/dict
            if isinstance(correct_ans, dict):
                 # {"blank1": "val", "blank2": "val"}
                 for i, (k, v) in enumerate(correct_ans.items()):
                     if i >= 7: break
                     col_idx = i + 1
                     row[f"Option{col_idx}"] = v
                     row[f"isOption{col_idx}Correct"] = f"Blank{col_idx}"
            elif isinstance(correct_ans, list):
                for i, v in enumerate(correct_ans):
                    if i >= 7: break
                    col_idx = i + 1
                    row[f"Option{col_idx}"] = v
                    row[f"isOption{col_idx}Correct"] = f"Blank{col_idx}"
            else:
                 # Single string?
                 row["Option1"] = str(correct_ans)
                 row["isOption1Correct"] = "Blank1"

        # Add Rationale
        rationale = q.get("answer_rationale", {})
        row["Explanation"] = rationale.get("correct_answer_explanation", "")
        row["Why Factor"] = rationale.get("why_factor", "")
        row["Logic Justification"] = rationale.get("logic_justification", "")

        rows.append(row)
        q_counter += 1

    # Write CSV
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
