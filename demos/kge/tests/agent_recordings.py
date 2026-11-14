"""Explicit replay of the approved Agent plan-contract fixtures, never runtime discovery."""
import base64
import copy
import hashlib
import json

from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.core import parse
from kge.services.ask.engine.scope import ProductScope
from tests.plan_recordings import recorded
from tests.scratch import DEMO

PATH = DEMO / 'tests/fixtures/agent-query-plan-v1-recordings.json'
VOCABULARY_MIGRATION = DEMO / 'tests/fixtures/agent_query_plan_readable_vocabulary_manifest.json'
VOCABULARY_402_MIGRATION = DEMO / 'tests/fixtures/agent_query_plan_402_vocabulary_manifest.json'


def key_of(text):
    return base64.b32encode(hashlib.sha256(text.encode()).digest()).decode().rstrip('=').lower()


def before_402_recordings():
    rows = json.loads(PATH.read_text())
    previous = {row['after']: row['before'] for row in json.loads(VOCABULARY_402_MIGRATION.read_text())['recordings']}
    for row in rows:
        row['prompt_hash'] = previous.get(row['prompt_hash'], row['prompt_hash'])
    return rows


def recordings():
    rows = {row['prompt_hash']: row['reply'] for row in json.loads(PATH.read_text())}
    for migration in json.loads(VOCABULARY_402_MIGRATION.read_text())['recordings']:
        rows[migration['before']] = rows[migration['after']]
    for migration in json.loads(VOCABULARY_MIGRATION.read_text())['recordings']:
        rows[migration['after']] = rows[migration['before']]
    return rows


def for_question(engine, question, principal, arm, mode='careful'):
    from kge.services.ask.engine.agent import Agent
    cfg = Agent(engine, llm=recorded({})).config(arm)
    request = parse(question, engine.term_names if cfg.glossary else [], engine, aliases=cfg.resolution not in ('none', 'words'))
    vocab = (planrules.vocabulary(engine, principal, 'analysis', ProductScope(engine, None), cfg, request) if cfg.graph
             else planrules.control_vocabulary(engine, principal, 'analysis', ProductScope(engine, None), request)[0])
    payload = planmodel.payload(question, vocab)
    rows = recordings()
    reply = copy.deepcopy(rows.get(key_of(payload['recording_key'])))
    if reply is None:
        model = recorded({})
        model.mode = model.chat.mode = mode
        return model
    turn = hashlib.sha256(json.dumps([question, cfg.label, vocab.binding.model_dump(mode='json'), []],
                                    sort_keys=True).encode()).hexdigest()[:32]
    def bind(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'turn' and child == 'turn':
                    value[key] = turn
                else:
                    bind(child)
        elif isinstance(value, list):
            for child in value:
                bind(child)
    bind(reply)
    model = recorded({payload['recording_key']: reply})
    model.mode = model.chat.mode = mode
    return model



def generate(engine):
    """Record full typed replies; preserve prior mock abstentions outside approved answer moves."""
    from kge.services.ask.engine.agent import Agent
    from tests.test_agent_loop import questions
    preserved = {'G018', 'G020', 'W01', 'W05', 'W14', 'W15', 'W18'}
    rows = {}
    for key, question, who, arm in questions():
        cfg = Agent(engine, llm=recorded({})).config(arm)
        request = parse(question, engine.term_names if cfg.glossary else [], engine,
                        aliases=cfg.resolution not in ('none', 'words'))
        scope = ProductScope(engine, None)
        original = planrules.vocabulary(engine, who, 'analysis', scope, cfg, request)
        vocab = original if cfg.graph else planrules.control_vocabulary(engine, who, 'analysis', scope, request)[0]
        data = planmodel.payload(question, vocab)
        reply = planrules.read(engine, request, original, cfg,
                               cohort=cfg.graph and bool(planrules.cohort_terms(engine, request))).model_dump(mode='json', by_alias=True)
        if cfg.graph and key.split(':')[1] in preserved:
            reply = {'kind': 'abstain', 'spans': [], 'missing': ['The recorded interpretation has no complete checked proposal.']}
        if key.split(':')[1] == 'F04':
            reply = {'kind': 'abstain', 'spans': [], 'missing': ['No declared product population supplies a complete interpretation of this threshold request.']}
        if not cfg.graph:
            identifiers = {}
            for ctx in original.contexts.values():
                identifier = 'schema:' + ':'.join((*ctx.values, ctx.aggregation))
                if identifier in vocab.contexts:
                    identifiers[ctx.id], identifiers[ctx.measure] = identifier, identifier + '/measure'
            identifiers.update({identifier: 'schema:entity:' + entity['table'] for identifier, entity in original.entities.items()})
            identifiers.update({calendar: 'schema_calendar' for calendar in original.calendars})
            def rename(value):
                if isinstance(value, dict):
                    return {key: rename(child) for key, child in value.items()}
                if isinstance(value, list):
                    return [rename(child) for child in value]
                return identifiers.get(value, value) if isinstance(value, str) else value
            reply = rename(reply)
        case = key
        key = key_of(data['recording_key'])
        rows[key] = {'key': key, 'reply': reply, 'case': case, 'question': question, 'arm': arm, 'version': 'query-plan-v1'}
    PATH.write_text(json.dumps(list(rows.values()), indent=1, sort_keys=True) + '\n')
    return len(rows)


def replay():
    """Bind explicit recorded evidence without prewarming any engine catalogue."""
    from kge.adapters.model.mock import ChatMock
    rows = recordings()
    class Replay(ChatMock):
        def _generate(self, messages, *args, **kwargs):
            prompt = messages[-1].content
            if prompt.startswith('query-plan-v1\n'):
                data = json.loads(prompt.split('\n', 1)[1])
                reply = copy.deepcopy(rows.get(key_of(data['recording_key'])))
                def bind(value):
                    if isinstance(value, dict):
                        return {key: data['turn_id'] if key == 'turn' else bind(child) for key, child in value.items()}
                    if isinstance(value, list):
                        return [bind(child) for child in value]
                    return value
                held = {data['recording_key']: bind(reply)} if reply else {}
                return ChatMock(mode=self.mode, recordings=held)._generate(messages, *args, **kwargs)
            return super()._generate(messages, *args, **kwargs)
    model = recorded({})
    model.chat = Replay()
    return model
