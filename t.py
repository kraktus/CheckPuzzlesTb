#!/usr/local/bin/python3
#coding: utf-8

"""
Generate a "leaderboard" of the players according to the number of puzzles generated from their games
"""

from __future__ import annotations

import chess
import csv
import json
import logging
import logging.handlers
import requests
import os
import re
import time
import sys

from chess import Board
from copy import deepcopy
from dataclasses import dataclass
from dotenv import load_dotenv
from pathlib import Path
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from typing import Any, Dict, List, Iterator, Optional, Set

#############
# Constants #
#############

load_dotenv()

DB_PATH = os.getenv("DB_PATH")
LOG_PATH = "puz.log"
PUZZLE_PATH = "puzzle.csv"
PUZZLE_CHECKED_PATH = "puzzle_checked.txt"

TB_API = "http://tablebase.lichess.ovh/standard?fen={}"


RETRY_STRAT = Retry(
    total=5,
    backoff_factor=200,
    status_forcelist=[429, 500, 502, 503, 504],
    method_whitelist=["GET"]
)
ADAPTER = HTTPAdapter(max_retries=RETRY_STRAT)


########
# Logs #
########

# Are My Games In Lichess Puzzles
log = logging.getLogger("puz")
log.setLevel(logging.DEBUG)
format_string = "%(asctime)s | %(levelname)-8s | %(message)s"

# 125000000 bytes = 1Gb
handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=125000000, backupCount=3, encoding="utf8")
handler.setFormatter(logging.Formatter(format_string))
handler.setLevel(logging.DEBUG)
log.addHandler(handler)

handler_2 = logging.StreamHandler(sys.stdout)
handler_2.setFormatter(logging.Formatter(format_string))
handler_2.setLevel(logging.INFO)
log.addHandler(handler_2)

###########
# Classes #
###########

class FileHandler:

    game_to_puzzle_id: Option[Dict[str, str]] = None

    def list_games_already_dl(self) -> List[str]:
        l = []
        try:
            with open(GAMES_DL_PATH, "r") as file_input:
                for line in file_input:
                    l.append(line.split()[0])
        except FileNotFoundError:
            log.info(f"{GAMES_DL_PATH} not found, 0 games dl")
        return l

    def add_puzzle(self, writer, puzzle: List[str], pieces: int) -> None:
        writer.writerow({'PuzzleId': puzzle[0], 
                         'FEN': puzzle[1], 
                         'Moves': puzzle[2], 
                         'Rating': puzzle[3], 
                         'RatingDeviation': puzzle[4],
                         'Popularity': puzzle[5], 
                         'NbPlays': puzzle[6], 
                         'Themes': puzzle[7], 
                         'GameUrl': puzzle[8], 
                         'pieces': pieces})


    def puzzle_inf_7piece(self) -> None:
        #Fields for the new db: PuzzleId,FEN,Moves,Rating,RatingDeviation,Popularity,NbPlays,Themes,GameUrl
        with open(DB_PATH, newline='') as csvfile:
            with open(PUZZLE_PATH, "w") as output:
                # pieces paramater is the number of piece of the first position with <=7 pieces on the board
                fieldnames = ['PuzzleId', 'FEN', 'Moves', 'Rating', 'RatingDeviation', 'Popularity', 'NbPlays', 'Themes', 'GameUrl', 'pieces']
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                puzzles = csv.reader(csvfile, delimiter=',', quotechar='|')
                line = 0
                dep = time.time()
                for puzzle in puzzles:
                    print(f"\r{line} puzzles processed, {time.time() - dep:.2f}s",end="")
                    line += 1
                    b = Board(fen=puzzle[1])
                    moves = puzzle[2].split()
                    nb_pieces = nb_piece(b)

                    if nb_pieces - len(moves) > 7: # Even if each move is a capture, there's still too many pieces
                        continue
                    elif nb_pieces <= 7:
                        self.add_puzzle(writer, puzzle, nb_pieces)
                        continue
                    for move in moves:
                        b.push_uci(move)
                        nb_pieces = nb_piece(b)
                        if nb_pieces <= 7:
                            self.add_puzzle(writer, puzzle, nb_pieces) # should be alwaya 7
                            break

class Error:
    Wrong = "Wrong"
    Multiple = "Multiple"

@dataclass
class Puzzle:
    fen: str
    moves: List[str]
    expected_winning: bool

class PuzzleChecker:

    def __init__(self):
        http = requests.Session()
        http.mount("https://", ADAPTER)
        http.mount("http://", ADAPTER)
        self.http = http

    def check(self):
        all_7p_puzzles = self.filtered_mate_puzzles()
        already_checked_puzzles = self.list_puzzles_checked()
        unchecked_puzzles = list(filter(lambda x: not x[0] in already_checked_puzzles, all_7p_puzzles.items()))
        log.warning(unchecked_puzzles[:10])
        log.info(f"{len(unchecked_puzzles)} still need to be checked")
        dep = time.time()
        with open(PUZZLE_CHECKED_PATH, "a") as output:
            for i, (puzzle_id, puzzle_info) in enumerate(unchecked_puzzles):
                print(f"\r{i} puzzles processed, {time.time() - dep:.2f}s",end="")
                b = Board(fen=puzzle_info.fen)
                res = set()
                for i, move in enumerate(puzzle_info.moves): 
                    if i % 2 and nb_piece(b) <= 7: # 0, 2, 4... are moves made by the opponent, we don't check them
                        res = res.union(self.req(b.fen(), move, puzzle_info.expected_winning))
                    b.push_uci(move)
                if bool(res): # Not empty
                    log.error(f"puzzle {puzzle_id} contains some errors: {res}")
                output.write(puzzle_id + " " + " ".join(res) + "\n")
                time.sleep(0.55) #rate-limited otherwise


    def req(self, fen: str, expected_move: str, expected_winning: bool = True) -> Set[Error]:
        """
        return a set of errors taking in count the goal of the puzzle
        log wrongs puzzles
        """
        #log.warning(f"fen: {fen}, expected_move: {expected_move}, {expected_winning}: expected_winning")
        r = self.http.get(TB_API.format(fen))
        rep = r.json()
        log.debug(rep)
        if expected_winning:
            res = self.check_winning(fen, expected_move, rep)
        else: # For equality puzzles
            res = self.check_drawing(fen, expected_move, rep)
        return res

    def check_winning(self, fen: str, expected_move: str, rep: Dict[str, Any]) -> Set[Error]:
        res = set()
        if rep["wdl"] != 2:
            log.error(f"position {fen} can't be won by side to move, wdl: " + "{}".format(rep["wdl"]))
            res.add(Error.Wrong)
        for move in rep["moves"]:
            # move["wdl"] is from the opponent's point of vue
            if move["uci"] == expected_move and move["wdl"] != -2:
                log.error(f"in position {fen}," + " {}({}) is not winning, opponent's wdl: {}".format(move["uci"], move["san"], move["wdl"]))
                res.add(Error.Wrong)
            elif move["wdl"] == -2 and move["uci"] != expected_move: # a move winning which is not `expected_move`, puzzle is wrong 
                log.error(f"in position {fen}," + " {}({}) is also winning".format(move["uci"], move["san"]))
                res.add(Error.Multiple)
        return res

    def check_drawing(self, fen: str, expected_move: str, rep: Dict[str, Any]) -> Set[Error]:
        res = set()
        if rep["wdl"] != 0:
            log.error(f"position {fen} is not draw, wdl: " + "{}".format(rep["wdl"] ))
            res.add(Error.Wrong)
        for move in rep["moves"]:
            # move["wdl"] is from the opponent's point of vue
            if move["uci"] == expected_move and move["wdl"] != 0:
                log.error(f"in position {fen}," + " {}({}) is not drawing, opponent's wdl: {}".format(move["uci"], move["san"], move["wdl"]))
                res.add(Error.Wrong)
            elif move["wdl"] == 0 and move["uci"] != expected_move: # a move winning which is not `expected_move`, puzzle is wrong 
                log.error(f"in position {fen}," + " {}({}) is also drawing".format(move["uci"], move["san"]))
                res.add(Error.Multiple)
        return res

    def list_puzzles_checked(self) -> Set[str]:
        """
        return a set of checked puzzles, and at the same time check if one puzzle is not there twice
        """
        s = set()
        try:
            with open(PUZZLE_CHECKED_PATH, "r") as file_input:
                for line in file_input:
                    # puzzle_id error1 error2 error3...
                    puzzle_id = line.split()[0]
                    if puzzle_id in s:
                        log.error(f"{puzzle_id} checked more than once")
                    s.add(puzzle_id)
        except FileNotFoundError:
            log.info(f"{PUZZLE_CHECKED_PATH} not found, 0 puzzle checked")
        return s

    def filtered_mate_puzzles(self) -> Dict[str, Puzzle]:
        """
        returns a dic puzzle_id -> bool
        bool is True if the goal of the puzzle is to win, False if it's to draw.
        Mate puzzles are currently discarded because there can be possibly multiple solutions
        """
        #Fields for the filtered puzzles: PuzzleId,FEN,Moves,Rating,RatingDeviation,Popularity,NbPlays,Themes,GameUrl,pieces
        dic = {}
        with open(PUZZLE_PATH, newline='') as csvfile:
            puzzles = csv.DictReader(csvfile)
            for puzzle in puzzles:
                if "mate" in puzzle["Themes"]:
                    continue

                expected_winning = not "equality" in puzzle["Themes"]
                dic[puzzle["PuzzleId"]] = Puzzle(fen=puzzle["FEN"], moves=puzzle["Moves"].split(), expected_winning=expected_winning)

        return dic

#############
# Functions #
#############

def add_to_list_of_values(dic: "Dict[A, List[B]]", key: "A", val: "B") -> None:
    l_elem = dic.get(key)
    if l_elem is None:
        dic[key] = [val]
    else:
        l_elem.append(val)

def nb_piece(b: Board) -> int:
    return bin(b.occupied).count('1')

def filtering_7pieces():
    log.info("Looking for puzzles with <= 7 pieces")
    file_handler = FileHandler()
    file_handler.puzzle_inf_7piece()
    log.info("done")

def checking_puzzles():
    log.info("Checking puzzles with <= 7 pieces")
    checker = PuzzleChecker()
    checker.check()
    #log.debug(list(checker.filtered_mate_puzzles().items())[:10])
    log.info("done")


def main():
    checking_puzzles()
    


########
# Main #
########

if __name__ == "__main__":
    print('#'*80)
    main()

    # Maybe futur test
    #print(PuzzleChecker().req("6k1/6p1/6K1/4q2Q/7P/8/8/8 b - - 12 71", "e5f6"))
    #print(PuzzleChecker().req("8/2K5/4B3/3N4/8/8/4k3/8 b - - 0 1", "e5f6"))
    #print(PuzzleChecker().req("8/8/8/8/8/6K1/4Q3/7k b - - 0 1", "h1g1"))
    #print(PuzzleChecker().req("7Q/8/8/1pk5/8/1q2K3/8/8 w - - 0 1", "e3d2", False))
    

