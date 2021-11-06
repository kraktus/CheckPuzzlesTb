#!/usr/local/bin/python3
#coding: utf-8

"""
Confront lichess puzzles V2 to syzygy tablebase
"""

import unittest

from t import PuzzleChecker, Puzzle, Error

#########
#Classes#
#########

class TestChecker(unittest.TestCase):

    def setUp(self):
        self.checker = PuzzleChecker()

    def test_wrong_winning_puzzle(self):
        puzzle = Puzzle(fen="8/2R5/1P4p1/2K5/5pk1/5b2/8/8 w - - 0 50", # id (now deletd): 0XVra
            moves="c7c6 f3c6 c5c6 f4f3 b6b7 f3f2 b7b8q f2f1q".split(),
            expected_winning=True) 
        self.assertEqual(self.checker.check_puzzle(puzzle), set([Error.Wrong]))

    def test_multiple_right_moves_winning_puzzle(self):
        puzzle = Puzzle(fen="8/8/8/P6R/5K2/1r5p/5k2/8 w - - 3 57", # id (now deletd): 0BFLx
            moves="a5a6 b3b4 f4g5 b4b5 g5g6 b5h5 g6h5 h3h2".split(),
            expected_winning=True) 
        self.assertEqual(self.checker.check_puzzle(puzzle), set([Error.Multiple]))


    def test_wrong_multiple_drawing_puzzle(self):
        puzzle = Puzzle(fen="8/8/8/K1Pk1pp1/8/7P/1P6/8 w - - 0 43", # id (in fact winning): 05dOY
            moves="b2b4 g5g4 h3g4 f5g4 b4b5 g4g3 c5c6 d5d6".split(),
            expected_winning=False) 
        self.assertEqual(self.checker.check_puzzle(puzzle), set([Error.Wrong, Error.Multiple]))

######
#Main#
######

if __name__ == "__main__":
    print("#"*80)
    unittest.main()