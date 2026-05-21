import sys
import traceback
import argparse
import json
import os
import re
import shutil
import subprocess
import time
from google import genai
from google.genai import types
from dotenv import load_dotenv

# ===========================
# START TIMER
# ===========================
start_time = time.perf_counter()

# ===========================
# HELPER FOR GEMINI CALLS
# ===========================
def call_gemini_with_retry(prompt, retries=5, backoff=2):
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                )
            )
            return response
        except Exception as e:
            error_msg = str(e)
            if "503" in error_msg or "429" in error_msg or "quota" in error_msg.lower() or "demand" in error_msg.lower():
                wait_time = backoff * (2 ** attempt)
                print(f"  [API rate limit or high demand. Retrying in {wait_time}s...] (Attempt {attempt+1}/{retries})")
                time.sleep(wait_time)
            else:
                raise e
    raise Exception(f"Failed to call Gemini API after {retries} attempts.")

# ===========================
# LOAD API KEY
# ===========================
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY not found in .env file")

client = genai.Client(api_key=api_key)

# ===========================
# DIRECTORIES
# ===========================
program_folder = "Programs"
testcase_folder = "TestCases"
report_folder = "Report"

os.makedirs(testcase_folder, exist_ok=True)
try:
    if os.path.exists(testcase_folder):
        os.chmod(testcase_folder, 0o777)
except Exception:
    pass

os.makedirs(report_folder, exist_ok=True)
try:
    if os.path.exists(report_folder):
        os.chmod(report_folder, 0o777)
except Exception:
    pass

# ===========================
# INPUT PYTHON FILE
# ===========================
parser = argparse.ArgumentParser(description="Generate test cases for a Python program.")
parser.add_argument("file", help="Path to the Python file (e.g., Programs/sample.py)")
args = parser.parse_args()

file_path = args.file
file_name = os.path.basename(file_path)
file_name_base = os.path.splitext(file_name)[0]

if not os.path.isfile(file_path):
    raise FileNotFoundError(f"{file_path} not found.")

with open(file_path, "r", encoding="utf-8") as f:
    python_code = f.read()

# ===========================
# CREATE OUTPUT DIRECTORY
# ===========================
output_dir = os.path.join(
    testcase_folder,
    f"{file_name_base}_testcases"
)
if os.path.exists(output_dir):
    try:
        os.chmod(output_dir, 0o777)
    except Exception:
        pass
os.makedirs(output_dir, exist_ok=True)
try:
    os.chmod(output_dir, 0o777)
except Exception:
    pass

shutil.copy(file_path, output_dir)

# Base name for report files
report_base_name = os.path.splitext(file_name)[0]

# Single consolidated report file
consolidated_report_file = os.path.join(
    report_folder,
    f"{report_base_name}_consolidated_report.txt"
)

# Initialize the consolidated report file (overwrite if exists)
with open(consolidated_report_file, "w") as report:
    report.write("="*50 + "\n")
    report.write(f"TEST CASE GENERATION REPORT FOR: {file_name}\n")
    report.write(f"Started at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    report.write("="*50 + "\n\n")

# ===========================
# LOOP VARIABLES
# ===========================
k = 0
max_iterations = 10
cache = set()
original_dir = os.getcwd()
output_dir = os.path.abspath(output_dir)
file_name_base = os.path.splitext(file_name)[0]
refined_prompt = None  # Track refined prompt from feedback LLMs

# Track final coverage for summary
total_coverage = 0.0
line_coverage = 0.0
branch_coverage = 0.0

def count_input_calls(code):
    """Count the number of input() calls in the Python source code."""
    return len(re.findall(r'\binput\s*\(', code))

def analyze_input_patterns(code):
    """Analyze how each input() call is used to determine expected types.
    Returns a list of descriptions for each input() call in order."""
    patterns = []
    for line in code.splitlines():
        # Find lines containing input()
        if not re.search(r'\binput\s*\(', line):
            continue
        line_stripped = line.strip()
        # Detect ord(input()[0]) pattern - expects a single character
        if re.search(r'ord\s*\(\s*input\s*\(\s*\)', line_stripped):
            patterns.append("single CHARACTER (used with ord(), e.g. 'a', 'Z', '0', '!', chr(0)-chr(255))")
        # Detect int(input()) pattern - expects an integer
        elif re.search(r'int\s*\(\s*input\s*\(', line_stripped):
            patterns.append("INTEGER number")
        # Detect float(input()) pattern - expects a float
        elif re.search(r'float\s*\(\s*input\s*\(', line_stripped):
            patterns.append("FLOAT number")
        else:
            patterns.append("STRING value")
    return patterns

# Count input() calls so LLM knows exactly how many values to generate
num_inputs = count_input_calls(python_code)
input_patterns = analyze_input_patterns(python_code)
print(f"Detected {num_inputs} input() call(s) in {file_name}")
if input_patterns:
    print("Input pattern analysis:")
    for i, p in enumerate(input_patterns, 1):
        print(f"  Input #{i}: {p}")

# ===========================
# HELPER FUNCTIONS FOR FEEDBACK LLMs
# ===========================

def extract_coverage_gaps(coverage_json_path, file_name):
    """Extract uncovered lines and branches from coverage JSON."""
    try:
        with open(coverage_json_path, 'r') as f:
            cov_data = json.load(f)
        
        file_data = cov_data.get('files', {}).get(file_name, {})
        
        # Get all lines and missing lines
        executed_lines = file_data.get('executed_lines', [])
        missing_lines = file_data.get('missing_lines', [])
        
        # Get branch data
        missing_branches = []
        excluded_branches = file_data.get('excluded_branches', [])
        branches = file_data.get('branches', {})
        
        # Find uncovered branches
        for branch_info in branches:
            # branch_info is typically [line_num, from_line, to_line, is_covered]
            if len(branch_info) >= 4 and not branch_info[3]:
                missing_branches.append(branch_info[0])  # line number
        
        return {
            'missing_lines': missing_lines,
            'missing_branches': missing_branches,
            'executed_lines': executed_lines
        }
    except Exception as e:
        print(f"Error extracting coverage gaps: {e}")
        return {'missing_lines': [], 'missing_branches': [], 'executed_lines': []}

def get_line_coverage_feedback(python_code, missing_lines, existing_prompt):
    """LLM-2: Analyze uncovered lines and suggest prompt refinements."""
    if not missing_lines:
        return ""
    
    feedback_prompt = f"""You are a test case generation expert analyzing code coverage gaps.

Python Code:
{python_code}

Uncovered Lines: {missing_lines}

Current Prompt Strategy:
{existing_prompt}

Analyze why these specific lines are not being covered by the current test generation strategy.
Suggest refinements to the prompt that would help generate test cases targeting these uncovered lines.

Output format (JSON):
{{
  "analysis": "Brief analysis of why lines are uncovered",
  "prompt_refinement": "Specific additions/changes to the prompt to cover these lines"
}}
"""
    
    try:
        response = call_gemini_with_retry(feedback_prompt)
        feedback = json.loads(response.text.strip())
        return feedback.get('prompt_refinement', '')
    except Exception as e:
        print(f"LLM-2 Error: {e}")
        return ""

def get_branch_coverage_feedback(python_code, missing_branches, existing_prompt):
    """LLM-3: Analyze uncovered branches and suggest prompt refinements."""
    if not missing_branches:
        return ""
    
    feedback_prompt = f"""You are a test case generation expert analyzing branch coverage gaps.

Python Code:
{python_code}

Lines with Uncovered Branches: {missing_branches}

Current Prompt Strategy:
{existing_prompt}

Analyze why these branch conditions are not being covered by the current test generation strategy.
Suggest refinements to the prompt that would help generate test cases targeting edge cases and alternate branch paths.

Output format (JSON):
{{
  "analysis": "Brief analysis of uncovered branch conditions",
  "prompt_refinement": "Specific additions/changes to the prompt to cover these branches"
}}
"""
    
    try:
        response = call_gemini_with_retry(feedback_prompt)
        feedback = json.loads(response.text.strip())
        return feedback.get('prompt_refinement', '')
    except Exception as e:
        print(f"LLM-3 Error: {e}")
        return ""

def merge_prompt_refinements(base_prompt, line_refinement, branch_refinement):
    """Prompt Merger: Combine line and branch feedback into refined prompt."""
    refinements = []
    
    if line_refinement:
        refinements.append(f"Line Coverage Focus: {line_refinement}")
    
    if branch_refinement:
        refinements.append(f"Branch Coverage Focus: {branch_refinement}")
    
    if not refinements:
        return base_prompt
    
    refined = base_prompt + "\n\nAdditional Focus Areas:\n" + "\n".join(refinements)
    return refined

# ===========================
# MAIN LOOP
# ===========================
while k < max_iterations:

    iteration_start = time.perf_counter()

    # Build input type description for the prompt
    input_type_desc = ""
    if input_patterns:
        lines_desc = []
        for i, p in enumerate(input_patterns, 1):
            lines_desc.append(f"  Input #{i}: expects a {p}")
        input_type_desc = "\nDetailed input types (in order):\n" + "\n".join(lines_desc) + "\n"

    # Use refined prompt if available from previous iteration
    base_prompt = f"""
Generate diverse test values for the following Python program.

IMPORTANT: This program calls input() exactly {num_inputs} time(s).
Each test case MUST contain exactly {num_inputs} value(s), one per input() call, in the order they are called.
{input_type_desc}
Pay close attention to how each input is parsed in the code.
- If input is used as ord(input()[0]), provide a SINGLE CHARACTER (like "a", "Z", "5", "!", etc.)
- If input is used as int(input()), provide an INTEGER
- If input is used as float(input()), provide a FLOAT

Include diverse cases:
- For character inputs: lowercase letters, uppercase letters, digits, special characters, boundary chars (chr(0), chr(127), chr(255))
- For integer inputs: 0, 1, -1, large positive, large negative, boundary values
- Edge cases and boundary values

Output format must be a JSON object with a "test_cases" key containing a list of lists.
Each inner list must have exactly {num_inputs} element(s) in the order the program reads them.
Example (for a program with {num_inputs} input() calls):
{{
  "test_cases": [
    {json.dumps([1] * num_inputs)},
    {json.dumps([0] * num_inputs)},
    {json.dumps([-1] * num_inputs)}
  ]
}}

Do not include explanations.

Previously generated values:
{list(cache)}

Python Program:
{python_code}
"""
    
    # Use refined prompt if available
    if refined_prompt:
        prompt = refined_prompt
        print(f"\n[Using Refined Prompt from LLM-2 & LLM-3]\n")
    else:
        prompt = base_prompt

    try:
        # ===========================
        # CALL GEMINI
        # ===========================
        response = call_gemini_with_retry(prompt)
        test_cases_text = response.text.strip()
        print(f"\nIteration {k+1} Generated:\n{test_cases_text}")

        # ===========================
        # PARSE LLM OUTPUT
        # ===========================
        try:
            data = json.loads(test_cases_text)
            new_cases = data.get("test_cases", [])
            
            for case in new_cases:
                if isinstance(case, list):
                    values_list = tuple(str(v) for v in case)
                elif isinstance(case, dict):
                    values_list = tuple(str(v) for v in case.values())
                else:
                    continue
                # Only add if it has the right number of inputs
                if num_inputs == 0 or len(values_list) >= num_inputs:
                    cache.add(values_list)
                else:
                    print(f"  [Skipped test case with {len(values_list)} values, need {num_inputs}]")

        except json.JSONDecodeError:
            print(f"Failed to parse JSON: {test_cases_text}")
            continue

        # ===========================
        # WRITE TEST CASE FILES
        # ===========================
        for i, test_case in enumerate(cache, start=1):
            testcase_path = os.path.join(output_dir, f"testcase{i}.txt")
            if os.path.exists(testcase_path):
                try:
                    os.remove(testcase_path)
                except Exception as del_err:
                    print(f"Warning: Could not delete {testcase_path}: {del_err}")
            
            with open(testcase_path, "w") as f:
                f.write("\n".join(test_case))

        # ===========================
        # RUN COVERAGE
        # ===========================
        # ===========================
        # RUN COVERAGE
        # ===========================
        # os.chdir(output_dir) # Removing chdir to avoid state issues

        subprocess.run([sys.executable, "-m", "coverage", "erase"], cwd=output_dir)

        for testcase in cache:
            input_string = "\n".join(testcase)

            subprocess.run(
                [sys.executable, "-m", "coverage", "run", "-a", "--branch", file_name],
                input=input_string,
                text=True,
                cwd=output_dir,
                stderr=subprocess.DEVNULL  # Suppress EOFError traceback noise
            )

        result = subprocess.run(
            [sys.executable, "-m", "coverage", "report"],
            capture_output=True,
            text=True,
            cwd=output_dir
        )

        coverage_output = result.stdout
        print("\nCoverage Output:\n", coverage_output)
        
        # Generate JSON coverage report for gap analysis
        coverage_json_path = os.path.join(output_dir, "coverage.json")
        subprocess.run(
            [sys.executable, "-m", "coverage", "json", "-o", coverage_json_path],
            cwd=output_dir
        )

        # os.chdir(original_dir) # Removing chdir

        # ===========================
        # EXTRACT COVERAGE %
        # ===========================
        # Extract total coverage (last column)
        match_total = re.search(r"TOTAL\s+.*\s+(\d+)%", coverage_output)
        total_coverage = float(match_total.group(1)) if match_total else 0.0
        
        # Extract line and branch coverage separately
        # Coverage report format: Name Stmts Miss Branch BrPart Cover
        # We need to parse the TOTAL line more carefully
        line_coverage = 0.0
        branch_coverage = 0.0
        
        # Parse the TOTAL line to extract individual metrics
        total_line_match = re.search(r"TOTAL\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)%", coverage_output)
        if total_line_match:
            total_stmts = int(total_line_match.group(1))
            total_miss = int(total_line_match.group(2))
            total_branches = int(total_line_match.group(3))
            total_partial = int(total_line_match.group(4))
            total_coverage = float(total_line_match.group(5))
            
            # Calculate line coverage
            if total_stmts > 0:
                line_coverage = ((total_stmts - total_miss) / total_stmts) * 100
            
            # Calculate branch coverage
            if total_branches > 0:
                # Assuming partial branches count as half covered
                branch_coverage = ((total_branches - total_partial) / total_branches) * 100
        else:
            # Fallback to simpler parsing if detailed format not found
            line_coverage = total_coverage

        iteration_time = time.perf_counter() - iteration_start

        # Append iteration report to consolidated file
        with open(consolidated_report_file, "a") as report:
            report.write(f"\n" + "="*50 + "\n")
            report.write(f"ITERATION {k+1} REPORT\n")
            report.write("="*50 + "\n")
            report.write(f"Total Coverage: {total_coverage}%\n")
            report.write(f"Line Coverage: {line_coverage:.2f}%\n")
            report.write(f"Branch Coverage: {branch_coverage:.2f}%\n")
            report.write(f"Time Taken: {iteration_time:.2f}s\n")
            report.write(f"Test Cases So Far: {len(cache)}\n")
            report.write("="*50 + "\n")
            report.write("\nCoverage Output:\n")
            report.write(coverage_output)
            report.write("\n")

        print(f"\nIteration {k+1} Coverage:")
        print(f"  Total Coverage: {total_coverage}%")
        print(f"  Line Coverage: {line_coverage:.2f}%")
        print(f"  Branch Coverage: {branch_coverage:.2f}%")
        
        # ===========================
        # FEEDBACK LLMs (LLM-2 & LLM-3)
        # ===========================
        if total_coverage < 90:  # Only get feedback if we haven't reached target
            print("\n[Analyzing Coverage Gaps with LLM-2 and LLM-3...]")
            
            # Extract coverage gaps from JSON
            gaps = extract_coverage_gaps(coverage_json_path, file_name)
            
            print(f"Missing Lines: {gaps['missing_lines']}")
            print(f"Lines with Missing Branches: {gaps['missing_branches']}")
            
            # Get feedback from LLM-2 (Line Coverage)
            line_refinement = ""
            if gaps['missing_lines']:
                print("\n[Calling LLM-2 for Line Coverage Feedback...]")
                line_refinement = get_line_coverage_feedback(
                    python_code,
                    gaps['missing_lines'],
                    base_prompt
                )
                if line_refinement:
                    print(f"LLM-2 Refinement: {str(line_refinement)[:100]}...")
            
            # Get feedback from LLM-3 (Branch Coverage)
            branch_refinement = ""
            if gaps['missing_branches']:
                print("\n[Calling LLM-3 for Branch Coverage Feedback...]")
                branch_refinement = get_branch_coverage_feedback(
                    python_code,
                    gaps['missing_branches'],
                    base_prompt
                )
                if branch_refinement:
                    print(f"LLM-3 Refinement: {str(branch_refinement)[:100]}...")
            
            # Merge refinements for next iteration
            if line_refinement or branch_refinement:
                print("\n[Merging Prompt Refinements...]")
                refined_prompt = merge_prompt_refinements(
                    base_prompt,
                    line_refinement,
                    branch_refinement
                )
                print("[Refined prompt will be used in next iteration]\n")

        # Check stopping conditions (per flow diagram)
        if total_coverage >= 90:
            print("\n✓ Coverage threshold reached (>= 90%). Stopping.")
            break

    except Exception as e:
        print("Error:", e)
        traceback.print_exc()

    k += 1
    
    # Check max iterations (per flow diagram)
    if k >= max_iterations:
        print(f"\n✓ Max iterations ({max_iterations}) reached. Stopping.")
        break

# ===========================
# FINAL OUTPUT
# ===========================
end_time = time.perf_counter()
total_execution_time = end_time - start_time

print("\n" + "="*50)
print("FINAL RESULTS")
print("="*50)
print(f"Total Iterations: {k}")
print(f"Unique Test Cases Generated: {len(cache)}")
print(f"Final Total Coverage: {total_coverage}%")
print(f"Final Line Coverage: {line_coverage:.2f}%")
print(f"Final Branch Coverage: {branch_coverage:.2f}%")
print(f"Total Execution Time: {total_execution_time:.2f}s")
print("\nAll Test Cases:")
for case in cache:
    print(" ".join(case))

# Append final summary to consolidated report file
with open(consolidated_report_file, "a") as report:
    report.write("\n" + "="*50 + "\n")
    report.write("FINAL SUMMARY\n")
    report.write("="*50 + "\n")
    report.write(f"Total Iterations: {k}\n")
    report.write(f"Unique Test Cases Generated: {len(cache)}\n")
    report.write(f"Final Total Coverage: {total_coverage}%\n")
    report.write(f"Final Line Coverage: {line_coverage:.2f}%\n")
    report.write(f"Final Branch Coverage: {branch_coverage:.2f}%\n")
    report.write(f"Total Execution Time: {total_execution_time:.2f}s\n")
    report.write(f"Completed at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    report.write("="*50 + "\n")
    report.write("\nAll Test Cases:\n")
    for case in cache:
        report.write(" ".join(case) + "\n")

print(f"\nConsolidated report saved to: {consolidated_report_file}")
