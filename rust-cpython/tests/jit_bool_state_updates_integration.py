import _opcode
import argparse
import dis
import gc
import itertools
import sys
import weakref

parser = argparse.ArgumentParser()
parser.add_argument('--expect', choices=('baseline', 'candidate', 'interpreter'), default='candidate')
expect = parser.parse_args().expect
jit_expected = expect != 'interpreter'
FIELDS = ('packet_pending', 'task_waiting', 'task_holding')
STATES = {'running': (False, False, False), 'packetPending': (True, False, False),
          'waiting': (False, True, False), 'waitingWithPacket': (True, True, False)}


def entries(fn):
    result = []
    for offset in range(0, len(fn.__code__.co_code), 2):
        try:
            result.extend(_opcode.get_executor(fn.__code__, offset))
        except (ValueError, RuntimeError):
            pass
    return result


def make(layout='managed', method='running', bound=False, direct=False):
    namespace = {'entries': entries}
    layout_code = "__slots__ = ('packet_pending', 'task_waiting', 'task_holding')" if layout == 'slots' else 'pass'
    call = ('source.pop().' + method + '()' if bound else 'fn(source.pop())') if direct else ('owner.' + method + '()' if bound else 'fn(owner)')
    load = '' if direct else '        owner = source.pop()\n'
    exec('''class Owner:
    %s
    def running(self):
        self.packet_pending = False
        self.task_waiting = False
        self.task_holding = False
        return self
    def packetPending(self):
        self.packet_pending = True
        self.task_waiting = False
        self.task_holding = False
        return self
    def waiting(self):
        self.packet_pending = False
        self.task_waiting = True
        self.task_holding = False
        return self
    def waitingWithPacket(self):
        self.packet_pending = True
        self.task_waiting = True
        self.task_holding = False
        return self
    def integer(self):
        self.packet_pending = 1
        return self

def loop(n, fn, source, active):
    trace = []
    observed = False
    for index in range(n):
%s        observed = active()
        value = %s
        if index == 6999:
            trace = entries(loop)
    return value, trace, observed, index
''' % (layout_code, load, call), namespace)
    owner_type = namespace['Owner']
    return owner_type, getattr(owner_type, method), namespace['loop']


def assign(owner, flags):
    for name, value in zip(FIELDS, flags):
        setattr(owner, name, value)


def values(owner):
    return tuple(getattr(owner, name) for name in FIELDS)


def shape(trace, fn, layout='managed', eligible=True):
    if not jit_expected:
        return
    names = {row[0] for row in trace}
    if expect == 'baseline' or layout == 'slots' or not eligible:
        assert '_PUSH_FRAME' in names, names
        assert '_BEGIN_VIRTUAL_CALL' not in names, names
        return
    assert '_BEGIN_VIRTUAL_CALL' in names and '_RETURN_VIRTUAL_CALL' in names, names
    stores = [row for row in trace if row[0] == '_STORE_ATTR_INSTANCE_VALUE_BOOL']
    offsets = [ins.offset // 2 for ins in dis.get_instructions(fn) if ins.opname == 'STORE_ATTR']
    assert len(stores) == len(offsets) == 3, (stores, offsets)
    for store, offset in zip(stores, offsets):
        target = store[2]
        destination = trace[target:target + 3]
        materializers = [row for row in destination if row[0] == '_MATERIALIZE_VIRTUAL_FRAME']
        assert len(materializers) == 1 and materializers[0][3] == offset, (store, destination, offset)


for layout, method, bound, flags in itertools.product(
        ('managed', 'slots'), STATES, (False, True), itertools.product((False, True), repeat=3)):
    owner_type, fn, caller = make(layout, method, bound)
    owner = owner_type()
    assign(owner, flags)
    result, trace, active, progress = caller(7016, fn, [owner] * 7016, sys._jit.is_active)
    assert (active or not jit_expected) and progress == 7015
    assert result is owner and values(owner) == STATES[method]
    shape(trace, fn, layout)


def old_value_case(index, action, bound):
    owner_type, fn, caller = make(bound=bound)
    warm, fault = owner_type(), owner_type()
    assign(warm, (True, True, True))
    assign(fault, (True, True, True))
    events, replacements, unraisable = [], [], []
    stores = [ins for ins in dis.get_instructions(fn) if ins.opname == 'STORE_ATTR']

    class Old:
        def __del__(self):
            frame = sys._getframe(1)
            ins = next(item for item in dis.get_instructions(frame.f_code) if item.offset == frame.f_lasti)
            events.append((frame.f_code is fn.__code__, frame.f_locals == {'self': fault},
                           ins.offset, ins.opname, ins.argval, values(fault)))
            if action == 'mutate':
                fault.packet_pending = True
            elif action == 'rebind':
                replacement = owner_type()
                assign(replacement, (True, True, True))
                replacements.append(replacement)
                frame.f_locals['self'] = replacement
            elif action == 'raise':
                raise ValueError('old value')

    setattr(fault, FIELDS[index], Old())
    old = weakref.ref(getattr(fault, FIELDS[index]))
    source = [fault] + [warm] * 7015
    previous = sys.unraisablehook
    sys.unraisablehook = lambda error: unraisable.append((error.exc_type, str(error.exc_value), error.object is None, error.err_msg.startswith('Exception ignored while calling deallocator ') and 'Old.__del__' in error.err_msg))
    try:
        result, trace, active, progress = caller(7016, fn, source, sys._jit.is_active)
    finally:
        sys.unraisablehook = previous
    assert (active or not jit_expected) and progress == 7015 and not source
    shape(trace, fn)
    seen = (False,) * (index + 1) + (True,) * (2 - index)
    assert events == [(True, True, stores[index].offset, 'STORE_ATTR', FIELDS[index], seen)], events
    assert old() is None
    if action == 'rebind':
        assert result is replacements[0] and values(fault) == seen
        assert values(result) == (True,) * (index + 1) + (False,) * (2 - index)
    else:
        assert result is fault and values(fault) == ((True, False, False) if action == 'mutate' else STATES['running'])
    assert unraisable == ([(ValueError, 'old value', True, True)] if action == 'raise' else []), unraisable


for index, action, bound in itertools.product(range(3), ('observe', 'mutate', 'rebind', 'raise'), (False, True)):
    old_value_case(index, action, bound)


def storage_case(scenario, bound):
    owner_type, fn, caller = make(bound=bound)
    warm, fault = owner_type(), owner_type()
    assign(warm, (True, True, True))
    assign(fault, (True, True, True))
    keys = list(FIELDS)
    events = []
    if scenario.startswith('missing'):
        missing = FIELDS[int(scenario[-1])]
        delattr(fault, missing)
        keys.remove(missing)
        keys.append(missing)
    elif scenario == 'clear':
        fault.__dict__.clear()
    elif scenario == 'replace':
        fault.__dict__ = {'extra': 17, 'task_holding': True}
        keys = ['extra', 'task_holding', 'packet_pending', 'task_waiting']
    elif scenario in ('descriptor', 'descriptor-error'):
        class Other:
            @property
            def packet_pending(self):
                return self._pending
            @packet_pending.setter
            def packet_pending(self, value):
                frame = sys._getframe(1)
                ins = next(item for item in dis.get_instructions(frame.f_code) if item.offset == frame.f_lasti)
                events.append((frame.f_code is fn.__code__, frame.f_locals == {'self': self}, ins.opname, ins.argval, value))
                if scenario == 'descriptor-error':
                    raise ValueError('descriptor')
                self._pending = value
        setattr(Other, 'running', fn)
        fault = Other()
    elif scenario == 'wrong':
        fault = object()
    source = [fault] + [warm] * 7015
    try:
        result, trace, active, progress = caller(7016, fn, source, sys._jit.is_active)
    except (ValueError, AttributeError) as error:
        assert scenario in ('descriptor-error', 'wrong'), (scenario, error)
        assert type(error) is (ValueError if scenario == 'descriptor-error' else AttributeError)
        tb = error.__traceback__
        found = []
        while tb:
            frame = tb.tb_frame
            if frame.f_code is caller.__code__:
                trace, active, progress = (frame.f_locals[key] for key in ('trace', 'observed', 'index'))
            if frame.f_code is fn.__code__:
                ins = next(item for item in dis.get_instructions(fn) if item.offset == tb.tb_lasti)
                found.append((ins.opname, ins.argval, frame.f_locals == {'self': fault}))
            tb = tb.tb_next
        assert found == [('STORE_ATTR', 'packet_pending', True)], found
        assert error.__context__ is None
    else:
        assert scenario not in ('descriptor-error', 'wrong')
        assert result is fault and values(fault) == STATES['running']
        if scenario not in ('descriptor', 'descriptor-error'):
            assert list(fault.__dict__) == keys, (scenario, list(fault.__dict__), keys)
    assert (active or not jit_expected) and progress == 7015 and not source
    shape(trace, fn)
    assert events == ([(True, True, 'STORE_ATTR', 'packet_pending', False)] if scenario.startswith('descriptor') else []), events


for scenario, bound in itertools.product(('missing0', 'missing1', 'missing2', 'clear', 'replace', 'descriptor', 'descriptor-error'), (False, True)):
    storage_case(scenario, bound)
storage_case('wrong', False)


def ownership(bound):
    owner_type, fn, caller = make(bound=bound, direct=True)
    events = []
    class FinalOwner(owner_type):
        def __del__(self):
            events.append(values(self))
    owner, warm = FinalOwner(), FinalOwner()
    assign(owner, (True, True, True))
    assign(warm, (True, True, True))
    reference = weakref.ref(owner)
    source = [owner] + [warm] * 7015
    del owner
    result, trace, active, progress = caller(7016, fn, source, sys._jit.is_active)
    assert (active or not jit_expected) and progress == 7015 and not source
    assert result is reference() and not events and values(result) == STATES['running']
    shape(trace, fn)
    del result
    gc.collect()
    assert reference() is None and events == [STATES['running']], events


for bound in (False, True):
    ownership(bound)


for bound in (False, True):
    owner_type, fn, caller = make(method='integer', bound=bound)
    owner = owner_type()
    assign(owner, (False, False, False))
    result, trace, active, progress = caller(7016, fn, [owner] * 7016, sys._jit.is_active)
    assert (active or not jit_expected) and progress == 7015 and result is owner
    assert type(owner.packet_pending) is int and owner.packet_pending == 1
    shape(trace, fn, eligible=False)


def observers(mode, before):
    owner_type, fn, caller = make()
    owner = owner_type()
    assign(owner, (True, True, True))
    events = []
    def trace(frame, event, arg):
        if frame.f_code is fn.__code__ and event in ('call', 'return'):
            events.append((event, frame.f_locals == {'self': owner}, arg is owner if event == 'return' else None))
        return trace
    monitoring = sys.monitoring
    tool = 5
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
                events.append(('return', True, value is owner))
        def enable():
            monitoring.use_tool_id(tool, 'bool-state-updates')
            monitoring.register_callback(tool, monitoring.events.PY_START, started)
            monitoring.register_callback(tool, monitoring.events.PY_RETURN, returned)
            monitoring.set_events(tool, monitoring.events.PY_START | monitoring.events.PY_RETURN)
        def disable():
            monitoring.set_events(tool, 0)
            monitoring.register_callback(tool, monitoring.events.PY_START, None)
            monitoring.register_callback(tool, monitoring.events.PY_RETURN, None)
            monitoring.free_tool_id(tool)
    if not before:
        result, trace_entries, active, progress = caller(7016, fn, [owner] * 7016, sys._jit.is_active)
        assert (active or not jit_expected) and result is owner
        shape(trace_entries, fn)
    enable()
    try:
        result, trace_entries, active, progress = caller(4, fn, [owner] * 4, sys._jit.is_active)
    finally:
        disable()
    assert result is owner and values(owner) == STATES['running']
    assert events == [('call', True, None), ('return', True, True)] * 4, (mode, before, events)


for mode, before in itertools.product(('trace', 'profile', 'monitoring'), (False, True)):
    observers(mode, before)

print('OK Boolean state updates', expect, '64 managed and 64 slot traces, partial progress, finalizers, storage and observers')
