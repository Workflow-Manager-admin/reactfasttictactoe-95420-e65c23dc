from fastapi import FastAPI, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import uuid4
import copy

# --- Models ---


class CreateGameRequest(BaseModel):
    """Request to start a new game"""
    player_x: Optional[str] = Field(None, description="Optional name/identifier for player X")
    player_o: Optional[str] = Field(None, description="Optional name/identifier for player O")


class MoveRequest(BaseModel):
    """A move in the game (API input)"""
    row: int = Field(..., ge=0, le=2, description="Row index [0-2]")
    col: int = Field(..., ge=0, le=2, description="Column index [0-2]")


class PlayerSymbol(str):
    pass  # just for semantic clarity ("X" or "O")


class GameState(str):
    pass  # going to be: "in_progress", "draw", or "won"


class TicTacToeGame(BaseModel):
    """In-memory state for a Tic Tac Toe game."""
    game_id: str
    board: List[List[Optional[PlayerSymbol]]] = Field(..., description="3x3 board with X/O/None")
    current_player: PlayerSymbol = Field(..., description='"X" or "O" indicating whose move')
    player_x: Optional[str] = Field(None, description="Name or id for player X")
    player_o: Optional[str] = Field(None, description="Name or id for player O")
    state: GameState = Field(..., description='"in_progress", "draw", or "won"')
    winner: Optional[PlayerSymbol] = Field(
        None, description='"X" or "O" if someone won; otherwise None'
    )
    move_history: List[dict] = Field(default_factory=list)


class GameSummary(BaseModel):
    """Summary object for the match history listing"""
    game_id: str
    winner: Optional[PlayerSymbol] = None
    state: GameState
    player_x: Optional[str]
    player_o: Optional[str]


class ApiErrorResponse(BaseModel):
    """Standard error format."""
    detail: str

# --- Game Store (In-Memory) ---


class GameMemoryStore:
    """
    In-memory storage for active and finished games.
    """
    def __init__(self):
        # key: game_id, value: TicTacToeGame
        self.active_games = {}
        self.finished_games = {}

    def create_game(self, player_x=None, player_o=None) -> TicTacToeGame:
        game_id = str(uuid4())
        board = [[None for _ in range(3)] for _ in range(3)]
        game = TicTacToeGame(
            game_id=game_id,
            board=board,
            current_player="X",
            player_x=player_x,
            player_o=player_o,
            state="in_progress",
            winner=None,
            move_history=[],
        )
        self.active_games[game_id] = game
        return game

    def get_game(self, game_id) -> TicTacToeGame:
        if game_id in self.active_games:
            return self.active_games[game_id]
        elif game_id in self.finished_games:
            return self.finished_games[game_id]
        else:
            return None

    def end_game(self, game: TicTacToeGame):
        self.finished_games[game.game_id] = copy.deepcopy(game)
        self.active_games.pop(game.game_id, None)

    def list_finished_games(self) -> List[GameSummary]:
        return [
            GameSummary(
                game_id=g.game_id,
                winner=g.winner,
                state=g.state,
                player_x=g.player_x,
                player_o=g.player_o,
            )
            for g in self.finished_games.values()
        ]


store = GameMemoryStore()


# --- Game Logic ---


def check_winner(board: List[List[Optional[str]]]) -> Optional[str]:
    """
    Returns "X" or "O" if there is a winner, or None otherwise.
    """
    lines = []
    # rows, columns
    lines.extend(board)
    lines.extend([[board[r][c] for r in range(3)] for c in range(3)])
    # diagonals
    lines.append([board[i][i] for i in range(3)])
    lines.append([board[i][2 - i] for i in range(3)])
    for line in lines:
        if line[0] and line.count(line[0]) == 3:
            return line[0]
    return None


def is_draw(board: List[List[Optional[str]]]) -> bool:
    """Returns True if board is full and there is no winner"""
    for row in board:
        if any(cell is None for cell in row):
            return False
    if check_winner(board):
        return False
    return True


# --- FastAPI App ---

app = FastAPI(
    title="Tic Tac Toe FastAPI Backend",
    description="RESTful API for a simple in-memory Tic Tac Toe game. Create games, make moves, and query results.",
    version="1.0.0",
    openapi_tags=[
        {"name": "game", "description": "Game play and state endpoints"},
        {"name": "history", "description": "Match history endpoints"},
        {"name": "health", "description": "Health check"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# PUBLIC_INTERFACE
@app.get("/", tags=["health"])
def health_check():
    """Returns API health status."""
    return {"message": "Healthy"}


# PUBLIC_INTERFACE
@app.post(
    "/game",
    response_model=TicTacToeGame,
    summary="Start a new game",
    description="Creates a new tic tac toe game and returns its state.",
    tags=["game"],
    responses={400: {"model": ApiErrorResponse}},
)
def create_game(req: CreateGameRequest):
    """Creates and returns a new game."""
    game = store.create_game(req.player_x, req.player_o)
    return game


# PUBLIC_INTERFACE
@app.post(
    "/game/{game_id}/move",
    response_model=TicTacToeGame,
    summary="Make a move",
    description="Submit a move for the specified game. Returns updated game state.",
    tags=["game"],
    responses={
        404: {"model": ApiErrorResponse},
        400: {"model": ApiErrorResponse},
    },
)
def make_move(
    game_id: str = Path(..., description="ID of the game"),
    move: MoveRequest = None,
):
    """
    Applies the move for the current player ("X" or "O") in the specified game.

    - Returns new board state and the next player, or winner/draw state if game ended.
    - Only allows moves if game is still in progress.
    - Handles invalid moves (out of bounds, taken cell,
      game over).
    """
    game = store.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found.")

    if game.state != "in_progress":
        raise HTTPException(
            status_code=400, detail="Game already finished. No further moves allowed."
        )
    r, c = move.row, move.col
    if not (0 <= r <= 2 and 0 <= c <= 2):
        raise HTTPException(status_code=400, detail="Row and col must be in 0..2 range.")
    if game.board[r][c] is not None:
        raise HTTPException(status_code=400, detail="Cell already taken.")
    # Apply move
    game.board[r][c] = game.current_player
    game.move_history.append({"player": game.current_player, "row": r, "col": c})
    winner = check_winner(game.board)
    if winner:
        game.state = "won"
        game.winner = winner
        store.end_game(game)
    elif is_draw(game.board):
        game.state = "draw"
        store.end_game(game)
    else:
        # Change turn
        game.current_player = "O" if game.current_player == "X" else "X"
    return game


# PUBLIC_INTERFACE
@app.get(
    "/game/{game_id}",
    response_model=TicTacToeGame,
    summary="Get game state",
    description="Get current state and board for a game.",
    tags=["game"],
    responses={
        404: {"model": ApiErrorResponse},
    },
)
def get_game(game_id: str = Path(..., description="Game id")):
    """
    Returns the full state of a given game (board, player turns, winner, etc.)
    """
    game = store.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found.")
    return game


# PUBLIC_INTERFACE
@app.get(
    "/games/history",
    response_model=List[GameSummary],
    summary="Get finished games history",
    description="Returns list of all completed games (draw or won).",
    tags=["history"],
)

def get_match_history():
    """
    Get summary listing of all finished games.
    """
    return store.list_finished_games()

# End of main.py
