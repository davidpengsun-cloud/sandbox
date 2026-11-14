"""Direct legacy callers retain path events without duplicating the live graph stream."""
from kge.services.ask.engine.path import PathStreamRecorder, build_path


def test_direct_recorder_projects_the_complete_final_path_and_state():
    answer = dict(question='Metric for June 2025', behaviour='figure', figure=12, unit='retail',
                  sql='SELECT 12', trace=[dict(step='parse', term='Metric')])
    answer['path'] = build_path(None, answer)
    events = []
    recorder = PathStreamRecorder(answer['question'], events.append)
    recorder.emit_initial()
    recorder.on_stage('plan')
    recorder.emit_final(answer)
    assert [e['node']['id'] for e in events if e['event'] == 'node_added'] == [n['id'] for n in answer['path']['nodes']]
    assert [e['edge'] for e in events if e['event'] == 'edge_added'] == answer['path']['edges']
    assert [e['node'] for e in events if e['event'] == 'state_changed'] == answer['path']['nodes']
    assert events[-1] == dict(event='answer', answer=answer)


def test_live_graph_finalization_never_replays_legacy_nodes_or_edges():
    answer = dict(path=dict(nodes=[dict(id='legacy')], edges=[]), graph_path=dict(nodes=[], edges=[], unused=3))
    events = []
    PathStreamRecorder('Metric', events.append).emit_final(answer)
    assert events == [dict(event='graph_summary', unused=3), dict(event='answer', answer=answer)]
