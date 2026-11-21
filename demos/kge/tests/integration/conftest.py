"""The integration lane's ledger mirror (#514, B2.1). Every test in tests/integration/ runs inside a Mirror over every
thread (kge/eval/phrasemirror.py), opened around the whole test, its fixtures' setup and teardown included, so a read a
module- or session-scoped fixture makes is watched too: each question-lexer read the test makes in this process is
lexed again afterwards with the graph's phrase table and with the coded constant, and a read on a graph table whose
two ledgers differ fails the test's teardown. This is how the held-out rewordings of the golden 20 (HELD_OUT) are
checked: through this mirror alone, never by a test that reads their words. Each test's teardown report carries its
graph reads in user_properties, which reach the controlling process under xdist too (make test-integration and make
extended name the test files, so this conftest is loaded there); the run prints them per module and fails when a
held-out test ran its call and the module recorded no graph read, so the check never passes on nothing. HELD_OUT is in
the extended tier (tests/lanes.py EXTENDED), so the guard is live under make extended or a run of that file by name,
never under make test-integration, which leaves it out. It holds where pytest is given test files; given a parent
folder under xdist, the controlling process never
loads this conftest, and only a mismatch, which fails in the worker, is caught. A test that never reached its call
(skipped, or failed in setup) reports no count. A message names the turn, the position and each
piece's kind, operation, place and candidates, never the question. Reads that fell back to the constant table, and a
stand-in vocabulary's, are not failures here: grounding-eval counts those. Lane marking stays with tests/conftest.py."""
import pytest

from kge.eval.phrasemirror import Mirror

HELD_OUT = 'test_answer_modes.py'
READS = 'graph_ledger_reads'
WATCHED = pytest.StashKey()
CALLED = pytest.StashKey()
COUNTS = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Opens the item's Mirror around its setup, call and teardown."""
    with Mirror(threads='all') as watched:
        item.stash[WATCHED] = watched
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """At teardown, the last phase, compares the item's recorded reads, fails the teardown on a graph-table mismatch and
    reports the graph reads compared."""
    outcome = yield
    watched = item.stash.get(WATCHED, None)
    if watched is None:
        return
    report = outcome.get_result()
    if call.when == 'call':
        item.stash[CALLED] = True
    if call.when != 'teardown':
        return
    graph = [read for read in watched.results() if read.source == 'graph']
    if item.stash.get(CALLED, False):
        report.user_properties.append((READS, len(graph)))
    differ = [read for read in graph if not read.equal]
    if differ:
        message = 'graph and constant ledgers differ:\n' + '\n'.join(
            f'- turn {read.turn}, piece {read.index}: graph {read.graph}; constant {read.constant}' for read in differ)
        report.longrepr = f'{report.longrepr}\n\n{message}' if report.failed and report.longrepr else message
        report.outcome = 'failed'


def pytest_runtest_logreport(report):
    """Adds a teardown report's graph reads to its module's count."""
    reads = dict(report.user_properties).get(READS) if report.when == 'teardown' else None
    if reads is not None:
        module = report.nodeid.split('::', 1)[0].rsplit('/', 1)[-1]
        COUNTS[module] = COUNTS.get(module, 0) + reads


def held_out_unchecked() -> bool:
    """Whether the held-out module ran and its rewordings recorded no graph read."""
    return COUNTS.get(HELD_OUT, 1) == 0


def pytest_sessionfinish(session, exitstatus):
    """Fails the run where the held-out module checked nothing; a worker leaves it to the controlling process, which
    holds every module's count."""
    if not hasattr(session.config, 'workerinput') and held_out_unchecked():
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter):
    """Prints the graph ledger reads compared per module, and the held-out module's verdict."""
    if hasattr(terminalreporter.config, 'workerinput') or not COUNTS:
        return
    terminalreporter.section('phrase baseline v1: graph ledger reads compared')
    for module, reads in sorted(COUNTS.items()):
        terminalreporter.write_line(f'{module}: {reads}')
    if held_out_unchecked():
        terminalreporter.write_line(f'{HELD_OUT} ran and recorded no graph ledger read: the held-out check checked nothing')
