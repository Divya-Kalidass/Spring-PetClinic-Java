
import json
import csv
import subprocess
import sys
import time
import shutil
import re
import xml.etree.ElementTree as ET

from pathlib import Path
from datetime import datetime, timezone


BASE_DIR = Path(__file__).resolve().parents[2]

SELECTION_FILE = BASE_DIR / "sealights_selection.json"

OUTPUT_DIR = BASE_DIR / "execution_output"

REPORT_DIR = BASE_DIR / "target" / "surefire-reports"

JACOCO_XML = BASE_DIR / "target" / "site" / "jacoco" / "jacoco.xml"

PER_TEST_COVERAGE_DIR = OUTPUT_DIR / "per_test_coverage"

TEST_LOG_DIR = OUTPUT_DIR / "test_execution_logs"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PER_TEST_COVERAGE_DIR.mkdir(parents=True, exist_ok=True)
TEST_LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = OUTPUT_DIR / "execution.log"


JACOCO_VERSION = "0.8.12"


def log(message):

    timestamp = datetime.now(timezone.utc).isoformat()

    formatted = f"[{timestamp}] {message}"

    print(formatted, flush=True)

    with open(LOG_FILE, "a", encoding="utf-8") as file:

        file.write(formatted + "\n")


def load_selection():

    with open(SELECTION_FILE, "r", encoding="utf-8") as file:

        selection = json.load(file)

    if selection.get("execution_type") != "java_maven":

        raise ValueError("Unsupported execution type.")

    if not selection.get("tests"):

        raise ValueError("No tests selected.")

    return selection


def get_maven_command():

    wrapper = BASE_DIR / "mvnw"

    if wrapper.exists():

        wrapper.chmod(0o755)

        return ["./mvnw"]

    if shutil.which("mvn"):

        return ["mvn"]

    raise FileNotFoundError("Maven was not found.")


def sanitize_name(value):

    value = re.sub(r"[^A-Za-z0-9_.-]", "_", value)

    return value[:180]


def build_individual_test_list(selection):

    individual_tests = []

    for test in selection.get("tests", []):

        class_name = test.get("test_class")

        methods = test.get("test_methods", [])

        if not class_name:

            continue

        if methods:

            for method in methods:

                individual_tests.append({

                    "test_class": class_name,

                    "test_method": method

                })

        else:

            individual_tests.append({

                "test_class": class_name,

                "test_method": None

            })

    return individual_tests


def build_test_selector(test):

    class_name = test["test_class"]

    method_name = test.get("test_method")

    if method_name:

        return f"{class_name}#{method_name}"

    return class_name


def execute_individual_test(test, index):

    selector = build_test_selector(test)

    safe_name = sanitize_name(selector)

    test_output_dir = PER_TEST_COVERAGE_DIR / f"{index:04d}_{safe_name}"

    test_output_dir.mkdir(parents=True, exist_ok=True)

    test_log = TEST_LOG_DIR / f"{index:04d}_{safe_name}.log"

    # Remove old reports before running the current test.

    if REPORT_DIR.exists():

        shutil.rmtree(REPORT_DIR)

    if JACOCO_XML.exists():

        JACOCO_XML.unlink()

    # command = (

    #     get_maven_command()

    #     + [

    #         "-B",

    #         "clean",

    #         f"org.jacoco:jacoco-maven-plugin:{JACOCO_VERSION}:prepare-agent",

    #         "test",

    #         f"org.jacoco:jacoco-maven-plugin:{JACOCO_VERSION}:report",

    #         "-Dspring-javaformat.validate.skip=true",
            
    #         "-Dmaven.test.failure.ignore=true", 

    #         f"-Dtest={selector}"

    #     ]

    # )
    command = (
        get_maven_command()
        + [
            "-B",
            "clean",
            "test",
            "org.jacoco:jacoco-maven-plugin:0.8.15:report",
            "-Dspring-javaformat.validate.skip=true",
            "-Dmaven.test.failure.ignore=true",
            "-Dsurefire.failIfNoSpecifiedTests=false",
            f"-Dtest={selector}"
        ]
    )

    log("=" * 70)

    log(f"Executing individual test: {selector}")

    log(f"Command: {' '.join(command)}")

    start = time.time()

    try:

        result = subprocess.run(

            command,

            cwd=str(BASE_DIR),

            capture_output=True,

            text=True,

            timeout=2400,

            shell=False

        )

        duration = round(time.time() - start, 2)

        output = (result.stdout or "") + "\n" + (result.stderr or "")

        with open(test_log, "w", encoding="utf-8") as file:

            file.write(output)

        with open(LOG_FILE, "a", encoding="utf-8") as file:

            file.write("\n" + output + "\n")

        log(f"Test Maven exit code: {result.returncode}")

        log(f"Test duration: {duration} seconds")

        coverage_file = None

        if JACOCO_XML.exists():

            coverage_file = test_output_dir / "jacoco.xml"

            shutil.copy2(JACOCO_XML, coverage_file)

            log(f"JaCoCo report copied: {coverage_file}")

        else:

            log("JaCoCo XML was not generated for this test.")

        test_results = parse_surefire_reports()

        for item in test_results:

            item["selected_test"] = selector

            item["individual_execution_index"] = index

            item["jacoco_report"] = (

                str(coverage_file.relative_to(OUTPUT_DIR))

                if coverage_file

                else None

            )

        if not test_results:

            status = "FAILED" if result.returncode != 0 else "UNKNOWN"

            test_results = [{

                "test_class": test["test_class"],

                "test_method": test.get("test_method") or "",

                "status": status,

                "duration_seconds": duration,

                "message": "No Surefire testcase result found.",

                "details": output[-10000:],

                "source_report": "",

                "selected_test": selector,

                "individual_execution_index": index,

                "jacoco_report": (

                    str(coverage_file.relative_to(OUTPUT_DIR))

                    if coverage_file

                    else None

                )

            }]

        return {

            "selector": selector,

            "return_code": result.returncode,

            "duration": duration,

            "results": test_results,

            "jacoco_report": str(coverage_file.relative_to(OUTPUT_DIR))

                if coverage_file else None,

            "log_file": str(test_log.relative_to(OUTPUT_DIR))

        }

    except subprocess.TimeoutExpired as error:

        duration = round(time.time() - start, 2)

        output = str(error)

        with open(test_log, "w", encoding="utf-8") as file:

            file.write(output)

        log(f"Test execution timed out: {selector}")

        return {

            "selector": selector,

            "return_code": -1,

            "duration": duration,

            "results": [{

                "test_class": test["test_class"],

                "test_method": test.get("test_method") or "",

                "status": "ERROR",

                "duration_seconds": duration,

                "message": "Test execution timed out.",

                "details": output,

                "source_report": "",

                "selected_test": selector,

                "individual_execution_index": index,

                "jacoco_report": None

            }],

            "jacoco_report": None,

            "log_file": str(test_log.relative_to(OUTPUT_DIR))

        }


def parse_surefire_reports():

    results = []

    if not REPORT_DIR.exists():

        log("Surefire report directory not found.")

        return results

    for xml_file in REPORT_DIR.glob("TEST-*.xml"):

        try:

            root = ET.parse(xml_file).getroot()

            for case in root.findall(".//testcase"):

                failure = case.find("failure")

                error = case.find("error")

                skipped = case.find("skipped")

                if failure is not None:

                    status = "FAILED"

                    message = failure.get("message", "")

                    details = failure.text or ""

                elif error is not None:

                    status = "ERROR"

                    message = error.get("message", "")

                    details = error.text or ""

                elif skipped is not None:

                    status = "SKIPPED"

                    message = skipped.get("message", "")

                    details = skipped.text or ""

                else:

                    status = "PASSED"

                    message = ""

                    details = ""

                results.append({

                    "test_class": case.get("classname", ""),

                    "test_method": case.get("name", ""),

                    "status": status,

                    "duration_seconds": float(case.get("time", 0)),

                    "message": message,

                    "details": details,

                    "source_report": xml_file.name

                })

        except Exception as error:

            log(f"Report parsing error: {error}")

    return results


def generate_reports(selection, results, return_code, duration):

    summary = {

        "total": len(results),

        "passed": sum(x["status"] == "PASSED" for x in results),

        "failed": sum(x["status"] == "FAILED" for x in results),

        "errors": sum(x["status"] == "ERROR" for x in results),

        "skipped": sum(x["status"] == "SKIPPED" for x in results)

    }

    report = {

        "project": BASE_DIR.name,

        "execution_type": selection["execution_type"],

        "execution_date": datetime.now(timezone.utc).isoformat(),

        "maven_exit_code": return_code,

        "execution_duration_seconds": duration,

        "coverage_collection_mode": "individual_test_execution",

        "summary": summary,

        "tests": results

    }

    with open(

        OUTPUT_DIR / "execution_report.json",

        "w",

        encoding="utf-8"

    ) as file:

        json.dump(report, file, indent=4)

    fields = [

        "test_class",

        "test_method",

        "status",

        "duration_seconds",

        "message",

        "details",

        "source_report",

        "selected_test",

        "individual_execution_index",

        "jacoco_report"

    ]

    with open(

        OUTPUT_DIR / "execution_report.csv",

        "w",

        newline="",

        encoding="utf-8"

    ) as file:

        writer = csv.DictWriter(file, fieldnames=fields)

        writer.writeheader()

        writer.writerows(results)

    log(f"Execution summary: {summary}")

    return summary


def main():

    log("=" * 70)

    log("SEALIGHTS INDIVIDUAL TEST EXECUTION WITH JACOCO")

    log("=" * 70)

    selection = load_selection()

    individual_tests = build_individual_test_list(selection)

    if not individual_tests:

        raise ValueError("No individual test methods were identified.")

    log(f"Individual tests to execute: {len(individual_tests)}")

    all_results = []

    all_execution_codes = []

    total_duration = 0

    for index, test in enumerate(individual_tests, start=1):

        log(f"Test {index}/{len(individual_tests)}")

        execution = execute_individual_test(test, index)

        all_results.extend(execution["results"])

        all_execution_codes.append(execution["return_code"])

        total_duration += execution["duration"]

    overall_return_code = (

        0 if all(code == 0 for code in all_execution_codes) else 1

    )

    summary = generate_reports(

        selection,

        all_results,

        overall_return_code,

        round(total_duration, 2)

    )

    log("=" * 70)

    log("INDIVIDUAL TEST EXECUTION COMPLETED")

    log(f"Summary: {summary}")

    log("=" * 70)

    # Preserve reports even when tests fail.

    if summary["failed"] or summary["errors"]:

        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":

    main()
