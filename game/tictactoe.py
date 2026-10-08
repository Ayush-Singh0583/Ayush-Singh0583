#!/usr/bin/env python3
"""Tic-tac-toe for my GitHub profile README.

Visitors play X by clicking a square in the README. The click opens an issue
titled "ttt|move|<1-9>" (or "ttt|new" to start over). The workflow in
.github/workflows/tictactoe.yml then runs this script, which:

  1. loads the current game from game/state.json,
  2. plays the visitor's move,
  3. lets the bot answer with minimax, so it never loses,
  4. redraws the board between the TTT markers in README.md,
  5. writes the reply that the workflow posts on the issue.

Only the standard library is used, so the workflow needs no installs.

Redraw the board without playing a move:
    python3 game/tictactoe.py --render
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from functools import lru_cache
from html import escape
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "game" / "state.json"
README_FILE = ROOT / "README.md"
START_MARK = "<!-- TTT:START -->"
END_MARK = "<!-- TTT:END -->"

DEFAULT_REPO = "Ayush-Singh0583/Ayush-Singh0583"
DEFAULT_BRANCH = "main"

HUMAN, BOT, EMPTY = "X", "O", ""
LINES = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),  # rows
    (0, 3, 6), (1, 4, 7), (2, 5, 8),  # columns
    (0, 4, 8), (2, 4, 6),             # diagonals
)
TITLE_RE = re.compile(r"^ttt\|(?:move\|(?P<cell>[1-9])|(?P<new>new))$")
RECENT_LIMIT = 5
EMOJI = {HUMAN: "❌", BOT: "⭕", EMPTY: "⬜"}


# --------------------------------------------------------------------------
# Game rules and the minimax bot
# --------------------------------------------------------------------------

def winning_line(board):
    """Return the three cells of a completed line, or None."""
    for a, b, c in LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return (a, b, c)
    return None


def result_of(board):
    """'human_won', 'bot_won', 'draw', or None while the game goes on."""
    line = winning_line(board)
    if line:
        return "human_won" if board[line[0]] == HUMAN else "bot_won"
    if all(board):
        return "draw"
    return None


def _key(board):
    return "".join(cell or "." for cell in board)


@lru_cache(maxsize=None)
def _score(key, to_move):
    """Minimax value of a position, seen from the bot's side.

    Positive means the bot can force a win, negative means the human can,
    zero means a draw with best play. Wins that come sooner (more empty
    squares left) score higher, so the bot wins fast and loses slow.
    """
    board = [EMPTY if c == "." else c for c in key]
    empties = key.count(".")
    line = winning_line(board)
    if line:
        return (1 + empties) if board[line[0]] == BOT else -(1 + empties)
    if empties == 0:
        return 0
    nxt = HUMAN if to_move == BOT else BOT
    scores = [_score(key[:i] + to_move + key[i + 1:], nxt)
              for i, c in enumerate(key) if c == "."]
    return max(scores) if to_move == BOT else min(scores)


def best_bot_moves(board):
    """All moves that are equally best for the bot."""
    key = _key(board)
    best, moves = None, []
    for i, c in enumerate(key):
        if c != ".":
            continue
        score = _score(key[:i] + BOT + key[i + 1:], HUMAN)
        if best is None or score > best:
            best, moves = score, [i]
        elif score == best:
            moves.append(i)
    return moves


def bot_move(board, rng):
    # Picking randomly among equally good moves keeps games from repeating.
    return rng.choice(best_bot_moves(board))


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def fresh_game(stats=None, recent=None):
    return {
        "board": [EMPTY] * 9,
        "status": "playing",
        "winning_line": [],
        "last_move": None,
        "stats": stats or {"games": 0, "bot_wins": 0, "draws": 0, "human_wins": 0},
        "recent": recent or [],
    }


def load_state(path=STATE_FILE):
    if not path.exists():
        return fresh_game()
    state = json.loads(path.read_text(encoding="utf-8"))
    board = state.get("board")
    if not isinstance(board, list) or len(board) != 9 or any(c not in (HUMAN, BOT, EMPTY) for c in board):
        raise ValueError(f"{path} has an invalid board: {board!r}")
    base = fresh_game()
    base.update(state)
    return base


def save_state(state, path=STATE_FILE):
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def clean_user(login):
    """GitHub logins only use letters, digits and hyphens."""
    login = re.sub(r"[^A-Za-z0-9-]", "", login or "")[:39]
    return login or "someone"


# --------------------------------------------------------------------------
# Links and text
# --------------------------------------------------------------------------

ISSUE_BODY = (
    "Just submit this issue without changing the title.\n\n"
    "A GitHub Action will play your move, the bot will answer, and the board "
    "on the profile will update in about a minute."
)


def issue_link(repo, title):
    return (f"https://github.com/{repo}/issues/new"
            f"?title={quote(title, safe='')}&body={quote(ISSUE_BODY, safe='')}")


def profile_url(repo):
    return f"https://github.com/{repo.split('/')[0]}"


def board_as_emoji(board):
    rows = ("".join(EMOJI[c] for c in board[r * 3:r * 3 + 3]) for r in range(3))
    return "\n".join(rows)


# --------------------------------------------------------------------------
# Playing one issue
# --------------------------------------------------------------------------

def play(state, title, user, issue, repo, rng):
    """Apply one issue to the game.

    Returns (new_state, reply_markdown, changed). `changed` is False when the
    board did not change (bad title, taken square, finished game...).
    """
    board_link = f"[the board]({profile_url(repo)})"
    new_game = f"[Start a new game]({issue_link(repo, 'ttt|new')})"
    footer = "\n\n<sub>This issue closes itself. Thanks for playing!</sub>"

    match = TITLE_RE.match((title or "").strip())
    if not match:
        reply = ("I couldn't read that move. Please use the squares on "
                 f"{board_link} instead of typing a title yourself.")
        return state, reply + footer, False

    if match.group("new"):
        if state["status"] == "playing":
            if any(state["board"]):
                reply = ("A game is still going, so I didn't reset it. "
                         f"Jump in and make the next move on {board_link}!")
            else:
                reply = f"A fresh game is already waiting. Make the first move on {board_link}!"
            return state, reply + footer, False
        state = fresh_game(state["stats"], state["recent"])
        reply = f"New game started, @{user}! You're ❌. Make the first move on {board_link}."
        return state, reply + footer, True

    cell = int(match.group("cell")) - 1
    if state["status"] != "playing":
        reply = f"This game has already finished. {new_game}, then make your move."
        return state, reply + footer, False
    if state["board"][cell]:
        reply = (f"Square {cell + 1} is already taken. Someone may have played just before you. "
                 f"Have a look at {board_link} and pick another square.")
        return state, reply + footer, False

    board = list(state["board"])
    board[cell] = HUMAN
    result = result_of(board)
    reply_cell = None
    if result is None:
        reply_cell = bot_move(board, rng)
        board[reply_cell] = BOT
        result = result_of(board)

    state = dict(state)
    state["board"] = board
    state["last_move"] = {"user": user, "cell": cell, "bot": reply_cell, "issue": issue}
    state["recent"] = ([state["last_move"]] + list(state["recent"]))[:RECENT_LIMIT]

    lines = [f"You played ❌ on square {cell + 1}"
             + (f", and the bot answered with ⭕ on square {reply_cell + 1}." if reply_cell is not None else ".")]
    if result:
        stats = dict(state["stats"])
        stats["games"] += 1
        state["status"] = result
        state["winning_line"] = list(winning_line(board) or [])
        if result == "bot_won":
            stats["bot_wins"] += 1
            lines.append(f"⭕ **The bot completed a line and won this round.** Good game, @{user}! {new_game}.")
        elif result == "draw":
            stats["draws"] += 1
            lines.append(f"🤝 **It's a draw!** That's the best anyone can do against this bot. {new_game}.")
        else:
            stats["human_wins"] += 1
            lines.append(f"🎉 **You beat the bot, @{user}!** That shouldn't be possible. {new_game}.")
        state["stats"] = stats
    else:
        lines.append(f"Your move again, @{user}! Pick your next square on {board_link}.")
    lines.append(board_as_emoji(board))
    return state, "\n\n".join(lines) + footer, True


# --------------------------------------------------------------------------
# README rendering
# --------------------------------------------------------------------------

def _user_link(user):
    user = clean_user(user)
    return f'<a href="https://github.com/{user}">@{user}</a>'


def render(state, repo, branch):
    """HTML for the game block. Plain HTML with no blank lines inside, so
    GitHub's Markdown parser leaves it alone. The board sits in a centered
    div, which is the most reliable way to center a table on GitHub."""
    assets = f"https://raw.githubusercontent.com/{repo}/{branch}/assets/ttt"
    board = state["board"]
    playing = state["status"] == "playing"
    win = set(state.get("winning_line") or [])

    rows = []
    for r in range(3):
        cells = []
        for c in range(3):
            i = r * 3 + c
            piece = board[i]
            if piece:
                name = ("x" if piece == HUMAN else "o") + ("-win" if i in win else "")
                img = f'<img src="{assets}/{name}.svg" width="72" height="72" alt="{piece} on square {i + 1}">'
            elif playing:
                img = (f'<a href="{escape(issue_link(repo, f"ttt|move|{i + 1}"))}">'
                       f'<img src="{assets}/empty.svg" width="72" height="72" alt="Play square {i + 1}"></a>')
            else:
                img = f'<img src="{assets}/blank.svg" width="72" height="72" alt="Empty square {i + 1}">'
            cells.append(f"<td>{img}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

    new_game = f'<a href="{escape(issue_link(repo, "ttt|new"))}">Start a new game</a>'
    last = state.get("last_move")
    status = state["status"]
    if status == "playing" and not any(board):
        message = "<b>Your move!</b> You're ❌. Click any square to start a game against my bot."
    elif status == "playing":
        message = (f"<b>Your move!</b> You're ❌. {_user_link(last['user'])} played square {last['cell'] + 1}"
                   f" and the bot answered with square {last['bot'] + 1}.") if last else "<b>Your move!</b> You're ❌."
    elif status == "bot_won":
        message = f"⭕ <b>The bot won this round.</b> {new_game}"
    elif status == "draw":
        message = f"🤝 <b>It's a draw!</b> Nobody has beaten the bot yet. {new_game}"
    else:
        message = f"🎉 <b>A human beat the bot!</b> {new_game}"

    s = state["stats"]
    stats = (f"Games played: {s['games']} · Bot wins: {s['bot_wins']} · "
             f"Draws: {s['draws']} · Human wins: {s['human_wins']}")

    recent = []
    for m in state.get("recent", []):
        answer = f", bot answered {m['bot'] + 1}" if m.get("bot") is not None else ""
        issue = int(m.get("issue") or 0)
        ref = f' (<a href="https://github.com/{repo}/issues/{issue}">#{issue}</a>)' if issue else ""
        recent.append(f"<li>{_user_link(m['user'])} played square {m['cell'] + 1}{answer}{ref}</li>")

    parts = [
        '<div align="center">',
        f"<p>{message}</p>",
        '<table align="center">',
        *rows,
        "</table>",
        f"<p><sub>{stats}</sub></p>",
        "</div>",
    ]
    if recent:
        parts += ["<details>", "<summary>Recent moves</summary>", "<ul>", *recent, "</ul>", "</details>"]
    return "\n".join(parts)


def write_readme(block, path=README_FILE):
    text = path.read_text(encoding="utf-8")
    start = text.find(START_MARK)
    end = text.find(END_MARK)
    if start == -1 or end == -1 or end < start:
        raise SystemExit(f"Could not find {START_MARK} ... {END_MARK} in {path}")
    new = text[:start + len(START_MARK)] + "\n" + block + "\n" + text[end:]
    if new != text:
        path.write_text(new, encoding="utf-8")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--render", action="store_true", help="only redraw the board in README.md")
    args = parser.parse_args(argv)

    repo = os.environ.get("REPO") or DEFAULT_REPO
    branch = os.environ.get("BRANCH") or DEFAULT_BRANCH
    state = load_state()

    if args.render:
        save_state(state)
        write_readme(render(state, repo, branch))
        return 0

    title = os.environ.get("ISSUE_TITLE", "")
    user = clean_user(os.environ.get("ISSUE_USER", ""))
    number = os.environ.get("ISSUE_NUMBER", "")
    issue = int(number) if number.isdigit() else 0
    rng = random.Random(issue)

    state, reply, changed = play(state, title, user, issue, repo, rng)
    if changed:
        save_state(state)
        write_readme(render(state, repo, branch))

    reply_file = os.environ.get("REPLY_FILE")
    if reply_file:
        Path(reply_file).write_text(reply + "\n", encoding="utf-8")
    else:
        print(reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
