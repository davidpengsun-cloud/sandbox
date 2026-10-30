"""Compatibility shim, for the two frozen snapshot tests alone (tests/test_context.py, tests/test_agent_loop.py), which are
never edited and import the demo by its names from before the restructure (#83): api.app is kge.app and each other
api.<module> is the kge module that holds it now (api.identity is kge.services.identity.service, the rest
kge.services.ask.engine.<module>), the same module object, so a test's monkeypatch reaches the code. Nothing else imports it, and the image does not hold it."""
