import json

import pytest

from kge.semantics.plan import Binding, Propose
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.semantics.query import Context, Join, Rule
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import parse
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from kge.services.ask.engine import wording
from kge.services.ask.engine import runtrace
from tests.bigquery_fake import budgets, pilot
from tests.integration.test_combined import Q, Stub
from tests.plan_fixtures import grounded, model, monthly_time, name, time_dimension


@pytest.mark.parametrize('configured', [False, True])
def test_latest_released_offer_keeps_the_abstention_warehouse_job_budget(monkeypatch, tmp_path, configured):
    from kge.semantics.plan import Abstain
    from kge.services.ask.engine.core import PRESETS
    from tests.integration.test_plan_wording_round2 import Recording

    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        reply = Abstain(kind='abstain', spans=(), missing=('December 2026 has not started. No figure is given.',))
        caller = Combined(engine, llm=Recording(reply) if configured else None)
        config = PRESETS['graph'].with_stages(resolution='glossary+model') if configured else PRESETS['graph']
        for _ in range(2):
            fake.requests.clear()
            answer = caller.ask('What was certified NNA for December 2026?', 'analyst_enterprise', config=config)
            assert answer['behaviour'] in ('abstain', 'abstain_with_reason'), answer
            assert answer['reason'].endswith(
                'The latest released month is June 2025. Would you like that month instead?'), answer
            assert answer.get('figure') is None, answer
            assert not any(answer.get(key) for key in ('figures', 'result', 'sql', 'rows', 'parts')), answer
            counts = budgets(fake)
            jobs = 0 if configured else 1
            assert {k: v for k, v in counts.items() if k != 'graph'} == dict(
                warehouse=jobs, dry=jobs, catalogue=0, retrieval=0), counts
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('cached', [True, False])
def test_external_release_offer_uses_its_own_calendar_or_omits_the_offer_without_jobs(monkeypatch, tmp_path, cached):
    from kge.semantics.plan import Abstain
    from kge.services.ask.engine.core import PRESETS
    from tests.integration.test_plan_wording_round2 import Recording

    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        if cached:
            engine.g._release_periods['external'] = tuple(
                (table, '2025-05-31', ready, on)
                for table, period, ready, on in engine.g._release_periods['reference'])
        monkeypatch.setattr(engine.w, 'select_runtime', lambda *a, **kw: {'ok': True, 'runtime_id': 'external'})
        reply = Abstain(kind='abstain', spans=(), missing=('December 2026 has not started. No figure is given.',))
        caller = Combined(engine, llm=Recording(reply))
        config = PRESETS['graph'].with_stages(resolution='glossary+model')
        for attempt in range(2):
            fake.requests.clear()
            answer = caller.ask('What was certified NNA for December 2026?', 'analyst_enterprise', config=config,
                                runtimes=['external'])
            assert answer['behaviour'] in ('abstain', 'abstain_with_reason'), answer
            assert 'No figure is given.' in answer['reason']
            assert ('The latest released month is May 2025. Would you like that month instead?' in answer['reason']) is cached
            assert 'June 2025' not in answer['reason']
            assert ('Would you like' in answer['reason']) is cached
            assert not any(answer.get(key) for key in ('figure', 'figures', 'result', 'sql', 'rows', 'parts')), answer
            assert budgets(fake) == dict(graph=2 if attempt == 0 else 0, warehouse=0, dry=0, catalogue=0, retrieval=0)
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_golden_request_graph_jobs_stay_constant_across_principals(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import kge.app as appmod

    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    measured = []
    try:
        engine = opened()
        monkeypatch.setenv('MODEL_PROVIDER', 'none')
        for name, value in (('PROFILE', engine.profile), ('_engine', engine), ('_ids', None),
                            ('_combined', None), ('_agent', None)):
            monkeypatch.setattr(appmod, name, value)
        with TestClient(appmod.app) as client:
            for principal in ('analyst_retail', 'analyst_retail', 'advisor_wealth'):
                token = appmod.ids().token_for(principal + '.meridian@example.com')
                fake.requests.clear()
                response = client.get('/golden', headers={'authorization': f'Bearer {token}'})
                assert response.status_code == 200
                glossary = {item['term'] for item in response.json()['glossary']}
                if principal == 'analyst_retail':
                    assert 'Active Customer' in glossary
                jobs = budgets(fake)
                assert {k: v for k, v in jobs.items() if k != 'graph'} == dict(
                    warehouse=0, dry=0, catalogue=0, retrieval=0), jobs
                measured.append(jobs['graph'])
        assert measured == [2, 0, 0], measured
        print(json.dumps(dict(golden_graph_jobs=measured)))
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('base,followup,jobs', [
    ('NNA for June 2025', 'And for April 2025?', 2),
    ('Retail revenue for June 2025', 'And for SME?', 1),
    ('NNA for June 2025 from BROKERAGE', 'From BANK?', 2),
    ('How many customers does the bank have across Retail and Corporate?', None, 1),
])
def test_short_followup_keeps_the_native_bigquery_job_ceiling(monkeypatch, tmp_path, base, followup, jobs):
    from kge.semantics.plan import HistoryEntry
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        caller = Combined(engine, llm=None)
        first = caller.ask(base, 'analyst_enterprise')
        assert first['behaviour'] == ('clarify' if followup is None else 'figure'), first
        if followup is None:
            followup = base
            choice, = first['clarification']['choices']
            for span in sorted(first['clarification']['spans'], key=lambda s: s['start'], reverse=True):
                followup = followup[:span['start']] + choice['label'] + followup[span['end']:]
        for _ in range(2):
            fake.requests.clear()
            answer = caller.ask(followup, 'analyst_enterprise',
                                history=[HistoryEntry.model_validate(h) for h in first['history']])
            assert answer['behaviour'] == 'figure' and len(answer['history']) == 2, answer
            measured = budgets(fake)
            assert measured == dict(graph=0, retrieval=0, warehouse=jobs, dry=jobs, catalogue=0), measured
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('arm', ['graph', 'catalog'])
def test_partial_combination_keeps_one_answer_job_and_no_catalogue_jobs(monkeypatch, tmp_path, arm):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        for cold in (True, False):
            fake.requests.clear()
            answer = engine.answer('Combined Retail and Corporate revenue for June 2025', 'analyst_enterprise', arm_name=arm)
            assert answer['behaviour'] == 'parts_with_reason' and answer['missing_parts'] == ['corporate']
            assert 'figure' not in answer and answer['parts']['retail'] is not None and answer['parts']['corporate'] is None
            jobs = budgets(fake)
            assert {k: v for k, v in jobs.items() if k != 'graph'} == dict(warehouse=1, dry=1, catalogue=0, retrieval=0)
            assert jobs['graph'] <= (14 if cold else 1)
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('question,dry,behaviour', [
    ('Complaints received by email in June 2025', 0, 'abstain'),
    ('Number of complaints in June 2025 older than 30 days', 0, 'abstain'),
    ('Credit exposure for June 2025 on facilities older than 5 years', 0, 'abstain'),
    ('Days past due older than 90 days in June 2025', 0, 'abstain'),
    ('Fee income for June 2025 by age band', 0, 'abstain'),
    ('NNA for March 2025 as reported, and the age of the data', 0, 'abstain'),
    ('Total corporate internal rating June 2025', 1, 'abstain'),
])
def test_incomplete_or_unplannable_request_has_no_answer_job(monkeypatch, tmp_path, question, dry, behaviour):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        for cold in (True, False):
            fake.requests.clear()
            answer = engine.answer(question, 'analyst_enterprise')
            assert answer['behaviour'] == behaviour and answer.get('figure') is None
            if dry:
                assert 'dry run failed' in answer['reason']
            jobs = budgets(fake)
            assert {k: v for k, v in jobs.items() if k != 'graph'} == dict(warehouse=0, dry=dry, catalogue=0, retrieval=0)
            assert jobs['graph'] <= (14 if cold else 1)
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_known_definition_keeps_the_rules_request_budget_without_a_model_call(monkeypatch, tmp_path):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    records = []
    try:
        for modeled in (False, True):
            engine = opened()
            model = Stub()
            for temperature in ('cold', 'warm'):
                fake.requests.clear()
                answer = (Combined(engine, llm=model).ask('What is Revenue?', 'analyst_enterprise') if modeled
                          else engine.answer('What is Revenue?', 'analyst_enterprise'))
                assert answer['behaviour'] in ('cite_certified', 'glossary_definition') and answer.get('figure') is None
                measured = budgets(fake)
                assert measured['warehouse'] == measured['dry'] == measured['catalogue'] == 0
                records.append(measured)
            assert model.prompts == []
            engine.close()
            engines.remove(engine)
        assert records[:2] == records[2:], records
        print(json.dumps(records))
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('question,principal,cohort,behaviour,denied', [
    (Q.replace('Retail', 'SME'), 'analyst_retail', True, 'refuse', ['sme']),
    ('For dual clients, deposits vs AUM and AUM share of total, 31 Mar 2025', 'analyst_retail', False, 'blocked_access', ['wealth']),
    ('For dual clients, deposits vs AUM and AUM share of total, 31 Mar 2025', 'advisor_wealth', False, 'blocked_access', ['retail']),
])
def test_access_scope_is_resolved_without_data_jobs(monkeypatch, tmp_path, question, principal, cohort, behaviour, denied):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        for cold in (True, False):
            fake.requests.clear()
            answer = (Combined(engine, llm=None).ask(question, principal) if cohort else engine.answer(question, principal))
            assert answer['behaviour'] == behaviour and answer['denied_warehouses'] == denied
            if not cohort:
                assert answer['warehouses_involved'] == ['retail', 'wealth']
            assert answer.get('figure') is None and 'sql' not in answer
            jobs = budgets(fake)
            assert {k: v for k, v in jobs.items() if k != 'graph'} == dict(warehouse=0, dry=0, catalogue=0, retrieval=0)
            assert jobs['graph'] <= (14 if cold else 1)
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('arm', ['graph', 'catalog', 'schema'])
def test_qualified_private_column_is_refused_without_data_jobs(monkeypatch, tmp_path, arm):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        for cold in (True, False):
            fake.requests.clear()
            answer = engine.answer('Average retail revenue by customer.email in June 2025', 'analyst_enterprise', arm_name=arm)
            assert answer['behaviour'] == 'refuse' and 'restricted' in answer['reason'].lower()
            assert answer.get('figure') is None and 'sql' not in answer
            jobs = budgets(fake)
            assert jobs['warehouse'] == jobs['dry'] == jobs['retrieval'] == 0
            assert jobs['catalogue'] == (8 if cold and arm == 'schema' else 0)
            assert jobs['graph'] <= (14 if cold else 1)
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def cohort_contexts(sem):
    contexts = []
    for identifier, term, table, expression, aggregation, period in (
        ('active_variant', 'First cohort', 'customer_activity', 'count(distinct customer_activity.customer_id)', 'count', 'month_start'),
        ('complaint_variant', 'Second cohort', 'complaint', 'count(distinct complaint.customer_id)', 'count', 'raised_on'),
        ('balance_variant', 'Balance metric', 'daily_balance', 'avg(daily_balance.ledger_balance)', 'avg', 'as_of'),
        ('fee_variant', 'Fee metric', 'monthly_revenue', 'sum(monthly_revenue.fee_income)', 'sum', 'month_start'),
    ):
        joins, filters, selectors = (), (), ()
        customer = table + '.customer_id'
        if table in ('daily_balance', 'monthly_revenue'):
            joins = (Join(identifier + '_account', 'key', table + '.account_id = account.account_id', None, None, 'retail.' + table, 'retail.account', True),)
            customer = 'account.customer_id'
        if table == 'customer_activity':
            filters = (Rule('active_filter', '', 'customer_activity.is_active_flag', 'retail.customer_activity'),)
        else:
            selectors = (Rule(identifier + '_active', '', 'customer_activity.customer_id = ' + customer +
                              " AND customer_activity.month_start = date_trunc('month', " + table + '.' + period + ') AND customer_activity.is_active_flag', 'retail.customer_activity', 'include'),)
        if table in ('daily_balance', 'monthly_revenue'):
            selectors += (Rule(identifier + '_complaint', '', 'complaint.customer_id = ' + customer +
                               " AND date_trunc('month', complaint.raised_on) = date_trunc('month', " + table + '.' + period + ')', 'retail.complaint', 'include'),)
        ctx = Context(identifier, identifier, '', term, identifier + '_measure', expression, aggregation, 'day', 'retail.' + table,
                      ('retail.' + table + '.' + ('customer_id' if aggregation == 'count' else 'ledger_balance' if aggregation == 'avg' else 'fee_income'),), filters, selectors, joins)
        sem.contexts[identifier] = ctx
        contexts.append(ctx)
    return contexts


def proposal(engine, scenario, width, scoped):
    sem = engine.semantics
    if scenario == 'cohort':
        contexts = cohort_contexts(sem)
        context = contexts[0]
        term = context.term
    elif scenario == 'context':
        context = sem.contexts['nna']
        term = context.term
    else:
        context = Context('test_revenue', 'Test revenue', '', 'Revenue', 'retail.revenue', 'sum(monthly_revenue.total_revenue_usd)', 'sum', 'month',
                          'retail.monthly_revenue', ('retail.monthly_revenue.total_revenue_usd',), (), (), ())
        sem.contexts[context.id] = context
        term = 'Revenue'
    if scenario != 'cohort':
        contexts = [context] * width
        if width > 1:
            contexts = [Context('variant_' + str(i), 'Variant ' + str(i), context.description, context.term, context.measure, context.expression, context.aggregation, context.grain, context.fact, context.values, context.filters, context.selectors, context.joins) for i in range(width)]
            sem.contexts.update({c.id: c for c in contexts})
    readable = engine.g.readable_units('analyst_enterprise')
    tables = {t for t in sem.tables if t.split('.')[0] in readable}
    if scoped:
        tables = {context.fact, *(j.target for j in context.joins), *(s.table for s in context.selectors)}
    binding = Binding(release_identity=json.dumps(engine.identity, sort_keys=True), access_fingerprint='test-access', products=(context.fact,) if scoped else (), purpose='analysis')
    v = build_vocabulary(sem, binding, readable, readable, [MeasureSpec(c.measure, 'USD', c.fact, True) for c in contexts],
                         aliases=[('Metric', context.measure)], scoped_tables=tables)
    measures = [{'kind': 'measure', 'id': 'm' + str(i), 'measure': name(c.measure), 'context': name(c.id), 'mode': 'current', 'filters': []} for i, c in enumerate(contexts)]
    p = model(measures=measures, time=monthly_time('2025-04' if width > 4 else '2025-06', '2025-06'),
              dimensions=[time_dimension()] if width > 4 else [],
              comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': [m['id'] for m in measures], 'evidence': []}] if width > 4 else [])
    data = p.model_dump(mode='json', by_alias=True)
    calendar = sem.d.PERIODS[context.fact]['calendar']
    for endpoint in ('first', 'last'):
        data['time'][endpoint]['calendar'] = name(calendar)
    for dimension in data['dimensions']:
        dimension['calendar'] = name(calendar)
    p = type(p).model_validate(data)
    text = (' and '.join(dict.fromkeys(c.term for c in contexts)) if scenario == 'cohort' else 'Metric') + (' by month 2025-04 through 2025-06 previous period' if width > 4 else ' 2025-06 through 2025-06')
    assignments = {c.term: [m['id'] for m, other in zip(measures, contexts) if other.term == c.term] for c in contexts}
    p = grounded(p, text, v, {**assignments, 'Metric': [m['id'] for m in measures], 'by': 't', 'month': 't',
                            '2025-06': 'time', '2025-04': 'time', 'through': 'time', 'previous period': 'c'})
    return Planner(v, engine.w, Hidden.of(engine, 'analyst_enterprise')), Propose(kind='propose', base_turn=None, plan=p), text, term


@pytest.mark.parametrize('scenario,scoped,width,warehouse_jobs', [
    ('simple', False, 1, 1), ('context', False, 1, 2), ('context', True, 1, 2),
    ('context', False, 8, 2), ('cohort', False, 4, 1),
])
def test_whole_request_graph_retrieval_and_warehouse_budget_against_existing_baseline(monkeypatch, tmp_path, scenario, scoped, width, warehouse_jobs):
    """Keep frozen request budgets; SCD2 declarations and release calendars are loaded at startup."""
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    baseline_question = 'NNA for June 2025' if scenario == 'context' else Q if scenario == 'cohort' else 'Retail revenue for June 2025'
    products = ['wealth.TRAN_FACT_1'] if scoped else None
    records = []
    try:
        for path in ('baseline', 'proposed'):
            monkeypatch.setattr(wording, '_catalogues', {})
            fake.requests.clear()
            engine = opened()
            startup = budgets(fake)
            for temperature in ('cold', 'warm'):
                fake.requests.clear()
                if path == 'baseline':
                    Hidden.of(engine, 'analyst_enterprise')
                    result = Combined(engine, llm=None).ask(baseline_question, 'analyst_enterprise', products=products)
                    assert result['behaviour'] in ('figure', 'combined'), result
                else:
                    planner, reply, text, term = proposal(engine, scenario, width, scoped)
                    result = planner.answer(text, reply)
                    assert result['kind'] == 'result', result
                    if scenario == 'cohort':
                        assert [round(float(c['value']['value']), 2) for c in result['result']['rows'][0]['cells']] == [176, 3, 6400.62, 42.64]
                    request = parse(baseline_question, engine.term_names, engine)
                    if scenario == 'cohort':
                        Combined(engine, llm=None).sources(baseline_question, request.terms_found, 'analyst_enterprise', 'analysis', 'retail')
                measured = budgets(fake)
                records.append((path, temperature, startup, measured))
            engine.close()
            engines.remove(engine)
        baseline, proposed = records[:2], records[2:]
        assert baseline[0][2] == proposed[0][2]
        assert baseline[0][2] == dict(graph=37, retrieval=0, warehouse=1, dry=1, catalogue=10)
        historical_graph = (23, 10) if scenario == 'cohort' else (13, 0)
        historical_retrieval = (3, 2) if scenario == 'cohort' else (0, 0)
        for index, (old, new) in enumerate(zip(baseline, proposed)):
            original = dict(graph=historical_graph[index], retrieval=historical_retrieval[index],
                            warehouse=warehouse_jobs, dry=warehouse_jobs, catalogue=0)
            for record in (old, new):
                measured = record[3]
                assert measured['warehouse'] == measured['dry'] == warehouse_jobs
                assert measured['retrieval'] == original['retrieval']
                assert measured['catalogue'] == original['catalogue']
                assert sum(measured[k] for k in ('graph', 'retrieval', 'warehouse', 'catalogue')) <= sum(
                    original[k] for k in ('graph', 'retrieval', 'warehouse', 'catalogue')) + 1, records
            assert new[3]['retrieval'] == old[3]['retrieval']
        print(json.dumps(records))
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('question', [
    'NNA in FY26-Q1 from clients who also bank with Retail',
    'NNA in FY26-Q1 from clients that also bank with Retail',
])
def test_membership_outside_context_reach_stops_before_data_jobs(monkeypatch, tmp_path, question):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        for temperature in ('cold', 'warm'):
            fake.requests.clear()
            answer = Combined(engine, llm=None).ask(question, 'analyst_enterprise')
            assert answer['behaviour'] == 'abstain' and answer.get('figure') is None
            measured = budgets(fake)
            assert measured['warehouse'] == measured['dry'] == 0
            assert measured['retrieval'] == measured['catalogue'] == 0
            assert measured['graph'] <= (14 if temperature == 'cold' else 1), measured
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('arm', ['schema', 'catalog', 'control'])
def test_restored_baseline_resolution_keeps_the_existing_schema_introspection_budget(monkeypatch, tmp_path, arm):
    from kge.services.ask.engine.config import AGENT_ARMS, PRESETS
    from kge.services.ask.engine.question import question_words
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        cfg = PRESETS.get(arm) or AGENT_ARMS[arm]
        original = opened()
        fake.requests.clear()
        if cfg.resolution == 'words':
            original.guess(question_words('Total retail revenue for June 2025', original))
        else:
            original.resolve('Revenue', cfg)
        historical = budgets(fake)
        assert historical['catalogue'] == (0 if arm == 'catalog' else 8)
        assert historical['warehouse'] == historical['dry'] == historical['retrieval'] == 0
        original.close()
        engines.remove(original)
        engine = opened()
        for temperature in ('cold', 'warm'):
            fake.requests.clear()
            answer = engine.answer('Total retail revenue for June 2025', 'analyst_enterprise',
                                   config=PRESETS.get(arm) or AGENT_ARMS[arm])
            if arm == 'schema':
                assert answer['behaviour'] == 'abstain' and answer.get('figure') is None
            else:
                assert answer['figure'] == 13287.67
            measured = budgets(fake)
            assert measured['warehouse'] == measured['dry'] == (0 if arm == 'schema' else 1)
            assert measured['catalogue'] == (historical['catalogue'] if temperature == 'cold' else 0)
            assert measured['retrieval'] == 0
            assert measured['graph'] <= (14 if temperature == 'cold' else 1), measured
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('question,reason', [
    ('How many accounts were excluded from NNA at 31 Mar 2025?', 'applies no condition on'),
    ('How many clients hold both retail deposits and advisory AUM at 31 Mar 2025?', 'not certified for every named source'),
    ('How many active customers in SME in June 2025 raised a complaint that month, and what were their average balance and fee income?',
     'Not certified in SME Banking: Average Balance, Complaint.'),
])
def test_unapplied_declared_filter_diagnostic_adds_no_data_jobs(monkeypatch, tmp_path, question, reason):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        for temperature in ('cold', 'warm'):
            fake.requests.clear()
            answer = engine.answer(question, 'analyst_enterprise')
            assert answer['behaviour'] == 'abstain' and reason in answer['reason']
            measured = budgets(fake)
            assert measured['warehouse'] == measured['dry'] == measured['catalogue'] == measured['retrieval'] == 0
            assert measured['graph'] <= (14 if temperature == 'cold' else 1), measured
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_native_currency_checks_reuse_cached_measure_declarations(monkeypatch, tmp_path):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        for temperature in ('cold', 'warm'):
            fake.requests.clear()
            answer = engine.answer('Retail revenue for June 2025 in USD', 'analyst_enterprise')
            assert answer['figure'] == 13287.67
            measured = budgets(fake)
            assert measured['warehouse'] == measured['dry'] == 1
            assert measured['catalogue'] == measured['retrieval'] == 0
            assert measured['graph'] <= (14 if temperature == 'cold' else 1), measured
        fake.requests.clear()
        answer = engine.answer('Retail revenue for June 2025 in EUR', 'analyst_enterprise')
        assert answer['behaviour'] == 'abstain' and answer.get('figure') is None
        measured = budgets(fake)
        assert measured['warehouse'] == measured['dry'] == measured['catalogue'] == measured['retrieval'] == 0
        assert measured['graph'] <= 1, measured
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('question', [
    'Which SME clients are also wealth households, and what are their emails?',
    'Which Retail, SME and Corporate customers match, and what are their emails?',
])
def test_private_no_join_requests_use_one_policy_job_and_no_data_jobs(monkeypatch, tmp_path, question):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        for temperature in ('cold', 'warm'):
            fake.requests.clear()
            answer = engine.answer(question, 'analyst_enterprise')
            assert answer['behaviour'] == 'refuse' and answer['joins_refused'] and answer.get('figure') is None
            assert '(POL-DATA-CLASS)' in answer['reason']
            measured = budgets(fake)
            assert measured['warehouse'] == measured['dry'] == measured['retrieval'] == measured['catalogue'] == 0
            assert measured['graph'] <= (11 if temperature == 'cold' else 1), measured
            print(json.dumps({'question': question, 'temperature': temperature, 'jobs': measured}))
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize("question,principal,products,behaviour,warehouse,dry", [
    ("Total revenue for June 2025?", "analyst_enterprise", ["wealth.F_NNA_FIN"], "parts_with_reason", 1, 1),
    ("Total revenue for June 2025?", "analyst_retail", [], "figure", 1, 1),
    ("How many suitability reviews and what was their revenue for Q1 2025?", "analyst_retail",
     ["retail.monthly_revenue"], "blocked_access", 0, 0),
    ("NNA for March 2025 as reported", "advisor_wealth", ["wealth.F_NNA_FIN"], "figure", 2, 2),
    ("Total revenue across the bank for the quarter to 30 June 2025", "analyst_enterprise",
     ["retail.monthly_revenue"], "parts_with_reason", 1, 1),
])
def test_page_fallback_and_unreadable_cohort_request_job_budgets(monkeypatch, tmp_path, question, principal,
                                                                products, behaviour, warehouse, dry):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        for cold in (True, False):
            fake.requests.clear()
            answer = engine.answer(question, principal, products=products)
            assert answer["behaviour"] == behaviour, answer
            measured = budgets(fake)
            assert {k: v for k, v in measured.items() if k != "graph"} == dict(
                warehouse=warehouse, dry=dry, catalogue=0, retrieval=0)
            assert measured["graph"] <= (14 if cold else 1), measured
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('question,unit,table,empty', [
    ('Revenue for June 2025', 'retail', 'monthly_revenue', True),
    ('NNA for June 2025', 'retail', 'monthly_revenue', False),
])
def test_governed_empty_page_result_does_not_fall_back_but_missing_declaration_does(monkeypatch, tmp_path, question, unit, table, empty):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        if empty:
            target = fake._physical(engine.w.dataset_id, engine.w.prefix(unit) + '_' + table)
            fake.con.execute(f'DELETE FROM "{target}"')
        for cold in (True, False):
            fake.requests.clear()
            answer = engine.answer(question, 'analyst_enterprise', products=[unit + '.' + table])
            assert answer['behaviour'] == 'figure', answer
            assert answer['unit'] == ('retail' if empty else 'wealth')
            assert (answer['figure'] is None) is empty
            assert any(step['step'] == 'scope_fallback' for step in answer['trace']) is (not empty)
            jobs = budgets(fake)
            assert {k: v for k, v in jobs.items() if k != 'graph'} == dict(
                warehouse=1 if empty else 2, dry=1 if empty else 2, catalogue=0, retrieval=0)
            assert jobs['graph'] <= (14 if cold else 1), jobs
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_context_candidates_do_not_replan_or_add_graph_jobs(monkeypatch, tmp_path):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        original = engine.semantics.contexts['nna']
        engine.semantics.contexts['nna'] = Context('nna', 'Selected receipts', original.description, original.term,
            original.measure, original.expression, original.aggregation, original.grain, original.fact,
            original.values, original.filters, original.selectors, original.joins)
        for n in range(6):
            engine.semantics.contexts['alternative_' + str(n)] = Context('alternative_' + str(n), 'Alternative ' + str(n),
                original.description, original.term, original.measure, original.expression, original.aggregation,
                original.grain, original.fact, original.values, original.filters, original.selectors, original.joins)
        planned = []
        real = engine.semantics.plan
        def counted(context, asked):
            planned.append(context.id)
            return real(context, asked)
        monkeypatch.setattr(engine.semantics, 'plan', counted)
        for cold in (True, False):
            fake.requests.clear()
            planned.clear()
            answer = engine.answer('Selected receipts for June 2025', 'analyst_enterprise')
            assert answer['behaviour'] == 'figure', answer
            assert planned == ['nna']
            jobs = budgets(fake)
            assert {k: v for k, v in jobs.items() if k != 'graph'} == dict(warehouse=2, dry=2, catalogue=0, retrieval=0)
            assert jobs['graph'] <= (14 if cold else 1), jobs
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()



@pytest.mark.parametrize('count', [1, 12])
def test_no_join_candidate_rejection_is_constant_work_per_request(monkeypatch, tmp_path, count):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        original = engine.semantics.contexts['nna']
        for n in range(count - 1):
            engine.semantics.contexts['candidate_' + str(n)] = Context('candidate_' + str(n), 'Alternative ' + str(n),
                original.description, original.term, original.measure, original.expression, original.aggregation,
                original.grain, original.fact, original.values, original.filters, original.selectors, original.joins)
        reads = []
        real = engine.g.no_join
        def no_join(a, b):
            reads.append((a, b))
            return real(a, b)
        monkeypatch.setattr(engine.g, 'no_join', no_join)
        for cold in (True, False):
            fake.requests.clear()
            reads.clear()
            answer = engine.answer('NNA for SME clients who are also wealth households', 'analyst_enterprise')
            assert answer['behaviour'] == 'refuse' and not answer.get('result'), answer
            assert len(reads) <= 1
            jobs = budgets(fake)
            assert {key: value for key, value in jobs.items() if key != 'graph'} == dict(warehouse=0, dry=0, catalogue=0, retrieval=0)
            assert jobs['graph'] <= (14 if cold else 1), jobs
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('question,doc', [
    ('Explain which fiscal calendar Wealth Management uses', 'STD-FISCAL-CAL'),
    ('Is it acceptable to embed restricted personal data for the assistant?', 'POL-DATA-CLASS'),
])
@pytest.mark.parametrize('modeled', [False, True])
def test_definition_paraphrases_use_only_bounded_native_document_jobs(monkeypatch, tmp_path, question, doc, modeled):
    from kge.services.ask.engine.agent import Agent
    from kge.adapters.model import LLM
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    records = []
    citations = []
    try:
        seed = opened()
        cert = seed.g.certification([doc])[doc]
        chunk = seed.g.q('MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE d.id = $d '
                        'RETURN c.id AS id, c.text AS text, c.purposes AS purposes, c.readiness AS readiness', {'d': doc})[0]
        physical = fake._physical('kge_meridian_graph', 'chunks_abababababababab_11111111')
        fake.con.execute(f'INSERT INTO "{physical}" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', [
            seed.g.embedder.embed(chunk['text']), seed.g.embedder.name, doc, cert['title'], cert['shelf'], cert['tier'],
            cert['ai_ready'], chunk['id'], chunk['readiness'], [], chunk['text'], chunk['purposes'], cert['unit']])
        seed.close()
        engines.remove(seed)
        for surface in ('ask', 'agent'):
            engine = opened()
            model = Stub() if surface == 'ask' else LLM('mock', 'mock')
            caller = Combined(engine, llm=model if modeled else None) if surface == 'ask' else Agent(engine, llm=model if modeled else None)
            for cold in (True, False):
                fake.requests.clear()
                answer = runtrace.run(surface, lambda: caller.ask(question, 'analyst_enterprise'), graph=engine.g)
                assert answer['behaviour'] == 'cite_certified' and doc in answer['cite'], answer
                citations.append(set(answer['cite']))
                jobs = budgets(fake)
                assert jobs == dict(graph=43 if cold else 3 if surface == 'ask' else 2,
                                    retrieval=5 if cold else 4, warehouse=0, dry=0, catalogue=0), jobs
                records.append(dict(surface=surface, cold=cold, jobs=jobs))
            engine.close()
            engines.remove(engine)
        assert all(cited == citations[0] for cited in citations)
        print(json.dumps(dict(question=question, modeled=modeled, requests=records)))
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_agent_whole_request_jobs_do_not_exceed_the_pre_migration_baselines(monkeypatch, tmp_path):
    """Whole native requests, with no catalogue prewarming; model/control uses its schema baseline.

    Before counts were measured at 3cd5f676 on the same graph, retrieval and warehouse
    twins. Its DuckDB-only trace formatter was disabled for native SQL; it submits no jobs.
    Rows give graph/retrieval/warehouse/dry/catalogue for cold and warm requests.
    """
    from kge.services.ask.engine.agent import Agent
    from tests.agent_recordings import replay
    from kge.eval import sets
    from tests.scratch import BUILD
    golden = {i['id']: i['question'] for i in sets.items(BUILD / 'golden.json')}
    wealth = {i['id']: i['question'] for i in sets.items(BUILD / 'golden_wealth_v1.json')}
    cases = {'scalar': golden['G001'], 'context': wealth['W02'], 'definition': golden['G031'],
             'coverage': golden['G029'], 'refusal': golden['G045']}
    before = {
        ('scalar', 'control', False): ((51, 3, 1, 1, 16), (0, 2, 1, 1, 0)),
        ('scalar', 'control', True): ((41, 3, 1, 1, 16), (0, 2, 1, 1, 0)),
        ('scalar', 'treatment', False): ((63, 5, 1, 1, 0), (4, 4, 1, 1, 0)),
        ('scalar', 'treatment', True): ((62, 5, 1, 1, 0), (4, 4, 1, 1, 0)),
        ('context', 'control', False): ((51, 0, 0, 0, 8), (0, 0, 0, 0, 0)),
        ('context', 'control', True): ((41, 3, 1, 1, 16), (0, 2, 1, 1, 0)),
        ('context', 'treatment', False): ((61, 5, 3, 3, 0), (2, 4, 3, 3, 0)),
        ('context', 'treatment', True): ((60, 5, 2, 2, 0), (2, 4, 2, 2, 0)),
        ('definition', 'control', False): ((42, 3, 0, 0, 0), (0, 2, 0, 0, 0)),
        ('definition', 'control', True): ((43, 3, 0, 0, 16), (0, 2, 0, 0, 0)),
        ('definition', 'treatment', False): ((46, 5, 0, 0, 0), (3, 4, 0, 0, 0)),
        ('definition', 'treatment', True): ((60, 5, 0, 0, 0), (7, 4, 0, 0, 0)),
        ('coverage', 'control', False): ((51, 0, 0, 0, 8), (0, 0, 0, 0, 0)),
        ('coverage', 'control', True): ((41, 3, 1, 1, 16), (0, 2, 1, 1, 0)),
        ('coverage', 'treatment', False): ((54, 0, 0, 0, 0), (0, 0, 0, 0, 0)),
        ('coverage', 'treatment', True): ((64, 5, 0, 0, 0), (7, 4, 0, 0, 0)),
        ('refusal', 'control', False): ((40, 0, 0, 0, 0), (0, 0, 0, 0, 0)),
        ('refusal', 'control', True): ((40, 3, 0, 0, 16), (0, 2, 0, 0, 0)),
        ('refusal', 'treatment', False): ((40, 0, 0, 0, 0), (0, 0, 0, 0, 0)),
        ('refusal', 'treatment', True): ((63, 5, 0, 0, 0), (7, 4, 0, 0, 0)),
    }
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path, keys=True)
    measured = []
    try:
        for (case, arm, modeled), limits in before.items():
            engine = opened()
            agent = Agent(engine, llm=replay() if modeled else None)
            for temperature, limit in zip(('cold', 'warm'), limits):
                fake.requests.clear()
                answer = runtrace.run('agent', lambda: agent.ask(cases[case], 'analyst_enterprise', arm=arm), graph=engine.g)
                actual = budgets(fake)
                expected = dict(zip(('graph', 'retrieval', 'warehouse', 'dry', 'catalogue'), limit))
                assert all(actual[k] <= expected[k] for k in expected), (case, arm, modeled, temperature, actual, expected)
                obsolete_context = case == 'context' and arm == 'treatment' and modeled
                jobs = 2 if case == 'context' and arm == 'treatment' else 1 if case == 'scalar' and not (arm == 'control' and modeled) else 0
                if obsolete_context:
                    truth = next(i['expect']['figure'] for i in sets.items(BUILD / 'golden_wealth_v1.json') if i['id'] == 'W02')
                    assert answer['behaviour'] == 'figure' and answer['figure'] == truth == 5232475.35, answer
                    refusal = next(step for step in answer['trace'] if step['step'] == 'model_plan_refused')
                    assert refusal['fallback'] == 'rules' and refusal['reason'] == 'measure does not belong to context', answer
                    assert next(step for step in answer['trace'] if step['step'] == 'plan')['interpretation'] == 'rules'
                    refused, = [edge for edge in answer['graph_path']['edges'] if edge['state'] == 'refused']
                    assert refused['from'] == 'Plan:model' and refused['reason'] == refusal['reason']
                assert actual['warehouse'] == actual['dry'] == jobs, (case, arm, modeled, actual)
                if case == 'scalar' and arm == 'control' and modeled:
                    assert answer['behaviour'] == 'clarify', answer
                assert 'history' in answer
                assert 'Missing plan recording' not in answer.get('reason', ''), answer
                measured.append(dict(case=case, arm=arm, modeled=modeled, temperature=temperature, before=expected, after=actual))
            engine.close()
            engines.remove(engine)
        (BUILD / 'step7-request-budgets.json').write_text(json.dumps(measured, indent=1) + '\n')
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('modeled', [False, True])
def test_agent_complete_cohort_uses_asks_single_answer_job_on_native_bigquery(monkeypatch, tmp_path, modeled):
    from kge.services.ask.engine.agent import Agent
    class Lazy(Stub):
        def __init__(self, engine):
            self.engine, self.prompts, self.sql = engine, [], []
        def json(self, prompt, max_tokens=800):
            self.proposal = Stub(self.engine, question=Q).proposal
            return super().json(prompt, max_tokens)
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        agent = Agent(engine, llm=Lazy(engine) if modeled else None)
        for cold in (True, False):
            fake.requests.clear()
            answer = runtrace.run('agent', lambda: agent.ask(Q, 'analyst_enterprise'), graph=engine.g)
            assert answer['behaviour'] == 'combined', answer
            assert [f['value'] for f in answer['figures']] == [176, 3, 6400.62, 42.64]
            measured = budgets(fake)
            assert {k: v for k, v in measured.items() if k != 'graph'} == dict(
                warehouse=1, dry=1, retrieval=3 if cold else 2, catalogue=0)
            assert measured['graph'] <= (63 if cold else 10), measured
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()



def test_schema_model_control_can_execute_a_recorded_plan_with_native_metadata(monkeypatch, tmp_path):
    from kge.eval import sets
    from kge.services.ask.engine.agent import Agent
    from tests.agent_recordings import replay
    from tests.scratch import BUILD
    question = next(i['question'] for i in sets.items(BUILD / 'heldout.json') if i['id'] == 'H001')
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path, keys=True)
    try:
        engine = opened()
        agent = Agent(engine, llm=replay())
        for cold in (True, False):
            fake.requests.clear()
            answer = runtrace.run('agent', lambda: agent.ask(question, 'analyst_enterprise', arm='control'), graph=engine.g)
            assert answer['behaviour'] == 'figure' and answer['figure'] == 321130.65, answer
            assert answer['history'][-1]['plan']['measures'][0]['context']['id'].startswith('schema:')
            measured = budgets(fake)
            assert measured['warehouse'] == measured['dry'] == 1
            assert measured['retrieval'] <= (3 if cold else 2)
            assert measured['catalogue'] <= (16 if cold else 0)
            assert measured['graph'] <= (41 if cold else 0), measured
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_cancellable_request_keeps_the_same_native_bigquery_job_counts(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from kge.pipeline.trace import cancellable

    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    measured = []
    try:
        for cancellation in (False, True):
            engine = opened()
            caller = Combined(engine, llm=None)
            requests = []
            for _ in range(2):
                fake.requests.clear()
                with cancellable() if cancellation else nullcontext():
                    answer = caller.ask('NNA for June 2025', 'analyst_enterprise')
                assert answer['behaviour'] == 'figure', answer
                requests.append(budgets(fake))
                assert not any('/cancel' in req['url'] for req in fake.requests)
            measured.append(requests)
            engine.close()
            engines.remove(engine)
        assert measured[0] == measured[1], measured
        assert all(count['warehouse'] == count['dry'] == 2 for count in measured[1]), measured
        print(json.dumps(dict(cancellable_request_jobs=measured)))
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('question', [
    'NNA for June 2025 by region',
    'Certified NNA for April 2025 by household',
    'NNA year to date through June 2025',
    'NNA fiscal year to date through June 2025',
])
def test_context_scoped_model_prompt_adds_no_native_jobs(monkeypatch, tmp_path, question):
    import copy
    import re
    from kge.services.ask.engine import planrules
    from kge.services.ask.engine.core import PRESETS
    from kge.services.ask.engine.scope import ProductScope
    from tests.integration.test_context_scope import TABLES

    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        req = parse(question, engine.term_names, engine)
        vocab = planrules.vocabulary(engine, 'analyst_enterprise', 'analysis', ProductScope(engine, None), PRESETS['graph'], req)
        proposal = planrules.read(engine, req, vocab, PRESETS['graph'])
        assert isinstance(proposal, Propose), proposal
        raw = proposal.model_dump(mode='json', by_alias=True)

        class ScopedModel:
            name = 'test:scoped'

            def json(self, prompt):
                payload = json.loads(prompt.split('\n', 1)[1])
                shown = set(re.findall(r'wealth\.([A-Z][A-Z0-9_]+)', json.dumps(payload['vocabulary'])))
                assert shown <= {t.split('.')[1] for t in TABLES}
                def bind(value):
                    if isinstance(value, dict):
                        return {k: payload['turn_id'] if k == 'turn' else bind(v) for k, v in value.items()}
                    return [bind(v) for v in value] if isinstance(value, list) else value
                return bind(copy.deepcopy(raw))

        engine.answer(question, 'analyst_enterprise')
        fake.requests.clear()
        baseline = engine.answer(question, 'analyst_enterprise')
        before = budgets(fake)
        assert baseline['behaviour'] == 'figure', baseline
        fake.requests.clear()
        answer = Combined(engine, llm=ScopedModel()).ask(question, 'analyst_enterprise')
        after = budgets(fake)
        assert answer['behaviour'] == 'figure', answer
        assert {k: v for k, v in after.items() if k != 'graph'} == dict(warehouse=2, dry=2, catalogue=0, retrieval=0)
        assert after['graph'] <= before['graph']
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_branch_weekday_transaction_join_preserves_native_job_budget(monkeypatch, tmp_path):
    from decimal import Decimal
    from tests.integration.test_plan_wording_round2 import dimension, measure, vocabulary

    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        question = 'Branch sales NNA by day of week during May 2025'
        v = vocabulary(engine, question)
        ctx = v.contexts['nna_branch_sales']
        p = grounded(model(measures=[measure(ctx)],
                           time=dict(kind='single', period=dict(kind='month', value='2025-05', calendar=name('calendar'))),
                           dimensions=[dimension(ctx, 'wealth.DATE_DIM_1.DAY_OF_WEEK', ['branch_sales_date'])]),
                     question, v, {'by day of week': 'd', 'May 2025': 'time'})
        for _ in range(2):
            fake.requests.clear()
            result = Planner(v, engine.w, Hidden([], set(), [])).answer(question, Propose(kind='propose', base_turn=None, plan=p))
            assert result['kind'] == 'result', result
            values = {row['dimensions'][0]['value']: Decimal(row['cells'][0]['value']['value'])
                      for row in result['result']['rows'] if not row.get('total')}
            assert len(values) == 7 and values['Saturday'] == Decimal('279652.06')
            assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_worded_nna_definition_keeps_the_pre_link_service_request_job_budget(monkeypatch, tmp_path):
    from kge.services.ask import service
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    measured = {}
    try:
        for linked in (False, True):
            engine = opened()
            caller = Combined(engine, llm=None)
            if not linked:
                original = engine.g.q
                def before_link(cypher, params=None):
                    rows = original(cypher, params)
                    if 'OPTIONAL MATCH (d:Document)-[:DEFINES]->(t)' in cypher:
                        return [{**row, 'doc': None} if row.get('term') == 'Net New Assets' else row for row in rows]
                    return rows
                monkeypatch.setattr(engine.g, 'q', before_link)
            requests = []
            for _ in range(2):
                fake.requests.clear()
                answer = service.ask(caller, engine.profile, 'NNA for June 2025', 'analyst_enterprise', 'analysis',
                                     'graph', caller.default_config())
                assert answer['figure'] == 6647562.78 and answer['answer_text'], answer
                assert answer['certified_definitions'][0]['term'] == 'Net New Assets'
                requests.append(budgets(fake))
                assert requests[-1]['warehouse'] == requests[-1]['dry'] == 2
                assert requests[-1]['catalogue'] == requests[-1]['retrieval'] == 0
            measured[linked] = requests
            engine.close()
            engines.remove(engine)
        for before, after in zip(measured[False], measured[True]):
            assert after['graph'] <= before['graph'], measured
        print(json.dumps(measured))
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()
