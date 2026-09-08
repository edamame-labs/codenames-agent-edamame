from abc import ABC, abstractmethod


class Guesser(ABC):
    """Interface-compatible subset of the official Guesser base class."""

    def __init__(self):
        self.move_history = []

    def set_move_history(self, move_history):
        self.move_history = move_history

    def get_move_history(self):
        return self.move_history

    @abstractmethod
    def set_board(self, words_on_board):
        pass

    @abstractmethod
    def set_clue(self, clue, num_guesses):
        pass

    @abstractmethod
    def keep_guessing(self):
        pass

    @abstractmethod
    def get_answer(self):
        pass
