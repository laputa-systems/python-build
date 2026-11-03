import _opcode
import contextlib
import dis
import gc
import importlib.util
import operator
import os
import shlex
import signal
import struct
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from pathlib import Path


REQUIRE_RAW = '--require-raw' in sys.argv
if REQUIRE_RAW:
    sys.argv.remove('--require-raw')


def sum_loop(n, a, b, c, d):
    result = 0.0
    for _ in range(n):
        result = a * b + c * d
    return result


def left_loop(n, a, b, c):
    result = 0.0
    for _ in range(n):
        result = (a * b) - c
    return result


def right_loop(n, a, b, c):
    result = 0.0
    for _ in range(n):
        result = a - (b * c)
    return result


def right_add_loop(n, a, b, c):
    result = 0.0
    for _ in range(n):
        result = a + (b * c)
    return result


def left_multiply_loop(n, a, b, c):
    result = 0.0
    for _ in range(n):
        result = (a + b) * c
    return result


def right_multiply_loop(n, a, b, c):
    result = 0.0
    for _ in range(n):
        result = a * (b + c)
    return result


def compare_loop(n, a, b, c, d, bound):
    result = False
    for _ in range(n):
        result = a * b + c * d < bound
    return result


def alias_loop(n, a, b):
    first = alias = second = result = None
    for _ in range(n):
        first = a * b
        alias = first
        second = a * b
        result = first - second
    return first, alias, second, result


def guard_loop(n, a, b, c, d):
    first = alias = second = result = None
    for _ in range(n):
        first = a * b
        alias = first
        second = c * d
        result = first + second
    return first, alias, second, result


def stack_guard_loop(n, a, b, c, d):
    result = 0.0
    for _ in range(n):
        result = (a * b) + (c * d)
    return result


def empty_loop(n, a, b, c, drain):
    result = 0.0
    for _ in range(n):
        drain()
        result = a * b + c
    return result


def adopted_loop(n, a, c, d, drain):
    value = alias = result = None
    for _ in range(n):
        value = alias = result = None
        drain()
        value = (a / d) + c
        alias = value
        result = value * d
    return value, alias, result


def full_loop(n, holder, a, b, c, d):
    second = alias = resumed = None
    for _ in range(n):
        root1 = holder.pop()
        root2 = holder.pop()
        second = None
        alias = None
        resumed = None
        first = a * b
        second = c * d
        alias = second
        root1 = None
        root2 = None
        first = None
        resumed = second + d
    return second, alias, resumed


def full_root_loop(n, holder, a, b, c, d):
    second = alias = resumed = None
    for _ in range(n):
        root1 = holder.pop()
        root2 = holder.pop()
        root3 = holder.pop()
        second = None
        alias = None
        resumed = None
        first = a * b
        second = c * d
        alias = second
        root1 = None
        root2 = None
        root3 = None
        first = None
        resumed = second + d
    return second, alias, resumed


def pending_loop(n, a, b, c, d):
    first = alias = second = result = None
    for _ in range(n):
        first = a * b
        alias = first
        second = c * d
        result = first + second
    return first, alias, second, result


def bits(value):
    return struct.pack('>d', value)


def executors(function):
    found = []
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            executor = _opcode.get_executor(function.__code__, offset)
        except ValueError:
            continue
        if executor.get_jit_code():
            found.append(executor)
    return found


def check_raw_chain(function, producer, consumer):
    matches = []
    for executor in executors(function):
        names = [item[0] for item in executor]
        for index, name in enumerate(names):
            if not name.startswith(producer) or (
                    producer.endswith('_INPLACE') and name.startswith(producer + '_RIGHT')):
                continue
            for later in range(index + 1, len(names)):
                if names[later].startswith('_JIT_FLOAT_MATERIALIZE'):
                    break
                if names[later].startswith(consumer) and not (
                        consumer.endswith('_INPLACE') and names[later].startswith(consumer + '_RIGHT')):
                    matches.append(executor)
                    break
    if not matches:
        raise AssertionError((function.__name__, producer, consumer,
                              [[item[0] for item in executor] for executor in executors(function)]))
    return matches


@contextlib.contextmanager
def native_probe():
    scratch = Path(__file__).resolve().parents[2] / '.work' / 'tmp'
    scratch.mkdir(parents=True, exist_ok=True)
    include = Path(sysconfig.get_path('include'))
    with tempfile.TemporaryDirectory(prefix='jit-raw-float-', dir=scratch) as temporary:
        library = Path(temporary) / ('_jit_raw_float_probe' + sysconfig.get_config_var('EXT_SUFFIX'))
        command = [*shlex.split(sysconfig.get_config_var('LDSHARED')),
                   *shlex.split(sysconfig.get_config_var('CONFIGURE_CPPFLAGS') or ''),
                   '-I', str(include), '-I', str(include / 'internal'),
                   '-I', str(include / 'internal' / 'mimalloc'),
                   str(Path(__file__).with_name('jit_raw_float_probe.c')), '-o', str(library)]
        subprocess.run(command, env=dict(os.environ, TMPDIR=str(scratch)), check=True)
        spec = importlib.util.spec_from_file_location('_jit_raw_float_probe', library)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module


class RawFloatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe_context = native_probe()
        cls.probe = cls.probe_context.__enter__()
        cls.addClassCleanup(cls.probe_context.__exit__, None, None, None)
        for function in (sum_loop, guard_loop, stack_guard_loop, pending_loop):
            function(20_000, 1.25, 2.0, 3.0, 4.0)
        left_loop(20_000, 1.25, 2.0, 3.0)
        right_loop(20_000, 1.25, 2.0, 3.0)
        right_add_loop(20_000, 1.25, 2.0, 3.0)
        left_multiply_loop(20_000, 1.25, 2.0, 3.0)
        right_multiply_loop(20_000, 1.25, 2.0, 3.0)
        compare_loop(20_000, 1.25, 2.0, 3.0, 4.0, 100.0)
        alias_loop(20_000, 1.25, 2.0)
        empty_loop(20_000, 1.25, 2.0, 3.0, cls.probe.drain)
        adopted_loop(20_000, 3.0, 0.25, 2.0, cls.probe.drain)
        full_loop(20_000, [float(i) for i in range(40_000)], 13.125, 7.25, 3.25, 4.5)
        full_root_loop(20_000, [float(i) for i in range(60_000)], 13.125, 7.25, 3.25, 4.5)
        if REQUIRE_RAW:
            if not sys._jit.is_enabled():
                raise AssertionError('--require-raw requires the JIT')
            for function in (sum_loop, guard_loop, stack_guard_loop, pending_loop):
                check_raw_chain(function, '_JIT_FLOAT_MULTIPLY', '_JIT_FLOAT_ADD')
            check_raw_chain(alias_loop, '_JIT_FLOAT_MULTIPLY', '_JIT_FLOAT_SUBTRACT')
            check_raw_chain(left_loop, '_JIT_FLOAT_MULTIPLY', '_JIT_FLOAT_SUBTRACT_INPLACE')
            check_raw_chain(right_loop, '_JIT_FLOAT_MULTIPLY', '_JIT_FLOAT_SUBTRACT_INPLACE_RIGHT')
            check_raw_chain(right_add_loop, '_JIT_FLOAT_MULTIPLY', '_JIT_FLOAT_ADD_INPLACE_RIGHT')
            check_raw_chain(left_multiply_loop, '_JIT_FLOAT_ADD', '_JIT_FLOAT_MULTIPLY_INPLACE')
            check_raw_chain(right_multiply_loop, '_JIT_FLOAT_ADD', '_JIT_FLOAT_MULTIPLY_INPLACE_RIGHT')
            check_raw_chain(compare_loop, '_JIT_FLOAT_ADD', '_JIT_FLOAT_COMPARE')
            check_raw_chain(empty_loop, '_JIT_FLOAT_MULTIPLY', '_JIT_FLOAT_ADD')
            check_raw_chain(adopted_loop, '_JIT_FLOAT_ADD_INPLACE', '_JIT_FLOAT_MULTIPLY')
            check_raw_chain(full_loop, '_JIT_FLOAT_MULTIPLY', '_JIT_FLOAT_MULTIPLY')
            check_raw_chain(full_root_loop, '_JIT_FLOAT_MULTIPLY', '_JIT_FLOAT_MULTIPLY')

    def test_binary64_rounding_and_inplace_operand_order(self):
        x = 1.0 + 2.0 ** -27
        y = 1.0 - 2.0 ** -27
        nan = struct.unpack('>d', bytes.fromhex('fff8000000001245'))[0]
        cases = [(-1.0, 1.0, x, y), (-0.0, 1.0, -0.0, 1.0),
                 (float('inf'), 1.0, float('-inf'), 1.0),
                 (nan, 2.0, 3.0, 4.0), (2.0 ** -1022, 0.5, 2.0 ** -1074, -1.0)]
        for a, b, c, d in cases:
            with self.subTest(values=(a, b, c, d)):
                product1 = operator.mul(a, b)
                product2 = operator.mul(c, d)
                self.assertEqual(bits(sum_loop(4, a, b, c, d)),
                                 bits(operator.add(product1, product2)))
                self.assertEqual(bits(left_loop(4, a, b, c)),
                                 bits(operator.sub(product1, c)))
                self.assertEqual(bits(right_loop(4, a, b, c)),
                                 bits(operator.sub(a, operator.mul(b, c))))
                self.assertEqual(bits(right_add_loop(4, a, b, c)),
                                 bits(operator.add(a, operator.mul(b, c))))
                self.assertEqual(bits(left_multiply_loop(4, a, b, c)),
                                 bits(operator.mul(operator.add(a, b), c)))
                self.assertEqual(bits(right_multiply_loop(4, a, b, c)),
                                 bits(operator.mul(a, operator.add(b, c))))
                for bound in (0.0, nan):
                    self.assertIs(compare_loop(4, a, b, c, d, bound),
                                  operator.lt(operator.add(product1, product2), bound))
        self.assertEqual(bits(sum_loop(4, -1.0, 1.0, x, y)), bits(0.0))
        self.assertEqual(bits(left_loop(4, x, y, 1.0)), bits(0.0))
        self.assertEqual(bits(right_loop(4, 1.0, x, y)), bits(0.0))
        self.assertEqual(bits(left_loop(4, -0.0, 1.0, 0.0)), bits(-0.0))
        self.assertEqual(bits(right_loop(4, -0.0, 1.0, 0.0)), bits(-0.0))

    def test_distinct_results_and_shared_alias_refcounts(self):
        a = float('1.25')
        b = float('2.0')
        before = sys.getrefcount(a), sys.getrefcount(b)
        first, alias, second, result = alias_loop(4, a, b)
        self.assertIs(first, alias)
        self.assertIsNot(first, second)
        self.assertEqual(tuple(map(bits, (first, alias, second, result))),
                         tuple(map(bits, (2.5, 2.5, 2.5, 0.0))))
        references = sys.getrefcount(first)
        del alias
        self.assertEqual(sys.getrefcount(first), references - 1)
        self.assertEqual((sys.getrefcount(a), sys.getrefcount(b)), before)

    def test_empty_cache_uses_original_allocation_and_source_position(self):
        arguments = (3, 1.25, 2.0, 3.0, self.probe.drain)
        self.assertEqual(self.probe.run_oom(empty_loop, arguments, False), 5.5)
        requests, failures, drains, _, _ = self.probe.counts()
        self.assertGreater(requests, 0)
        self.assertEqual((failures, drains), (0, 3))
        try:
            self.probe.run_oom(empty_loop, arguments, True)
        except MemoryError as error:
            traceback = error.__traceback__
        else:
            self.fail('float allocator failure did not raise MemoryError')
        while traceback is not None and traceback.tb_frame.f_code is not empty_loop.__code__:
            traceback = traceback.tb_next
        self.assertIsNotNone(traceback)
        instruction = next(item for item in dis.get_instructions(empty_loop)
                           if item.offset == traceback.tb_lasti)
        self.assertEqual((instruction.opname, instruction.argrepr), ('BINARY_OP', '*'))
        self.assertEqual(traceback.tb_frame.f_locals['result'], 5.5)
        self.assertEqual(self.probe.counts()[1:3], (1, 2))
        with self.assertRaisesRegex(AssertionError, 'armed float allocation hook was not called'):
            self.probe.run_oom(lambda: None, (), True)

    @unittest.skipUnless(sys._jit.is_enabled(), 'requires upstream in-place optimization')
    def test_adopted_box_and_alias_survive_empty_cache_exit(self):
        try:
            self.probe.run_oom(adopted_loop, (3, 3.0, 0.25, 2.0, self.probe.drain), True, 1)
        except MemoryError as error:
            traceback = error.__traceback__
        else:
            self.fail('adopted result did not reach the original failing allocation')
        while traceback is not None and traceback.tb_frame.f_code is not adopted_loop.__code__:
            traceback = traceback.tb_next
        self.assertIsNotNone(traceback)
        instruction = next(item for item in dis.get_instructions(adopted_loop)
                           if item.offset == traceback.tb_lasti)
        self.assertEqual((instruction.opname, instruction.argrepr), ('BINARY_OP', '*'))
        local = traceback.tb_frame.f_locals
        self.assertIs(local['value'], local['alias'])
        self.assertIs(type(local['value']), float)
        self.assertEqual(bits(local['value']), bits(1.75))
        self.assertIsNone(local['result'])
        self.assertEqual(self.probe.counts()[1:3], (1, 2))

    def test_guard_exit_materializes_locals_aliases_and_operand_stack(self):
        test = self
        seen = []
        replacement = 123.5

        class Guarded(float):
            def __rmul__(self, other):
                frame = sys._getframe(1)
                if frame.f_code is guard_loop.__code__:
                    local = frame.f_locals
                    test.assertIs(type(local['first']), float)
                    test.assertIs(local['first'], local['alias'])
                    test.assertEqual(bits(local['alias']), bits(2.5))
                    local['first'] = replacement
                    seen.append('locals')
                elif frame.f_code is stack_guard_loop.__code__:
                    seen.append('stack')
                return float.__rmul__(self, other)

        guards = []
        if REQUIRE_RAW:
            for executor in executors(guard_loop):
                for index, item in enumerate(executor):
                    if item[0].startswith('_JIT_FLOAT_GUARD_'):
                        guards.append((executor, index, self.probe.exit_state(executor, index)))
            self.assertTrue(guards)
        first, alias, second, result = guard_loop(2, 1.25, 2.0, 3.0, Guarded(4.0))
        self.assertIs(first, replacement)
        self.assertEqual(tuple(map(bits, (alias, second, result))),
                         tuple(map(bits, (2.5, 12.0, 135.5))))
        self.assertEqual(stack_guard_loop(2, 1.25, 2.0, 3.0, Guarded(4.0)), 14.5)
        self.assertEqual(seen, ['locals', 'locals', 'stack', 'stack'])
        if REQUIRE_RAW:
            self.assertTrue(any(self.probe.exit_state(executor, index) != before
                                for executor, index, before in guards))

    def test_full_cache_destroy_observer_sees_canonical_aliases_and_recursive_trace(self):
        replacement = 123.5
        expected = operator.mul(3.25, 4.5)
        product = operator.mul(13.125, 7.25)
        for function in (full_loop, full_root_loop):
            with self.subTest(function=function.__name__):
                observed = []
                if function is full_loop:
                    watched = product
                    holder = [float(i) + 0.125 for i in range(4)]
                else:
                    watched = 555.25
                    holder = [float(value) for value in
                              ('555.25', '1.125', '2.125', '555.25', '3.125', '4.125')]

                def observer():
                    frame = sys._getframe(1)
                    self.assertIs(frame.f_code, function.__code__)
                    local = frame.f_locals
                    self.assertEqual(self.probe.cache_size(), 100)
                    self.assertIs(type(local['second']), float)
                    self.assertIs(local['second'], local['alias'])
                    self.assertEqual(bits(local['alias']), bits(expected))
                    if function is full_loop:
                        self.assertIsNone(local['first'])
                    else:
                        self.assertIs(type(local['first']), float)
                        self.assertEqual(bits(local['first']), bits(product))
                    local['second'] = replacement
                    recursive = guard_loop(8, 1.25, 2.0, 3.0, 4.0)
                    self.assertIs(recursive[0], recursive[1])
                    self.assertEqual(tuple(map(bits, recursive)),
                                     tuple(map(bits, (2.5, 2.5, 12.0, 14.5))))
                    del recursive
                    recursive, created, destroyed = self.probe.run_traced(
                        guard_loop, (8, 1.25, 2.0, 3.0, 4.0))
                    self.assertIs(recursive[0], recursive[1])
                    self.assertEqual(recursive[3], 14.5)
                    self.assertEqual((created, destroyed), (24, 21))
                    observed.append(True)

                second, alias, resumed = self.probe.run_full(
                    function, (2, holder, 13.125, 7.25, 3.25, 4.5), holder, watched, observer)
                self.assertIs(second, replacement)
                self.assertEqual(bits(alias), bits(expected))
                self.assertEqual(bits(resumed), bits(128.0))
                self.assertEqual(holder, [])
                self.assertEqual(observed, [True])
                self.assertEqual(self.probe.counts()[3:5], (2, 1))

    def test_tracer_counts_original_allocations_after_warmup(self):
        result, created, destroyed = self.probe.run_traced(
            guard_loop, (8, 1.25, 2.0, 3.0, 4.0))
        self.assertIs(result[0], result[1])
        self.assertEqual(result[3], 14.5)
        self.assertEqual((created, destroyed), (24, 21))
        self.assertEqual(sum_loop(4, 1.25, 2.0, 3.0, 4.0), 14.5)

    @unittest.skipUnless(hasattr(signal, 'setitimer'), 'requires interval signals')
    def test_pending_exit_exposes_boxed_frame_and_preserves_aliases(self):
        class Observed(Exception):
            pass

        seen = []

        def interrupt(signum, frame):
            if frame.f_code is not pending_loop.__code__:
                return
            local = frame.f_locals
            if any(local[name] is None for name in ('first', 'alias', 'second', 'result')):
                return
            for name, expected in (('first', 2.5), ('alias', 2.5),
                                   ('second', 12.0), ('result', 14.5)):
                self.assertIs(type(local[name]), float)
                self.assertEqual(bits(local[name]), bits(expected))
            if local['first'] is local['alias']:
                seen.append(True)
                raise Observed

        previous = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        self.assertEqual(previous_timer, (0.0, 0.0))
        signal.signal(signal.SIGALRM, interrupt)
        try:
            signal.setitimer(signal.ITIMER_REAL, 0.001, 0.001)
            with self.assertRaises(Observed):
                pending_loop(2_000_000, 1.25, 2.0, 3.0, 4.0)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, previous)
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        self.assertEqual(seen, [True])
        gc.collect()


if __name__ == '__main__':
    unittest.main()
