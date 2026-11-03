import array
import operator
import struct
import unittest


class DoubleAssignmentTests(unittest.TestCase):
    def test_preserves_binary64_bits(self):
        for pattern in (0, 1 << 63, 1, 0x7FEFFFFFFFFFFFFF,
                        0x7FF0000000000000, 0xFFF0000000000000,
                        0x7FF8000000001234, 0xFFF8000000005678):
            with self.subTest(pattern=hex(pattern)):
                raw = struct.pack('=Q', pattern)
                value = struct.unpack('=d', raw)[0]
                values = array.array('d', [value])
                self.assertEqual(values.tobytes(), raw)
                for _ in range(20000):
                    values[0] = value
                self.assertEqual(values.tobytes(), raw)

    def test_retains_float_subclass_conversion(self):
        class Float(float):
            def __float__(self):
                raise AssertionError('float subclasses use their stored value')

        values = array.array('d', [0.0])
        values[0] = Float(1.25)
        self.assertEqual(values[0], 1.25)

    def test_conversion_can_resize_or_raise(self):
        values = array.array('d', [1.0])

        class ClearingFloat:
            def __float__(self):
                values.clear()
                return 2.0

        with self.assertRaises(IndexError):
            values[0] = ClearingFloat()
        self.assertEqual(values.tolist(), [])
        values.append(1.0)

        class RaisingFloat:
            def __float__(self):
                raise ValueError('conversion failed')

        with self.assertRaisesRegex(ValueError, 'conversion failed'):
            values[0] = RaisingFloat()
        self.assertEqual(values.tolist(), [1.0])

    def test_scalar_index_values_and_overflow(self):
        class Integer(int):
            def __index__(self):
                raise AssertionError('integer subclasses use their stored value')

        for index, position in ((0, 0), (-1, 2), (False, 0), (True, 1),
                                (Integer(0), 0), (Integer(-1), 2)):
            values = array.array('d', [1.0, 2.0, 3.0])
            expected = [1.0, 2.0, 3.0]
            self.assertEqual(values[index], expected[position])
            values[index] = 4.0
            expected[position] = 4.0
            self.assertEqual(values.tolist(), expected)
            del values[index]
            del expected[position]
            self.assertEqual(values.tolist(), expected)

        for index in (2**30, -(2**30), 2**100, -(2**100)):
            values = array.array('d', [1.0, 2.0])
            for operation in (operator.getitem, operator.delitem,
                              lambda values, index: operator.setitem(values, index, 3.0)):
                with self.assertRaises(IndexError):
                    operation(values, index)
                self.assertEqual(values.tolist(), [1.0, 2.0])

    def test_index_conversion_can_resize(self):
        for operation in (operator.getitem, operator.delitem,
                          lambda values, index: operator.setitem(values, index, 3.0)):
            values = array.array('d', [1.0, 2.0])
            calls = []

            class Index:
                def __index__(self):
                    calls.append('index')
                    values.clear()
                    return 0

            with self.assertRaises(IndexError):
                operation(values, Index())
            self.assertEqual(values.tolist(), [])
            self.assertEqual(calls, ['index'])


if __name__ == '__main__':
    unittest.main()
