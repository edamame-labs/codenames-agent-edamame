from abc import ABC, abstractmethod


class Codemaster(ABC):
    """Interface-compatible subset of the official Codemaster base class."""

    def __init__(self):
        self.move_history = []

    def set_move_history(self, move_history):
        self.move_history = move_history

    def get_move_history(self):
        return self.move_history

    @abstractmethod
    def set_game_state(self, words_on_board, key_grid):
        pass

    @abstractmethod
    def get_clue(self):
        pass
