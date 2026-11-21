"""Each filter the plan takes from the question's own words is a note on the answer (#474 G1)."""


def test_each_filter_read_from_the_question_s_words_is_a_note():
    from types import SimpleNamespace as N

    from kge.services.ask.engine.planpublic import question_filter_notes
    question = 'Metric that flowed in for Upper Tier'
    span = lambda start, end, turn='t': N(turn=turn, start=start, end=end)  # noqa: E731
    column = lambda name: N(kind='column', name=N(id='u.' + name))  # noqa: E731
    direction = N(kind='compare', id='f1', left=column('events.direction'), op='eq', value=N(value='I'), magnitude=False, evidence=())
    level = N(kind='in', id='f2', left=column('events.level'), values=(N(value='UT'),), evidence=(span(26, 36),))
    defined = N(kind='compare', id='f3', left=column('events.kept'), op='eq', value=N(value='Y'), magnitude=False, evidence=())
    earlier = N(kind='null', id='f4', left=column('events.closed'), is_null=True, evidence=(span(0, 6, 'before'),))
    plan = N(filters=(N(kind='all', id='all', children=(direction, level, defined, earlier), evidence=()),), measures=(),
             obligations=(N(state='applied', targets=('all', 'f1'), spans=(span(12, 21),)),
                          N(state='removed', targets=('f3',), spans=(span(0, 6),))))
    assert question_filter_notes(plan, question, 't') == [
        'filter read from "flowed in": events.direction = I', 'filter read from "Upper Tier": events.level in (UT)']


def test_an_exclusion_and_a_negation_are_each_one_note_never_their_inverted_parts():
    from types import SimpleNamespace as N

    from kge.services.ask.engine.planpublic import question_filter_notes
    question = 'Metric excluding Upper Tier, not flagged'
    words = N(turn='t', start=7, end=27)
    flag = N(turn='t', start=29, end=40)
    column = lambda name: N(kind='column', name=N(id='u.events.' + name), path=(), model_dump=lambda **_: {})  # noqa: E731
    inner = N(kind='in', id='f1', left=column('level'), values=(N(value='UT'),), evidence=())
    empty = N(kind='null', id='f2', left=column('level'), is_null=True, evidence=())
    excluded = N(kind='any', id='x', children=(N(kind='not', id='n', child=inner, evidence=()), empty), evidence=())
    negated = N(kind='not', id='m', child=N(kind='compare', id='f3', left=column('flag'), op='eq', value=N(value=True),
                                            magnitude=False, evidence=()), evidence=())
    plan = N(filters=(excluded, negated), measures=(),
             obligations=(N(state='applied', targets=('x', 'n', 'f1', 'f2'), spans=(words,)),
                          N(state='applied', targets=('m', 'f3'), spans=(flag,))))
    assert question_filter_notes(plan, question, 't') == [
        'filter read from "excluding Upper Tier": events.level not in (UT) or empty',
        'filter read from "not flagged": not (events.flag = true)']
