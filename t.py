#!/usr/local/bin/python3
#coding: utf-8

"""
Confront lichess puzzles V2 to syzygy tablebase
"""

from __future__ import annotations

import argparse
import chess
import csv
import json
import logging
import logging.handlers
import requests
import os
import time
import sys

from argparse import RawTextHelpFormatter
from chess import Board
from dataclasses import dataclass
from dotenv import load_dotenv
from enum import Enum
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

#############
# Constants #
#############

load_dotenv()

DB_PATH = os.getenv("DB_PATH") # lichess puzzle V2 db
LOG_PATH = "puz.log"
PUZZLE_PATH = "puzzle.csv"
PUZZLE_CHECKED_PATH = "puzzle_checked.txt"

TB_API = "http://tablebase.lichess.ovh/standard?fen={}"


RETRY_STRAT = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)
ADAPTER = HTTPAdapter(max_retries=RETRY_STRAT)


########
# Logs #
########

log = logging.getLogger("puz")
log.setLevel(logging.DEBUG)
format_string = "%(asctime)s | %(levelname)-8s | %(message)s"

# 125000000 bytes = 125Mb
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

    def __init__(self: FileHandler) -> None:
        # try to dynamically get the fieldnames from the first line of the csv if present, or fallback to old fieldnames for backward compatibility
        with open(DB_PATH, newline='') as puzzle_db: # type: ignore
            first_line = puzzle_db.readline()
        if "PuzzleId" in first_line:
            self.fieldnames = first_line.split(",")
            self.has_headers = True
        else:
            self.fieldnames = ['PuzzleId', 'FEN', 'Moves', 'Rating', 'RatingDeviation', 'Popularity', 'NbPlays', 'Themes', 'GameUrl']
            self.has_headers = False

    def add_puzzle(self: FileHandler, writer: csv.DictWriter, puzzle: List[str]) -> None:
        """Add a puzzle to the csv file"""
        # write row using fieldnames
        writer.writerow({self.fieldnames[i]: puzzle[i] for i in range(len(puzzle))})


    def extract_puzzle_inf_7piece(self: FileHandler) -> None:
        #Fields for the new db: PuzzleId,FEN,Moves,Rating,RatingDeviation,Popularity,NbPlays,Themes,GameUrl
        with open(DB_PATH, newline='') as csvfile: # type: ignore
            with open(PUZZLE_PATH, "w") as output:
                # pieces parameter is the number of piece of the first position with <=7 pieces on the board
                writer = csv.DictWriter(output, fieldnames=self.fieldnames)
                writer.writeheader()
                puzzles = csv.reader(csvfile, delimiter=',', quotechar='|')
                if self.has_headers:
                    next(puzzles) # skip headers
                dep = time.time()
                for line, puzzle in enumerate(puzzles):
                    if line % 1000 == 0:
                        print(f"\r{line} puzzles processed, {time.time() - dep:.2f}s",end="")
                    if self.has_puzzle_fewer_8p(puzzle):
                        self.add_puzzle(writer, puzzle)


    def has_puzzle_fewer_8p(self: FileHandler, puzzle: List[str]) -> bool:
        """Returns `True` if at a point the puzzle has 7 pieces or fewer."""
        b = Board(fen=puzzle[1])
        moves = puzzle[2].split()
        nb_pieces = nb_piece(b)

        if nb_pieces - len(moves) > 7: # Even if each move is a capture, there're still too many pieces
            return False
        elif nb_pieces <= 7:
            return True
        for move in moves:
            b.push_uci(move)
            nb_pieces = nb_piece(b)
            if nb_pieces <= 7:
                return True # should be always 7
        return False


    def remove_puzzles(self, l_puzzle_id: Set[str]) -> None:
        """Remove all `l_puzzle_id` puzzles from `PUZZLE_CHECKED_PATH`"""
        temp_name = "temporary_file.txt"
        with open(PUZZLE_CHECKED_PATH, 'r') as file_input, open(temp_name, 'w') as temp_file:
            for line in file_input:
                puzzle_id = line.split()[0]
                if not puzzle_id in l_puzzle_id:
                    temp_file.write(f"{line}")
        os.replace(temp_name, PUZZLE_CHECKED_PATH) # temp_name -> PUZZLE_CHECKED_PATH



# Not upper-case for backward compatibility with previous output
class Error(Enum):
    Wrong = "Wrong"
    Multiple = "Multiple"

@dataclass
class Puzzle:
    fen: str
    moves: List[str]
    expected_winning: bool
    mate: Optional[int] = None # in terms of number of plies, so twice the number in the theme list.

class PuzzleChecker:

    def __init__(self: PuzzleChecker) -> None:
        http = requests.Session()
        http.mount("https://", ADAPTER)
        http.mount("http://", ADAPTER)
        self.http = http
        self.dep = time.time()

    def tl(self: PuzzleChecker) -> float:
        """time elapsed"""
        return time.time() - self.dep

    def check(self: PuzzleChecker, mate = False) -> None:
        unchecked_puzzles = self.list_unchecked_puzzles(mate)
        log.info(f"{len(unchecked_puzzles)} still need to be checked")
        with open(PUZZLE_CHECKED_PATH, "a") as output:
            for i, (puzzle_id, puzzle_info) in enumerate(unchecked_puzzles):
                print(f"\r{i} puzzles processed, {self.tl():.2f}s",end="")
                res = self.check_puzzle(puzzle_info)
                if bool(res): # Not empty
                    log.error(f"puzzle {puzzle_id} contains some errors: {res}")
                output.write(puzzle_id + " " + " ".join([x.name for x in res]) + "\n")
                time.sleep(0.55) #rate-limited otherwise

    def check_puzzle(self: PuzzleChecker, puzzle: Puzzle) -> Set[Error]:
        b = Board(fen=puzzle.fen)
        res: Set[Error] = set()
        for i, move in enumerate(puzzle.moves):
            if i % 2 and nb_piece(b) <= 7: # 0, 2, 4... are moves made by the opponent, we don't check them
                res = res.union(self.req(b.fen(), move, puzzle, i))
            b.push_uci(move)
        return res

    def req(self: PuzzleChecker, fen: str, expected_move: str, puzzle: Puzzle, move_number: int) -> Set[Error]:
        """
        return a set of errors taking in count the goal of the puzzle
        log wrong puzzles
        """
        r = self.http.get(TB_API.format(fen))
        rep = r.json()
        log.debug(f"fen: {fen} rep: {str(rep)}")
        if puzzle.mate is not None:
            res = self.check_mate(fen, expected_move, rep, puzzle.mate - move_number)
        elif puzzle.expected_winning:
            res = self.check_winning(fen, expected_move, rep)
        else: # For equality puzzles
            res = self.check_drawing(fen, expected_move, rep)
        return res

    def check_winning(self: PuzzleChecker, fen: str, expected_move: str, rep: Dict[str, Any]) -> Set[Error]:
        res = set()
        if rep["category"] != "win":
            log.error(f"position {fen} can't be won by side to move, category: " + "{}".format(rep["category"]))
            res.add(Error.Wrong)
        for move in rep["moves"]:
            # move["category"] is from the opponent's point of vue
            if move["uci"] == expected_move and move["category"] != "loss":
                log.error(f"in position {fen}," + " {}({}) is not winning, opponent's category: {}".format(move["uci"], move["san"], move["category"]))
                res.add(Error.Wrong)
            elif move["category"] == "loss" and move["uci"] != expected_move: # a winning move which is not `expected_move`, puzzle is wrong 
                log.error(f"in position {fen}," + " {}({}) is also winning".format(move["uci"], move["san"]))
                res.add(Error.Multiple)
        return res

    def check_drawing(self: PuzzleChecker, fen: str, expected_move: str, rep: Dict[str, Any]) -> Set[Error]:
        res = set()
        if not is_draw(rep):
            log.error(f"position {fen} is not draw, category: " + "{}".format(rep["category"] ))
            res.add(Error.Wrong)
        for move in rep["moves"]:
            # move["category"] is from the opponent's point of vue
            if move["uci"] == expected_move and not is_draw(move):
                log.error(f"in position {fen}," + " {}({}) is not drawing, opponent's category: {}".format(move["uci"], move["san"], move["category"]))
                res.add(Error.Wrong)
            elif is_draw(move) and move["uci"] != expected_move: # a drawing move which is not `expected_move`, puzzle is wrong 
                log.error(f"in position {fen}," + " {}({}) is also drawing".format(move["uci"], move["san"]))
                res.add(Error.Multiple)
        return res

    def check_mate(self: PuzzleChecker, fen: str, expected_move: str, rep: Dict[str, Any], mate_in: int) -> Set[Error]:
        res = set()
        if rep["category"] != "win":
            log.error(f"position {fen} can't be won by side to move, category: " + "{}".format(rep["category"]))
            res.add(Error.Wrong)
        if rep["dtm"] is not None and rep["dtm"] != mate_in:
            log.error("position {} is not mate in {}, but {}.".format(fen, mate_in, rep["dtm"]))
            res.add(Error.Wrong)
        for move in rep["moves"]:
            # move["category"] is from the opponent's point of vue
            if move["checkmate"]: # Always good
                continue
            if move["uci"] == expected_move:
                if move["category"] != "loss":
                    log.error(f"in position {fen}," + " {}({}) is not winning, opponent's category: {}".format(move["uci"], move["san"], move["category"]))
                    res.add(Error.Wrong)
            elif move["dtm"] is not None:
                # Another move that result in a mate in the same number of moves
                if move["category"] == "loss" and -move["dtm"] == (mate_in - 1) : # DTM is negative since from the opponent point of view
                    log.error("position {} after {} is not mate in {}, but {}.".format(fen, move["uci"], mate_in - 1, -move["dtm"]))
                    res.add(Error.Multiple)
            # Not checking puzzles without DTM because it is not possible to reliably convert DTZ to DTM.
        return res

    # It is not possible to reliably convert DTZ to DTM.
    # def probe_DTZ_to_DTM(self: PuzzleChecker, fen: str, nb_plies: int) -> bool:
    #     """Used when DTM is not available. It probes DTZ for `nb_plies`, 
    #     and return `True` if what occurs.
    #     In the end probing DTZ >= DTM, so it will have false negatives (Not finding mate where it should), but no false positive.
    #     """
    #     res = False
    #     b = Board(fen=fen)
    #     print(b) # DEBUG
    #     while nb_plies > 0 and not res:
    #         r = self.http.get(TB_API.format(b.fen()))
    #         rep = r.json()
    #         log.debug(f"probing {nb_plies} moves, fen: {fen} rep: {str(rep)}")
    #         b.push_uci(rep["moves"][0]["uci"])
    #         print(b) # DEBUG
    #         res = rep["moves"][0]["checkmate"]
    #         nb_plies -= 1
    #     return res

    def list_unchecked_puzzles(self: PuzzleChecker, mate_puzzles = False) -> List[Tuple[str, Puzzle]]:
        if mate_puzzles:
            all_7p_puzzles = self.only_mate_puzzles()
        else:
            all_7p_puzzles = self.filtered_mate_puzzles()
        already_checked_puzzles = self.list_puzzles_checked()
        checked_but_not_listed_anymore = list(filter(lambda x: not x in all_7p_puzzles.keys(), already_checked_puzzles))
        if checked_but_not_listed_anymore and not mate_puzzles:
            log.error(f"{len(checked_but_not_listed_anymore)} puzzles were checked but are not in the list of all puzzles anymore: {checked_but_not_listed_anymore}")
        unchecked_puzzles = list(filter(lambda x: not x[0] in already_checked_puzzles, all_7p_puzzles.items()))
        return unchecked_puzzles

    def list_legacy_puzzle(self: PuzzleChecker) -> Set[str]:
        """Puzzles that are not longer in `DB_PATH`"""
        all_7p_puzzles_ids = self.filtered_mate_puzzles().keys()
        already_checked_puzzles = self.list_puzzles_checked()
        checked_but_not_listed_anymore = set(filter(lambda x: not x in all_7p_puzzles_ids, already_checked_puzzles))
        if checked_but_not_listed_anymore:
            log.error(f"{len(checked_but_not_listed_anymore)} puzzles were checked but are not in the list of all puzzles anymore: {checked_but_not_listed_anymore}")
        return checked_but_not_listed_anymore

    def list_puzzles_checked(self: PuzzleChecker) -> Set[str]:
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

    def list_incorrect_puzzles(self: PuzzleChecker) -> Set[str]:
        """
        return a set of checked and incorrect puzzles. At the same time check if one puzzle is not there twice
        """
        s = set()
        try:
            with open(PUZZLE_CHECKED_PATH, "r") as file_input:
                for line in file_input:
                    # puzzle_id error1 error2 error3...
                    args = line.split()
                    puzzle_id = args[0]
                    if puzzle_id in s:
                        log.error(f"{puzzle_id} checked more than once")
                    if len(args) > 1:
                        s.add(args[0])
        except FileNotFoundError:
            log.info(f"{PUZZLE_CHECKED_PATH} not found, 0 puzzle checked")
        return s

    def filtered_mate_puzzles(self: PuzzleChecker) -> Dict[str, Puzzle]:
        """
        returns a dic puzzle_id -> Puzzle
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

    def only_mate_puzzles(self: PuzzleChecker) -> Dict[str, Puzzle]:
        """
        returns a dic puzzle_id -> Puzzle
        only Mate puzzles are kept
        """
        #Fields for the filtered puzzles: PuzzleId,FEN,Moves,Rating,RatingDeviation,Popularity,NbPlays,Themes,GameUrl,pieces
        dic = {}
        with open(PUZZLE_PATH, newline='') as csvfile:
            puzzles = csv.DictReader(csvfile)
            for puzzle in puzzles:
                if not "mate" in puzzle["Themes"]:
                    continue
                mateIn = None
                for theme in  puzzle["Themes"].split():
                    if "mateIn" in theme:
                        assert mateIn is None # only one "mateIn" tag should be possible
                        mateIn = int(theme[6:])*2
                assert mateIn is not None # checkmate puzzles have all "mateIn" tags
                expected_winning = not "equality" in puzzle["Themes"]
                dic[puzzle["PuzzleId"]] = Puzzle(fen=puzzle["FEN"], moves=puzzle["Moves"].split(), expected_winning=expected_winning, mate=mateIn)

        return dic

#############
# Functions #
#############

def nb_piece(b: Board) -> int:
    return len(b.occupied)

def is_draw(api_rep: Dict[str, str]) -> bool:
    return api_rep["category"] in ["cursed-win", "draw", "blessed-loss"]

def filtering_7pieces() -> None:
    """
    Select all puzzles from `DB_PATH` that have at point <= 7 pieces on the board, and save them in file `PUZZLE_PATH`
    """
    log.info("Looking for puzzles with <= 7 pieces")
    file_handler = FileHandler()
    file_handler.extract_puzzle_inf_7piece()
    log.info("done")

def checking_puzzles() -> None:
    """
    Look at every puzzle in `PUZZLE_PATH`, minus the ones with mate tag, and check them gainst syzygy tb.
    Save the results in `PUZZLE_CHECKED_PATH`, with every line being `<puzzle_id> <optional[error_1]> <optional[error_2]>...
    """
    log.info("Checking puzzles with <= 7 pieces")
    checker = PuzzleChecker()
    checker.check()
    log.info("done")

def checking_mate_puzzles() -> None:
    """
    EXPERIMENTAL: Look at every puzzle in `PUZZLE_PATH` with a mate tag, and check them gainst syzygy tb.
    Save the results in `PUZZLE_CHECKED_PATH`, with every line being `<puzzle_id> <optional[error_1]> <optional[error_2]>...
    """
    log.info("Checking puzzles with <= 7 pieces")
    checker = PuzzleChecker()
    checker.check(mate=True)
    log.info("done")

def incorrect_puzzles() -> None:
    """
    Look at every puzzle in `PUZZLE_CHECKED_PATH`, and save the list of ids of the ones which are incorrect in `incorrect_puzzles_id.txt`.
    """
    log.info("Listing incorrect puzzles")
    checker = PuzzleChecker()
    puzzles = checker.list_incorrect_puzzles()
    with open("incorrect_puzzle_ids.txt", "w") as output:
        for p in puzzles:
            output.write(f"{p}\n")
    log.info("done")

def remove_puzzles_no_longer_db() -> None:
    """
    Remove all puzzles that are in `PUZZLE_CHECKED_PATH`, but not in `PUZZLE_PATH` anymore, which means they've been deleted on Lichess. /!\\ Run `filter` command before
    """
    log.info("Removing legacy puzzles")
    file_handler = FileHandler()
    checker = PuzzleChecker()
    puzzles = checker.list_legacy_puzzle()
    file_handler.remove_puzzles(puzzles)
    log.info("done")

def doc(dic: Dict[str, Callable[..., Any]]) -> str:
    """Produce documentation for every command based on doc of each function"""
    doc_string = ""
    for name_cmd, func in dic.items():
        doc_string += f"{name_cmd}: {func.__doc__}\n\n"
    return doc_string

def main() -> None:
    parser = argparse.ArgumentParser(formatter_class=RawTextHelpFormatter)
    commands = {
    "filter": filtering_7pieces,
    "check": checking_puzzles,
    "exp_check_mate": checking_mate_puzzles,
    "export": incorrect_puzzles,
    "clean": remove_puzzles_no_longer_db
    }
    parser.add_argument("command", choices=commands.keys(), help=doc(commands))
    args = parser.parse_args()
    commands[args.command]()


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


    # Some rarities where the player to move actually lose: https://lichess.org/training/DHvnF https://lichess.org/training/ERVwp https://lichess.org/training/U62fH https://lichess.org/training/jBUOV https://lichess.org/training/nBqNu https://lichess.org/training/snDwA
    

