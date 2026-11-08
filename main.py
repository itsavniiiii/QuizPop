#!/usr/bin/env python3
"""
QuizPop! — Five questions. Endless possibilities. How much do you know?

A colorful desktop trivia game built with nothing but the Python standard
library (Tkinter + Canvas for the interface, json for the question bank).

Run the game:        python3 main.py
Run the self-tests:  python3 main.py --selftest

File layout
-----------
This file is split into three layers so that the quiz rules never depend on
the user interface:

1. Quiz logic (no Tkinter): loading/validating questions, random selection,
   answer shuffling, scoring, share text, and the local leaderboard.
2. Drawing helpers: colors, rounded shapes, stars, and the CanvasButton class.
3. QuizPopApp: owns the window and renders each screen onto one big Canvas.

Every screen is drawn from the current game state, so the window can be
resized (or redrawn) at any time without losing progress.
"""

import datetime
import json
import math
import os
import random
import sys
import urllib.parse
import webbrowser

import tkinter as tk
from tkinter import font as tkfont


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_DIR = os.path.dirname(os.path.abspath(__file__))
QUESTIONS_FILE = os.path.join(APP_DIR, "questions.json")
LEADERBOARD_FILE = os.path.join(APP_DIR, "leaderboard.json")

QUESTIONS_PER_GAME = 5
OPTIONS_PER_QUESTION = 4
LEADERBOARD_SIZE = 10
MAX_NICKNAME_LENGTH = 16

# Brand palette
LAVENDER = "#A78BFA"
PINK = "#FF85B3"
SKY = "#7DD3FC"
MINT = "#6EE7B7"
YELLOW = "#FDE68A"
NAVY = "#1E293B"
OFFWHITE = "#FAFAFF"
PEACH = "#FDBA74"   # extra pastel so each of the six categories has its own color
WHITE = "#FFFFFF"
SLATE = "#64748B"   # secondary text

# Feedback colors: green for correct, pink/red for incorrect
CORRECT_FILL = "#D1FAE5"
CORRECT_EDGE = "#10B981"
WRONG_FILL = "#FFE4EE"
WRONG_EDGE = "#F43F7E"
DIMMED_FILL = "#F1F5F9"

# Each category gets a color and a friendly badge label.
CATEGORY_STYLES = {
    "Sports": {"color": SKY, "label": "SPORTS"},
    "Government": {"color": LAVENDER, "label": "GOVERNMENT"},
    "Constitution": {"color": PINK, "label": "U.S. CONSTITUTION"},
    "World Cultures": {"color": PEACH, "label": "WORLD CULTURES"},
    "Geography": {"color": MINT, "label": "GEOGRAPHY"},
    "General Knowledge": {"color": YELLOW, "label": "GENERAL KNOWLEDGE"},
}
DEFAULT_CATEGORY_STYLE = {"color": LAVENDER, "label": "TRIVIA"}

# Colors for the A/B/C/D letter bubbles (a nod to Kahoot-style answer tiles)
OPTION_COLORS = [LAVENDER, PINK, SKY, YELLOW]
OPTION_LETTERS = "ABCD"

RESULT_MESSAGES = {
    0: "Every genius starts somewhere! 🌱",
    1: "You're just warming up! 🔥",
    2: "Not bad! Keep exploring! 🌎",
    3: "Nice work, trivia explorer! ⭐",
    4: "You're a trivia superstar! 🚀",
    5: "PERFECT SCORE! Certified genius! 🏆",
}


# ===========================================================================
# 1. QUIZ LOGIC (no Tkinter below this line until the drawing helpers)
# ===========================================================================

class QuestionBankError(Exception):
    """Raised when questions.json is missing or doesn't pass validation."""


class AlreadyAnsweredError(RuntimeError):
    """Raised if the game is asked to score the same question twice."""


# Field name -> expected Python type for every question in the JSON file.
REQUIRED_FIELDS = {
    "id": int,
    "category": str,
    "question": str,
    "options": list,
    "correct_answer": str,
    "explanation": str,
}


def load_question_bank(path=QUESTIONS_FILE):
    """Read questions.json from disk and return a validated list of questions."""
    if not os.path.exists(path):
        raise QuestionBankError(
            f"Couldn't find the question bank.\nExpected it here:\n{path}")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise QuestionBankError(
            f"questions.json isn't valid JSON (line {exc.lineno}, "
            f"column {exc.colno}): {exc.msg}") from exc
    except OSError as exc:
        raise QuestionBankError(f"Couldn't read questions.json: {exc}") from exc
    return validate_question_bank(data)


def validate_question_bank(data):
    """
    Check the structure of the parsed JSON and return the list of questions.

    Every problem is collected (not just the first) so a broken file can be
    fixed in one pass. The game refuses to start on any error, because a
    single bad question could otherwise award points for a wrong answer.
    """
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise QuestionBankError(
            'questions.json must contain an object with a "questions" list.')

    errors = []
    seen_ids = set()
    for position, q in enumerate(data["questions"], start=1):
        where = f"Question #{position}"
        if not isinstance(q, dict):
            errors.append(f"{where} is not a JSON object.")
            continue
        if isinstance(q.get("id"), int):
            where = f"Question id {q['id']}"

        missing = False
        for field, expected in REQUIRED_FIELDS.items():
            value = q.get(field)
            # bool is a subclass of int in Python, so reject it explicitly.
            if not isinstance(value, expected) or isinstance(value, bool):
                errors.append(f"{where}: '{field}' is missing or not a {expected.__name__}.")
                missing = True
            elif isinstance(value, str) and not value.strip():
                errors.append(f"{where}: '{field}' is empty.")
                missing = True
        if missing:
            continue

        if q["id"] in seen_ids:
            errors.append(f"{where}: duplicate id.")
        seen_ids.add(q["id"])

        options = q["options"]
        if len(options) != OPTIONS_PER_QUESTION:
            errors.append(f"{where}: needs exactly {OPTIONS_PER_QUESTION} options "
                          f"(found {len(options)}).")
        elif not all(isinstance(o, str) and o.strip() for o in options):
            errors.append(f"{where}: every option must be non-empty text.")
        elif len({o.strip().casefold() for o in options}) != len(options):
            errors.append(f"{where}: options must all be different.")
        elif options.count(q["correct_answer"]) != 1:
            errors.append(f"{where}: correct_answer must match exactly one option.")

    if errors:
        shown = "\n".join(f"• {e}" for e in errors[:6])
        more = f"\n…and {len(errors) - 6} more." if len(errors) > 6 else ""
        raise QuestionBankError(f"questions.json has problems:\n{shown}{more}")
    if len(data["questions"]) < QUESTIONS_PER_GAME:
        raise QuestionBankError(
            f"questions.json needs at least {QUESTIONS_PER_GAME} questions.")
    return [dict(q) for q in data["questions"]]


def select_questions(bank, count=QUESTIONS_PER_GAME, rng=random):
    """
    Pick `count` unique questions, spreading them across categories.

    Algorithm:
      1. Group the bank by category.
      2. random.sample() picks up to `count` *different* categories, and one
         random question is drawn from each — so a 5-question game with six
         categories always covers five different topics.
      3. If there are fewer categories than questions needed, the remaining
         slots are filled with random.sample() over the questions not yet
         chosen (sampling without replacement means no repeats).
      4. The final list is shuffled so category order is unpredictable too.

    Nothing is remembered between calls, so questions can return in later games.
    """
    if count > len(bank):
        raise ValueError(f"Need {count} questions but the bank only has {len(bank)}.")

    by_category = {}
    for q in bank:
        by_category.setdefault(q["category"], []).append(q)

    categories = list(by_category)
    chosen = [rng.choice(by_category[cat])
              for cat in rng.sample(categories, min(count, len(categories)))]

    if len(chosen) < count:
        chosen_ids = {q["id"] for q in chosen}
        leftovers = [q for q in bank if q["id"] not in chosen_ids]
        chosen.extend(rng.sample(leftovers, count - len(chosen)))

    rng.shuffle(chosen)
    return chosen


def shuffle_options(question, rng=random):
    """
    Return a copy of `question` with its options in random order.

    The correct answer is re-located by *value* after shuffling and stored as
    `correct_index`, so the answer key always follows the right text no
    matter where it lands. (Validation guarantees the value is unique.)
    """
    shuffled = rng.sample(question["options"], len(question["options"]))
    prepared = dict(question)
    prepared["options"] = shuffled
    prepared["correct_index"] = shuffled.index(question["correct_answer"])
    return prepared


def score_to_percentage(score, total=QUESTIONS_PER_GAME):
    """percentage = (score / total) * 100, rounded to a whole number."""
    return round(score / total * 100) if total else 0


def result_message(score):
    return RESULT_MESSAGES.get(score, RESULT_MESSAGES[0])


def donut_extents(score, total=QUESTIONS_PER_GAME):
    """Degrees of the donut chart for (correct, incorrect). They always sum to 360."""
    correct = 360.0 * score / total if total else 0.0
    return correct, 360.0 - correct


def build_share_message(score, total=QUESTIONS_PER_GAME):
    pct = score_to_percentage(score, total)
    return (f"🎉 I just scored {score}/{total} ({pct}%) on QuizPop! 🧠✨ "
            f"Think you can beat me? Try the trivia challenge!")


def whatsapp_share_url(message):
    """wa.me opens WhatsApp (app or web) with the message pre-typed."""
    return "https://wa.me/?text=" + urllib.parse.quote(message)


def email_share_url(message):
    """A mailto: link with a subject and body already filled in."""
    body = (message + "\n\n(QuizPop! is a desktop trivia game — "
            "you'll need your own copy to play.)")
    params = urllib.parse.urlencode(
        {"subject": "Can you beat my QuizPop! score? 🧠", "body": body},
        quote_via=urllib.parse.quote)
    return "mailto:?" + params


class QuizEngine:
    """
    Holds the state of one game: the five prepared questions, which question
    we're on, the score, and what the player picked for each question.
    It has no idea a GUI exists, which makes it easy to test.
    """

    def __init__(self, bank, rng=None):
        self.bank = bank
        self.rng = rng or random.Random()
        self.new_game()

    def new_game(self):
        """Reset everything and draw five fresh questions."""
        picked = select_questions(self.bank, QUESTIONS_PER_GAME, self.rng)
        self.questions = [shuffle_options(q, self.rng) for q in picked]
        self.current = 0
        self.score = 0
        self.answers = [None] * len(self.questions)

    @property
    def total(self):
        return len(self.questions)

    @property
    def current_question(self):
        return None if self.is_finished else self.questions[self.current]

    @property
    def current_answer(self):
        return None if self.is_finished else self.answers[self.current]

    @property
    def is_finished(self):
        return self.current >= self.total

    @property
    def is_last_question(self):
        return self.current == self.total - 1

    @property
    def answered_count(self):
        return sum(a is not None for a in self.answers)

    @property
    def incorrect_count(self):
        return self.answered_count - self.score

    @property
    def percentage(self):
        return score_to_percentage(self.score, self.total)

    def submit_answer(self, option_index):
        """
        Score the player's choice for the current question.
        Correct = +1 point, incorrect = 0. Each question can only be answered once.
        """
        if self.is_finished:
            raise AlreadyAnsweredError("The game is already over.")
        if self.answers[self.current] is not None:
            raise AlreadyAnsweredError("This question has already been answered.")
        q = self.questions[self.current]
        if not 0 <= option_index < len(q["options"]):
            raise IndexError(f"No option number {option_index}.")

        is_correct = option_index == q["correct_index"]
        if is_correct:
            self.score += 1
        self.answers[self.current] = {
            "selected_index": option_index,
            "selected": q["options"][option_index],
            "is_correct": is_correct,
        }
        return self.answers[self.current]

    def advance(self):
        """Move to the next question (only once the current one is answered)."""
        if self.is_finished:
            return False
        if self.answers[self.current] is None:
            raise RuntimeError("Answer the current question before moving on.")
        self.current += 1
        return not self.is_finished

    def summary(self):
        """One row per question for the results recap."""
        rows = []
        for q, a in zip(self.questions, self.answers):
            rows.append({
                "question": q["question"],
                "category": q["category"],
                "your_answer": a["selected"] if a else "—",
                "correct_answer": q["correct_answer"],
                "is_correct": bool(a and a["is_correct"]),
            })
        return rows


def clean_nickname(raw):
    """Trim spaces, collapse inner whitespace, and cap the length."""
    return " ".join(str(raw).split())[:MAX_NICKNAME_LENGTH]


class Leaderboard:
    """
    A tiny local leaderboard stored in leaderboard.json next to main.py.
    The file is created automatically the first time a score is saved. If it
    gets corrupted the game simply starts a fresh board instead of crashing.
    """

    def __init__(self, path=LEADERBOARD_FILE):
        self.path = path

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return []
        entries = data.get("scores", []) if isinstance(data, dict) else []
        valid = []
        for e in entries:
            if (isinstance(e, dict) and isinstance(e.get("nickname"), str)
                    and isinstance(e.get("score"), int)
                    and isinstance(e.get("percentage"), int)
                    and isinstance(e.get("date"), str)):
                valid.append(e)
        return valid

    @staticmethod
    def _rank_key(entry):
        # Highest score first; ties go to whoever got there first.
        return (-entry["score"], entry["date"])

    def top(self, n=LEADERBOARD_SIZE):
        return sorted(self.load(), key=self._rank_key)[:n]

    def add(self, nickname, score, total=QUESTIONS_PER_GAME, when=None):
        """Save a score and return (entry, rank). Rank is 1-based."""
        name = clean_nickname(nickname)
        if not name:
            raise ValueError("Please enter a nickname.")
        when = when or datetime.datetime.now()
        entry = {
            "nickname": name,
            "score": int(score),
            "percentage": score_to_percentage(score, total),
            "date": when.strftime("%Y-%m-%d %H:%M:%S"),
        }
        entries = sorted(self.load() + [entry], key=self._rank_key)[:100]
        # Write to a temp file then swap it in, so a crash can't leave half a file.
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"scores": entries}, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
        rank = entries.index(entry) + 1 if entry in entries else None
        return entry, rank


# ===========================================================================
# 2. DRAWING HELPERS
# ===========================================================================

def _hex_to_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def mix(c1, c2, t):
    """Blend two hex colors; t=0 gives c1, t=1 gives c2."""
    a, b = _hex_to_rgb(c1), _hex_to_rgb(c2)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def lighten(color, t):
    return mix(color, WHITE, t)


def darken(color, t):
    return mix(color, "#000000", t)


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    """
    Draw a rounded rectangle as a smoothed polygon.

    Each corner point is listed twice-ish (the straight edge stops r pixels
    before the corner, the corner itself, then the next edge starts r pixels
    after it). With smooth=True Tk fits a spline through those points, which
    bends the corners while keeping the edges straight.
    """
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, splinesteps=24, **kw)


def draw_star(canvas, cx, cy, r, fill, points=5, inner=0.45, **kw):
    """A star polygon: alternate between the outer and inner radius."""
    coords = []
    for i in range(points * 2):
        radius = r if i % 2 == 0 else r * inner
        angle = math.pi / points * i - math.pi / 2
        coords += [cx + radius * math.cos(angle), cy + radius * math.sin(angle)]
    return canvas.create_polygon(coords, fill=fill, outline="", **kw)


def draw_donut(canvas, cx, cy, radius, thickness, fraction, correct_color,
               incorrect_color, tags=()):
    """
    Draw the score ring. The pastel base ring is the "incorrect" portion and
    the colored arc on top is the "correct" portion, starting at 12 o'clock
    and running clockwise (negative extent in Tk).

    0% and 100% are special-cased: Tk can't draw a 360° arc, and a 0° arc
    still paints a sliver, so we draw a full ring (or nothing) instead.
    """
    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    canvas.create_oval(*box, outline=incorrect_color, width=thickness, tags=tags)
    if fraction >= 1:
        canvas.create_oval(*box, outline=correct_color, width=thickness,
                           tags=tags + ("donut_fg",))
    elif fraction > 0:
        canvas.create_arc(*box, start=90, extent=-360 * fraction, style="arc",
                          outline=correct_color, width=thickness,
                          tags=tags + ("donut_fg",))


class CanvasButton:
    """
    A rounded, pill-shaped button drawn on a Canvas.

    It's made of three items — a darker "shadow", the colored face, and the
    label — sharing one tag so the whole thing reacts as a unit. On hover the
    face lifts 2px (revealing more shadow) and brightens; on press it sinks.
    """

    _counter = 0

    def __init__(self, canvas, x, y, w, h, text, color, command, font,
                 text_color=NAVY, radius=None, tags=("content",)):
        CanvasButton._counter += 1
        self.tag = f"btn{CanvasButton._counter}"
        self.lift_tag = self.tag + "_lift"
        self.canvas = canvas
        self.command = command
        self.color = color
        self.hover_color = lighten(color, 0.3)
        self.offset = 0
        r = h / 2 if radius is None else radius
        base = (self.tag,) + tuple(tags)

        round_rect(canvas, x, y + 5, x + w, y + h + 5, r,
                   fill=darken(color, 0.2), outline="", tags=base)
        self.face = round_rect(canvas, x, y, x + w, y + h, r, fill=color,
                               outline="", tags=base + (self.lift_tag,))
        canvas.create_text(x + w / 2, y + h / 2, text=text, font=font,
                           fill=text_color, tags=base + (self.lift_tag,))

        canvas.tag_bind(self.tag, "<Enter>", self._on_enter)
        canvas.tag_bind(self.tag, "<Leave>", self._on_leave)
        canvas.tag_bind(self.tag, "<ButtonPress-1>", self._on_press)
        canvas.tag_bind(self.tag, "<ButtonRelease-1>", self._on_release)

    def _set_offset(self, offset):
        self.canvas.move(self.lift_tag, 0, offset - self.offset)
        self.offset = offset

    def _on_enter(self, _event):
        self.canvas.itemconfigure(self.face, fill=self.hover_color)
        self.canvas.configure(cursor="hand2")
        self._set_offset(-2)

    def _on_leave(self, _event):
        self.canvas.itemconfigure(self.face, fill=self.color)
        self.canvas.configure(cursor="")
        self._set_offset(0)

    def _on_press(self, _event):
        self._set_offset(3)

    def _on_release(self, _event):
        self._set_offset(-2)
        # Run after the event finishes: the command usually redraws the
        # screen, which deletes this button's items mid-callback otherwise.
        self.canvas.after_idle(self.command)


def pick_font_family(root):
    """Prefer a rounded, friendly font, falling back to safe system fonts."""
    available = set(tkfont.families(root))
    for family in ("Arial Rounded MT Bold", "Avenir Next", "Nunito",
                   "Segoe UI", "Helvetica Neue", "Helvetica", "Arial"):
        if family in available:
            return family
    return "TkDefaultFont"


def pick_body_family(root):
    available = set(tkfont.families(root))
    for family in ("Avenir Next", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial"):
        if family in available:
            return family
    return "TkDefaultFont"


# ===========================================================================
# 3. THE APP
# ===========================================================================

class QuizPopApp:
    """Owns the window, the game engine, and every screen's rendering."""

    MIN_W, MIN_H = 720, 560

    def __init__(self, root, questions_path=QUESTIONS_FILE,
                 leaderboard_path=LEADERBOARD_FILE, rng=None):
        self.root = root
        self.questions_path = questions_path
        self.leaderboard = Leaderboard(leaderboard_path)
        self.rng = rng or random.Random()

        self.engine = None
        self.load_error = None
        self.screen = "welcome"
        self.prev_screen = "welcome"

        # Per-game UI state for the results screen
        self.share_open = False
        self.share_status = ""
        self.saved_entry = None
        self.saved_rank = None
        self.save_error = ""
        self.chart_anim_pending = False
        self.game_seed = 0

        self._anim_token = 0      # bumps on every redraw to cancel old animations
        self._resize_job = None
        self._toast_job = None

        self._setup_window()
        self._load_questions()
        self.show_screen("error" if self.load_error else "welcome")

    # ----- setup ----------------------------------------------------------

    def _setup_window(self):
        root = self.root
        root.title("QuizPop!")
        root.configure(bg=OFFWHITE)
        root.minsize(self.MIN_W, self.MIN_H)

        # Fit comfortably on smaller laptop screens.
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(980, sw - 80), min(760, sh - 120)
        w, h = max(w, self.MIN_W), max(h, self.MIN_H)
        root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

        self.title_family = pick_font_family(root)
        self.body_family = pick_body_family(root)

        self.canvas = tk.Canvas(root, bg=OFFWHITE, highlightthickness=0,
                                yscrollincrement=20)
        self.canvas.pack(fill="both", expand=True)

        # The nickname box is a real Entry embedded in the canvas so it gets
        # proper typing, selection and paste behavior for free.
        self.nickname_var = tk.StringVar()
        self.nick_entry = tk.Entry(
            self.canvas, textvariable=self.nickname_var, relief="flat",
            bg=WHITE, fg=NAVY, insertbackground=NAVY, highlightthickness=2,
            highlightcolor=LAVENDER, highlightbackground=lighten(LAVENDER, 0.55),
            font=(self.body_family, 15), width=18)
        self.nick_entry.bind("<Return>", lambda e: self.save_score())

        self.canvas.bind("<Configure>", self._on_resize)
        root.bind("<Key>", self._on_key)
        # Mouse-wheel scrolling (Windows/macOS use <MouseWheel>, Linux uses buttons 4/5)
        root.bind_all("<MouseWheel>", self._on_wheel)
        root.bind_all("<Button-4>", lambda e: self._scroll(-3))
        root.bind_all("<Button-5>", lambda e: self._scroll(3))

        self.emoji_ok = self._probe_emoji()

    def _probe_emoji(self):
        """Older Tk builds can't draw characters outside the BMP (most emoji)."""
        try:
            item = self.canvas.create_text(-100, -100, text="🏆")
            self.canvas.delete(item)
            return True
        except tk.TclError:
            return False

    def _load_questions(self):
        try:
            bank = load_question_bank(self.questions_path)
        except QuestionBankError as exc:
            self.load_error = str(exc)
            self.engine = None
            print(f"[QuizPop!] {exc}", file=sys.stderr)
            return
        self.load_error = None
        self.engine = QuizEngine(bank, self.rng)

    # ----- small utilities --------------------------------------------------

    def t(self, text):
        """Strip emoji if this Tk can't render them, so nothing ever crashes."""
        if self.emoji_ok:
            return text
        return "".join(ch for ch in text if ord(ch) <= 0xFFFF).strip()

    def font(self, size, weight="normal", family=None):
        return (family or self.body_family, max(9, round(size * self.scale)), weight)

    def tfont(self, size, weight="bold"):
        return self.font(size, weight, self.title_family)

    def text_size(self, text, font, width=None):
        """Measure how big a (possibly wrapped) text block will be."""
        kw = {"width": width} if width else {}
        item = self.canvas.create_text(0, 0, text=text, font=font, anchor="nw", **kw)
        x1, y1, x2, y2 = self.canvas.bbox(item)
        self.canvas.delete(item)
        return x2 - x1, y2 - y1

    def button(self, x, y, text, color, command, size=16, h=None, w=None,
               text_color=NAVY, pad=34):
        """Create a CanvasButton sized to its label; returns (w, h)."""
        f = self.tfont(size)
        tw, th = self.text_size(text, f)
        bh = h or th + 26 * self.scale
        bw = w or tw + pad * 2 * self.scale
        CanvasButton(self.canvas, x, y, bw, bh, text, color, command, f,
                     text_color=text_color)
        return bw, bh

    def button_width(self, text, size=16, pad=34):
        return self.text_size(text, self.tfont(size))[0] + pad * 2 * self.scale

    def card(self, x1, y1, x2, y2, fill=WHITE, outline="", r=26, shadow=True, width=2):
        """A soft card with a faint drop shadow."""
        c = self.canvas
        if shadow:
            round_rect(c, x1, y1 + 6, x2, y2 + 6, r, fill="#ECEAF8", outline="",
                       tags=("content",))
        return round_rect(c, x1, y1, x2, y2, r, fill=fill, outline=outline,
                          width=width, tags=("content",))

    def pill(self, x, y, text, color, size=11, anchor="nw", text_color=NAVY):
        """A small rounded label; returns its (w, h)."""
        f = self.font(size, "bold")
        tw, th = self.text_size(text, f)
        w, h = tw + 28 * self.scale, th + 12 * self.scale
        if anchor == "n":
            x -= w / 2
        elif anchor == "ne":
            x -= w
        round_rect(self.canvas, x, y, x + w, y + h, h / 2, fill=color, outline="",
                   tags=("content",))
        self.canvas.create_text(x + w / 2, y + h / 2, text=text, font=f,
                                fill=text_color, tags=("content",))
        return w, h

    def text(self, x, y, text, font, fill=NAVY, anchor="nw", width=None,
             justify="left", tags=("content",)):
        kw = {"width": width} if width else {}
        return self.canvas.create_text(x, y, text=self.t(text), font=font,
                                       fill=fill, anchor=anchor, justify=justify,
                                       tags=tags, **kw)

    def column(self, max_w=820):
        """Centered content column (x0, width) for the current window size."""
        w = min(self.W - 48, max_w)
        return (self.W - w) / 2, w

    def make_clickable(self, tag, on_click, on_enter=None, on_leave=None):
        c = self.canvas

        def enter(_e):
            c.configure(cursor="hand2")
            if on_enter:
                on_enter()

        def leave(_e):
            c.configure(cursor="")
            if on_leave:
                on_leave()

        c.tag_bind(tag, "<Enter>", enter)
        c.tag_bind(tag, "<Leave>", leave)
        c.tag_bind(tag, "<ButtonRelease-1>", lambda e: c.after_idle(on_click))

    # ----- rendering pipeline ------------------------------------------------

    def show_screen(self, name, animate=True):
        if name != self.screen:
            self.prev_screen = self.screen
        self.screen = name
        self.render(animate=animate)
        self.canvas.yview_moveto(0)

    def render(self, animate=False):
        """Wipe the canvas and draw the current screen from game state."""
        c = self.canvas
        self._anim_token += 1
        c.delete("all")
        c.configure(cursor="")
        self.nick_entry.place_forget()

        self.W = max(c.winfo_width(), self.MIN_W) if c.winfo_width() > 1 else 980
        self.H = max(c.winfo_height(), 200) if c.winfo_height() > 1 else 760
        # Scale fonts gently with window size so small laptops still fit.
        self.scale = max(0.82, min(1.12, min(self.W / 960, self.H / 740)))

        renderer = {
            "welcome": self.render_welcome,
            "quiz": self.render_quiz,
            "results": self.render_results,
            "leaderboard": self.render_leaderboard,
            "error": self.render_error,
        }[self.screen]
        content_h = renderer()

        total_h = max(self.H, content_h)
        self.draw_background(self.W, total_h)
        c.configure(scrollregion=(0, 0, self.W, total_h))
        self.scrollable = total_h > self.H + 1

        if animate:
            self.slide_in()

    def center_vertically(self):
        """For short screens: nudge all content to the middle of the window."""
        bbox = self.canvas.bbox("content")
        if not bbox:
            return 0
        h = bbox[3] - bbox[1]
        if h < self.H:
            self.canvas.move("content", 0, (self.H - h) / 2 - bbox[1])
            return self.H
        return bbox[3] + 30

    def slide_in(self):
        """
        Screen transition: content starts 28px low and eases up into place.
        Tk has no opacity for canvas items, so motion does the "smooth" work.
        """
        token = self._anim_token
        c = self.canvas
        steps, offset = 12, 28
        c.move("content", 0, offset)
        state = {"pos": offset}

        def step(i):
            if token != self._anim_token:
                return
            t = i / steps
            target = offset * (1 - t) ** 3      # ease-out cubic
            c.move("content", 0, target - state["pos"])
            state["pos"] = target
            if i < steps:
                c.after(16, step, i + 1)

        c.after(16, step, 1)

    def draw_background(self, w, h):
        """Off-white page with soft pastel bubbles and sparkles in the corners."""
        c = self.canvas
        c.create_rectangle(0, 0, w, h, fill=OFFWHITE, outline="", tags=("bg",))
        blobs = [
            (-90, -110, 230, LAVENDER), (w - 150, -130, 260, PINK),
            (-120, self.H - 140, 240, SKY), (w - 170, self.H - 120, 250, MINT),
        ]
        for x, y, size, color in blobs:
            c.create_oval(x, y, x + size, y + size, fill=lighten(color, 0.82),
                          outline="", tags=("bg",))
        deco = random.Random(7)   # fixed seed: decorations don't jump on resize
        for _ in range(16):
            x, y = deco.uniform(10, w - 10), deco.uniform(10, h - 10)
            color = lighten(deco.choice([LAVENDER, PINK, SKY, MINT, YELLOW]), 0.45)
            if deco.random() < 0.5:
                r = deco.uniform(3, 6)
                c.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="",
                              tags=("bg",))
            else:
                draw_star(c, x, y, deco.uniform(6, 10), color, tags=("bg",))
        c.tag_lower("bg")

    def show_toast(self, message):
        """A little pill that pops up near the bottom of the window for 2s."""
        c = self.canvas
        c.delete("toast")
        f = self.tfont(14)
        tw, th = self.text_size(self.t(message), f)
        w, h = tw + 48, th + 24
        top = c.canvasy(0)
        x1, y1 = (self.W - w) / 2, top + self.H - h - 28
        round_rect(c, x1, y1 + 4, x1 + w, y1 + h + 4, h / 2, fill=darken(NAVY, 0.3),
                   outline="", tags=("toast",))
        round_rect(c, x1, y1, x1 + w, y1 + h, h / 2, fill=NAVY, outline="",
                   tags=("toast",))
        c.create_text(x1 + w / 2, y1 + h / 2, text=self.t(message), font=f,
                      fill=WHITE, tags=("toast",))
        if self._toast_job:
            c.after_cancel(self._toast_job)
        self._toast_job = c.after(2200, lambda: c.delete("toast"))

    # ----- event handlers ----------------------------------------------------

    def _on_resize(self, event):
        # Debounce: wait until the user stops dragging, then redraw once.
        if self._resize_job:
            self.canvas.after_cancel(self._resize_job)
        self._resize_job = self.canvas.after(40, self._redraw_after_resize)

    def _redraw_after_resize(self):
        self._resize_job = None
        frac = self.canvas.yview()[0]
        self.render(animate=False)
        self.canvas.yview_moveto(frac)

    def _scroll(self, units):
        if getattr(self, "scrollable", False):
            self.canvas.yview_scroll(units, "units")

    def _on_wheel(self, event):
        if sys.platform == "darwin":
            self._scroll(-event.delta)
        else:
            self._scroll(-int(event.delta / 120) * 3)

    def _on_key(self, event):
        """Keyboard shortcuts: 1-4 / A-D to answer, Enter or Space to continue."""
        if event.widget is self.nick_entry:
            return
        key = event.keysym.lower()
        if self.screen == "welcome" and key in ("return", "space"):
            self.start_game()
        elif self.screen == "quiz" and self.engine and not self.engine.is_finished:
            if self.engine.current_answer is None:
                if key in "1234" and len(key) == 1:
                    self.select_answer(int(key) - 1)
                elif key in "abcd" and len(key) == 1:
                    self.select_answer("abcd".index(key))
            elif key in ("return", "space", "right"):
                self.next_question()
        elif self.screen == "leaderboard" and key == "escape":
            self.close_leaderboard()

    # ----- game actions --------------------------------------------------------

    def start_game(self):
        """Fresh game: reset score/answers, pick five new questions, go to Q1."""
        if not self.engine:
            return
        self.engine.new_game()
        self.share_open = False
        self.share_status = ""
        self.saved_entry = None
        self.saved_rank = None
        self.save_error = ""
        self.game_seed = self.rng.randrange(1 << 30)
        self.show_screen("quiz")

    play_again = start_game

    def select_answer(self, index):
        """Lock in the player's choice; ignored if the question was answered."""
        if self.screen != "quiz" or self.engine.current_answer is not None:
            return
        self.engine.submit_answer(index)
        self.render(animate=False)
        # Make sure the explanation and Next button are visible.
        if self.scrollable:
            self.canvas.yview_moveto(1.0)

    def next_question(self):
        if self.engine.current_answer is None:
            return
        if self.engine.advance():
            self.show_screen("quiz")
        else:
            self.chart_anim_pending = True
            self.show_screen("results")

    def go_home(self):
        self.show_screen("welcome")

    def open_leaderboard(self):
        self.show_screen("leaderboard")

    def close_leaderboard(self):
        back = self.prev_screen if self.prev_screen in ("welcome", "results") else "welcome"
        self.show_screen(back)

    def toggle_share(self):
        self.share_open = not self.share_open
        self.share_status = ""
        frac = self.canvas.yview()[0]
        self.render(animate=False)
        self.canvas.yview_moveto(frac)

    def share_message(self):
        return build_share_message(self.engine.score, self.engine.total)

    def copy_results(self):
        """Put the score message on the system clipboard."""
        msg = self.share_message()
        self.root.clipboard_clear()
        self.root.clipboard_append(msg)
        self.root.update()   # keeps the clipboard filled after focus changes
        self._set_share_status("Copied! Send it to your friends!")

    def share_whatsapp(self):
        self._open_link(whatsapp_share_url(self.share_message()), "Opening WhatsApp…")

    def share_email(self):
        self._open_link(email_share_url(self.share_message()), "Opening your email app…")

    def _open_link(self, url, ok_message):
        try:
            opened = webbrowser.open(url)
        except Exception:   # webbrowser can raise on odd OS setups
            opened = False
        self._set_share_status(ok_message if opened else
                               "Couldn't open that app — try Copy Results instead.")

    def _set_share_status(self, message):
        self.share_status = message
        frac = self.canvas.yview()[0]
        self.render(animate=False)
        self.canvas.yview_moveto(frac)
        self.show_toast(message)

    def save_score(self):
        if self.screen != "results" or self.saved_entry:
            return
        try:
            self.saved_entry, self.saved_rank = self.leaderboard.add(
                self.nickname_var.get(), self.engine.score, self.engine.total)
            self.save_error = ""
        except ValueError as exc:
            self.save_error = str(exc)
        except OSError as exc:
            self.save_error = f"Couldn't save the leaderboard: {exc}"
        frac = self.canvas.yview()[0]
        self.render(animate=False)
        self.canvas.yview_moveto(frac)
        if self.saved_entry:
            self.show_toast("Score saved to the leaderboard! 🏆")

    def retry_load(self):
        self._load_questions()
        self.show_screen("error" if self.load_error else "welcome")

    # ===================================================================
    # Screens. Each render_* draws items tagged "content" and returns the
    # total height it needs (so the canvas knows how far it can scroll).
    # ===================================================================

    def draw_title(self, cx, y, size=58):
        """'QuizPop!' with each letter in a different color over a navy shadow."""
        f = self.tfont(size)
        measure = tkfont.Font(root=self.root, font=f)
        word = "QuizPop!"
        colors = [LAVENDER, PINK, SKY, MINT, PINK, LAVENDER, SKY, YELLOW]
        widths = [measure.measure(ch) for ch in word]
        x = cx - sum(widths) / 2
        for ch, cw, color in zip(word, widths, colors):
            self.canvas.create_text(x + 3, y + 4, text=ch, font=f, fill=NAVY,
                                    anchor="nw", tags=("content",))
            self.canvas.create_text(x, y, text=ch, font=f, fill=darken(color, 0.05),
                                    anchor="nw", tags=("content",))
            x += cw
        return measure.metrics("linespace")

    def draw_welcome_art(self, cx, y):
        """The bubble cluster: a big '?' bubble with friends, stars and sparkles."""
        c, s = self.canvas, self.scale
        cy = y + 95 * s
        bubbles = [  # (dx, dy, radius, color, glyph)
            (0, 0, 70, LAVENDER, "?"),
            (-125, 18, 40, PINK, "!"),
            (122, 22, 44, SKY, "?"),
            (-200, -32, 22, MINT, ""),
            (196, -40, 24, YELLOW, ""),
            (-70, 72, 18, YELLOW, ""),
            (76, 70, 16, MINT, ""),
        ]
        for i, (dx, dy, r, color, glyph) in enumerate(bubbles):
            tag = ("content", f"float{i % 3}")
            x, yy, r = cx + dx * s, cy + dy * s, r * s
            c.create_oval(x - r, yy - r + 6, x + r, yy + r + 6,
                          fill=darken(color, 0.15), outline="", tags=tag)
            c.create_oval(x - r, yy - r, x + r, yy + r, fill=color, outline="", tags=tag)
            # glossy highlight in the top-left of each bubble
            c.create_oval(x - r * 0.62, yy - r * 0.68, x - r * 0.12, yy - r * 0.3,
                          fill=lighten(color, 0.55), outline="", tags=tag)
            if glyph:
                c.create_text(x, yy + 2, text=glyph, fill=WHITE,
                              font=self.tfont(int(r / s * 1.05)), tags=tag)
        for dx, dy, r, color in [(-160, -70, 13, YELLOW), (160, -78, 11, PINK),
                                 (-38, -88, 9, SKY), (210, 40, 10, LAVENDER),
                                 (-226, 36, 9, PINK), (40, 90, 8, LAVENDER)]:
            draw_star(c, cx + dx * s, cy + dy * s, r * s, darken(color, 0.05),
                      tags=("content", "float1"))
        return 200 * s

    def float_animation(self):
        """Gently bob the welcome bubbles up and down (three groups, out of phase)."""
        token = self._anim_token
        c = self.canvas
        state = {"t": 0.0, "pos": [0.0, 0.0, 0.0]}

        def step():
            if token != self._anim_token or self.screen != "welcome":
                return
            state["t"] += 0.06
            for i in range(3):
                target = 5 * math.sin(state["t"] + i * 2.1)
                c.move(f"float{i}", 0, target - state["pos"][i])
                state["pos"][i] = target
            c.after(40, step)

        c.after(400, step)

    # ----- welcome --------------------------------------------------------------

    def render_welcome(self):
        s, cx = self.scale, self.W / 2
        x0, col = self.column(760)
        y = 20 * s
        y += self.draw_welcome_art(cx, y) + 6 * s
        y += self.draw_title(cx, y) + 2 * s
        self.text(cx, y, "Five questions. Endless possibilities. How much do you know?",
                  self.font(13, "bold"), fill=LAVENDER, anchor="n", width=col,
                  justify="center")
        y += 34 * s
        self.text(cx, y, "Think you're a trivia genius? Let's find out!",
                  self.tfont(22), anchor="n", width=col, justify="center")
        y += self.text_size("Ag", self.tfont(22), col)[1] + 10 * s
        self.text(cx, y, "5 random questions. 5 points up for grabs. One big brain.",
                  self.font(15), fill=SLATE, anchor="n", width=col, justify="center")
        y += 44 * s

        bw = 270 * s
        _, bh = self.button(cx - bw / 2, y, "Let's Play!", PINK, self.start_game,
                            size=24, w=bw, h=72 * s, text_color=WHITE)
        y += bh + 30 * s

        # Category chips laid out in rows that wrap on narrow windows.
        chips = [(CATEGORY_STYLES[k]["color"], k) for k in CATEGORY_STYLES]
        y += self.chip_rows(chips, cx, y, col) + 20 * s

        lw = self.button_width("🏆  Leaderboard", 13, pad=24)
        _, lh = self.button(cx - lw / 2, y, self.t("🏆  Leaderboard"), lighten(SKY, 0.35),
                            self.open_leaderboard, size=13, pad=24)
        y += lh + 18 * s
        self.text(cx, y, "Tip: press 1–4 or A–D to answer and Enter to continue.",
                  self.font(11), fill=SLATE, anchor="n")
        self.float_animation()
        return self.center_vertically()

    def chip_rows(self, chips, cx, y, max_w):
        """Lay out small colored pills centered in wrapping rows; returns height."""
        f = self.font(12, "bold")
        gap = 10 * self.scale
        sizes = [(self.text_size(label, f)[0] + 28 * self.scale) for _, label in chips]
        h = self.text_size("Ag", f)[1] + 12 * self.scale
        rows, row, row_w = [], [], 0
        for chip, w in zip(chips, sizes):
            if row and row_w + gap + w > max_w:
                rows.append((row, row_w))
                row, row_w = [], 0
            row_w += (gap if row else 0) + w
            row.append((chip, w))
        if row:
            rows.append((row, row_w))
        for r, (items, row_w) in enumerate(rows):
            x = cx - row_w / 2
            yy = y + r * (h + gap)
            for (color, label), w in items:
                self.pill(x, yy, label, color, size=12)
                x += w + gap
        return len(rows) * (h + gap) - gap

    # ----- quiz -------------------------------------------------------------------

    def render_quiz(self):
        e, s = self.engine, self.scale
        q, answer = e.current_question, e.current_answer
        style = CATEGORY_STYLES.get(q["category"], DEFAULT_CATEGORY_STYLE)
        x0, col = self.column(860)
        y = 20 * s

        # Top bar: logo on the left, Home + live score on the right.
        self.text(x0, y, "QuizPop!", self.tfont(20), fill=LAVENDER)
        sw, _ = self.pill(x0 + col, y, self.t(f"⭐ Score: {e.score}"), YELLOW, size=13,
                          anchor="ne")
        hw = self.button_width("Home", 11, pad=16)
        self.button(x0 + col - sw - hw - 12 * s, y - 2 * s, "Home", lighten(LAVENDER, 0.6),
                    self.go_home, size=11, pad=16)
        y += 54 * s

        # "Question 1 of 5" + segmented progress bar. Each segment becomes
        # green/pink once answered, lavender for the current question.
        self.text(x0, y, f"Question {e.current + 1} of {e.total}", self.tfont(17))
        done = e.answered_count
        self.text(x0 + col, y + 2 * s, f"{done}/{e.total} answered", self.font(12),
                  fill=SLATE, anchor="ne")
        y += 34 * s
        gap, bar_h = 8 * s, 14 * s
        seg_w = (col - gap * (e.total - 1)) / e.total
        for i in range(e.total):
            sx = x0 + i * (seg_w + gap)
            a = e.answers[i]
            if a is not None:
                color = MINT if a["is_correct"] else PINK
            elif i == e.current:
                color = LAVENDER
            else:
                color = lighten(LAVENDER, 0.78)
            round_rect(self.canvas, sx, y, sx + seg_w, y + bar_h, bar_h / 2, fill=color,
                       outline="", tags=("content",))
        y += bar_h + 22 * s

        # Category badge
        _, bh = self.pill(x0, y, style["label"], style["color"], size=12)
        y += bh + 14 * s

        # Question card
        qf = self.tfont(22)
        pad = 30 * s
        _, qh = self.text_size(q["question"], qf, col - pad * 2)
        self.card(x0, y, x0 + col, y + qh + pad * 2, outline=lighten(style["color"], 0.3))
        self.text(x0 + pad, y + pad, q["question"], qf, width=col - pad * 2)
        y += qh + pad * 2 + 24 * s

        # Answer options: 2×2 grid when there's room, a single column otherwise.
        cols = 2 if col >= 640 else 1
        gap = 16 * s
        opt_w = (col - gap * (cols - 1)) / cols
        heights = [self.option_height(opt, opt_w) for opt in q["options"]]
        for row_start in range(0, len(q["options"]), cols):
            row_h = max(heights[row_start:row_start + cols])
            for j in range(cols):
                i = row_start + j
                if i < len(q["options"]):
                    self.draw_option(i, x0 + j * (opt_w + gap), y, opt_w, row_h, q, answer)
            y += row_h + gap
        y += 8 * s

        if answer is not None:
            y = self.draw_feedback(x0, y, col, q, answer) + 30 * s
        return y

    def option_height(self, text, w):
        s = self.scale
        _, th = self.text_size(text, self.font(17, "bold"), w - 100 * s)
        return max(76 * s, th + 40 * s)

    def draw_option(self, i, x, y, w, h, q, answer):
        """One answer tile with its letter bubble. Locked once answered."""
        c, s = self.canvas, self.scale
        tag = f"opt{i}"
        tags = ("content", tag)
        bubble_color = OPTION_COLORS[i]
        letter = OPTION_LETTERS[i]
        fill, edge, text_color, mark = WHITE, lighten(bubble_color, 0.1), NAVY, letter

        # Decide the tile's look after an answer is locked in. The correct
        # answer is only revealed here — never before a choice is made.
        note = ""
        if answer is not None:
            if i == q["correct_index"]:
                fill, edge, bubble_color, mark = CORRECT_FILL, CORRECT_EDGE, CORRECT_EDGE, "✓"
                note = "Your answer" if answer["selected_index"] == i else "Correct answer"
            elif i == answer["selected_index"]:
                fill, edge, bubble_color, mark = WRONG_FILL, WRONG_EDGE, WRONG_EDGE, "✗"
                note = "Your answer"
            else:
                fill, edge, text_color = DIMMED_FILL, "#E2E8F0", "#94A3B8"
                bubble_color = "#CBD5E1"

        round_rect(c, x, y + 5, x + w, y + h + 5, 22 * s, fill=lighten(edge, 0.45),
                   outline="", tags=tags)
        face = round_rect(c, x, y, x + w, y + h, 22 * s, fill=fill, outline=edge,
                          width=3 if answer is not None and note else 2,
                          tags=tags + (tag + "_lift",))
        br = 22 * s
        bx, by = x + 22 * s + br, y + h / 2
        c.create_oval(bx - br, by - br, bx + br, by + br, fill=bubble_color, outline="",
                      tags=tags + (tag + "_lift",))
        c.create_text(bx, by, text=mark, font=self.tfont(17), fill=WHITE,
                      tags=tags + (tag + "_lift",))
        c.create_text(x + 80 * s, y + h / 2, text=q["options"][i], font=self.font(17, "bold"),
                      fill=text_color, anchor="w", width=w - 100 * s,
                      tags=tags + (tag + "_lift",))
        if note:
            nf = self.font(10, "bold")
            nw, nh = self.text_size(note, nf)
            nx, ny = x + w - nw - 34 * s, y - nh / 2 - 6 * s
            round_rect(c, nx, ny, nx + nw + 22 * s, ny + nh + 8 * s, 10 * s, fill=edge,
                       outline="", tags=tags)
            c.create_text(nx + (nw + 22 * s) / 2, ny + (nh + 8 * s) / 2, text=note,
                          font=nf, fill=WHITE, tags=tags)

        if answer is None:
            # Hover: tint the tile and lift it 2px. Click: lock in the answer.
            lifted = {"on": False}
            hover = lighten(OPTION_COLORS[i], 0.85)

            def on_enter():
                if not lifted["on"]:
                    c.itemconfigure(face, fill=hover)
                    c.move(tag + "_lift", 0, -2)
                    lifted["on"] = True

            def on_leave():
                if lifted["on"]:
                    c.itemconfigure(face, fill=WHITE)
                    c.move(tag + "_lift", 0, 2)
                    lifted["on"] = False

            self.make_clickable(tag, lambda: self.select_answer(i), on_enter, on_leave)

    def draw_feedback(self, x0, y, col, q, answer):
        """The reveal card: right/wrong, the correct answer, and the explanation."""
        s = self.scale
        good = answer["is_correct"]
        pad = 26 * s
        inner = col - pad * 2
        title = "Correct! +1 point 🎉" if good else "Not quite — 0 points this time 💪"
        lines = []
        if not good:
            lines.append(("The correct answer is: " + q["correct_answer"],
                          self.font(15, "bold")))
        lines.append((q["explanation"], self.font(15)))

        tf = self.tfont(20)
        th = self.text_size(self.t(title), tf, inner)[1]
        body_h = sum(self.text_size(t, f, inner)[1] + 8 * s for t, f in lines)
        btn_label = "See My Results! 🎉" if self.engine.is_last_question else "Next Question →"
        btn_h = 58 * s
        card_h = pad + th + 12 * s + body_h + 16 * s + btn_h + pad

        fill = CORRECT_FILL if good else WRONG_FILL
        edge = CORRECT_EDGE if good else WRONG_EDGE
        self.card(x0, y, x0 + col, y + card_h, fill=fill, outline=edge)
        yy = y + pad
        self.text(x0 + pad, yy, title, tf, fill=darken(edge, 0.25), width=inner)
        yy += th + 12 * s
        for t, f in lines:
            self.text(x0 + pad, yy, t, f, width=inner)
            yy += self.text_size(t, f, inner)[1] + 8 * s
        yy += 16 * s
        label = self.t(btn_label)
        bw = self.button_width(label, 18)
        self.button(x0 + col - pad - bw, yy, label,
                    LAVENDER if not self.engine.is_last_question else PINK,
                    self.next_question, size=18, h=btn_h, text_color=WHITE)
        self.text(x0 + pad, yy + btn_h / 2, "or press Enter", self.font(12), fill=SLATE,
                  anchor="w")
        return y + card_h

    # ----- results -------------------------------------------------------------------

    def render_results(self):
        e, s, c = self.engine, self.scale, self.canvas
        x0, col = self.column(880)
        cx = self.W / 2
        y = 28 * s

        # Confetti around the heading — seeded per game so it stays put on resize.
        conf = random.Random(self.game_seed)
        for _ in range(26):
            px = cx + conf.uniform(-col / 2, col / 2)
            py = y + conf.uniform(-10, 90) * s
            if abs(px - cx) < 170 * s and py > y + 5 * s:
                continue   # keep the heading itself clear
            color = conf.choice([LAVENDER, PINK, SKY, MINT, YELLOW, PEACH])
            kind = conf.random()
            if kind < 0.4:
                r = conf.uniform(4, 7) * s
                c.create_oval(px - r, py - r, px + r, py + r, fill=color, outline="",
                              tags=("content",))
            elif kind < 0.7:
                draw_star(c, px, py, conf.uniform(7, 11) * s, color, tags=("content",))
            else:
                a = conf.uniform(0, math.pi)
                dx, dy = math.cos(a) * 9 * s, math.sin(a) * 9 * s
                c.create_line(px - dx, py - dy, px + dx, py + dy, fill=color,
                              width=5 * s, capstyle="round", tags=("content",))

        hf = self.tfont(44)
        self.text(cx + 3, y + 3, "You Did It!", hf, fill=PINK, anchor="n")
        self.text(cx, y, "You Did It!", hf, anchor="n")
        y += self.text_size("Ag", hf)[1] + 10 * s
        self.text(cx, y, "Here's how your brain performed.", self.font(16), fill=SLATE,
                  anchor="n")
        y += 46 * s

        # Chart beside the message on wide windows; stacked on narrow ones.
        radius, thick = 104 * s, 30 * s
        wide = col >= 700
        if wide:
            chart_cx = x0 + radius + 40 * s
            chart_cy = y + radius + 18 * s
            rx = chart_cx + radius + 56 * s
            rw = x0 + col - rx
            ry = y + 10 * s
        else:
            chart_cx, chart_cy = cx, y + radius + 18 * s
            rw = col
            rx = x0
            ry = chart_cy + radius + 40 * s

        self.draw_score_chart(chart_cx, chart_cy, radius, thick)

        msg_f = self.tfont(24)
        msg = result_message(e.score)
        anchor = "nw" if wide else "n"
        mx = rx if wide else cx
        justify = "left" if wide else "center"
        self.text(mx, ry, msg, msg_f, anchor=anchor, width=rw, justify=justify)
        ry += self.text_size(self.t(msg), msg_f, rw)[1] + 12 * s
        self.text(mx, ry, f"You answered {e.score} of {e.total} questions correctly "
                          f"({e.percentage}%).", self.font(15), fill=SLATE, anchor=anchor,
                  width=rw, justify=justify)
        ry += 34 * s

        # Legend for the donut chart
        lf = self.font(14, "bold")
        legend = [(darken(MINT, 0.12), f"Correct: {e.score}"),
                  ("#FFD1E3", f"Incorrect: {e.incorrect_count}")]
        lx = rx if wide else cx - 120 * s
        for color, label in legend:
            c.create_oval(lx, ry + 2 * s, lx + 18 * s, ry + 20 * s, fill=color, outline="",
                          tags=("content",))
            self.text(lx + 26 * s, ry + 11 * s, label, lf, anchor="w")
            lx += 140 * s
        ry += 46 * s

        # Main action buttons
        specs = [
            (self.t("Share My Score! 💌"), PINK, self.toggle_share, WHITE),
            ("Play Again!", LAVENDER, self.play_again, WHITE),
            (self.t("🏆 Leaderboard"), lighten(SKY, 0.2), self.open_leaderboard, NAVY),
        ]
        ry += self.button_flow(specs, rx, ry, rw, center=not wide) + 10 * s

        y = max(chart_cy + radius + 40 * s, ry) + 10 * s

        if self.share_open:
            y = self.draw_share_panel(x0, y, col) + 24 * s
        y = self.draw_save_panel(x0, y, col) + 30 * s
        y = self.draw_recap(x0, y, col)
        return y + 40 * s

    def button_flow(self, specs, x, y, max_w, size=16, center=False):
        """Buttons placed left-to-right, wrapping onto new rows. Returns height."""
        s = self.scale
        gap = 14 * s
        h = self.text_size("Ag", self.tfont(size))[1] + 26 * s
        widths = [self.button_width(label, size, pad=26) for label, *_ in specs]
        rows, row, row_w = [], [], 0
        for spec, w in zip(specs, widths):
            if row and row_w + gap + w > max_w:
                rows.append((row, row_w))
                row, row_w = [], 0
            row_w += (gap if row else 0) + w
            row.append((spec, w))
        if row:
            rows.append((row, row_w))
        for r, (items, row_w) in enumerate(rows):
            bx = x + (max_w - row_w) / 2 if center else x
            by = y + r * (h + gap + 6 * s)
            for (label, color, cmd, tc), w in items:
                self.button(bx, by, label, color, cmd, size=size, w=w, h=h, text_color=tc)
                bx += w + gap
        return len(rows) * (h + gap + 6 * s)

    def draw_score_chart(self, cx, cy, radius, thick):
        """
        The donut chart. On the first draw after a game it animates: the arc
        sweeps from 0 to the real score and the percentage counts up with it.
        """
        e, c, s = self.engine, self.canvas, self.scale
        correct_color, incorrect_color = darken(MINT, 0.12), "#FFD1E3"
        final = e.score / e.total
        # White disc behind the ring so it pops off the page
        outer = radius + thick / 2 + 12 * s
        c.create_oval(cx - outer, cy - outer + 6, cx + outer, cy + outer + 6,
                      fill="#ECEAF8", outline="", tags=("content",))
        c.create_oval(cx - outer, cy - outer, cx + outer, cy + outer, fill=WHITE,
                      outline="", tags=("content",))

        pct_item = c.create_text(cx, cy - 12 * s, text="", font=self.tfont(40), fill=NAVY,
                                 tags=("content",))
        self.text(cx, cy + 30 * s, f"{e.score} / {e.total} Correct", self.font(15, "bold"),
                  fill=SLATE, anchor="center")

        def draw(fraction):
            c.delete("donut")
            draw_donut(c, cx, cy, radius, thick, fraction, correct_color, incorrect_color,
                       tags=("content", "donut"))
            c.tag_raise(pct_item)
            c.itemconfigure(pct_item, text=f"{round(fraction * 100)}%")

        if not self.chart_anim_pending:
            draw(final)
            return
        self.chart_anim_pending = False
        token = self._anim_token
        steps = 40
        draw(0)

        def step(i):
            if token != self._anim_token:
                return
            t = i / steps
            draw(final * (1 - (1 - t) ** 3))   # ease-out so it "settles" into place
            if i < steps:
                c.after(18, step, i + 1)
            else:
                draw(final)

        c.after(250, step, 1)

    def draw_share_panel(self, x0, y, col):
        s = self.scale
        pad = 24 * s
        inner = col - pad * 2
        msg = self.t(self.share_message())
        title_f, msg_f = self.tfont(18), self.font(15)
        _, title_h = self.text_size("Ag", title_f)
        _, msg_h = self.text_size(msg, msg_f, inner - 32 * s)
        note = ("Friends receive your score message. QuizPop! is a desktop game, so "
                "they'll need their own copy to play.")
        note_f = self.font(12)
        _, note_h = self.text_size(note, note_f, inner)
        btn_h = self.text_size("Ag", self.tfont(15))[1] + 26 * s

        # Measure the button rows first so the card is tall enough.
        specs = [
            (self.t("📋 Copy Results"), YELLOW, self.copy_results, NAVY),
            (self.t("💬 WhatsApp"), MINT, self.share_whatsapp, NAVY),
            (self.t("✉️ Email"), SKY, self.share_email, NAVY),
        ]
        widths = [self.button_width(l, 15, pad=26) for l, *_ in specs]
        rows_needed = 1 if sum(widths) + 28 * s <= inner else len(specs)
        btn_block = rows_needed * (btn_h + 20 * s)
        status_h = 30 * s if self.share_status else 0
        card_h = (pad + title_h + 14 * s + msg_h + 32 * s + 16 * s + btn_block
                  + status_h + note_h + pad)

        self.card(x0, y, x0 + col, y + card_h, fill=lighten(PINK, 0.88),
                  outline=lighten(PINK, 0.4))
        yy = y + pad
        self.text(x0 + pad, yy, "Share your score 💌", title_f)
        yy += title_h + 14 * s
        round_rect(self.canvas, x0 + pad, yy, x0 + col - pad, yy + msg_h + 32 * s, 18 * s,
                   fill=WHITE, outline="", tags=("content",))
        self.text(x0 + pad + 16 * s, yy + 16 * s, msg, msg_f, width=inner - 32 * s)
        yy += msg_h + 32 * s + 16 * s
        yy += self.button_flow(specs, x0 + pad, yy, inner, size=15)
        if self.share_status:
            self.text(x0 + pad, yy, self.share_status, self.font(14, "bold"),
                      fill=darken(CORRECT_EDGE, 0.15))
            yy += status_h
        self.text(x0 + pad, yy, note, note_f, fill=SLATE, width=inner)
        return y + card_h

    def draw_save_panel(self, x0, y, col):
        """Optional local leaderboard: type a nickname, press Save."""
        s = self.scale
        pad = 24 * s
        title_f = self.tfont(17)
        _, title_h = self.text_size("Ag", title_f)
        row_h = 50 * s
        msg_h = 28 * s if self.save_error else 0
        card_h = pad + title_h + 14 * s + row_h + msg_h + pad
        self.card(x0, y, x0 + col, y + card_h, fill=lighten(LAVENDER, 0.85),
                  outline=lighten(LAVENDER, 0.45))
        yy = y + pad
        if self.saved_entry:
            self.text(x0 + pad, yy, "Saved to the leaderboard! 🏆", title_f)
            yy += title_h + 14 * s
            rank = f"#{self.saved_rank}" if self.saved_rank else "on the board"
            self.text(x0 + pad, yy + row_h / 2,
                      f"{self.saved_entry['nickname']} is {rank} on this computer's "
                      f"leaderboard.", self.font(15), anchor="w", width=col - pad * 2)
            return y + card_h

        self.text(x0 + pad, yy, "Add your score to the local leaderboard", title_f)
        yy += title_h + 14 * s
        self.nick_entry.configure(font=self.font(15))
        entry_w = min(320 * s, col - pad * 2 - 160 * s)
        self.canvas.create_window(x0 + pad, yy + row_h / 2, window=self.nick_entry,
                                  anchor="w", width=entry_w, height=44 * s,
                                  tags=("content",))
        if not self.nickname_var.get():
            self.nick_entry.insert(0, "")
        self.button(x0 + pad + entry_w + 14 * s, yy + (row_h - 46 * s) / 2, "Save", MINT,
                    self.save_score, size=15, h=46 * s, pad=26)
        yy += row_h
        if self.save_error:
            self.text(x0 + pad, yy + 6 * s, self.save_error, self.font(13, "bold"),
                      fill=WRONG_EDGE)
        self.text(x0 + col - pad, y + pad + 4 * s, "Nickname is stored only on this computer",
                  self.font(11), fill=SLATE, anchor="ne")
        return y + card_h

    def draw_recap(self, x0, y, col):
        """Question-by-question summary: your answer vs. the correct one."""
        s, c = self.scale, self.canvas
        self.text(x0, y, "Question Recap", self.tfont(22))
        y += 46 * s
        pad = 20 * s
        inner = col - pad * 2 - 64 * s
        qf, af = self.font(15, "bold"), self.font(14)
        for n, row in enumerate(self.engine.summary(), start=1):
            good = row["is_correct"]
            your = f"Your answer: {row['your_answer']}"
            right = f"Correct answer: {row['correct_answer']}"
            qh = self.text_size(row["question"], qf, inner)[1]
            ah = self.text_size(your, af, inner)[1]
            card_h = pad + 26 * s + qh + 10 * s + ah * (1 if good else 2) + 6 * s + pad
            self.card(x0, y, x0 + col, y + card_h, fill=WHITE,
                      outline=lighten(CORRECT_EDGE if good else WRONG_EDGE, 0.45), r=20)
            # ✓ / ✗ circle
            r = 20 * s
            bx, by = x0 + pad + r, y + pad + r
            edge = CORRECT_EDGE if good else WRONG_EDGE
            c.create_oval(bx - r, by - r, bx + r, by + r, fill=edge, outline="",
                          tags=("content",))
            c.create_text(bx, by, text="✓" if good else "✗", font=self.tfont(16),
                          fill=WHITE, tags=("content",))
            tx = x0 + pad + 64 * s
            style = CATEGORY_STYLES.get(row["category"], DEFAULT_CATEGORY_STYLE)
            pw, _ = self.pill(tx, y + pad - 2 * s, f"Q{n} · {style['label']}",
                              lighten(style["color"], 0.35), size=9)
            yy = y + pad + 26 * s
            self.text(tx, yy, row["question"], qf, width=inner)
            yy += qh + 10 * s
            self.text(tx, yy, your, af, fill=darken(edge, 0.2), width=inner)
            if not good:
                yy += ah + 4 * s
                self.text(tx, yy, right, self.font(14, "bold"),
                          fill=darken(CORRECT_EDGE, 0.2), width=inner)
            y += card_h + 14 * s
        return y

    # ----- leaderboard -------------------------------------------------------------

    def render_leaderboard(self):
        s, c = self.scale, self.canvas
        x0, col = self.column(700)
        cx = self.W / 2
        y = 24 * s
        draw_star(c, cx, y + 26 * s, 26 * s, YELLOW, tags=("content",))
        y += 64 * s
        self.text(cx, y, "Leaderboard", self.tfont(36), anchor="n")
        y += 56 * s
        self.text(cx, y, "Top 10 scores on this computer", self.font(15), fill=SLATE,
                  anchor="n")
        y += 40 * s

        entries = self.leaderboard.top(LEADERBOARD_SIZE)
        row_h = 58 * s
        if not entries:
            self.card(x0, y, x0 + col, y + 110 * s)
            self.text(cx, y + 55 * s,
                      "No scores yet — play a round and save your score! 🎈",
                      self.font(16, "bold"), anchor="center", width=col - 40 * s,
                      justify="center")
            y += 110 * s
        else:
            pad = 16 * s
            card_h = pad * 2 + row_h * len(entries)
            self.card(x0, y, x0 + col, y + card_h)
            medal = {1: YELLOW, 2: "#E2E8F0", 3: PEACH}
            for i, entry in enumerate(entries, start=1):
                ry = y + pad + (i - 1) * row_h
                if i % 2 == 0:
                    round_rect(c, x0 + 10 * s, ry + 3 * s, x0 + col - 10 * s,
                               ry + row_h - 3 * s, 16 * s, fill=lighten(LAVENDER, 0.9),
                               outline="", tags=("content",))
                mid = ry + row_h / 2
                r = 18 * s
                bx = x0 + 30 * s + r
                c.create_oval(bx - r, mid - r, bx + r, mid + r,
                              fill=medal.get(i, lighten(LAVENDER, 0.6)), outline="",
                              tags=("content",))
                c.create_text(bx, mid, text=str(i), font=self.tfont(14), fill=NAVY,
                              tags=("content",))
                self.text(bx + r + 18 * s, mid, entry["nickname"], self.font(16, "bold"),
                          anchor="w")
                date = entry["date"][:16]
                self.text(x0 + col - 30 * s, mid, date, self.font(12), fill=SLATE,
                          anchor="e")
                score_x = x0 + col - 30 * s - self.text_size("0000-00-00 00:00",
                                                             self.font(12))[0] - 26 * s
                self.text(score_x, mid,
                          f"{entry['score']}/{QUESTIONS_PER_GAME}  ·  {entry['percentage']}%",
                          self.font(15, "bold"), fill=darken(LAVENDER, 0.25), anchor="e")
            y += card_h
        y += 34 * s
        label = "Back to Results" if self.prev_screen == "results" else "Back"
        bw = self.button_width(label, 16)
        self.button(cx - bw / 2, y, label, LAVENDER, self.close_leaderboard, size=16,
                    text_color=WHITE)
        y += 70 * s
        return self.center_vertically()

    # ----- error -----------------------------------------------------------------

    def render_error(self):
        s = self.scale
        x0, col = self.column(640)
        cx = self.W / 2
        y = 30 * s
        self.draw_title(cx, y, size=40)
        y += 80 * s
        pad = 28 * s
        f = self.font(14)
        msg = self.load_error or "Something went wrong."
        _, mh = self.text_size(msg, f, col - pad * 2)
        card_h = pad + 40 * s + mh + pad
        self.card(x0, y, x0 + col, y + card_h, fill=WRONG_FILL, outline=WRONG_EDGE)
        self.text(x0 + pad, y + pad, "Oops! QuizPop! couldn't load its questions.",
                  self.tfont(18), width=col - pad * 2)
        self.text(x0 + pad, y + pad + 40 * s, msg, f, width=col - pad * 2)
        y += card_h + 30 * s
        specs = [("Try Again", LAVENDER, self.retry_load, WHITE),
                 ("Quit", lighten(PINK, 0.3), self.root.destroy, NAVY)]
        y += self.button_flow(specs, x0, y, col, center=True)
        return self.center_vertically()


# ===========================================================================
# Self-tests (python3 main.py --selftest) — logic only, no window needed
# ===========================================================================

def run_self_tests(questions_path=QUESTIONS_FILE):
    import tempfile
    results = []

    def check(name, condition):
        results.append((name, bool(condition)))
        print(f"  {'PASS' if condition else 'FAIL'}  {name}")

    print("QuizPop! self-tests\n")
    bank = load_question_bank(questions_path)
    check("question bank loads and validates", True)
    check("bank has at least 100 questions", len(bank) >= 100)
    expected = {"Sports": 20, "Government": 15, "Constitution": 15,
                "World Cultures": 20, "Geography": 15, "General Knowledge": 15}
    counts = {}
    for q in bank:
        counts[q["category"]] = counts.get(q["category"], 0) + 1
    check(f"category counts match spec {counts}", counts == expected)
    check("every question has 4 unique options",
          all(len(set(q["options"])) == 4 for q in bank))
    check("every correct_answer appears exactly once in its options",
          all(q["options"].count(q["correct_answer"]) == 1 for q in bank))
    check("question ids are unique", len({q["id"] for q in bank}) == len(bank))
    check("question texts are unique", len({q["question"] for q in bank}) == len(bank))

    # Validation rejects broken data
    bad = {"questions": [dict(bank[0], options=["a", "b", "c"])] + bank[1:]}
    try:
        validate_question_bank(bad)
        check("validator rejects a 3-option question", False)
    except QuestionBankError:
        check("validator rejects a 3-option question", True)
    bad = {"questions": [dict(bank[0], correct_answer="nope")] + bank[1:]}
    try:
        validate_question_bank(bad)
        check("validator rejects an answer not in the options", False)
    except QuestionBankError:
        check("validator rejects an answer not in the options", True)
    with tempfile.TemporaryDirectory() as tmp:
        broken = os.path.join(tmp, "q.json")
        with open(broken, "w") as fh:
            fh.write("{ not json")
        try:
            load_question_bank(broken)
            check("invalid JSON file raises a friendly error", False)
        except QuestionBankError:
            check("invalid JSON file raises a friendly error", True)
        try:
            load_question_bank(os.path.join(tmp, "missing.json"))
            check("missing file raises a friendly error", False)
        except QuestionBankError:
            check("missing file raises a friendly error", True)

    rng = random.Random(1234)
    ok_unique, ok_variety, seen = True, True, set()
    for _ in range(2000):
        picked = select_questions(bank, 5, rng)
        ids = [q["id"] for q in picked]
        ok_unique &= len(ids) == 5 and len(set(ids)) == 5
        ok_variety &= len({q["category"] for q in picked}) == 5
        seen.update(ids)
    check("2000 games: always exactly 5 unique questions", ok_unique)
    check("2000 games: 5 different categories every game", ok_variety)
    check(f"every question can appear across games ({len(seen)}/{len(bank)} seen)",
          len(seen) == len(bank))

    ok_map, positions = True, set()
    for _ in range(500):
        for q in bank:
            p = shuffle_options(q, rng)
            ok_map &= p["options"][p["correct_index"]] == q["correct_answer"]
            ok_map &= sorted(p["options"]) == sorted(q["options"])
            positions.add(p["correct_index"])
    check("shuffling always keeps the correct answer mapped", ok_map)
    check("correct answer lands in every position A–D", positions == {0, 1, 2, 3})

    engine = QuizEngine(bank, random.Random(99))
    q = engine.current_question
    engine.submit_answer(q["correct_index"])
    check("correct answer awards exactly 1 point", engine.score == 1)
    try:
        engine.submit_answer(q["correct_index"])
        check("same question cannot be answered twice", False)
    except AlreadyAnsweredError:
        check("same question cannot be answered twice", engine.score == 1)
    engine.advance()
    q = engine.current_question
    engine.submit_answer((q["correct_index"] + 1) % 4)
    check("incorrect answer awards 0 points", engine.score == 1)
    try:
        engine2 = QuizEngine(bank, random.Random(5))
        engine2.advance()
        check("cannot skip a question without answering", False)
    except RuntimeError:
        check("cannot skip a question without answering", True)

    for target in range(6):
        eng = QuizEngine(bank, random.Random(target))
        for i in range(5):
            qq = eng.current_question
            pick = qq["correct_index"] if i < target else (qq["correct_index"] + 1) % 4
            eng.submit_answer(pick)
            eng.advance()
        c_deg, i_deg = donut_extents(eng.score)
        check(f"score {target}/5 -> {eng.percentage}%, donut {c_deg:.0f}°/{i_deg:.0f}°, "
              f"{eng.incorrect_count} wrong",
              eng.is_finished and eng.score == target and eng.percentage == target * 20
              and abs(c_deg - 72 * target) < 1e-9 and abs(c_deg + i_deg - 360) < 1e-9
              and eng.incorrect_count == 5 - target)
        msg = build_share_message(target)
        check(f"share text for {target}/5",
              f"{target}/5 ({target * 20}%)" in msg and msg.startswith("🎉"))
        check(f"result message for {target}/5", result_message(target) == RESULT_MESSAGES[target])

    msg = build_share_message(4)
    wa = whatsapp_share_url(msg)
    check("WhatsApp URL decodes back to the message",
          wa.startswith("https://wa.me/?text=")
          and urllib.parse.unquote(wa.split("text=", 1)[1]) == msg)
    mail = email_share_url(msg)
    parsed = urllib.parse.parse_qs(mail.split("?", 1)[1])
    check("mailto link has subject and body with the score",
          mail.startswith("mailto:?") and "subject" in parsed and msg in parsed["body"][0])

    engine.new_game()
    check("new game resets score, answers and position",
          engine.score == 0 and engine.current == 0
          and all(a is None for a in engine.answers) and len(engine.questions) == 5)

    with tempfile.TemporaryDirectory() as tmp:
        board = Leaderboard(os.path.join(tmp, "lb.json"))
        check("empty leaderboard when file is missing", board.top() == [])
        base = datetime.datetime(2026, 1, 1, 12, 0, 0)
        for i, (name, sc) in enumerate([("Ava", 3), ("Ben", 5), ("Cy", 4), ("Di", 5)]):
            board.add(name, sc, when=base + datetime.timedelta(minutes=i))
        top = board.top()
        check("leaderboard ranks by score, ties by earliest",
              [e["nickname"] for e in top] == ["Ben", "Di", "Cy", "Ava"])
        for i in range(12):
            board.add(f"P{i}", 1, when=base + datetime.timedelta(hours=i + 1))
        check("leaderboard shows only the top 10", len(board.top()) == 10)
        try:
            board.add("   ", 3)
            check("blank nickname is rejected", False)
        except ValueError:
            check("blank nickname is rejected", True)
        with open(board.path, "w") as fh:
            fh.write("garbage")
        check("corrupted leaderboard file is handled", board.top() == [])

    passed = sum(ok for _, ok in results)
    print(f"\n{passed}/{len(results)} checks passed.")
    return passed == len(results)


def main():
    if "--selftest" in sys.argv:
        sys.exit(0 if run_self_tests() else 1)

    if sys.platform == "win32":
        # Crisp text on high-DPI Windows displays.
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    QuizPopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
