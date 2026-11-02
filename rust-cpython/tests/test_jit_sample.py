import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jit_sample


class SampleAttributionTests(unittest.TestCase):
    def test_attributes_samples_to_trace_and_uop_and_counts_runtime(self):
        mappings = '''T\t42\t0x1000\t0x1020\t7\t3\t12\tcompute\texample.py\t2
U\t42\t0x1000\t0x1008\t0\t_START_EXECUTOR_r00
U\t42\t0x1008\t0x1018\t1\t_BINARY_OP_ADD_INT_r00
Z\t42\t7
'''
        sample = '''Analysis of sampling python (pid 42) every 1 millisecond
Call graph:
    15 Thread_1
      10 ???  (in <unknown binary>)  [0x100c]
        2 ???  (in <unknown binary>)  [0x101c]
      5 PyLong_FromLong  (in libpython.dylib) + 4  [0x2010]

Sort by top of stack, same collapsed (when >= 5):

Binary Images:
'''
        result = jit_sample.attribute(sample, mappings)
        self.assertEqual(result.jit_samples, 10)
        self.assertEqual(result.named_samples, 8)
        self.assertEqual(result.unresolved_samples, 2)
        self.assertEqual(result.total_samples, 15)
        self.assertEqual(result.uops, {('compute', 12, 7, 3, '_BINARY_OP_ADD_INT_r00'): 8})
        self.assertEqual(result.runtime, {'PyLong_FromLong': 5})

    def test_reused_addresses_remain_unresolved(self):
        mappings = '''T\t42\t0x1000\t0x1010\t7\t1\t2\tfirst\ta.py\t1
U\t42\t0x1000\t0x1010\t0\t_FIRST
Z\t42\t7
T\t42\t0x1000\t0x1010\t8\t2\t4\tsecond\tb.py\t1
U\t42\t0x1000\t0x1010\t0\t_SECOND
Z\t42\t8
'''
        sample = '''Analysis of sampling python (pid 42) every 1 millisecond
Call graph:
    9 Thread_1
      9 ???  (in <unknown binary>)  [0x1004]

Sort by top of stack, same collapsed (when >= 5):

Binary Images:
'''
        result = jit_sample.attribute(sample, mappings)
        self.assertEqual(result.jit_samples, 9)
        self.assertEqual(result.named_samples, 0)
        self.assertEqual(result.unresolved_samples, 9)

    def test_rejects_uop_range_outside_its_trace(self):
        mappings = '''T\t42\t0x1000\t0x1010\t7\t1\t2\tf\ta.py\t1
U\t42\t0x1008\t0x1020\t0\t_OP
Z\t42\t7
'''
        with self.assertRaises(ValueError):
            jit_sample.parse_map(mappings, 42)


    def test_decodes_unicode_names_and_ignores_other_processes(self):
        mappings = ("T\t10\t0x1000\t0x1010\t1\t0\t4\tother\tother.py\t1\n"
                    "U\t10\t0x1000\t0x1010\t0\t_OTHER\nZ\t10\t1\n"
                    "T\t42\t0x1000\t0x1010\t7\t1\t2\tcaf\\xe9\\twork\ta.py\t1\n"
                    "U\t42\t0x1000\t0x1010\t0\t_OP\nZ\t42\t7\n")
        trace, = jit_sample.parse_map(mappings, 42)
        self.assertEqual(trace.name, 'café\twork')
        self.assertEqual(trace.uops[0].name, '_OP')

    def test_rejects_incomplete_trace(self):
        mappings = "T\t42\t0x1000\t0x1010\t7\t1\t2\tf\ta.py\t1\n"
        with self.assertRaises(ValueError):
            jit_sample.parse_map(mappings, 42)


if __name__ == '__main__':
    unittest.main()
