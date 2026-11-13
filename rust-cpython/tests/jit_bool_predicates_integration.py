import _opcode
import argparse
import dis
import itertools
import gc
import weakref
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--expect', choices=('baseline', 'candidate', 'interpreter'), default='candidate')
expect = parser.parse_args().expect
jit_expected = expect != 'interpreter'


def entries(fn):
    result = []
    for offset in range(0, len(fn.__code__.co_code), 2):
        try:
            result.extend(_opcode.get_executor(fn.__code__, offset))
        except (ValueError, RuntimeError):
            pass
    return result


def make(layout, method, bound):
    namespace = {}
    slots = "__slots__ = ('task_holding', 'packet_pending', 'task_waiting')" if layout == 'slots' else 'pass'
    call = 'owner.' + method + '()' if bound else 'fn(owner)'
    exec('''class Owner:
    %s
    def isTaskHoldingOrWaiting(self):
        return self.task_holding or (not self.packet_pending and self.task_waiting)
    def isWaitingWithPacket(self):
        return self.packet_pending and self.task_waiting and not self.task_holding

def loop(n, fn, owner, active):
    observed = False
    for index in range(n):
        observed = active()
        value = %s
    return value, observed
''' % (slots, call), namespace)
    owner = namespace['Owner']()
    return owner, getattr(namespace['Owner'], method), namespace['loop']


def assign(owner, flags):
    owner.task_holding, owner.packet_pending, owner.task_waiting = flags


def expected(method, flags):
    holding, pending, waiting = flags
    if method == 'isTaskHoldingOrWaiting':
        return holding or (not pending and waiting)
    return pending and waiting and not holding


for layout, method, bound, flags in itertools.product(
        ('slots', 'managed'), ('isTaskHoldingOrWaiting', 'isWaitingWithPacket'),
        (False, True), itertools.product((False, True), repeat=3)):
    owner, fn, loop = make(layout, method, bound)
    assign(owner, flags)
    value, active = loop(8000, fn, owner, sys._jit.is_active)
    trace = entries(loop)
    names = {entry[0] for entry in trace}
    assert (active or not jit_expected) and value is expected(method, flags), (layout, method, bound, flags, active, value)
    if expect == 'baseline':
        assert '_PUSH_FRAME' in names, (layout, method, bound, flags, names)
    elif expect == 'candidate':
        assert '_BEGIN_VIRTUAL_CALL' in names and '_RETURN_VIRTUAL_CALL' in names, (layout, method, bound, flags, names)
        assert '_TO_BOOL_BOOL' in names and any('GUARD_BIT_IS_' in name for name in names), names
    for changed in itertools.product((False, True), repeat=3):
        assign(owner, changed)
        value, _ = loop(1, fn, owner, sys._jit.is_active)
        assert value is expected(method, changed), (layout, method, bound, flags, changed, value)

def callback_case(layout, behavior):
    owner, fn, _ = make(layout, 'isTaskHoldingOrWaiting', False)
    assign(owner, (False, False, True))
    events = []

    class Probe:
        def __bool__(self):
            frame = sys._getframe(1)
            instruction = next(item for item in dis.get_instructions(frame.f_code) if item.offset == frame.f_lasti)
            events.append((frame.f_code is fn.__code__, frame.f_locals == {'self': owner}, instruction.opname, instruction.offset))
            if behavior == 'raise':
                raise ValueError('probe')
            if behavior == 'bad':
                return 1
            if behavior == 'mutate':
                frame.f_locals['self'].task_waiting = False
            if behavior == 'rebind':
                replacement = type(owner)()
                assign(replacement, (True, True, False))
                frame.f_locals['self'] = replacement
            return behavior == 'true'

    probe = Probe()
    namespace = {'entries': entries}
    exec("""def loop(n, fn, owner, active, source):
    observed = False
    trace = []
    for index in range(n):
        owner.task_holding = source.pop()
        observed = active()
        value = fn(owner)
        if index == 6999:
            trace = entries(loop)
    return value, observed, index, trace
""", namespace)
    loop = namespace['loop']
    source = [probe] + [False] * 7999
    try:
        value, active, index, trace = loop(8000, fn, owner, sys._jit.is_active, source)
    except (ValueError, TypeError) as error:
        assert behavior in ('raise', 'bad')
        assert type(error) is (ValueError if behavior == 'raise' else TypeError)
        tb = error.__traceback__
        frames = []
        while tb:
            frames.append(tb.tb_frame)
            tb = tb.tb_next
        caller = next(frame for frame in frames if frame.f_code is loop.__code__)
        active, index, trace = (caller.f_locals[key] for key in ('observed', 'index', 'trace'))
        assert any(frame.f_code is fn.__code__ for frame in frames)
    else:
        assert behavior in ('return', 'mutate', 'rebind', 'true')
        assert value is (probe if behavior == 'true' else behavior not in ('mutate', 'rebind'))
    assert (active or not jit_expected) and index == 7999 and not source
    first_bool = next(item.offset for item in dis.get_instructions(fn) if item.opname == 'TO_BOOL')
    assert events == [(True, True, 'TO_BOOL', first_bool)], events
    names = {entry[0] for entry in trace}
    if expect == 'baseline':
        assert '_PUSH_FRAME' in names, names
    elif expect == 'candidate':
        assert '_BEGIN_VIRTUAL_CALL' in names and '_TO_BOOL_BOOL' in names, names


for layout, behavior in itertools.product(('slots', 'managed'), ('return', 'true', 'raise', 'bad', 'mutate', 'rebind')):
    callback_case(layout, behavior)

def source_loop(method, bound, direct=False):
    namespace = {'entries': entries}
    call = ('source.pop().' + method + '()' if bound else 'fn(source.pop())') if direct else ('owner.' + method + '()' if bound else 'fn(owner)')
    load = '' if direct else '        owner = source.pop()\n'
    exec('''def loop(n, fn, source, active):
    trace = []
    observed = False
    for index in range(n):
%s        observed = active()
        value = %s
        if index == 6999:
            trace = entries(loop)
    return value, trace, observed, index
''' % (load, call), namespace)
    return namespace['loop']


def require_shape(trace, fn):
    if not jit_expected:
        return
    names = {entry[0] for entry in trace}
    if expect == 'baseline':
        assert '_PUSH_FRAME' in names, names
        return
    assert '_BEGIN_VIRTUAL_CALL' in names and '_RETURN_VIRTUAL_CALL' in names, names
    returned = {item.offset // 2 for item in dis.get_instructions(fn) if item.opname == 'RETURN_VALUE'}
    regions = []
    region = None
    for index, entry in enumerate(trace):
        if entry[0] == '_BEGIN_VIRTUAL_CALL':
            region = []
        elif entry[0] == '_RETURN_VIRTUAL_CALL' and region is not None:
            regions.append(region)
            region = None
        elif region is not None and 'GUARD_BIT_IS_' in entry[0]:
            target = entry[2]
            assert target > index and target < len(trace), (index, entry)
            destination = trace[target:target + 3]
            materializers = [row for row in destination if row[0] == '_MATERIALIZE_VIRTUAL_FRAME']
            assert len(materializers) == 1 and materializers[0][3] in returned, (entry, destination, returned)
            region.append(entry)
    assert regions and any(regions), trace


def fault_case(layout, method, bound, scenario):
    warm, fn, _ = make(layout, method, bound)
    flags = (False, False, True) if method == 'isTaskHoldingOrWaiting' else (False, True, True)
    assign(warm, flags)
    fault = type(warm)()
    assign(fault, flags)
    events = []
    sentinel = object()
    result = expected(method, flags)
    missing = None
    if scenario == 'branch':
        assign(fault, (True, False, False))
        result = expected(method, (True, False, False))
    elif scenario == 'result':
        class Result:
            def __bool__(self):
                raise AssertionError('final attribute was coerced')
        sentinel = Result()
        if method == 'isTaskHoldingOrWaiting':
            fault.task_waiting = sentinel
            result = sentinel
        else:
            fault.task_holding = sentinel
            result = False
    elif scenario == 'missing':
        missing = 'task_waiting'
        del fault.task_waiting
    elif scenario == 'alias':
        fault.task_waiting = fault
        result = fault
    elif scenario == 'replace':
        fault.__dict__ = dict(task_holding=True, packet_pending=False, task_waiting=False)
        result = expected(method, (True, False, False))
    elif scenario == 'clear':
        fault.__dict__.clear()
        missing = 'task_holding' if method == 'isTaskHoldingOrWaiting' else 'packet_pending'
    elif scenario in ('descriptor', 'descriptor-error'):
        class Other:
            @property
            def task_holding(self):
                frame = sys._getframe(1)
                ins = next(item for item in dis.get_instructions(frame.f_code) if item.offset == frame.f_lasti)
                events.append((frame.f_code is fn.__code__, frame.f_locals == {'self': self}, ins.opname, ins.argval))
                if scenario == 'descriptor-error':
                    raise ValueError('descriptor')
                return True
            packet_pending = True
            task_waiting = True
        setattr(Other, method, fn)
        fault = Other()
        result = method == 'isTaskHoldingOrWaiting'
    caller = source_loop(method, bound)
    source = [fault] + [warm] * 7015
    try:
        value, trace, active, progress = caller(7016, fn, source, sys._jit.is_active)
    except (AttributeError, ValueError) as error:
        assert missing or scenario == 'descriptor-error', (scenario, error)
        tb = error.__traceback__
        callee = []
        while tb:
            frame = tb.tb_frame
            if frame.f_code is caller.__code__:
                active, progress, trace = (frame.f_locals[key] for key in ('observed', 'index', 'trace'))
            if frame.f_code is fn.__code__:
                ins = next(item for item in dis.get_instructions(fn) if item.offset == tb.tb_lasti)
                callee.append((ins.opname, ins.argval, frame.f_locals == {'self': fault}))
            tb = tb.tb_next
        assert len(callee) == 1 and callee[0][0] == 'LOAD_ATTR' and callee[0][2], callee
        if missing:
            assert type(error) is AttributeError and error.name == missing and error.obj is fault
            assert callee[0][1] == missing, callee
        else:
            assert type(error) is ValueError and str(error) == 'descriptor'
        assert error.__context__ is None
    else:
        assert not missing and scenario != 'descriptor-error', scenario
        assert value is result, (layout, method, bound, scenario, value, result)
    assert (active or not jit_expected) and progress == 7015 and not source, (scenario, active, progress, len(source))
    require_shape(trace, fn)
    if scenario.startswith('descriptor'):
        assert events == [(True, True, 'LOAD_ATTR', 'task_holding')], events
    else:
        assert not events


for layout, method, bound in itertools.product(
        ('slots', 'managed'), ('isTaskHoldingOrWaiting', 'isWaitingWithPacket'), (False, True)):
    for scenario in ('branch', 'missing', 'descriptor', 'descriptor-error'):
        fault_case(layout, method, bound, scenario)
    if method == 'isTaskHoldingOrWaiting':
        fault_case(layout, method, bound, 'result')
        fault_case(layout, method, bound, 'alias')
    if layout == 'managed':
        for scenario in ('replace', 'clear'):
            fault_case(layout, method, bound, scenario)


def ownership(layout, bound):
    warm, fn, _ = make(layout, 'isTaskHoldingOrWaiting', bound)
    assign(warm, (False, False, True))
    events = []

    class Result:
        def __bool__(self):
            raise AssertionError('returned attribute was coerced')
        def __del__(self):
            events.append('result')

    class FinalOwner(type(warm)):
        __slots__ = ()
        def __del__(self):
            events.append(('owner', sys._getframe(1).f_code.co_name))

    owner = FinalOwner()
    assign(owner, (False, False, Result()))
    owner_ref = weakref.ref(owner) if layout == 'managed' else None
    result_ref = weakref.ref(owner.task_waiting)
    caller = source_loop('isTaskHoldingOrWaiting', bound, direct=True)
    training = FinalOwner()
    assign(training, (False, False, True))
    source = [owner] + [training] * 7015
    del owner
    value, trace, active, progress = caller(7016, fn, source, sys._jit.is_active)
    assert (active or not jit_expected) and progress == 7015 and not source
    require_shape(trace, fn)
    assert type(value) is Result and result_ref() is value
    assert events == [('owner', 'loop')], events
    if owner_ref:
        assert owner_ref() is None
    del value
    gc.collect()
    assert result_ref() is None and events == [('owner', 'loop'), 'result'], events


for layout, bound in itertools.product(('slots', 'managed'), (False, True)):
    ownership(layout, bound)


def observers(mode, before_warm):
    owner, fn, caller = make('managed', 'isTaskHoldingOrWaiting', False)
    assign(owner, (False, False, True))
    events = []

    def trace(frame, event, argument):
        if frame.f_code is fn.__code__ and event in ('call', 'return'):
            events.append((event, frame.f_locals == {'self': owner}, argument if event == 'return' else None))
        return trace

    tool = 5
    monitoring = sys.monitoring
    if mode == 'trace':
        enable = lambda: sys.settrace(trace)
        disable = lambda: sys.settrace(None)
    elif mode == 'profile':
        enable = lambda: sys.setprofile(trace)
        disable = lambda: sys.setprofile(None)
    else:
        def started(code, offset):
            if code is fn.__code__:
                events.append(('call', True, None))
        def returned(code, offset, value):
            if code is fn.__code__:
                events.append(('return', True, value))
        def enable():
            monitoring.use_tool_id(tool, 'boolean-predicate-test')
            monitoring.register_callback(tool, monitoring.events.PY_START, started)
            monitoring.register_callback(tool, monitoring.events.PY_RETURN, returned)
            monitoring.set_events(tool, monitoring.events.PY_START | monitoring.events.PY_RETURN)
        def disable():
            monitoring.set_events(tool, 0)
            monitoring.register_callback(tool, monitoring.events.PY_START, None)
            monitoring.register_callback(tool, monitoring.events.PY_RETURN, None)
            monitoring.free_tool_id(tool)
    if not before_warm:
        value, active = caller(8000, fn, owner, sys._jit.is_active)
        assert (active or not jit_expected) and value is True
        require_shape(entries(caller), fn)
    enable()
    try:
        value, active = caller(4, fn, owner, sys._jit.is_active)
    finally:
        disable()
    assert value is True
    assert events == [('call', True, None), ('return', True, True)] * 4, (mode, before_warm, events)


for mode, before_warm in itertools.product(('trace', 'profile', 'monitoring'), (False, True)):
    observers(mode, before_warm)

print('boolean predicates:', expect, '64 warmed traces, flag flips, callbacks and ownership passed')
