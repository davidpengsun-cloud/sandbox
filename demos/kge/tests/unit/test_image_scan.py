"""The kit's image gate refuses fixes, unaccepted findings and expired exceptions."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml

from kge.pipeline.settings import ROOT
from tests.unit.test_image import final_stage
from tests.unit.test_image_base_probe import LINKS, kerberos_links
from tests.unit.test_portal_image import stages


SCRIPT = ROOT / "kit/check_image_scan.py"
TODAY = date(2026, 11, 11)
EXCEPTION = {"cve": "CVE-2026-12345", "package": "krb5-libs", "reason": "No vendor fix; review within 30 days.",
             "expires": "2026-12-01"}
KERBEROS = {"krb5-libs", "libcurl-minimal", "curl-minimal", "librepo", "cyrus-sasl-lib", "openldap", "gnupg2", "gpgme",
            "libtirpc", "libnsl2"}
FINDING = {"VulnerabilityID": EXCEPTION["cve"], "PkgName": EXCEPTION["package"], "Severity": "HIGH", "FixedVersion": ""}


def gate():
    spec = importlib.util.spec_from_file_location("kit_image_scan", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OS = {"Family": "redhat", "Name": "9.8"}


def report(*findings):
    return {"SchemaVersion": 2, "ArtifactName": "kge:test", "Metadata": {"OS": OS},
            "Results": [{"Class": "os-pkgs", "Type": "redhat", "Vulnerabilities": list(findings)}]}


def test_unfixed_findings_need_an_exact_cve_and_package_exception():
    check = gate().check
    assert check([EXCEPTION], [report(FINDING), report(FINDING)], TODAY) == []
    for entries in ([], [{**EXCEPTION, "package": "another-package"}], [{**EXCEPTION, "cve": "CVE-2026-99999"}]):
        assert "no unexpired exception" in "\n".join(check(entries, [report(FINDING)], TODAY))


@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL"])
@pytest.mark.parametrize("excepted", [False, True])
def test_a_planted_fixable_finding_blocks_even_with_an_exception(severity, excepted):
    found = {**FINDING, "Severity": severity, "FixedVersion": "1.2.3"}
    assert "fix available" in "\n".join(gate().check([EXCEPTION] if excepted else [], [report(found)], TODAY))


@pytest.mark.parametrize("offset", [-1, 0])
def test_an_expired_exception_blocks_even_when_the_finding_has_disappeared(offset):
    entry = {**EXCEPTION, "expires": (TODAY + timedelta(days=offset)).isoformat()}
    assert "expired" in "\n".join(gate().check([entry], [report()], TODAY))


@pytest.mark.parametrize("change", [{"reason": ""}, {"package": ""}, {"cve": "not-a-cve"}, {"expires": "next month"},
                                    {"expires": "2026-12-12"}])
def test_exceptions_require_a_reason_identity_and_expiry_within_thirty_days(change):
    assert gate().check([{**EXCEPTION, **change}], [report()], TODAY)


@pytest.mark.parametrize("broken", [{}, {"SchemaVersion": 2}, {"SchemaVersion": 1, "Results": []},
                                    {"SchemaVersion": 2, "Results": [None]}, report({"Severity": "HIGH"})])
def test_invalid_reports_fail_closed(broken):
    assert gate().check([], [broken], TODAY)


@pytest.mark.parametrize("key,value", [
    ("SchemaVersion", None), ("SchemaVersion", "2"), ("SchemaVersion", 2.0),
    ("Results", None), ("Results", {}), ("Results", ""), ("Results", False),
    ("ArtifactName", None), ("ArtifactName", {}), ("ArtifactName", []),
    ("ArtifactName", ""), ("ArtifactName", "  "), ("ArtifactName", 1),
])
def test_report_required_fields_reject_wrong_types_and_empty_names(key, value):
    broken = {**report(), key: value}
    assert "expected a Trivy schema 2 report" in "\n".join(gate().check([], [broken], TODAY))


@pytest.mark.parametrize("key", ["SchemaVersion", "Results", "ArtifactName"])
def test_report_required_fields_cannot_be_omitted(key):
    broken = report()
    del broken[key]
    assert "expected a Trivy schema 2 report" in "\n".join(gate().check([], [broken], TODAY))


@pytest.mark.parametrize("value", [{}, "", False, 0, None, {"Severity": "HIGH"}])
def test_vulnerabilities_must_be_a_list_even_when_falsey(value):
    broken = {**report(), "Results": [{"Class": "os-pkgs", "Vulnerabilities": value}]}
    assert gate().check([], [broken], TODAY) == ["invalid Trivy vulnerabilities"]


@pytest.mark.parametrize("value", [None, {}, [], 1, False])
def test_fixed_version_rejects_non_strings(value):
    assert gate().check([], [report({**FINDING, "FixedVersion": value})], TODAY) == [
        "invalid Trivy finding FixedVersion: expected a string"]


def test_clean_trivy_results_can_omit_the_optional_vulnerabilities_key():
    assert gate().check([], [{**report(), "Results": [{"Class": "os-pkgs"}, {"Class": "lang-pkgs"}]}], TODAY) == []


@pytest.mark.parametrize("value", [None, [], "", False, 0])
def test_result_entries_must_be_objects(value):
    assert gate().check([], [{**report(), "Results": [{"Class": "os-pkgs"}, value]}], TODAY) == ["invalid Trivy result"]


@pytest.mark.parametrize("value", [None, [], "", False, 0])
def test_finding_entries_must_be_objects(value):
    assert gate().check([], [report(value)], TODAY) == ["invalid Trivy finding"]


@pytest.mark.parametrize("key", ["Severity", "VulnerabilityID", "PkgName"])
@pytest.mark.parametrize("value", [None, {}, [], False, 0, ""])
def test_each_finding_field_requires_a_nonempty_string(key, value):
    assert gate().check([], [report({**FINDING, key: value})], TODAY) == ["invalid Trivy finding"]


@pytest.mark.parametrize("value", ["high", "HGIH", "unexpected"])
def test_unknown_severity_cannot_bypass_the_gate(value):
    assert gate().check([], [report({**FINDING, "Severity": value})], TODAY) == ["invalid Trivy finding Severity"]


@pytest.mark.parametrize("broken", [
    {"SchemaVersion": 2, "Results": []},
    {"SchemaVersion": 2, "ArtifactName": "kge:test"},
    {"SchemaVersion": 2, "ArtifactName": "kge:test", "Results": [{"Vulnerabilities": {}}]},
])
def test_malformed_schema_blocks_the_command_line_gate(tmp_path, broken):
    entries, scan = tmp_path / "exceptions.json", tmp_path / "scan.json"
    entries.write_text("[]")
    scan.write_text(json.dumps(broken))
    done = subprocess.run([sys.executable, str(SCRIPT), str(entries), str(scan)], capture_output=True, text=True)
    assert done.returncode == 1 and "image scan refused:" in done.stderr
    assert "Image scan passed" not in done.stdout


@pytest.mark.parametrize("metadata", [None, {}, {"OS": None}, {"OS": {}}, {"OS": {"Family": "redhat"}},
                                      {"OS": {"Name": "9.8"}}, {"OS": {"Family": "", "Name": "9.8"}},
                                      {"OS": {"Family": "redhat", "Name": " "}}, {"OS": {"Family": 1, "Name": "9.8"}},
                                      {"OS": "redhat 9.8"}, []])
def test_a_report_without_a_detected_os_is_refused(metadata):
    broken = report()
    if metadata is None:
        del broken["Metadata"]
    else:
        broken["Metadata"] = metadata
    assert gate().check([], [broken], TODAY) == ["kge:test: no operating system detected, so no OS package was checked"]


@pytest.mark.parametrize("eosl", [True, "true", 1])
def test_an_os_past_its_end_of_support_is_refused(eosl):
    broken = {**report(), "Metadata": {"OS": {**OS, "EOSL": eosl}}}
    assert "end of support" in "\n".join(gate().check([], [broken], TODAY))
    assert gate().check([], [{**report(), "Metadata": {"OS": {**OS, "EOSL": False}}}], TODAY) == []


@pytest.mark.parametrize("results", [[], [{"Class": "lang-pkgs", "Type": "python-pkg"}], [{"Vulnerabilities": []}],
                                     [{"Class": "OS-PKGS"}]])
def test_a_report_with_no_os_package_result_is_refused(results):
    problems = gate().check([], [{**report(), "Results": results}], TODAY)
    assert problems == ["kge:test: no os-pkgs result for redhat 9.8, so no OS package was checked"]


UBI10 = ROOT / "tests/fixtures/trivy_ubi10_portal.json"


def test_the_expected_os_is_read_from_both_dockerfiles_base_pins():
    assert gate().base_systems() == {("redhat", "9")}


KGE_BASE = "ARG BASE_IMAGE=registry.access.redhat.com/ubi9/python-312-minimal:9.8@sha256:" + "0" * 64
PORTAL_BASE = "ARG BASE_IMAGE=registry.access.redhat.com/ubi9/nodejs-22-minimal:9.8@sha256:" + "0" * 64
DEBIAN_BASE = "ARG BASE_IMAGE=python:3.12.15-slim-bookworm@sha256:" + "0" * 64
NO_PIN = "ARG OTHER=registry.access.redhat.com/ubi9/python-312-minimal:9.8"


def demo_with(tmp_path, kge, portal):
    (tmp_path / "portal").mkdir(exist_ok=True)
    (tmp_path / "Dockerfile").write_text(kge + "\n")
    (tmp_path / "portal/Dockerfile").write_text(portal + "\n")
    return tmp_path


def test_two_ubi9_pins_give_redhat_9(tmp_path):
    assert gate().base_systems(demo_with(tmp_path, KGE_BASE, PORTAL_BASE)) == {("redhat", "9")}


@pytest.mark.parametrize("changed", ["Dockerfile", "portal/Dockerfile"])
@pytest.mark.parametrize("pin,problem", [
    (DEBIAN_BASE, "knows no OS for the base"),
    (NO_PIN, "no ARG BASE_IMAGE pin"),
    (KGE_BASE.replace("ubi9", "ubi10").replace(":9.8", ":10.1"), "redhat 10, which the scanner is not known to cover"),
    (KGE_BASE.replace("ubi9", "ubi8").replace(":9.8", ":8.10"), "redhat 8, which the scanner is not known to cover")])
def test_either_dockerfile_alone_with_an_unknown_or_uncovered_base_is_refused(tmp_path, changed, pin, problem):
    kge, portal = (pin, PORTAL_BASE) if changed == "Dockerfile" else (KGE_BASE, pin)
    with pytest.raises(ValueError, match=re.escape(changed) + ": .*" + re.escape(problem)):
        gate().base_systems(demo_with(tmp_path, kge, portal))


@pytest.mark.parametrize("changed", ["Dockerfile", "portal/Dockerfile"])
def test_pins_that_disagree_are_refused_even_when_both_are_covered(tmp_path, changed):
    ten = (KGE_BASE if changed == "Dockerfile" else PORTAL_BASE).replace("ubi9", "ubi10")
    kge, portal = (ten, PORTAL_BASE) if changed == "Dockerfile" else (KGE_BASE, ten)
    with pytest.raises(ValueError, match="the images' bases differ"):
        gate().base_systems(demo_with(tmp_path, kge, portal), frozenset({("redhat", "9"), ("redhat", "10")}))


def test_a_ubi10_pin_is_refused_even_with_a_matching_ubi10_report(tmp_path):
    ten = demo_with(tmp_path, KGE_BASE.replace("ubi9", "ubi10"), PORTAL_BASE.replace("ubi9", "ubi10"))
    with pytest.raises(ValueError, match="redhat 10, which the scanner is not known to cover"):
        gate().base_systems(ten)
    blind = json.loads(UBI10.read_text())
    assert "not the images' base OS" in "\n".join(gate().check([], [blind], TODAY, {("redhat", "10")}))
    (ten / "kit").mkdir()
    shutil.copyfile(SCRIPT, ten / "kit/check_image_scan.py")
    (ten / "exceptions.json").write_text("[]")
    done = subprocess.run([sys.executable, str(ten / "kit/check_image_scan.py"), str(ten / "exceptions.json"), str(UBI10)],
                          capture_output=True, text=True)
    assert done.returncode == 1 and "not known to cover" in done.stderr, done.stdout + done.stderr
    assert "Image scan passed" not in done.stdout


def test_a_ubi10_report_with_an_empty_os_result_is_refused_on_a_ubi9_build(tmp_path):
    """The UBI 10 kit's real report: redhat 10.3, not end of support, an os-pkgs result and no finding at all, because
    the scanner has no RHEL 10 advisories. It must not pass while the images are built on UBI 9."""
    blind = json.loads(UBI10.read_text())
    assert blind["Metadata"]["OS"] == {"Family": "redhat", "Name": "10.3"}
    assert [r["Class"] for r in blind["Results"]] == ["os-pkgs", "lang-pkgs"]
    assert gate().check([], [blind], TODAY) == [
        "kge-portal:0.471.691: scanned OS redhat 10.3 is not the images' base OS (redhat 9, from the Dockerfiles' "
        "BASE_IMAGE pins), and the scanner may have no advisories for it; a new base needs a deliberate change to "
        "check_image_scan.py"]
    assert "not the images' base OS" in "\n".join(gate().check([], [report()], TODAY, {("redhat", "10")}))
    entries = tmp_path / "exceptions.json"
    entries.write_text("[]")
    done = subprocess.run([sys.executable, str(SCRIPT), str(entries), str(UBI10)], capture_output=True, text=True)
    assert done.returncode == 1 and "not the images' base OS" in done.stderr
    assert "Image scan passed" not in done.stdout


@pytest.mark.parametrize("name", ["9", "9.8", "9.10"])
def test_any_minor_of_the_base_major_passes(name):
    assert gate().check([], [{**report(), "Metadata": {"OS": {**OS, "Name": name}}}], TODAY) == []


def test_one_report_without_os_data_fails_the_gate_beside_a_complete_one(tmp_path):
    blind = {**report(), "ArtifactName": "kge-portal:test", "Metadata": {}, "Results": []}
    assert gate().check([], [report(), blind], TODAY) == [
        "kge-portal:test: no operating system detected, so no OS package was checked"]
    entries, good, bad = tmp_path / "exceptions.json", tmp_path / "good.json", tmp_path / "bad.json"
    entries.write_text("[]")
    good.write_text(json.dumps(report()))
    bad.write_text(json.dumps(blind))
    done = subprocess.run([sys.executable, str(SCRIPT), str(entries), str(good), str(bad)], capture_output=True, text=True)
    assert done.returncode == 1 and "no operating system detected" in done.stderr
    assert "Image scan passed" not in done.stdout


def test_a_duplicate_exception_is_refused_and_lower_severities_need_none():
    assert gate().check([EXCEPTION, EXCEPTION], [report()], TODAY)
    assert gate().check([], [report({**FINDING, "Severity": "MEDIUM"})], TODAY) == []


def test_the_workflow_gate_exits_nonzero_for_a_planted_fix_and_an_expired_exception(tmp_path):
    entries = tmp_path / "exceptions.json"
    scan = tmp_path / "scan.json"
    for exceptions, finding, expected in (([], {**FINDING, "FixedVersion": "1.2.3"}, "fix available"),
                                           ([{**EXCEPTION, "expires": "2000-01-01"}], FINDING, "expired")):
        entries.write_text(json.dumps(exceptions))
        scan.write_text(json.dumps(report(finding)))
        done = subprocess.run([sys.executable, str(SCRIPT), str(entries), str(scan)], capture_output=True, text=True)
        assert done.returncode == 1 and expected in done.stderr, done.stdout + done.stderr
    entries.write_text("[]")
    scan.write_text(json.dumps(report()))
    assert subprocess.run([sys.executable, str(SCRIPT), str(entries), str(scan)], capture_output=True).returncode == 0
    scan.write_text("not json")
    assert subprocess.run([sys.executable, str(SCRIPT), str(entries), str(scan)], capture_output=True).returncode == 1


@pytest.mark.parametrize("portal", [False, True])
def test_runtime_images_erase_build_tools_from_rpm_bases_without_requiring_rpm_on_other_bases(portal):
    stage = stages()[1][-1] if portal else final_stage()
    removals = [args for word, args in stage if word == "RUN" and "rpm -e" in args]
    assert len(removals) == 1
    removal = removals[0]
    assert removal.startswith("set -eu;") and 'command -v rpm' in removal and 'rpm -q "$package"' in removal
    required = {"microdnf", "libdnf", "json-glib", "python3.12-pip-wheel"}
    if portal:
        required |= {"npm", "nodejs-nodemon"}
    packages = set(re.search(r"for package in (.*?); do", removal)[1].split())
    assert required | KERBEROS <= packages
    if not portal:
        assert "rm -f /usr/lib64/python3*/lib-dynload/nis.cpython-*.so;" in removal
        assert removal.index("rpm -e") < removal.index("nis.cpython")
    fixed = [f"{name}-2.37.4-28.el9_9.1" for name in ("libblkid", "libmount", "libsmartcols", "libuuid")]
    fixed += ["libxml2-2.9.13-18.el9_9.1"] if portal else ["expat-2.5.0-9.el9_9.4", "libxml2-2.9.13-18.el9_9.1"]
    assert removal.count("microdnf update") == 1
    assert f"microdnf update -y {' '.join(fixed)}; microdnf clean all;" in removal
    assert removal.index("microdnf update") < removal.index("rpm -e")
    if portal:
        install = next(i for i, (word, args) in enumerate(stage) if word == "RUN" and "npm ci --omit=dev" in args)
        assert install < stage.index(("RUN", removal))
    else:
        assert any(word == "RUN" and "importlib.metadata" in args and 'd.metadata["Name"] == "pip"' in args
                   and "p.unlink()" in args for word, args in stage)


def test_both_runtime_scans_are_complete_pinned_and_gated_before_upload_and_publication():
    workflow = yaml.safe_load((ROOT.parents[1] / ".github/workflows/kit-demo.yml").read_text())
    steps = workflow["jobs"]["kit"]["steps"]
    scanners = [step for step in steps if step.get("uses", "").startswith("aquasecurity/trivy-action@")]
    assert len(scanners) == 2
    checks = (ROOT.parents[1] / ".github/workflows/checks.yml").read_text()
    version = "${{ steps.version.outputs.version }}"
    for step, image in zip(scanners, ("kge", "kge-portal")):
        assert re.fullmatch(r"aquasecurity/trivy-action@[0-9a-f]{40}", step["uses"])
        assert step["uses"] in checks
        assert step["if"] == "inputs.demo == 'kge'"
        assert step["with"] == {
            "scan-type": "image", "image-ref": f"{image}:{version}", "scanners": "vuln", "vuln-type": "os,library",
            "severity": "HIGH,CRITICAL", "ignore-unfixed": False, "format": "json", "exit-code": "0",
            "output": f"demos/kge/build/kit/kge-{version}/images/{image}-{version}.trivy.json"}
        assert step["env"] == {"TRIVY_IGNOREFILE": "/dev/null"}
    build = next(i for i, step in enumerate(steps) if step.get("run") == 'make kit VERSION="$VERSION"')
    gate_step = next(step for step in steps if "kit/check_image_scan.py" in step.get("run", ""))
    at = steps.index(gate_step)
    assert gate_step["if"] == "inputs.demo == 'kge'"
    assert all(build < steps.index(step) < at for step in scanners)
    assert '"$out/images/scan-exceptions.json"' in gate_step["run"]
    assert '"$out/images/kge-$VERSION.trivy.json" "$out/images/kge-portal-$VERSION.trivy.json"' in gate_step["run"]
    assert "set -euo pipefail" in gate_step["run"] and "xargs sha256sum > SHA256SUMS" in gate_step["run"]
    assert at < next(i for i, step in enumerate(steps) if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert "kit" in workflow["jobs"]["release"]["needs"]
    assert not any(step.get("continue-on-error") for step in [*scanners, gate_step])


@pytest.mark.parametrize("problem", [None, "fix", "expired", "unexcepted"])
def test_workflow_gate_blocks_bad_reports_and_checksums_the_shipped_evidence(tmp_path, problem):
    steps = yaml.safe_load((ROOT.parents[1] / ".github/workflows/kit-demo.yml").read_text())["jobs"]["kit"]["steps"]
    step = next(step for step in steps if "kit/check_image_scan.py" in step.get("run", ""))
    script = tmp_path / "kit/check_image_scan.py"
    script.parent.mkdir()
    shutil.copyfile(SCRIPT, script)
    (tmp_path / "portal").mkdir()
    for dockerfile in ("Dockerfile", "portal/Dockerfile"):
        shutil.copyfile(ROOT / dockerfile, tmp_path / dockerfile)
    out = tmp_path / "build/kit/kge-0.0.1"
    images = out / "images"
    images.mkdir(parents=True)
    expiry = (datetime.now(timezone.utc).date() + timedelta(days=20)).isoformat()
    entry = {**EXCEPTION, "expires": "2000-01-01" if problem == "expired" else expiry}
    (images / "scan-exceptions.json").write_text(json.dumps([] if problem == "unexcepted" else [entry]))
    (images / "kge-0.0.1.trivy.json").write_text(json.dumps(report(FINDING)))
    finding = {**FINDING, "FixedVersion": "1.2.3"} if problem == "fix" else FINDING
    (images / "kge-portal-0.0.1.trivy.json").write_text(json.dumps(report(finding)))
    (images / "kge-0.0.1.sbom.cdx.json").write_text("{}")
    (out / "SHA256SUMS").write_text("before scan\n")
    done = subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, env={**os.environ, "VERSION": "0.0.1"},
                          capture_output=True, text=True)
    assert (done.returncode == 0) is (problem is None), done.stdout + done.stderr
    if problem:
        assert (out / "SHA256SUMS").read_text() == "before scan\n"
    else:
        for path in images.iterdir():
            assert f"images/{path.name}" in (out / "SHA256SUMS").read_text()
        checked = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=out, capture_output=True)
        assert checked.returncode == 0


def test_the_kerberos_link_probe_counts_only_inspected_files_and_reports_failures_and_links():
    output = "\n".join([
        "== ok /usr/bin/node", "\tlibz.so.1 => /lib64/libz.so.1 (0x1)",
        "== ok /usr/lib64/libcurl.so.4", "\tlibgssapi_krb5.so.2 => not found",
        "== static /usr/bin/static-tool",
        "== failed /usr/lib64/broken.so", "ldd: exited with unknown exit code (139)",
        "== ok /usr/lib64/libldap.so.2", "\tlibk5crypto.so.3 => /lib64/libk5crypto.so.3 (0x2)"])
    inspected, linked, failed = kerberos_links(output)
    assert inspected == 3 and failed == ["/usr/lib64/broken.so"]
    assert linked == ["/usr/lib64/libcurl.so.4: libgssapi_krb5.so.2 => not found",
                      "/usr/lib64/libldap.so.2: libk5crypto.so.3 => /lib64/libk5crypto.so.3 (0x2)"]
    assert kerberos_links("\n".join(f"== failed /lib/{n}.so" for n in range(200)))[0] == 0
    assert "if out=$(ldd \"$f\" 2>&1); then" in LINKS and "|| true" not in LINKS


def test_portal_sbom_reads_installed_packages_without_npm_including_nested_and_scoped_packages(tmp_path):
    source = (ROOT / "kit/sbom_node.mjs").read_text()
    script = re.search(r"const NPM_PACKAGES = `(.*?)`;", source, re.S)[1]
    expected = [{"name": "express", "version": "5.1.0"}, {"name": "@scope/nested", "version": "2.0.0"},
                {"name": "@scope/outer", "version": "3.0.0"}]
    for folder, package in zip(("express", "express/node_modules/@scope/nested", "@scope/outer"), expected):
        path = tmp_path / "node_modules" / folder / "package.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(package))
    done = subprocess.run(["node", "-e", script], cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert sorted(json.loads(done.stdout), key=lambda p: p["name"]) == sorted(expected, key=lambda p: p["name"])
    assert 'run("npm"' not in source


def test_install_guide_explains_the_shipped_scan_reports_and_exception_fields():
    guide = (ROOT / "kit/INSTALL.md").read_text().split("## Image vulnerability scans\n", 1)[1].split("\n## ", 1)[0]
    for phrase in ("Trivy", "HIGH and CRITICAL", "operating-system and language packages", "available fix blocks",
                   "images/kge-@VERSION@.trivy.json", "images/kge-portal-@VERSION@.trivy.json", ".sbom.cdx.json",
                   "images/scan-exceptions.json", "SHA256SUMS", "`cve`", "`package`", "`reason`", "`expires`", "30 days"):
        assert phrase in guide
    assert not re.search(r"github\.com|demos/|make\s|README\.md", guide)
