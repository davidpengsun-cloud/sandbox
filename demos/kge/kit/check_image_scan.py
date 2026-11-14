"""Gate Trivy image reports against temporary, package-specific exceptions with no available fix."""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


DEMO = Path(__file__).resolve().parents[1]
DOCKERFILES = ("Dockerfile", "portal/Dockerfile")
BASE_PIN = re.compile(r"^ARG BASE_IMAGE=(\S+)", re.M)
RED_HAT_UBI = re.compile(r"registry\.access\.redhat\.com/ubi(\d+)/")
SCANNED = frozenset({("redhat", "9")})


def base_systems(demo: Path = DEMO, scanned: frozenset[tuple[str, str]] = SCANNED) -> set[tuple[str, str]]:
    """The operating system family and major version the images are built on, read from each Dockerfile's
    BASE_IMAGE pin: Red Hat's UBI <n> is redhat <n>. Both pins must name the same OS, and it must be one in SCANNED,
    the systems the scanner is known to hold advisories for. Anything else is refused, so a change of base is met by
    a deliberate change here, after confirming the scanner covers the new OS."""
    systems = {}
    for name in DOCKERFILES:
        pin = BASE_PIN.search((demo / name).read_text())
        if not pin:
            raise ValueError(f"{name}: no ARG BASE_IMAGE pin")
        ubi = RED_HAT_UBI.match(pin[1])
        if not ubi:
            raise ValueError(f"{name}: the gate knows no OS for the base {pin[1]}; confirm the scanner has advisory "
                             "data for it, then teach check_image_scan.py its OS")
        system = "redhat", ubi[1]
        if system not in scanned:
            raise ValueError(f"{name}: the base {pin[1]} is {' '.join(system)}, which the scanner is not known to "
                             "cover; confirm it has advisory data for it, then add it to check_image_scan.py's SCANNED")
        systems[name] = system
    if len(set(systems.values())) != 1:
        raise ValueError("the images' bases differ: " + ", ".join(f"{name} {' '.join(system)}"
                                                                for name, system in systems.items()))
    return set(systems.values())


def os_coverage(report: dict, expected: set[tuple[str, str]]) -> list[str]:
    name = report["ArtifactName"]
    metadata = report.get("Metadata")
    system = metadata.get("OS") if isinstance(metadata, dict) else None
    if not isinstance(system, dict) or any(
            not isinstance(system.get(key), str) or not system[key].strip() for key in ("Family", "Name")):
        return [f"{name}: no operating system detected, so no OS package was checked"]
    if (system["Family"], system["Name"].split(".")[0]) not in expected & SCANNED:
        bases = ", ".join(f"{family} {major}" for family, major in sorted(expected & SCANNED))
        return [f"{name}: scanned OS {system['Family']} {system['Name']} is not the images' base OS ({bases}, from the "
                "Dockerfiles' BASE_IMAGE pins), and the scanner may have no advisories for it; a new base needs a "
                "deliberate change to check_image_scan.py"]
    if system.get("EOSL", False) is not False:
        return [f"{name}: {system['Family']} {system['Name']} is end of support, so the scanner has no advisories for it"]
    if not any(isinstance(result, dict) and result.get("Class") == "os-pkgs" for result in report["Results"]):
        return [f"{name}: no os-pkgs result for {system['Family']} {system['Name']}, so no OS package was checked"]
    return []


def check(exceptions: list[dict], reports: list[dict], today: date,
          expected: set[tuple[str, str]] | None = None) -> list[str]:
    problems = []
    accepted = set()
    if not isinstance(exceptions, list):
        return ["exceptions must be a list"]
    for entry in exceptions:
        if not isinstance(entry, dict) or any(
            not isinstance(entry.get(key), str) or not entry[key].strip()
            for key in ("cve", "package", "reason", "expires")
        ):
            problems.append("each exception needs cve, package, reason and expires")
            continue
        key = entry["cve"], entry["package"]
        if not re.fullmatch(r"CVE-\d{4}-\d{4,}", key[0]):
            problems.append(f"{key}: invalid CVE")
        if key in accepted:
            problems.append(f"{key}: duplicate exception")
        try:
            expiry = date.fromisoformat(entry["expires"])
        except ValueError:
            problems.append(f"{key}: invalid expiry date")
            continue
        if expiry <= today:
            problems.append(f"{key}: exception expired on {expiry}")
        if expiry > today + timedelta(days=30):
            problems.append(f"{key}: exception must expire within 30 days")
        accepted.add(key)
    if expected is None and reports:
        expected = base_systems()
    for report in reports:
        if (not isinstance(report, dict) or type(report.get("SchemaVersion")) is not int or report["SchemaVersion"] != 2
                or not isinstance(report.get("Results"), list)
                or not isinstance(report.get("ArtifactName"), str) or not report["ArtifactName"].strip()):
            problems.append("expected a Trivy schema 2 report with Results (list) and ArtifactName (nonempty string)")
            continue
        problems.extend(os_coverage(report, expected))
        for result in report["Results"]:
            if not isinstance(result, dict):
                problems.append("invalid Trivy result")
                continue
            findings = result.get("Vulnerabilities", [])
            if not isinstance(findings, list):
                problems.append("invalid Trivy vulnerabilities")
                continue
            for finding in findings:
                if not isinstance(finding, dict) or any(
                    not isinstance(finding.get(key), str) or not finding[key].strip()
                    for key in ("Severity", "VulnerabilityID", "PkgName")
                ):
                    problems.append("invalid Trivy finding")
                    continue
                if "FixedVersion" in finding and not isinstance(finding["FixedVersion"], str):
                    problems.append("invalid Trivy finding FixedVersion: expected a string")
                    continue
                if finding["Severity"] not in {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}:
                    problems.append("invalid Trivy finding Severity")
                    continue
                if finding["Severity"] not in {"HIGH", "CRITICAL"}:
                    continue
                key = finding["VulnerabilityID"], finding["PkgName"]
                if finding.get("FixedVersion", "").strip():
                    problems.append(f"{key}: fix available: {finding['FixedVersion']}")
                elif key not in accepted:
                    problems.append(f"{key}: no unexpired exception")
    return problems


def main(paths: list[str]) -> int:
    if len(paths) < 2:
        print("usage: check_image_scan.py <exceptions.json> <trivy.json> [<trivy.json> ...]", file=sys.stderr)
        return 2
    try:
        exceptions, *reports = [json.loads(Path(path).read_text()) for path in paths]
        problems = check(exceptions, reports, datetime.now(timezone.utc).date())
    except (OSError, ValueError) as exc:
        print(f"image scan refused: {exc}", file=sys.stderr)
        return 1
    for problem in problems:
        print(f"image scan refused: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"Image scan passed: {len(reports)} reports, each with its OS packages checked, no fixable HIGH/CRITICAL "
          "findings; all others excepted.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
