from dataclasses import asdict, replace
from types import SimpleNamespace
from uuid import uuid4
import time
from kge.adapters.modelusage import usage_scope, current_usage
from kge.pipeline import trace
from kge.services.ask import service
from kge.services.ask.engine.config import PRESETS
from tests.unit.test_model_usage_transport import _fake_ask_service, ledger, identity


def test_background_pass_has_saved_trace_context_and_settles_its_usage(tmp_path, monkeypatch):
    c = _fake_ask_service(monkeypatch, lambda *_args, **_kwargs: {'behaviour':'figure'})
    cfg = replace(PRESETS['graph'], renderer='model')
    book, question, inv = ledger(tmp_path), identity(), identity()
    book.open_question(question); book.start_invocation(question, inv); book.claim_invocation(question, inv)
    session=str(uuid4()); seen=[]
    def advance(*args,**kwargs):
        seen.append((trace.telemetry_current(),current_usage()[1:]))
        return {'state':'running'}
    monkeypatch.setattr(service,'_advance_pass',advance)
    record={'id':'a'*32,'config':asdict(cfg),'telemetry_scope':{'client':'sample','agent':'cerebro','session':session}}
    with usage_scope(book,question,inv):
        result=service._advance_run(c,SimpleNamespace(client='sample'),record,object(),'1',time.time)
    assert result['state']=='running'
    assert seen[0][0]['kge.client']=='sample' and seen[0][0]['gen_ai.conversation.id']==session
    assert seen[0][1]==(question,inv)
    book.finish_invocation(question,inv); book.finish_question(question)
    assert book.report(question)['complete'] and book.report(question)['calls']==0


def test_background_saved_context_never_marks_undocumented_model_call_as_zero(tmp_path, monkeypatch):
    import pytest
    from kge.adapters.modelusage import UsageConflict
    from kge.services.ask.engine.modelcall import Contract, structured_call
    c = _fake_ask_service(monkeypatch, lambda *_args, **_kwargs: {'behaviour':'figure'})
    cfg = replace(PRESETS['graph'], renderer='model')
    book, question, inv = ledger(tmp_path), identity(), identity()
    book.open_question(question); book.start_invocation(question,inv); book.claim_invocation(question,inv)
    session=str(uuid4()); seen=[]
    def advance(*args,**kwargs):
        seen.append(trace.telemetry_current())
        class Uninstrumented:
            def json(self,_prompt): return {}
        structured_call(Uninstrumented(),'plan',version='v1',instructions='',data={},question='Metric',contract=Contract,schema={},legacy_prompt='',validate=False)
        return {'state':'running'}
    monkeypatch.setattr(service,'_advance_pass',advance)
    record={'id':'a'*32,'config':asdict(cfg),'telemetry_scope':{'client':'sample','agent':'cerebro','session':session}}
    with usage_scope(book,question,inv):
        service._advance_run(c,SimpleNamespace(client='sample'),record,object(),'1',time.time)
    assert seen[0]['gen_ai.conversation.id']==session
    with pytest.raises(UsageConflict): book.finish_invocation(question,inv)
    assert not book.report(question)['complete']
