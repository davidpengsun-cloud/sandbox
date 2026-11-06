"""Review guidance explains waits that an operator or editor can encounter."""
from kge.pipeline.settings import ROOT
from kge.services.onboard import jobs


def test_editing_documents_instance_lock_duration():
    assert "An edit holds the profile lock for its whole duration, so a slow edit delays job starts on that instance." in jobs.editing.__doc__


def test_install_and_access_admin_explain_regrant_throttle():
    for path in ("kit/INSTALL.md", "docs/playbooks/access-admin.md"):
        text = (ROOT / path).read_text()
        assert "5 failures, 300 s lockout" in text
        assert "retries repeatedly in the minute after a regrant" in text
        assert "wait for the refresh before retrying" in text
