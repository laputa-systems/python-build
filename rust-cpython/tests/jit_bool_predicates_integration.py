import _opcode
import argparse
import dis
import itertools
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--expect', choices=('baseline', 'candidate'), required=True)
expect = parser.parse_args().expect


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
    assert active and value is expected(method, flags), (layout, method, bound, flags, active, value)
    if expect == 'baseline':
        assert '_PUSH_FRAME' in names, (layout, method, bound, flags, names)
    else:
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
            events.append((frame.f_code is fn.__code__, frame.f_locals['self'] is owner, instruction.opname))
            if behavior == 'raise':
                raise ValueError('probe')
            if behavior == 'bad':
                return 1
            if behavior == 'mutate':
                frame.f_locals['self'].task_waiting = False
            return False

    probe = Probe()
    namespace = {}
    exec("""def loop(n, fn, owner, active, source):
    observed = False
    for index in range(n):
        owner.task_holding = source.pop()
        observed = active()
        value = fn(owner)
    return value, observed, index
""", namespace)
    loop = namespace['loop']
    source = [probe] + [False] * 7999
    try:
        value, active, index = loop(8000, fn, owner, sys._jit.is_active, source)
    except (ValueError, TypeError) as error:
        assert behavior in ('raise', 'bad')
        assert type(error) is (ValueError if behavior == 'raise' else TypeError)
        tb = error.__traceback__
        frames = []
        while tb:
            frames.append(tb.tb_frame)
            tb = tb.tb_next
        caller = next(frame for frame in frames if frame.f_code is loop.__code__)
        active, index = caller.f_locals['observed'], caller.f_locals['index']
        assert any(frame.f_code is fn.__code__ for frame in frames)
    else:
        assert behavior in ('return', 'mutate')
        assert value is (behavior != 'mutate')
    assert active and index == 7999 and not source
    assert events == [(True, True, 'TO_BOOL')], events
    names = {entry[0] for entry in entries(loop)}
    if expect == 'baseline':
        assert '_PUSH_FRAME' in names, names
    else:
        assert '_BEGIN_VIRTUAL_CALL' in names and '_TO_BOOL_BOOL' in names, names


for layout, behavior in itertools.product(('slots', 'managed'), ('return', 'raise', 'bad', 'mutate')):
    callback_case(layout, behavior)

print('boolean predicates:', expect, '32 warmed traces, flag flips and callback cases passed')
