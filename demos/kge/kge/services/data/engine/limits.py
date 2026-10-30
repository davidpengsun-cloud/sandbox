"""Bound untrusted SQL before parsing and the entire analysis and execution budget."""
import sys
import time
from contextlib import contextmanager

from sqlglot import exp, parse, tokenize
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

from kge.ports import QueryRefused


class AnalysisTimeout(BaseException):
    pass


@contextmanager
def bounded(deadline):
    """Interrupt Python SQL analysis; warehouse timers own I/O and connection cleanup. Its own frame is never interrupted:
    resumed to unwind an exception, it would otherwise raise the timeout from its own clean-up, past the refusal."""
    previous = sys.gettrace()
    own = sys._getframe().f_code
    events = 0

    def tick(frame, event, arg):
        nonlocal events
        if frame.f_code is own:
            return None
        filename = frame.f_code.co_filename.replace("\\", "/")
        if event == "call" and not ("/sqlglot/" in filename or filename.endswith((
            "/kge/pipeline/sqlguard.py", "/kge/semantics/query.py",
            "/kge/services/data/engine/governance.py", "/kge/services/data/engine/limits.py",
        ))):
            return None
        events += 1
        if events % 128 == 0 and time.monotonic() >= deadline:
            raise AnalysisTimeout()
        return tick

    try:
        sys.settrace(tick)
        if time.monotonic() >= deadline:
            raise AnalysisTimeout()
        yield
        if time.monotonic() >= deadline:
            raise AnalysisTimeout()
    except AnalysisTimeout:
        raise QueryRefused("query exceeded the 5 second time limit") from None
    finally:
        sys.settrace(previous)


def complexity(sql, dialect):
    reason = "query exceeds the complexity limit (32768 characters, 4096 tokens/nodes, 128 OR/UNION terms, 32 parenthesis levels, 192 AST levels)"
    if len(sql) > 32768:
        raise QueryRefused(reason)
    try:
        tokens = tokenize(sql, read=dialect)
        depth = terms = 0
        if len(tokens) > 4096:
            raise QueryRefused(reason)
        for token in tokens:
            if token.token_type == TokenType.L_PAREN:
                depth += 1
            elif token.token_type == TokenType.R_PAREN:
                depth -= 1
            elif token.token_type in (TokenType.OR, TokenType.UNION):
                terms += 1
            if depth > 32 or terms >= 128:
                raise QueryRefused(reason)
        nodes = 0
        for tree in parse(sql, read=dialect):
            if tree is None:
                continue
            pending = [(tree, 0)]
            while pending:
                node, depth = pending.pop()
                nodes += 1
                if nodes > 4096 or depth > 192:
                    raise QueryRefused(reason)
                pending.extend((child, depth + 1) for child in node.iter_expressions() if isinstance(child, exp.Expression))
    except SqlglotError:
        return
    except RecursionError:
        raise QueryRefused("The query does not parse; use one read-only SELECT statement within the nesting limit.") from None
