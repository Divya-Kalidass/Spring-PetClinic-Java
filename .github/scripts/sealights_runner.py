
import json
import csv
import subprocess
import sys
import time
import shutil
import xml.etree.ElementTree as ET

from pathlib import Path
from datetime import datetime, timezone


BASE_DIR = Path(__file__).resolve().parents[2]

SELECTION_FILE = BASE_DIR / "sealights_selection.json"

OUTPUT_DIR = BASE_DIR / "execution_output"

REPORT_DIR = BASE_DIR / "target" / "surefire-reports"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = OUTPUT_DIR / "execution.log"


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


def build_selector(tests):

    selectors = []

    for test in tests:

        class_name = test["test_class"]

        methods = test.get("test_methods", [])

        if methods:

            selectors.append(
                f"{class_name}#{'+'.join(methods)}"
            )

        else:

            selectors.append(class_name)

    return ",".join(selectors)


def get_maven_command():

    wrapper = BASE_DIR / "mvnw"

    if wrapper.exists():

        wrapper.chmod(0o755)

        return ["./mvnw"]

    if shutil.which("mvn"):

        return ["mvn"]

    raise FileNotFoundError("Maven was not found.")


def execute_tests(selection):

    selector = build_selector(selection["tests"])

    command = (

        get_maven_command()

        + [

            "-B",

            "clean",

            "test",

            "-Dspring-javaformat.validate.skip=true",

            f"-Dtest={selector}"

        ]

    )

    log("Starting Maven test execution.")

    log(f"Selector: {selector}")

    log(f"Command: {' '.join(command)}")

    start = time.time()

    result = subprocess.run(

        command,

        cwd=str(BASE_DIR),

        capture_output=True,

        text=True,

        timeout=2400,

        shell=False

    )

    duration = round(time.time() - start, 2)

    with open(LOG_FILE, "a", encoding="utf-8") as file:

        file.write("\n===== MAVEN STDOUT =====\n")

        file.write(result.stdout or "")

        file.write("\n===== MAVEN STDERR =====\n")

        file.write(result.stderr or "")

    log(f"Maven exit code: {result.returncode}")

    log(f"Duration: {duration} seconds")

    return result.returncode, duration


def parse_reports():

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

                    "duration_seconds": float(
                        case.get("time", 0)
                    ),

                    "message": message,

                    "details": details,

                    "source_report": xml_file.name

                })

        except Exception as error:

            log(f"Report parsing error: {error}")

    return results


def generate_reports(
    selection,
    results,
    return_code,
    duration
):

    summary = {

        "total": len(results),

        "passed": sum(
            x["status"] == "PASSED" for x in results
        ),

        "failed": sum(
            x["status"] == "FAILED" for x in results
        ),

        "errors": sum(
            x["status"] == "ERROR" for x in results
        ),

        "skipped": sum(
            x["status"] == "SKIPPED" for x in results
        )

    }

    report = {

        "project": BASE_DIR.name,

        "execution_type": selection["execution_type"],

        "execution_date": datetime.now(
            timezone.utc
        ).isoformat(),

        "maven_exit_code": return_code,

        "execution_duration_seconds": duration,

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

        "source_report"

    ]

    with open(
        OUTPUT_DIR / "execution_report.csv",
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fields
        )

        writer.writeheader()

        writer.writerows(results)

    log(f"Summary: {summary}")

    return summary


def main():

    log("=" * 60)

    log("SEALIGHTS-LIKE REMOTE TEST EXECUTION")

    log("=" * 60)

    selection = load_selection()

    return_code, duration = execute_tests(selection)

    results = parse_reports()

    summary = generate_reports(

        selection,

        results,

        return_code,

        duration

    )

    if not results or return_code != 0:

        sys.exit(1)

    if summary["failed"] or summary["errors"]:

        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":

    main()
