import autopath
from pypy.module.builtin_app import range
from pypy.tool import test

class TestRange(test.TestCase):

   def setUp(self):
      pass

   def tearDown(self):
      pass

   def test_range_toofew(self):
      self.assertRaises(TypeError, range)

   def test_range_toomany(self):
      self.assertRaises(TypeError, range,  1, 2, 3, 4)

   def test_range_one(self):
      self.assertEqual(range(1), [0])

   def test_range_posstartisstop(self):
      self.assertEqual(range(1, 1), [])

   def test_range_negstartisstop(self):
      self.assertEqual(range(-1, -1), [])


   def test_range_zero(self):
      self.assertEqual(range(0), [])

   def test_range_twoargs(self):
      self.assertEqual(range(1, 2), [1])
      
   def test_range_decreasingtwoargs(self):
      self.assertEqual(range(3, 1), [])

   def test_range_negatives(self):
      self.assertEqual(range(-3), [])

   def test_range_decreasing_negativestep(self):
      self.assertEqual(range(5, -2, -1), [5, 4, 3, 2, 1, 0 , -1])

   def test_range_posfencepost1(self):
       self.assertEqual(range (1, 10, 3), [1, 4, 7])

   def test_range_posfencepost2(self):
       self.assertEqual(range (1, 11, 3), [1, 4, 7, 10])

   def test_range_posfencepost3(self):
       self.assertEqual(range (1, 12, 3), [1, 4, 7, 10])

   def test_range_negfencepost1(self):
       self.assertEqual(range (-1, -10, -3), [-1, -4, -7])

   def test_range_negfencepost2(self):
       self.assertEqual(range (-1, -11, -3), [-1, -4, -7, -10])

   def test_range_negfencepost3(self):
       self.assertEqual(range (-1, -12, -3), [-1, -4, -7, -10])

   def test_range_decreasing_negativelargestep(self):
      self.assertEqual(range(5, -2, -3), [5, 2, -1])

   def test_range_increasing_positivelargestep(self):
      self.assertEqual(range(-5, 2, 3), [-5, -2, 1])

   def test_range_zerostep(self):
      self.assertRaises(ValueError, range, 1, 5, 0)

"""
   def test_range_float(self):
      "How CPython does it - UGLY, ignored for now."
      self.assertEqual(range(0.1, 2.0, 1.1), [0, 1])
      """
      
if __name__ == '__main__':
    test.main()


