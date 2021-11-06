# CheckPuzzlesTb

A python script checking if [Lichess puzzles](https://database.lichess.org/#puzzles) with 7 or fewer pieces are correct according to the [syzygy tablebase api](https://github.com/niklasf/lila-tablebase).

## Usage

```
python3 -m venv venv && source venv/bin/activate
python3 t.py --help
usage: t.py [-h] {filter,check,export,clean}

positional arguments:
  {filter,check,export,clean}
                        filter: 
                            Select all puzzles from `DB_PATH` that have at point <= 7 pieces on the board, and save them in file `PUZZLE_PATH`
                            
                        
                        check: 
                            Look at every puzzle in `PUZZLE_PATH`, minus the ones with mate tag, and check them gainst syzygy tb.
                            Save the results in `PUZZLE_CHECKED_PATH`, with every line being `<puzzle_id> <optional[error_1]> <optional[error_2]>...
                            
                        
                        export: 
                            Look at every puzzle in `PUZZLE_CHECKED_PATH`, and save the list of ids of the ones which are incorrect in `incorrect_puzzles_id.txt`.
                            
                        
                        clean: 
                            Remove all puzzles that are in `PUZZLE_CHECKED_PATH`, but not in `PUZZLE_PATH` anymore, which means they've been deleted on Lichess. /!\ Run `filter` command before
                            
                        

optional arguments:
  -h, --help            show this help message and exit
```

## Installation

```
python3 -m venv venv && source venv/bin/activate
pip3 install -r requirements.txt
```