"""`contract`: propose the destination columns of a raw table and record the user's agreement
(design section 5; spec 2.2 P1; plan F03).

Owner: M5. Standard library only (openpyxl is not needed here).

- `propose` reads a sample CSV (or a list of column names), infers each column's type, counts nulls, tests key
  candidates for uniqueness, lists allowed values for low-cardinality text columns and writes
  `contracts/<table>.contract.json` (status "proposed") and the readable `contracts/<table>.contract.md`, which the
  agent shows the user. It writes the marker `rotm_model.json` when the folder has none (design D3).
- `agree` records an agreement only with the user's own reply (verbatim), their name and the date. With
  --channel task-file the reply must occur in the named task file. The tool never writes "agreed" by itself:
  a contract changed after it was agreed counts as not agreed (state "stale", design 5), and `propose --force`
  on an agreed contract resets it to "proposed".
- `propose`, `show` and `agree` record agreement.shown_sha256, the content hash of the JSON the .md was rendered
  from; `agree` refuses a contract whose .md does not show its current JSON (refusal F-SHOWN, round 2 verdict
  C-sol-3), a reply that asks for a change (R-CHANGE, A-aera-3 and C-sol-4) and a reply that postpones the
  decision (round 3 verdict, A-aera-4: no agreement was given; wait for the user's reply). Round 5 verdict
  (A-aera-1 and C-sol-1): explicit refusal and stop words (reject, decline, refuse, hold off, hold on, a negation
  before run, proceed, start, land, continue or go ahead, please wait, wait until, wait for, 거절, 반려, 거부, 보류,
  기다려, 하지 마) are refused too, and every refusal that is not a change request or a question says that no
  agreement was given and to wait for the user's reply. No word is required in an agreement.
- `propose` writes a source expression taken from a header as a GoogleSQL identifier, in backticks when the
  header is a reserved keyword or not a plain identifier, and notes a column named like a helper column build
  adds (N-HELPER-NAME; build refuses it) (round 3 verdict, A-aera-11 and A-aera-12).
- `remove` moves the files of a table that is no longer wanted into MODEL/removed/ (J-4).

Sources of the rules: Max's words, spec Part 1 section 1.2 ("coordinate and agree with the user, what columns will
be pulled into in which shapes"); the field list of spec 2.2 P1 (name, type, unit, grain, key, null rule, source,
expected row count or control total); researcher 2 section 4 (control totals come from the source side, never from
the landed data). The thresholds below are the plugin's own choices.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import math
import os
import re
import sys
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import common, modelfolder, schemas

MAX_ALLOWED_VALUES = 12       # propose lists allowed values for a column with at most this many distinct values (design 15)
PROFILE_MAX_ROWS = 100_000    # rows of a sample CSV that propose reads (the rest are counted, not profiled)
KEY_PAIR_COLUMNS = 8          # pairs and triples of key candidates are tried among the first 8 eligible columns
MAX_KEY_CANDIDATES = 5
EXCEL_MAX_DATA_ROWS = 1_048_575   # one sheet holds 1,048,576 rows; row 1 is the header
ROWS_FACTOR = 10              # proposed maximum rows = 10 times the sample's rows, at least MIN_PROPOSED_MAX (plugin's choice)
MIN_PROPOSED_MAX = 1000
EXCEL_SERIAL_MAX = 2_958_465  # 9999-12-31 as an Excel serial number
INTEGER_MAX_DIGITS = 15       # longer digit strings stay text: Excel keeps 15 significant digits
EXCEL_EPOCH = _dt.datetime(1899, 12, 30)
CHANNELS_CLI = ("session", "task-file", "example")

# First words that make a reply read as a refusal; such a reply is never recorded as an agreement (plugin's choice).
# A reply that starts 'No need to' is not refused by its first word (round 5 verdict, A-aera-1 and C-sol-1: 'No need
# to wait, go ahead.' is recorded); the rest of the reply is read as usual (_NO_NEED_RE).
NEGATIVE_FIRST_WORDS = frozenset({"no", "nope", "nah", "not", "don't", "dont", "disagree", "disagreed",
                                  "아니", "아니요", "아뇨", "아니오", "안돼", "안됩니다", "싫어"})
# A negated agreement anywhere in the reply also makes it a refusal (round 1 verdict, A-aera-3), and so do the
# explicit refusal and stop words of the round 5 verdict (A-aera-1 and C-sol-1): reject, decline and refuse in their
# verb and noun forms; a negation followed by run, proceed, start, land, continue or go ahead ('Do not run the query
# yet.', "don't proceed", 'no need to run'); the Korean forms 거절, 반려, 거부 and 하지 마 (also 하지마). These are
# patterns of refusal only: no word is required in an agreement and the reply need not name the table, so
# free-form approvals are still recorded. The round 5 verdict closes the list.
_NEG = (r"(?:not|never|cannot|can'?t|don'?t|doesn'?t|didn'?t|haven'?t|hasn'?t|hadn'?t|won'?t|wouldn'?t|"
        r"couldn'?t|shouldn'?t|isn'?t|aren'?t|wasn'?t|weren'?t)")
_AGREE = r"(?:agree|agreed|agrees|agreeing|approve|approved|approves|accept|accepted|accepts|consent|consented)"
_FILL = r"(?:\s+(?:yet|really|fully|quite|entirely|completely|be|been|ever))*"
_KO = r"(?:동의|합의|승인|찬성)"
# 'declining balance' (a depreciation method) and 반려동물 (a pet) are not refusals
_REJECT = (r"(?:reject|rejects|rejected|rejecting|rejection|decline|declines|declined|declining(?!\s+balance)|"
           r"refuse|refuses|refused|refusing|refusal)")
_GO = r"(?:run|proceed|start|land|continue|go\s+ahead)"
_GO_FILL = r"(?:\s+(?:yet|just|please|to|be|ever|really|now|still|even|actually))*"
REFUSAL_PATTERNS = (
    re.compile(rf"\b{_NEG}{_FILL}\s+{_AGREE}\b"),                       # not agreed, haven't yet agreed, cannot agree
    re.compile(r"\bdisagree(?:s|d|ing)?\b"),
    re.compile(r"\bno\s+(?:agreement|approval|consent)\b"),
    re.compile(rf"\b{_NEG}\s+(?:ok|okay|fine|good|right|correct|acceptable)\b"),
    re.compile(rf"\b{_REJECT}\b"),                                      # reject, rejection, declined, refuse, refusal
    re.compile(rf"\b(?:(?:do\s+)?{_NEG}|no\s+need\s+to|needn'?t){_GO_FILL}\s+{_GO}\b"),   # do not run, don't go ahead
    re.compile(rf"{_KO}\s*(?:하|되|해|드리)?지\s*(?:않|못|말)"),            # 동의하지 않습니다, 승인되지 않
    re.compile(rf"{_KO}\s*(?:안|못)\s*(?:해|하|함|합|했|할|돼|되|됨|됐|됩|드)"),   # 동의 안 해요, 동의 못 합니다
    re.compile(rf"{_KO}\s+(?:안|못)\s*(?=$|[.!?,~])"),                    # 동의 안.
    re.compile(rf"{_KO}\s*(?:할|될|드릴)\s*수\s*(?:가\s*)?없"),               # 동의할 수 없습니다
    re.compile(rf"(?<![가-힣])(?:미|비|부){_KO}"),                          # 미동의, 미승인 (not 전부동의)
    re.compile(rf"{_KO}\s*(?:불가|거부|거절|보류|철회)"),                     # 승인 보류
    re.compile(r"거절|반려(?!\s*(?:동물|견|묘|식물))|거부|하지\s*마"),        # 거절합니다, 반려, 진행하지 마세요
)
_NO_NEED_RE = re.compile(r"no\s+need\s+to\b")
# A reply that postpones the decision gives no agreement either (round 3 verdict, A-aera-4): 'will review',
# 'review it later' or 'tomorrow', 'get back to you', 'need to check' or 'look first', 'not yet', 'let me check',
# and the Korean forms 나중에, 내일, 검토 후, 확인해 볼게 and 아직 (also 아직 동의 전). The round 5 verdict (A-aera-1
# and C-sol-1) adds the stop words 'hold off', 'hold on', 'please wait', 'wait until' and 'wait for' (also 'waiting
# for'), 보류 and 기다려; 'hold off', 'hold on', 'wait until' and 'wait for' do not count when a negative word comes
# before them in the clause ('no need to wait for', "don't wait for", 'without waiting for'; _negated), and 보류 does
# not count in 보류하지 말고 or 보류 없이. Its reason starts with POSTPONE_REASON and the refusal says
# POSTPONE_MESSAGE. These are patterns of postponement only: no word is required in an agreement ('Reviewed,
# agreed.' and 'Checked the totals, agreed.' are recorded).
POSTPONE_REASON = "postpones the decision"
POSTPONE_MESSAGE = "no agreement was given; wait for the user's reply"
_DEFER = (r"(?:review|re-?check|check|look|read|go\s+(?:over|through)|think|consider|confirm|decide|verify|answer|"
          r"reply|respond|ask|talk|discuss)")
_LATER = r"(?:later|tomorrow|tonight|next\s+(?:week|month|time))"
_GAP = r"[^.!?;,\n]{0,40}?"            # within one clause
_HOLD_RE = re.compile(r"\bhold\s+(?:off|on)\b")
_WAIT_RE = re.compile(r"\bwait(?:ing)?\s+(?:until|till|for)\b")
UNLESS_NEGATED = (_HOLD_RE, _WAIT_RE)  # postponement patterns that a negative word before them cancels
POSTPONE_PATTERNS = (
    # will review, I'll look at it, we are going to go over it, I will get back to you
    re.compile(r"\b(?:will|'ll|shall|going\s+to|gonna)\s+(?:(?:first|still|then|also)\s+)?(?:review|"
               r"look\s+(?:at|into|over|through)|go\s+(?:over|through)|think|get\s+back|come\s+back)\b"),
    # let me check, let's look at it, let us think
    re.compile(rf"\blet(?:'s|\s+me|\s+us)\s+(?:{_DEFER}|see|get\s+back)\b"),
    # get back to you, come back to you on this, circle back
    re.compile(r"\b(?:get|come)\s+back\s+to\s+(?:you|u|this|it)\b|\bcircle\s+back\b"),
    # review it later, check tomorrow, decide next week; a reply that starts 'later' or 'tomorrow'
    re.compile(rf"\b{_DEFER}\b{_GAP}\b{_LATER}\b"),
    re.compile(rf"^(?:maybe\s+|perhaps\s+)?{_LATER}\b"),
    # need to check first, look at it first, check with finance first; I still need to review, we need to check
    re.compile(rf"\b{_DEFER}\b{_GAP}\bfirst\b(?=\s*(?:$|[.!?;,)]))"),
    re.compile(rf"\b(?:i|we)\s+(?:still\s+)?(?:need|have|want)\s+to\s+{_DEFER}\b"),
    re.compile(r"\bnot\s+yet\b"),
    re.compile(r"나중에|내일|아직|(?:검토|확인)\s*(?:후|하고|해\s*보|해\s*볼|해서|중)"),
    # round 5 verdict (A-aera-1, C-sol-1): hold off, hold on, please wait, wait until, wait for; 보류, 기다려
    _HOLD_RE,
    re.compile(r"\bplease\s+wait\b"),
    _WAIT_RE,
    re.compile(r"보류(?!\s*(?:하지\s*(?:말|않)|없이|할\s*필요))|기다려"),
)
# A reply that asks for a change is not an agreement either (round 2 verdict, A-aera-3 and C-sol-4; rule R-CHANGE):
# a request ('please change', 'can you rename', 'should be changed', 'needs to change') or an imperative at the
# start of a sentence or clause ('Replace the key', '..., but drop region'), and the Korean forms 바꿔, 변경해,
# 수정해 and 고쳐. A negated change ('no changes', 'nothing to change', 'change nothing', 'without changes', 'no need
# to change') is not a request. Again no word is required in an agreement.
CHANGE_REASON = "asks for a change"
_CHANGE_VERB = r"(?:change|rename|remove|drop|replace|switch|fix|revise|modify)"
_CHANGE_PART = r"(?:changed|renamed|removed|dropped|replaced|switched|fixed|corrected|revised|modified)"
_MAKE_CHANGE = r"make\s+(?:a\s+|one\s+|some\s+|the\s+|these\s+|those\s+|two\s+|a\s+few\s+)?changes?"
_REQUEST = (r"(?:please|pls|plz|kindly|(?:can|could|would|will)\s+(?:you|we)|should|must|needs?\s+to|"
            r"ha(?:ve|s)\s+to|wants?\s+(?:you\s+)?to|(?:would|i'd|we'd)\s+like\s+(?:you\s+)?to|let'?s|let\s+us)")
_FILLERS = r"(?:(?:also|just|then|please|kindly|quickly)[\s,]+)*"
_CLAUSE_START = r"(?:^|[.!?;:,\n(]\s*|\b(?:but|and|then|also|just|so|ok|okay|yes|now)\s+)"
_OBJECT = r"(?:the|a|an|this|that|these|those|it|them|its|their|our|my|your|all|each|every|both)\b"
CHANGE_PATTERNS = (
    # a request: please change ..., can you rename ..., should be changed, needs to change, please make a change
    re.compile(rf"\b(?P<w>{_REQUEST}[\s,]+{_FILLERS}(?:{_CHANGE_VERB}|correct|{_MAKE_CHANGE}|be\s+{_CHANGE_PART})\b)"),
    # an imperative at the start of a sentence or clause: Change the key ..., Looks good, but drop region
    re.compile(rf"{_CLAUSE_START}{_FILLERS}(?P<w>(?:{_CHANGE_VERB}|{_MAKE_CHANGE})\s+(?!(?:nothing|none)\b)"
               r"[^\s.!?,;:]+)"),
    # 'correct' is also an adjective ('Correct, go ahead'): as an imperative it needs an object
    re.compile(rf"{_CLAUSE_START}{_FILLERS}(?P<w>correct\s+(?:{_OBJECT}|[^\s.!?,;:]+\s+to\b))"),
)
_KO_CHANGE = re.compile(r"바꿔|변경해|수정해|고쳐")
# A reply in which every sentence is a question and that holds no statement is not an answer (round 4 verdict,
# A-aera-4 and C-sol-2): 'What does the amount column include?', '금액 컬럼에 무엇이 들어가나요?'. A sentence is a
# question when it ends with '?' or '？', or with a Korean question ending (KO_QUESTION_ENDINGS). A clause before
# the last one of a question sentence (split at , ; :) is a statement unless it starts with a question word
# (QUESTION_WORDS) or ends as a question: 'Looks good, can you go ahead?' holds the statement 'Looks good' and is
# recorded; 'What does amount include, and is it net of VAT?' holds none. These are patterns of questions only: no
# word is required in an agreement.
QUESTION_REASON = "is a question"
QUESTION_MESSAGE = "no agreement was given; answer the question and wait for the user's reply"
KO_QUESTION_ENDINGS = ("까", "까요", "나요", "가요", "죠", "니", "냐", "는지요")
QUESTION_WORDS = frozenset({
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how", "is", "are", "am", "was", "were", "do",
    "does", "did", "can", "could", "would", "will", "shall", "should", "may", "might", "must", "have", "has", "had",
    "isn't", "aren't", "wasn't", "weren't", "don't", "doesn't", "didn't", "can't", "couldn't", "won't", "wouldn't",
    "shouldn't", "haven't", "hasn't", "무엇", "무엇이", "무엇을", "뭐", "뭐가", "뭘", "왜", "어떻게", "어디", "어디에",
    "언제", "누가", "누구", "몇", "어느", "어떤"})
_CONJUNCTIONS = frozenset({"and", "or", "but", "also", "so", "then", "그리고", "또", "또는", "그런데"})
_NEGATORS = frozenset({"no", "not", "never", "nothing", "none", "without", "nobody", "neither", "nor", "dont",
                       "doesnt", "didnt", "cant", "cannot", "wont", "shouldnt", "neednt", "mustnt", "isnt", "arent"})
_NO_CHANGES_RE = re.compile(r"no\s+(?:further\s+|more\s+|other\s+)?changes?\b")
NAME_NOTE_MAX = 12            # contract propose notes a longer table name (round 2 verdict, J-4; note N-NAME)
HELPER_NAME_NOTE = "N-HELPER-NAME"   # note of contract propose: a column named like a helper column (v4, A-aera-12)

_INT_RE = re.compile(r"^[+-]?\d+$")
_NUM_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_LEADING_ZERO_RE = re.compile(r"^[+-]?0\d")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COLUMN_RE = re.compile(schemas.COLUMN_RE)
_TABLE_RE = re.compile(schemas.TABLE_RE)


# ----------------------------------------------------------------------------- values and types

def is_null(value: Any) -> bool:
    """None and empty text are null (design 4: an empty CSV field is null)."""
    return value is None or (isinstance(value, str) and value == "")


def _parse_date_text(text: str) -> _dt.date | None:
    if not _DATE_RE.match(text):
        return None
    try:
        return _dt.date.fromisoformat(text)
    except ValueError:
        return None


def _parse_datetime_text(text: str) -> _dt.datetime | None:
    """A date and time read by land.read_datetime_text, the reader land uses (round 4 verdict, A-aera-16), so every
    text propose classifies as datetime lands; a Z or UTC offset is dropped and the time kept as written, a fraction
    of a second cut. A date alone counts only as YYYY-MM-DD (as the date type of propose reads it)."""
    from . import land
    if _DATE_RE.match(text):
        return land.read_datetime_text(text)[0]
    return land.read_datetime_text(text, dates=False)[0]


def _serial(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not 1 <= value <= EXCEL_SERIAL_MAX:
        return None
    return float(value)


def coerce(value: Any, ctype: str) -> tuple[bool, Any]:
    """Fit one value to a contract type: (True, typed value) or (False, None). A null gives (True, None).
    Accepts CSV text and the Python values openpyxl reads from a sheet (int, float, datetime, bool)."""
    if is_null(value):
        return True, None
    if ctype == "text":
        if isinstance(value, str):
            return True, value
        if isinstance(value, bool):
            return True, "TRUE" if value else "FALSE"
        if isinstance(value, float) and value.is_integer():
            return True, str(int(value))
        if isinstance(value, (_dt.datetime, _dt.date)):
            return True, value.isoformat()
        return True, str(value)
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return True, None
        if ctype == "integer":
            if _INT_RE.match(text):
                return True, int(text)
            if _NUM_RE.match(text) and float(text).is_integer():
                return True, int(float(text))
            return False, None
        if ctype == "number":
            if _NUM_RE.match(text):
                f = float(text)
                return (True, f) if math.isfinite(f) else (False, None)
            return False, None
        if ctype == "date":
            d = _parse_date_text(text)
            if d is not None:
                return True, d
            dt = _parse_datetime_text(text)
            if dt is not None and dt.time() == _dt.time(0, 0):
                return True, dt.date()
            return False, None
        if ctype == "datetime":
            dt = _parse_datetime_text(text)
            return (True, dt) if dt is not None else (False, None)
        if ctype == "boolean":
            up = text.upper()
            if up in ("TRUE", "FALSE"):
                return True, up == "TRUE"
            return False, None
        return False, None
    if ctype == "integer":
        if isinstance(value, bool):
            return False, None
        if isinstance(value, int):
            return True, value
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            return True, int(value)
        return False, None
    if ctype == "number":
        if isinstance(value, bool):
            return False, None
        if isinstance(value, (int, float)) and math.isfinite(value):
            return True, value
        return False, None
    if ctype == "date":
        if isinstance(value, _dt.datetime):
            return (True, value.date()) if value.time() == _dt.time(0, 0) else (False, None)
        if isinstance(value, _dt.date):
            return True, value
        s = _serial(value)
        if s is not None and s.is_integer():
            return True, (EXCEL_EPOCH + _dt.timedelta(days=s)).date()
        return False, None
    if ctype == "datetime":
        if isinstance(value, _dt.datetime):
            return True, value.replace(tzinfo=None)
        if isinstance(value, _dt.date):
            return True, _dt.datetime(value.year, value.month, value.day)
        s = _serial(value)
        if s is not None:
            return True, EXCEL_EPOCH + _dt.timedelta(days=s)
        return False, None
    if ctype == "boolean":
        return (True, value) if isinstance(value, bool) else (False, None)
    return False, None


def value_text(value: Any) -> str:
    """A typed value as the text used in messages, allowed lists and comparisons."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() and abs(value) < 1e15 else format(value, ".15g")
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    return str(value)


class _TypeVotes:
    """Which contract types every non-null text value of one column fits (inference, design 15)."""

    def __init__(self) -> None:
        self.boolean = self.integer = self.number = self.date = self.datetime = True
        self.seen = 0

    def add(self, text: str) -> None:
        t = text.strip()
        self.seen += 1
        if self.boolean and t.upper() not in ("TRUE", "FALSE"):
            self.boolean = False
        if self.integer and not (_INT_RE.match(t) and not _LEADING_ZERO_RE.match(t)
                                 and len(t.lstrip("+-")) <= INTEGER_MAX_DIGITS):
            self.integer = False
        if self.number and not (_NUM_RE.match(t) and not _LEADING_ZERO_RE.match(t)
                                and not (_INT_RE.match(t) and len(t.lstrip("+-")) > INTEGER_MAX_DIGITS)):
            self.number = False
        if self.date and _parse_date_text(t) is None:
            self.date = False
        if self.datetime and _parse_datetime_text(t) is None:
            self.datetime = False

    def result(self) -> str:
        if self.seen == 0:
            return "text"
        for name in ("boolean", "integer", "number", "date", "datetime"):
            if getattr(self, name):
                return name
        return "text"


def infer_type(values: Iterable[str]) -> str:
    """The narrowest contract type that every non-null value fits, in the order boolean, integer, number, date,
    datetime, text. Digits with a leading zero (an ID such as 00123) and digit strings longer than 15 characters
    stay text."""
    votes = _TypeVotes()
    for v in values:
        if not is_null(v):
            votes.add(str(v))
    return votes.result()


# ----------------------------------------------------------------------------- CSV profile

def clean_name(header: str, index: int) -> str:
    """A contract column name for a CSV header: kept when it is already valid, else lower case with every run of
    other characters turned into one '_' (Unicode letters are kept), prefixed with 'c_' when it starts with a digit,
    cut to 41 characters; an empty header becomes column_<n>."""
    text = (header or "").strip()
    if _COLUMN_RE.match(text):
        return text
    name = re.sub(r"\W+", "_", text, flags=re.UNICODE).strip("_").lower()
    if not name:
        return f"column_{index + 1}"
    if name[0].isdigit() or name[0] == "_":
        name = "c_" + name
    return name[:41]


def _read_csv_rows(csv_path: Path, max_rows: int) -> tuple[list[str], list[list[str]], int]:
    """(header, up to max_rows data rows, total data rows). Empty lines are skipped. Raises common.Refusal for a
    file that is missing, not UTF-8, empty or not comma-separated."""
    if not csv_path.is_file():
        raise common.Refusal(f"{csv_path.as_posix()}: CSV file not found", fix="give the path of an existing sample CSV")
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is None:
                raise common.Refusal(f"{csv_path.name}: the file is empty",
                                     fix="give a CSV with a header row and at least one data row")
            rows: list[list[str]] = []
            total = 0
            for row in reader:
                if not any(c.strip() for c in row):
                    continue
                total += 1
                if len(rows) < max_rows:
                    rows.append(row)
    except UnicodeDecodeError as exc:
        raise common.Refusal(f"{csv_path.name}: not UTF-8 text ({exc.reason} at byte {exc.start})",
                             fix="save the CSV as UTF-8 (in Excel: Save As > CSV UTF-8 (Comma delimited))") from None
    except csv.Error as exc:
        raise common.Refusal(f"{csv_path.name}: not a readable CSV ({exc})",
                             fix="save it as a comma-separated file with one header row") from None
    except OSError as exc:
        raise common.Refusal(f"{csv_path.as_posix()}: cannot be read ({exc.strerror or exc})",
                             fix="close any program that holds the file, or check its permissions") from None
    if len(header) == 1:
        for sep, name in ((";", "semicolons"), ("\t", "tabs"), ("|", "'|' characters")):
            if sep in header[0]:
                raise common.Refusal(f"{csv_path.name}: the header has one column and contains {name}; "
                                     "the file is not comma-separated",
                                     fix="save it with commas between the fields (design section 4)")
    return header, rows, total


def profile_csv(csv_path: str | Path, *, max_rows: int = PROFILE_MAX_ROWS) -> dict[str, Any]:
    """Profile a sample CSV: per column the proposed name, inferred type, null count, distinct count, minimum,
    maximum, sum and examples; the key candidates; the row count. The numbers describe the sample only."""
    p = common.native_path(csv_path)
    header, rows, total = _read_csv_rows(Path(p), max_rows)
    notes: list[str] = []
    names: list[str] = []
    used: set[str] = set()
    for i, h in enumerate(header):
        name = clean_name(h, i)
        base, k = name, 2
        while name.lower() in used:
            name = f"{base[:38]}_{k}"
            k += 1
        if name != (h or "").strip():
            if base != name:
                notes.append(f"header {i + 1} ('{h}') repeats an earlier header: named '{name}'")
            elif (h or "").strip():
                notes.append(f"header {i + 1} ('{h}') is not a valid column name: named '{name}'")
            else:
                notes.append(f"header {i + 1} is empty: named '{name}'")
        used.add(name.lower())
        names.append(name)
    width = len(header)
    short_rows = sum(1 for r in rows if len(r) < width)
    long_rows = sum(1 for r in rows if len(r) > width and any(c.strip() for c in r[width:]))
    if short_rows:
        notes.append(f"{short_rows} sample rows have fewer fields than the header ({width}); the missing fields count as null")
    if long_rows:
        notes.append(f"{long_rows} sample rows have more fields than the header ({width}); the extra fields were ignored")
    cols: list[dict[str, Any]] = []
    for ci in range(width):
        values = [r[ci] if ci < len(r) else "" for r in rows]
        votes = _TypeVotes()
        distinct: dict[str, None] = {}
        nulls = 0
        for v in values:
            if is_null(v) or v.strip() == "":
                nulls += 1
                continue
            votes.add(v)
            if v not in distinct:
                distinct[v] = None
        ctype = votes.result()
        cols.append({"name": names[ci], "header": header[ci], "type": ctype, "rows": len(values),
                     "nulls": nulls, "non_null": len(values) - nulls, "distinct": len(distinct),
                     "examples": list(distinct)[:3], "_values": values, "_distinct": list(distinct)})
    return {"csv": Path(p).as_posix(), "csv_name": Path(p).name, "header": header, "names": names, "columns": cols,
            "rows": total, "profiled_rows": len(rows), "truncated": total > len(rows), "notes": notes}


def _typed_column(col: dict[str, Any], ctype: str) -> tuple[list[Any], int]:
    """Typed values of a profiled column for `ctype`, and the count of non-null values that do not fit."""
    out: list[Any] = []
    bad = 0
    for v in col["_values"]:
        ok, typed = coerce(v, ctype)
        if not ok:
            bad += 1
            out.append(None)
        else:
            out.append(typed)
    return out, bad


def _observed(col: dict[str, Any], ctype: str) -> dict[str, Any]:
    """The sample statistics stored with a proposed column (JSON values only)."""
    typed, bad = _typed_column(col, ctype)
    present = [v for v in typed if v is not None]
    obs: dict[str, Any] = {"sample_rows": col["rows"], "nulls": col["nulls"], "distinct": col["distinct"],
                           "examples": [str(e) for e in col["examples"]]}
    if bad:
        obs["off_type"] = bad
    if present and ctype in ("integer", "number", "date", "datetime"):
        obs["min"] = value_text(min(present)) if ctype in ("date", "datetime") else min(present)
        obs["max"] = value_text(max(present)) if ctype in ("date", "datetime") else max(present)
    if present and ctype in ("integer", "number"):
        s = math.fsum(float(x) for x in present)
        obs["sum"] = int(s) if ctype == "integer" and s.is_integer() else s
    return obs


def _plain_decimals(code: str) -> int | None:
    """The decimals of a plain number format code (digits, separators, padding; no percent, date, text or
    words), from its first section; None for any other code."""
    first = re.sub(r'"[^"]*"|\[[^\]]*\]|_.|\\.', "", code.split(";")[0])
    if re.search(r"[A-Za-z%@]", first) or not re.search(r"[0#]", first):
        return None
    return len(re.match(r"[0#]*", first.split(".", 1)[1]).group(0)) if "." in first else 0


def _decimals(value: float) -> int:
    """The decimals a number needs to be shown in full (0.0123: 4; 1.2e-05: 6)."""
    from decimal import Decimal
    exp = Decimal(repr(float(value))).normalize().as_tuple().exponent
    return max(0, -int(exp)) if isinstance(exp, int) else 0


def proposed_format(values: Sequence[Any], ctype: str, d: Any) -> tuple[str, str]:
    """(number-format key, reason) for an integer or number column from its typed sample values (round 1
    verdict, J-4 a). Whole numbers: the type's default key. Values mostly below 1 in size (more than half of the
    values that are not 0): the plain number format of the style dictionary with the fewest decimals that is at
    least 2 and at least the decimals the values below 1 need, or general when no key has that many. Other
    values with decimals: amount2. No key is added to the dictionary."""
    default = d.format_for_column_type(ctype)
    nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v)]
    if ctype == "integer" or all(v.is_integer() for v in nums):
        return default, "whole numbers"
    nonzero = [v for v in nums if v != 0]
    small = [v for v in nonzero if abs(v) < 1]
    if len(small) * 2 > len(nonzero):
        need = max(2, max(_decimals(v) for v in small))
        fits = sorted((dec, i, key) for i, key in enumerate(d.number_format_keys())
                      if (dec := _plain_decimals(d.number_format(key))) is not None and dec >= need)
        if fits:
            return fits[0][2], f"most values are below 1 and need {need} decimals"
        return "general", f"most values are below 1 and need {need} decimals, more than any number format shows"
    return "amount2", "the values hold decimals"


def _dictionary(root: Path) -> Any:
    """The style dictionary of the model folder (its style_overrides.json applied), or the plugin's dictionary
    when the override cannot be read (build reports that override itself)."""
    from . import dictionary
    try:
        return dictionary.load_for_model(root)
    except common.Refusal:
        return dictionary.load_dictionary()


def _format_words(d: Any, key: str) -> str:
    """'2 decimals', or what a format that is not a plain number format is."""
    dec = _plain_decimals(d.number_format(key))
    if dec is not None:
        return f"{dec} decimal{'s' if dec != 1 else ''}"
    return "General, Excel's default" if key == "general" else d.number_format(key)


def _key_text(tup: Sequence[Any]) -> tuple[Any, ...]:
    """A key as land, check and the workbook compare it (common.key_tuple): exact text, case-sensitive, no
    wildcard, text never read as a number."""
    return common.key_tuple(tup)


def _unique(columns: Sequence[list[Any]]) -> bool:
    seen: set[tuple[Any, ...]] = set()
    for tup in zip(*columns):
        if any(v is None for v in tup):
            return False
        key = _key_text(tup)
        if key in seen:
            return False
        seen.add(key)
    return True


def _repeats(columns: Sequence[list[Any]]) -> list[tuple[int, int, str]]:
    """(earlier line, line, key text) for every sample row whose key equals an earlier row's key (lines of the CSV
    file: the header is line 1). Rows with an empty key part are skipped."""
    seen: dict[tuple[Any, ...], int] = {}
    out: list[tuple[int, int, str]] = []
    for i, tup in enumerate(zip(*columns)):
        if any(v is None for v in tup):
            continue
        key = _key_text(tup)
        if key in seen:
            out.append((seen[key] + 2, i + 2, " + ".join(value_text(v) for v in tup)))
        else:
            seen[key] = i
    return out


def key_candidates(profile: dict[str, Any], types: Sequence[str]) -> list[list[str]]:
    """Columns, or pairs or triples of columns, whose values are never null and unique in the sample.
    Single columns are tried among text and integer columns; pairs and triples also use date columns, among the
    first 8 eligible columns (a number column such as an amount is not proposed as a key)."""
    cols = profile["columns"]
    if not cols or profile["profiled_rows"] == 0:
        return []
    typed = [_typed_column(c, t)[0] for c, t in zip(cols, types)]
    out: list[list[str]] = []
    single = [i for i, t in enumerate(types) if t in ("text", "integer") and cols[i]["nulls"] == 0]
    for i in single:
        if _unique([typed[i]]):
            out.append([cols[i]["name"]])
    if out:
        return out[:MAX_KEY_CANDIDATES]
    eligible = [i for i, t in enumerate(types) if t in ("text", "integer", "date") and cols[i]["nulls"] == 0]
    eligible = eligible[:KEY_PAIR_COLUMNS]
    for size in (2, 3):
        for combo in combinations(eligible, size):
            if _unique([typed[i] for i in combo]):
                out.append([cols[i]["name"] for i in combo])
                if len(out) >= MAX_KEY_CANDIDATES:
                    return out
        if out:
            return out
    return out


def infer_columns(csv_path: str | Path, *, max_rows: int = PROFILE_MAX_ROWS) -> list[dict[str, Any]]:
    """Column proposals from a CSV (design 15): name, type, null rule, allowed values (text columns outside the key
    with 2 to 12 distinct values that repeat), the source expression placeholder (the sample's header) and the observed
    sample statistics. Key candidates are in profile_csv() / key_candidates()."""
    prof = profile_csv(csv_path, max_rows=max_rows)
    types = [c["type"] for c in prof["columns"]]
    keys = key_candidates(prof, types)
    key_cols = set(keys[0]) if keys else set()
    return [_column_proposal(c, c["type"], in_key=c["name"] in key_cols) for c in prof["columns"]]


def _column_proposal(col: dict[str, Any], ctype: str, *, in_key: bool, unit: str | None = None,
                     null_rule: str | None = None) -> dict[str, Any]:
    allowed = None
    if (ctype == "text" and not in_key and 2 <= col["distinct"] <= MAX_ALLOWED_VALUES
            and col["non_null"] > col["distinct"]):
        allowed = sorted(col["_distinct"])
    if unit is None and ctype in ("date", "datetime"):
        unit = ctype
    return {"name": col["name"], "type": ctype, "unit": unit,
            "null_rule": null_rule or ("never" if col["nulls"] == 0 else "allowed"),
            "allowed": allowed, "source_expression": source_identifier((col["header"] or "").strip() or col["name"]),
            "observed": _observed(col, ctype)}


def source_identifier(header: str) -> str:
    """The source expression `propose` writes for a column it takes from a header (or a --columns name): the header
    as a GoogleSQL identifier, bare when it is a plain identifier (^[A-Za-z_][A-Za-z0-9_]*$) and not a GoogleSQL
    reserved keyword (land.GOOGLESQL_RESERVED), else in backticks: `order`, `Order Date` (round 3 verdict,
    A-aera-11). A source expression the user writes into the contract is used as written."""
    from . import land
    return land.googlesql_identifier(header)


# ----------------------------------------------------------------------------- the model folder

def _model_root(model_root: str | Path, *, create: bool) -> tuple[Path, bool]:
    """(root, created). With `create`, a folder without the marker gets one (design D3)."""
    r = Path(os.path.abspath(common.native_path(model_root)))
    if modelfolder.is_model_root(r):
        return r, False
    if not create:
        outer = modelfolder.find_model_root(r)
        hint = f"; it lies inside the model folder {outer.as_posix()}" if outer else ""
        raise common.Refusal(f"{r.as_posix()} is not a model folder (no {modelfolder.MARKER}){hint}",
                             fix=(f"give the model folder {outer.as_posix()}" if outer else
                                  f"run rotm.py init \"{r.as_posix()}\" or rotm.py contract propose first"))
    modelfolder.init_model(r)
    return r, True


def _check_table(table: str) -> None:
    if not isinstance(table, str) or not _TABLE_RE.match(table):
        raise common.Refusal(f"table name '{table}' is not allowed",
                             fix="use lower-case letters, digits and _, starting with a letter, at most 20 characters "
                                 "(e.g. sales)")


def _parse_rows(rows: str | None) -> dict[str, int] | None:
    if rows is None:
        return None
    text = str(rows).strip().replace(",", "").replace("_", "")
    m = re.fullmatch(r"(\d+)", text)
    if m:
        return {"exact": int(m.group(1))}
    m = re.fullmatch(r"(\d+)\s*[:\-]\s*(\d+)", text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            raise common.Refusal(f"--rows {rows}: the minimum {lo:,} is greater than the maximum {hi:,}",
                                 fix="write MIN:MAX with MIN at most MAX, e.g. --rows 1:100000")
        return {"min": lo, "max": hi}
    raise common.Refusal(f"--rows '{rows}' is not a row count", fix="write N (exact) or MIN:MAX, e.g. --rows 600 or --rows 1:100000")


def _split_list(text: str | Sequence[str] | None) -> list[str]:
    if text is None:
        return []
    if isinstance(text, str):
        return [t.strip() for t in text.split(",") if t.strip()]
    return [str(t).strip() for t in text if str(t).strip()]


def _parse_pairs(items: Sequence[str] | dict[str, Any] | None, flag: str) -> dict[str, str]:
    """['amount=KRW', ...] -> {'amount': 'KRW'} (a dict is passed through)."""
    if not items:
        return {}
    if isinstance(items, dict):
        return {str(k): str(v) for k, v in items.items()}
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise common.Refusal(f"{flag} '{item}' is not COLUMN=VALUE", fix=f"write {flag} column=value, e.g. {flag} amount=KRW")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _near(name: str, names: Sequence[str]) -> str:
    n = schemas.nearest(name, names)
    return f"; did you mean '{n}'?" if n else ""


def _check_column_refs(names: Sequence[str], refs: Iterable[str], flag: str) -> None:
    for ref in refs:
        if ref not in names:
            raise common.Refusal(f"{flag}: '{ref}' is not a column of this contract{_near(ref, names)}",
                                 fix="use one of: " + ", ".join(names))


def _fmt_int(n: int) -> str:
    return f"{n:,}"


# ----------------------------------------------------------------------------- versions

_CONTENT_KEYS = ("grain", "key", "columns", "expect", "source", "refresh")
_COLUMN_FIELDS = ("type", "unit", "null_rule", "known_nulls", "allowed", "source_expression", "format")


def _agreed_content(contract: dict[str, Any]) -> dict[str, Any]:
    """The parts of a contract an agreement covers, kept in the history when it is replaced (observed sample
    statistics and descriptions left out)."""
    out: dict[str, Any] = {}
    for k in _CONTENT_KEYS:
        if k not in contract:
            continue
        if k == "columns":
            out[k] = [{f: c.get(f) for f in ("name",) + _COLUMN_FIELDS if f in c} for c in contract[k]]
        else:
            out[k] = json.loads(json.dumps(contract[k], ensure_ascii=False, default=str))
    return out


def _history_entry(contract: dict[str, Any], state: str) -> dict[str, Any]:
    """The record of a replaced agreement. The agreed content is kept only when the contract still holds it
    (state agreed); a stale contract was edited after the agreement, so what was agreed is not known."""
    old = contract.get("agreement") or {}
    entry = {k: old.get(k) for k in ("status", "by", "at", "channel", "reply")}
    entry.update({"version": old.get("version") or contract.get("version") or 1,
                  "contract_sha256": old.get("contract_sha256"), "replaced_at": common.now_iso(), "state": state})
    if state == "agreed":
        entry["agreed_content"] = _agreed_content(contract)
    return entry


def changes_between(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """What changed from an agreed version (its agreed_content) to a contract, one line per change."""
    cur = _agreed_content(new)
    out: list[str] = []
    for k in ("grain", "key"):
        if old.get(k) != cur.get(k):
            out.append(f"{k}: {_brief(old.get(k))} -> {_brief(cur.get(k))}")
    old_cols = {c["name"]: c for c in old.get("columns") or []}
    new_cols = {c["name"]: c for c in cur.get("columns") or []}
    for name in new_cols:
        if name not in old_cols:
            out.append(f"column {name} added")
    for name in old_cols:
        if name not in new_cols:
            out.append(f"column {name} removed")
    for name, c in new_cols.items():
        o = old_cols.get(name)
        if o is None:
            continue
        for f in _COLUMN_FIELDS:
            if o.get(f) != c.get(f):
                out.append(f"column {name} {f}: {_brief(o.get(f))} -> {_brief(c.get(f))}")
    if [c["name"] for c in old.get("columns") or [] if c["name"] in new_cols] != \
            [c["name"] for c in cur.get("columns") or [] if c["name"] in old_cols]:
        out.append("column order changed")
    for k in ("expect", "source", "refresh"):
        o, c = old.get(k) or {}, cur.get(k) or {}
        for sub in sorted(set(o) | set(c)):
            if o.get(sub) != c.get(sub):
                out.append(f"{k}.{sub}: {_brief(o.get(sub))} -> {_brief(c.get(sub))}")
    return out


def _brief(value: Any) -> str:
    if value is None:
        return "none"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 80 else text[:77] + "..."


def last_agreed(contract: dict[str, Any]) -> dict[str, Any] | None:
    """The newest earlier agreement kept in the history that holds its content, or None."""
    for entry in reversed((contract.get("agreement") or {}).get("history") or []):
        if isinstance(entry, dict) and entry.get("status") == "agreed" and isinstance(entry.get("agreed_content"), dict):
            return entry
    return None


# ----------------------------------------------------------------------------- propose

def propose(model_root: str | Path, table: str, *, csv: str | Path | None = None,
            columns: Sequence[str] | str | None = None, key: Sequence[str] | str | None = None,
            grain: str | None = None, source: str | None = None, rows: str | None = None, force: bool = False,
            purpose: str | None = None, connector: str | None = None, system: str | None = None,
            where: str | None = None, units: Sequence[str] | dict[str, str] | None = None,
            types: Sequence[str] | dict[str, str] | None = None, nulls: Sequence[str] | dict[str, str] | None = None,
            totals: Sequence[str] | None = None, capacity: int | None = None, allow_empty: bool = False,
            max_rows: int = PROFILE_MAX_ROWS, key_repeats: int | None = None,
            known_nulls: Sequence[str] | dict[str, str] | None = None) -> Path:
    """Write contracts/<table>.contract.json (status proposed) and .contract.md; returns the JSON path.
    `force` replaces an existing contract; on an agreed contract it resets the agreement to proposed and keeps
    the earlier agreement (with the content it agreed) in agreement.history. Every proposal gets a version
    number: 1, then one more on each `force`.

    Known exceptions (references/column-contract.md): `key_repeats` N lets at most N rows repeat an earlier key;
    `known_nulls` {column: N} lets a never-null column be empty in at most N rows. Both keep their check in the
    workbook and in land and check; nothing is dropped. When the sample breaks the key the user gave, or a column is
    empty in at most 1% of 100 or more sample rows, propose writes them from the sample and says so in the notes."""
    _check_table(table)
    if key_repeats is not None and (isinstance(key_repeats, bool) or int(key_repeats) < 0):
        raise common.Refusal(f"--key-repeats {key_repeats}: give a whole number, 0 or more",
                             fix="write --key-repeats N, the number of rows that repeat an earlier key and are kept")
    if csv is not None and columns:
        raise common.Refusal("give --csv or --columns, not both",
                             fix="use --csv for a sample file, or --columns for a list of names when there is no file")
    if csv is None and not columns:
        raise common.Refusal("give the columns: --csv <sample.csv> or --columns a,b,c",
                             fix="pass a sample CSV of the raw table, or the column names (name or name:type)")
    if connector is not None and connector not in schemas.CONNECTORS:
        raise common.Refusal(f"--connector '{connector}' is not known{_near(connector, schemas.CONNECTORS)}",
                             fix="use one of: " + ", ".join(schemas.CONNECTORS))
    root = Path(os.path.abspath(common.native_path(model_root)))
    if root.exists() and not root.is_dir():
        raise common.Refusal(f"{root.as_posix()} is a file, not a folder", fix="give the model folder")
    cpath = modelfolder.contract_path(root, table)
    old_state, old_data, _old_problems = ("missing", None, [])
    if modelfolder.is_model_root(root):
        old_state, old_data, _old_problems = modelfolder.read_contract(root, table)
    if old_state != "missing" and not force:
        raise common.Refusal(f"contracts/{table}.contract.json exists (state {old_state}); nothing was written",
                             fix="pass --force to replace it (an agreed contract goes back to proposed), or show it "
                                 f"with rotm.py contract show \"{root.as_posix()}\" --table {table}")
    if csv is not None:
        csv_path = Path(os.path.abspath(common.native_path(csv)))
        _read_csv_rows(csv_path, 1)   # refuse a bad file before the folder is touched
    root, created = _model_root(root, create=True)
    notes: list[str] = []
    unit_map = _parse_pairs(units, "--unit")
    type_map = _parse_pairs(types, "--type")
    null_map = _parse_pairs(nulls, "--null")
    for col, t in type_map.items():
        if t not in schemas.COLUMN_TYPES:
            raise common.Refusal(f"--type {col}={t}: '{t}' is not a column type{_near(t, schemas.COLUMN_TYPES)}",
                                 fix="use one of: " + ", ".join(schemas.COLUMN_TYPES))
    for col, r in null_map.items():
        if r not in schemas.NULL_RULES:
            raise common.Refusal(f"--null {col}={r}: '{r}' is not a null rule", fix="use never or allowed")
    known_map: dict[str, int] = {}
    for col, n_text in _parse_pairs(known_nulls, "--known-nulls").items():
        try:
            n_known = int(str(n_text).replace(",", ""))
        except ValueError:
            raise common.Refusal(f"--known-nulls {col}={n_text}: '{n_text}' is not a whole number",
                                 fix="write --known-nulls column=N, e.g. --known-nulls region=1") from None
        if n_known < 1:
            raise common.Refusal(f"--known-nulls {col}={n_text}: give 1 or more",
                                 fix="leave the flag out when no empty cell is known")
        if null_map.get(col) == "allowed":
            raise common.Refusal(f"--known-nulls {col} and --null {col}=allowed contradict each other",
                                 fix="known empty cells belong to a never-null column: drop --null "
                                     f"{col}=allowed (or drop --known-nulls when empty values are normal)")
        known_map[col] = n_known
    profile: dict[str, Any] | None = None
    cols: list[dict[str, Any]] = []
    candidates: list[list[str]] = []
    if csv is not None:
        profile = profile_csv(csv_path, max_rows=max_rows)
        notes += profile["notes"]
        names = profile["names"]
        _check_column_refs(names, list(type_map) + list(unit_map) + list(null_map) + list(known_map),
                           "--type/--unit/--null/--known-nulls")
        final_types = [type_map.get(c["name"], c["type"]) for c in profile["columns"]]
        candidates = key_candidates(profile, final_types)
        key_list = _split_list(key)
        _check_column_refs(names, key_list, "--key")
        chosen_key = key_list or (candidates[0] if candidates else [])
        shown: list[str] = []
        dic = _dictionary(root) if any(t in ("integer", "number") for t in final_types) else None
        for c, t in zip(profile["columns"], final_types):
            proposal = _column_proposal(c, t, in_key=c["name"] in chosen_key, unit=unit_map.get(c["name"]),
                                        null_rule=null_map.get(c["name"]))
            if dic is not None and t in ("integer", "number"):
                fmt, why = proposed_format(_typed_column(c, t)[0], t, dic)
                proposal["format"] = fmt
                shown.append(f"{c['name']} {fmt} ({_format_words(dic, fmt)}; {why})")
            cols.append(proposal)
            if proposal["observed"].get("off_type"):
                notes.append(f"column '{c['name']}': {proposal['observed']['off_type']} sample values are not "
                             f"{t}; land and check report them until the source sends {t} values")
        if shown:
            notes.append("display formats proposed from the sample (keys of the style dictionary's number_formats, "
                         "column 'Format' above): " + "; ".join(shown) + ". Say another key if a column should show "
                         "more or fewer decimals")
        if candidates:
            notes.append("key candidates in the sample (unique and never null): "
                         + "; ".join(" + ".join(c) for c in candidates))
        if profile["truncated"]:
            notes.append(f"the sample has {_fmt_int(profile['rows'])} rows; the first {_fmt_int(profile['profiled_rows'])} "
                         "were profiled")
    else:
        specs = _split_list(columns)
        names = []
        for i, item in enumerate(specs):
            parts = [x.strip() for x in item.split(":")]
            name = parts[0]
            ctype = parts[1] if len(parts) > 1 and parts[1] else type_map.get(name, "text")
            if not _COLUMN_RE.match(name):
                raise common.Refusal(f"--columns: '{name}' is not a valid column name",
                                     fix="use letters, digits and _ only, starting with a letter, at most 41 "
                                         f"characters, no spaces (e.g. {clean_name(name, i)})")
            if ctype not in schemas.COLUMN_TYPES:
                raise common.Refusal(f"--columns: '{item}': '{ctype}' is not a column type"
                                     f"{_near(ctype, schemas.COLUMN_TYPES)}",
                                     fix="write name or name:type with a type of: " + ", ".join(schemas.COLUMN_TYPES))
            if name.lower() in {n.lower() for n in names}:
                raise common.Refusal(f"--columns: '{name}' is given twice", fix="give every column a different name")
            names.append(name)
            unit = unit_map.get(name) or (ctype if ctype in ("date", "datetime") else None)
            cols.append({"name": name, "type": ctype, "unit": unit, "null_rule": null_map.get(name, "allowed"),
                         "allowed": None, "source_expression": source_identifier(name)})
        _check_column_refs(names, list(type_map) + list(unit_map) + list(null_map) + list(known_map),
                           "--type/--unit/--null/--known-nulls")
        key_list = _split_list(key)
        _check_column_refs(names, key_list, "--key")
        chosen_key = key_list or [names[0]]
        for c in cols:
            if c["name"] in chosen_key and c["name"] not in null_map:
                c["null_rule"] = "never"
        cols[0]["null_rule"] = null_map.get(cols[0]["name"], "never")
        if not key_list:
            notes.append(f"key proposed as the first column '{names[0]}': say which columns identify one row")
        notes.append("types, units and null rules were not inferred (no sample CSV): check each column")
    # The key.
    if not chosen_key:
        chosen_key = [cols[0]["name"]]
        if profile is not None and profile["profiled_rows"]:
            notes.append("no column, pair or triple of columns is unique and never null in the sample; the key below "
                         f"is a guess ('{chosen_key[0]}'): say which columns identify one row")
    elif profile is not None and not _split_list(key):
        notes.append(f"key proposed from the sample: {' + '.join(chosen_key)} is unique and never null in "
                     f"{_fmt_int(profile['profiled_rows'])} rows")
    repeats_found: list[tuple[int, int, str]] = []
    if profile is not None and _split_list(key):
        typed_key = [_typed_column(profile["columns"][names.index(k)], cols[names.index(k)]["type"])[0] for k in chosen_key]
        if profile["profiled_rows"] and not _unique(typed_key):
            repeats_found = _repeats(typed_key)
            if not repeats_found:
                notes.append(f"the key {' + '.join(chosen_key)} has empty values in the sample; every key column "
                             "needs a value in every row: say which columns identify one row")
    # Known exceptions: empty cells in a never-null column, and rows that repeat an earlier key (kept, checked).
    by_name = {c["name"]: c for c in cols}
    sample_rows = profile["profiled_rows"] if profile is not None else 0
    for col_name, n_known in known_map.items():
        c = by_name[col_name]
        c["null_rule"] = "never"
        c["known_nulls"] = {"max": n_known, "note": f"agreed with --known-nulls {col_name}={n_known}"}
    if profile is not None:
        for c in cols[1:]:
            obs_nulls = (c.get("observed") or {}).get("nulls", 0)
            if not obs_nulls or c["name"] in chosen_key:
                continue
            lines_txt = ", ".join(str(i + 2) for i, v in enumerate(profile["columns"][names.index(c["name"])]["_values"])
                                  if is_null(v) or str(v).strip() == "")
            lines_txt = ", ".join(lines_txt.split(", ")[:5])
            if c["name"] in known_map:
                if obs_nulls > known_map[c["name"]]:
                    notes.append(f"column '{c['name']}' is empty on {_fmt_int(obs_nulls)} sample rows but "
                                 f"--known-nulls gives {known_map[c['name']]}: land will refuse this sample")
                continue
            if c["name"] in null_map:
                if null_map[c["name"]] == "allowed":
                    notes.append(f"column '{c['name']}' is empty on {_fmt_int(obs_nulls)} of {_fmt_int(sample_rows)} "
                                 f"sample rows (lines {lines_txt}); its null rule is allowed, so the workbook has no "
                                 f"null check for it. To keep the check and still land these rows, propose with "
                                 f"--known-nulls {c['name']}={obs_nulls} instead")
                continue
            if sample_rows >= 100 and obs_nulls * 100 <= sample_rows:
                c["null_rule"] = "never"
                where_txt = ("line " if obs_nulls == 1 else "lines ") + lines_txt
                c["known_nulls"] = {"max": obs_nulls, "note": f"empty on {obs_nulls} of {sample_rows} sample rows "
                                                              f"({where_txt})"}
                notes.append(f"column '{c['name']}' is empty on {_fmt_int(obs_nulls)} of {_fmt_int(sample_rows)} "
                             f"sample rows ({where_txt}): proposed as never null with {obs_nulls} empty "
                             f"cell(s) agreed as known, so the rows are landed and the workbook keeps a null check "
                             f"(it holds while at most {obs_nulls} are empty); say 'allowed' if empty values are "
                             "normal for this column, or have the source fill them")
    if repeats_found:
        ex = "; ".join(f"lines {a} and {b}: {k}" for a, b, k in repeats_found[:5])
        if key_repeats is None:
            key_repeats = len(repeats_found)
            notes.append(f"the key {' + '.join(chosen_key)} repeats on {_fmt_int(len(repeats_found))} sample row(s) "
                         f"({ex}; keys compared exactly, upper and lower case distinct): proposed as "
                         f"{len(repeats_found)} known "
                         "repeat(s), so every delivered row is landed and the workbook's key check holds while at "
                         f"most {len(repeats_found)} row(s) repeat an earlier key. Say whether the repeated rows are "
                         "real (keep them) or copies the source should remove; or agree another key")
        elif key_repeats < len(repeats_found):
            notes.append(f"the key {' + '.join(chosen_key)} repeats on {_fmt_int(len(repeats_found))} sample row(s) "
                         f"({ex}) but --key-repeats gives {key_repeats}: land will refuse this sample")
    elif key_repeats and profile is not None and profile["profiled_rows"]:
        notes.append(f"--key-repeats {key_repeats}: the key does not repeat in the sample")
    # The first column must never be null (it decides which raw rows exist, design 7.1).
    if cols[0]["null_rule"] != "never" or cols[0].get("known_nulls"):
        move = next((i for i, c in enumerate(cols) if c["name"] in chosen_key and c["null_rule"] == "never"
                     and not c.get("known_nulls")), None)
        if move is None:
            move = next((i for i, c in enumerate(cols) if c["null_rule"] == "never" and not c.get("known_nulls")),
                        None)
        if move is not None:
            moved = cols.pop(move)
            cols.insert(0, moved)
            notes.append(f"column '{moved['name']}' was moved first: the first column must never be null, because the "
                         "helper sheet reads it to decide which raw rows exist")
        else:
            raise common.Refusal(f"every column of '{table}' has nulls in the sample, but the first column must never "
                                 "be null (it decides which raw rows exist)",
                                 fix="give a column that is never null, e.g. --null <column>=never, or a better sample")
    # Units still missing.
    no_unit = [c["name"] for c in cols if c["type"] in ("integer", "number") and not c.get("unit")]
    if no_unit:
        notes.append("unit not known for " + ", ".join(no_unit) + ": say each unit (for example KRW, count, kg), "
                     "or pass --unit column=unit")
    # Grain.
    if not grain:
        grain = f"one row per {' and '.join(chosen_key)}"
        notes.append("grain proposed from the key: say what one row is (for example 'one row per invoice line')")
    # Expected rows.
    expect: dict[str, Any] = {}
    rows_spec = _parse_rows(rows)
    if rows_spec is None:
        if profile is not None:
            n = profile["rows"]
            hi = min(EXCEL_MAX_DATA_ROWS, max(MIN_PROPOSED_MAX, n * ROWS_FACTOR))
            rows_spec = {"min": 0 if allow_empty else 1, "max": hi}
            notes.append(f"expected rows proposed from the sample ({_fmt_int(n)} rows): from {rows_spec['min']} to "
                         f"{_fmt_int(hi)} (at most {ROWS_FACTOR} times the sample, at least {_fmt_int(MIN_PROPOSED_MAX)}); "
                         "give the real count or range (--rows N or MIN:MAX)")
        else:
            rows_spec = {"min": 0 if allow_empty else 1, "max": EXCEL_MAX_DATA_ROWS}
            notes.append(f"expected rows not given: from {rows_spec['min']} to {_fmt_int(EXCEL_MAX_DATA_ROWS)} "
                         "(the most one sheet holds); give the real count or range (--rows N or MIN:MAX)")
    for k in ("exact", "max"):
        if k in rows_spec and rows_spec[k] > EXCEL_MAX_DATA_ROWS:
            raise common.Refusal(f"--rows: {rows_spec[k]:,} rows do not fit on one sheet "
                                 f"(at most {_fmt_int(EXCEL_MAX_DATA_ROWS)} data rows)",
                                 fix="filter or aggregate the source in SQL to the contracted grain, or split the table")
    expect["rows"] = rows_spec
    if key_repeats:
        if repeats_found:
            ex = "; ".join(f"lines {a} and {b}: {k}" for a, b, k in repeats_found[:5])
            expect["key_repeats"] = {"max": int(key_repeats),
                                     "note": f"{len(repeats_found)} sample row(s) repeat an earlier key ({ex})"}
        else:
            expect["key_repeats"] = {"max": int(key_repeats), "note": f"agreed with --key-repeats {int(key_repeats)}"}
    total_list = []
    final_names = [c["name"] for c in cols]
    for item in totals or []:
        if "=" not in item:
            raise common.Refusal(f"--total '{item}' is not COLUMN=VALUE[:TOLERANCE]",
                                 fix="write --total amount=1234567.5 or --total amount=1234567.5:0.5")
        col, val = item.split("=", 1)
        col = col.strip()
        _check_column_refs(final_names, [col], "--total")
        tol = 0.0
        if ":" in val:
            val, tol_text = val.split(":", 1)
            try:
                tol = float(tol_text.replace(",", ""))
            except ValueError:
                raise common.Refusal(f"--total {item}: tolerance '{tol_text}' is not a number",
                                     fix="write the tolerance as a number, e.g. :0.5") from None
        try:
            value = float(val.replace(",", ""))
        except ValueError:
            raise common.Refusal(f"--total {item}: '{val}' is not a number", fix="write the source-side total as a number") from None
        entry: dict[str, Any] = {"column": col, "value": int(value) if value.is_integer() else value,
                                 "tolerance": int(tol) if float(tol).is_integer() else tol,
                                 "note": "source-side control total given with --total"}
        total_list.append(entry)
    if total_list:
        expect["totals"] = total_list
    elif any(c["type"] in ("integer", "number") for c in cols):
        notes.append("no control total: for a number column, give the source system's total (--total column=value); "
                     "a total computed from the landed rows proves nothing (researcher 2 section 4)")
    # Source.
    src: dict[str, Any] = {}
    if connector:
        src["connector"] = connector
    elif source is None and csv is not None:
        src["connector"] = "csv"
    src["system"] = system
    if source:
        src["object"] = source if not re.search(r"\s", source.strip()) else None
        src["from"] = source
    else:
        src["object"] = Path(csv_path).name if csv is not None else None
        src["from"] = None
        if csv is not None:
            notes.append("source expressions are the sample's headers (in backticks when a header is a GoogleSQL "
                         "reserved keyword or not a plain name, for example `order` or `Order Date`): replace each "
                         "with the SQL expression that produces the column (for example CAST(i.invoice_id AS STRING)) "
                         "when the data comes from SQL")
    src["where"] = where
    # A destination column named like a helper column build adds: note N-HELPER-NAME (A-aera-12); build refuses it.
    notes += [helper_name_note(table, name, helper) for name, helper in helper_name_clashes(cols)]
    contract: dict[str, Any] = {
        "rotm_contract": 1, "table": table, "raw_sheet": f"raw_{table}", "excel_table": f"tbl_raw_{table}",
        "purpose": purpose or "", "source": src, "grain": grain, "key": list(chosen_key), "columns": cols,
        "expect": expect,
        "refresh": {"frequency": None, "capacity_rows": int(capacity) if capacity else None,
                    "allow_empty": bool(allow_empty)},
        "notes": notes, "proposed_at": common.now_iso(), "proposed_by": "rotm.py contract propose",
    }
    if profile is not None:
        contract["description"] = (f"proposed from the sample {profile['csv_name']} "
                                   f"({_fmt_int(profile['rows'])} rows, {len(cols)} columns)")
    old_version = int((old_data or {}).get("version") or 1) if isinstance(old_data, dict) else 0
    contract["version"] = old_version + 1 if old_data else 1
    agreement: dict[str, Any] = {"status": "proposed", "contract_sha256": None, "reply": None, "by": None, "at": None,
                                 "channel": None, "source_file": None, "recorded_by": None,
                                 "note": "not agreed: show the user the .contract.md and record their reply with "
                                         "rotm.py contract agree"}
    if old_data and isinstance(old_data.get("agreement"), dict):
        old = old_data["agreement"]
        history = list(old.get("history") or [])
        if old.get("status") == "agreed":
            history.append(_history_entry(old_data, old_state))
        if history:
            agreement["history"] = history
    contract["agreement"] = agreement
    agreement["shown_sha256"] = shown_hash(contract)    # the .md below is rendered from this content (C-sol-3)
    schemas.require_valid(contract, "contract", cpath.name)
    common.write_json(cpath, contract)
    write_md(root, contract)
    return cpath


# ----------------------------------------------------------------------------- the readable copy

def _cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    text = value_text(value) if not isinstance(value, str) else value
    return text.replace("|", "\\|").replace("\n", " ")


def _num(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer() and abs(value) < 1e15):
        return f"{int(value):,}"
    if isinstance(value, float):
        return f"{value:,.6f}".rstrip("0").rstrip(".")
    return str(value)


def render_md(contract: dict[str, Any]) -> str:
    """The readable contract the agent shows the user (design section 5, last bullet)."""
    t = contract["table"]
    lines = [f"# Column contract: {t}", ""]
    if contract.get("purpose"):
        lines.append(f"- Purpose: {contract['purpose']}")
    src = contract.get("source") or {}
    if src:
        bits = [f"{k} {src[k]}" for k in ("connector", "system", "object", "from", "where") if src.get(k)]
        lines.append("- Source: " + ("; ".join(bits) if bits else "not given"))
    lines.append(f"- Grain: {contract['grain']}")
    known_repeats = isinstance((contract.get("expect") or {}).get("key_repeats"), dict)
    lines.append(f"- Key: {', '.join(contract['key'])} (unique on every row"
                 + (", except the known repeats below)" if known_repeats else ")"))
    lines.append(f"- Raw sheet: {contract.get('raw_sheet', 'raw_' + t)} (Excel table "
                 f"{contract.get('excel_table', 'tbl_raw_' + t)}): header in row 1 from A1, one record per row from "
                 "row 2, landed values only")
    if contract.get("description"):
        lines.append(f"- Proposal: {contract['description']}")
    if contract.get("version"):
        lines.append(f"- Version: {contract['version']}")
    lines += ["", "| # | Column | Type | Unit | Format | Null rule | Allowed values | Source expression | Description |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for i, c in enumerate(contract["columns"], 1):
        unit = c.get("unit")
        if not unit:
            unit = "?" if c["type"] in ("integer", "number") else None
        allowed = ", ".join(value_text(a) for a in c["allowed"]) if c.get("allowed") else None
        lines.append(f"| {i} | {_cell(c['name'])} | {c['type']} | {_cell(unit)} | {_cell(c.get('format') or 'default')} "
                     f"| {c['null_rule']} | {_cell(allowed)} | {_cell(c.get('source_expression'))} | "
                     f"{_cell(c.get('description'))} |")
    lines.append("")
    lines.append("- Format: the style dictionary key of the column's display format on the raw sheet; default means "
                 "the format of the column's type.")
    expect = contract.get("expect") or {}
    rows = expect.get("rows")
    if rows:
        if "exact" in rows:
            lines.append(f"- Expected rows: exactly {_num(rows['exact'])}")
        else:
            lines.append(f"- Expected rows: from {_num(rows['min'])} to {_num(rows['max'])}")
    totals = expect.get("totals") or []
    if totals:
        for tot in totals:
            lines.append(f"- Control total: sum of {tot['column']} = {_num(tot['value'])} "
                         f"(tolerance {_num(tot.get('tolerance', 0))})")
    else:
        lines.append("- Control total: none")
    kr = expect.get("key_repeats")
    if isinstance(kr, dict):
        lines.append(f"- Known exception: at most {_num(kr['max'])} row(s) repeat an earlier key; they are landed and "
                     f"counted by the key check ({kr.get('note') or 'no note'})")
    for c in contract["columns"]:
        kn = c.get("known_nulls")
        if isinstance(kn, dict):
            lines.append(f"- Known exception: {c['name']} is empty in at most {_num(kn['max'])} row(s); they are "
                         f"landed and counted by the null check ({kn.get('note') or 'no note'})")
    refresh = contract.get("refresh") or {}
    rbits = []
    if refresh.get("frequency"):
        rbits.append(f"refreshed {refresh['frequency']}")
    if refresh.get("capacity_rows"):
        rbits.append(f"helper rows for {_num(refresh['capacity_rows'])} raw rows")
    rbits.append("an empty result is allowed" if refresh.get("allow_empty") else "an empty result is not allowed")
    lines.append("- Refresh: " + "; ".join(rbits))
    observed = [c for c in contract["columns"] if isinstance(c.get("observed"), dict)]
    if observed:
        lines += ["", "Sample profile (describes the sample only; these numbers are not controls):", "",
                  "| Column | Rows | Nulls | Distinct | Min | Max | Sum | Examples |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for c in observed:
            o = c["observed"]
            ex = ", ".join(o.get("examples") or [])
            off = f" ({o['off_type']} not {c['type']})" if o.get("off_type") else ""
            lines.append(f"| {_cell(c['name'])} | {_num(o.get('sample_rows', 0))} | {_num(o.get('nulls', 0))}{off} | "
                         f"{_num(o.get('distinct', 0))} | {_cell(o.get('min'))} | {_cell(o.get('max'))} | "
                         f"{_cell(_num(o['sum']) if 'sum' in o else None)} | {_cell(ex)} |")
    notes = contract.get("notes") or []
    if notes:
        lines += ["", "Points to confirm:", ""]
        lines += [f"- {n}" for n in notes]
    prev = last_agreed(contract)
    if prev is not None:
        changes = changes_between(prev["agreed_content"], contract)
        lines += ["", f"Changes since version {prev.get('version') or 1}, agreed by {prev.get('by')} on "
                      f"{prev.get('at')}:", ""]
        lines += [f"- {c}" for c in changes] if changes else ["- none in the agreed parts"]
    lines.append("")
    ag = contract.get("agreement") or {}
    state = "agreed" if ag.get("status") == "agreed" else "proposed"
    if state == "agreed" and ag.get("contract_sha256") == modelfolder.contract_hash(contract):
        where = f" (task file {ag['source_file']})" if ag.get("channel") == "task-file" and ag.get("source_file") else ""
        reused = (f"; the same words as for version {ag['reply_reused_from']}" if ag.get("reply_reused_from")
                  else "")
        version = f" (contract version {ag['version']}{reused})" if ag.get("version") else ""
        lines.append(f"Agreed by {ag.get('by')} on {ag.get('at')} through {ag.get('channel')}{where}: "
                     f"'{ag.get('reply')}'{version}")
    else:
        lines.append("Reply 'agreed' or say what to change.")
    return "\n".join(lines) + "\n"


def md_path(model_root: str | Path, table: str) -> Path:
    return Path(model_root) / "contracts" / f"{table}.contract.md"


def write_md(model_root: str | Path, contract: dict[str, Any]) -> Path:
    path = md_path(model_root, contract["table"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(render_md(contract), encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path


def _rotm() -> str:
    """The rotm.py command line the messages print."""
    return f"python -P -X utf8 -B \"{(common.scripts_dir() / 'rotm.py').as_posix()}\""


def shown_hash(contract: dict[str, Any]) -> str:
    """The content hash recorded as agreement.shown_sha256 when the .md is rendered from `contract`: the function
    that gives agreement.contract_sha256 (modelfolder.contract_hash), on the contract as its JSON file holds it."""
    return modelfolder.contract_hash(json.loads(json.dumps(contract, ensure_ascii=False, default=str)))


# The last line of the .md says whether and by whom the contract is agreed (render_md); `rotm.py init --template`
# rewrites that line alone when it sets a copied contract back to proposed. It is not part of what is shown for
# agreement, so it is left out when the .md is compared with the JSON.
_STATUS_LINE_RE = re.compile(r"^(?:Agreed by |Agreed \(version |Reply 'agreed' or say what to change\.|"
                             r"Not agreed: copied from template )")


def _shown_text(text: str) -> str:
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if not _STATUS_LINE_RE.match(ln)]
    return "\n".join(lines).rstrip()


def shown_problem(root: Path, contract: dict[str, Any]) -> str | None:
    """Why contracts/<t>.contract.md does not show the current JSON (round 2 verdict, C-sol-3; refusal F-SHOWN), or
    None: the .md is missing; it differs from render_md(current JSON) (the status line at its end is not compared);
    or agreement.shown_sha256 is recorded and differs from the current content hash (an edit of a part the .md does
    not show). A contract without agreement.shown_sha256 (proposed before v3) is compared by the .md text only."""
    t = contract["table"]
    path = md_path(root, t)
    if not path.is_file():
        return f"contracts/{t}.contract.md is missing"
    try:
        current = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return f"contracts/{t}.contract.md cannot be read ({type(exc).__name__})"
    if _shown_text(current) != _shown_text(render_md(contract)):
        return (f"contracts/{t}.contract.md differs from the text rendered from contracts/{t}.contract.json (the "
                "JSON was edited after the .md was written, or the .md was edited by hand)")
    recorded = (contract.get("agreement") or {}).get("shown_sha256")
    now = shown_hash(contract)
    if recorded and recorded != now:
        return (f"contracts/{t}.contract.md was rendered from other content (agreement.shown_sha256 "
                f"sha256:{recorded[:12]}..., the JSON is now sha256:{now[:12]}...)")
    return None


# ----------------------------------------------------------------------------- agree

def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _quote(found: str) -> str:
    found = _collapse(found)
    return found if len(found) <= 40 else found[:37] + "..."


def _negated(text: str, start: int, end: int) -> bool:
    """Whether the change words text[start:end] are negated: a negative word among the 4 words before them in the
    same clause ('no need to change', "doesn't need to change", 'nothing should change'), or 'nothing' or
    'none' right after them ('please change nothing')."""
    clause = re.split(r"[.!?;,\n]", text[:start])[-1]
    if any(w.endswith("n't") or w.replace("'", "") in _NEGATORS for w in re.findall(r"[a-z']+", clause)[-4:]):
        return True
    return bool(re.match(r"\s+(?:nothing|none)\b", text[end:]))


def _asks_for_change(text: str) -> str | None:
    """The words of the first change request in a lower-case reply (CHANGE_PATTERNS, _KO_CHANGE), or None."""
    for pattern in CHANGE_PATTERNS:
        for m in pattern.finditer(text):
            if not _negated(text, m.start("w"), m.end("w")):
                return m.group("w")
    for m in _KO_CHANGE.finditer(text):
        after = re.split(r"[.!?,\n]", text[m.end():])[0]
        before = text[max(0, m.start() - 3):m.start()]
        if "없" in after or "않아도" in after or "안 해도" in after or re.search(r"안\s?$", before):
            continue                                     # 수정할 부분은 없습니다, 안 바꿔도 됩니다
        return m.group(0)
    return None


def _ends_as_question(text: str) -> bool:
    """Whether a sentence or clause ends with '?' or '？', or (after closing '.', '~' or '!') with a Korean question
    ending (KO_QUESTION_ENDINGS)."""
    t = text.rstrip()
    if t.endswith(("?", "？")):
        return True
    t = t.rstrip(".~!…。 \"')")
    return bool(t) and "가" <= t[-1] <= "힣" and t.endswith(KO_QUESTION_ENDINGS)


def _asks(clause: str) -> bool:
    """Whether a clause is part of a question: it starts with a question word (QUESTION_WORDS, after 'and', 'or',
    'but', ...) or ends as a question."""
    words = re.findall(r"[^\s,.;:!?？\"'()]+", clause.lower())
    while words and words[0] in _CONJUNCTIONS:
        words = words[1:]
    return (bool(words) and words[0] in QUESTION_WORDS) or _ends_as_question(clause)


def only_questions(reply: str) -> bool:
    """True when every sentence of the reply is a question and the reply holds no statement (round 4 verdict,
    A-aera-4 and C-sol-2). A question sentence holds a statement when a clause before its last one (split at
    ',', ';' or ':') is not part of the question (_asks): 'Looks good, can you go ahead?'."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?？。])\s+|\n+", (reply or "").strip()) if s.strip()]
    if not sents:
        return False
    for s in sents:
        if not _ends_as_question(s):
            return False
        clauses = [c for c in re.split(r"[,;:，、]", s) if c.strip()]
        if any(not _asks(c) for c in clauses[:-1]):
            return False
    return True


def reads_as_refusal(reply: str) -> str | None:
    """Why a reply reads as a refusal or a change request, or None. A reply that postpones the decision
    (POSTPONE_PATTERNS: 'will review', 'review it later', 'tomorrow', 'get back to you', 'need to check first',
    'not yet', 'let me check', 나중에, 내일, 검토 후, 확인해 볼게, 아직; round 5: 'hold off', 'hold on', 'please wait',
    'wait until', 'wait for', 보류, 기다려) gives no agreement; its reason starts with POSTPONE_REASON (round 3
    verdict, A-aera-4). 'hold off', 'hold on', 'wait until' and 'wait for' (UNLESS_NEGATED) do not count after a
    negative word in the same clause ('No need to wait for the totals'). A refusal is a reply whose first word is
    negative (NEGATIVE_FIRST_WORDS; 'No changes, ...' and 'No need to ...' are not refused by their first word) or
    that holds a refusal anywhere (REFUSAL_PATTERNS: 'not agreed', "don't agree", 'cannot agree', 'disagree',
    '동의하지 않', '동의 안', ...; round 5: reject, decline and refuse in their verb and noun forms, a negation followed
    by run, proceed, start, land, continue or go ahead, 거절, 반려, 거부, 하지 마). A change request is a reply that asks
    for a change (CHANGE_PATTERNS: 'please change', 'can you rename', 'should be changed', 'Replace the key ...',
    바꿔, 변경해, 수정해, 고쳐; not 'no changes', 'nothing to change', 'change nothing', 'without changes'); its reason
    starts with CHANGE_REASON (rule R-CHANGE). A reply in which every sentence is a question and that holds no
    statement (only_questions: 'What does the amount column include?', '금액 컬럼에 무엇이 들어가나요?') is no answer;
    its reason starts with QUESTION_REASON (round 4 verdict, A-aera-4 and C-sol-2). No word is required in an
    agreement and the reply need not name the table. The contract gate hook calls this function too."""
    text = re.sub(r"[‘’ʼ`]", "'", (reply or "").strip().lower())
    for pattern in POSTPONE_PATTERNS:
        for m in pattern.finditer(text):
            if pattern in UNLESS_NEGATED and _negated(text, m.start(), m.end()):
                continue                                 # no need to wait for ..., don't wait for ...
            return f"{POSTPONE_REASON} ('{_quote(m.group(0))}')"
    words = re.findall(r"[^\s,.;:!?]+", text)
    if words and words[0] in NEGATIVE_FIRST_WORDS and not (_NO_CHANGES_RE.match(text) or _NO_NEED_RE.match(text)):
        return f"starts with '{words[0]}'"
    for pattern in REFUSAL_PATTERNS:
        m = pattern.search(text)
        if m:
            return f"says '{_quote(m.group(0))}'"
    change = _asks_for_change(text)
    if change:
        return f"{CHANGE_REASON} ('{_quote(change)}')"
    if only_questions(text):
        return f"{QUESTION_REASON}, not an answer ('{_quote(text)}')"
    return None


def refusal_message(why: str) -> str:
    """What follows the reason in a refused reply's message: for a question, '; ' and QUESTION_MESSAGE; for a change
    request, nothing (its fix says to make the change and show the contract again); for every other refusal (a
    reply that postpones, a negative first word, a refusal word; round 5 verdict, A-aera-1 and C-sol-1), ': ' and
    POSTPONE_MESSAGE (no agreement was given; wait for the user's reply). `contract agree` and the contract gate
    hook print the same words."""
    if why.startswith(QUESTION_REASON):
        return f"; {QUESTION_MESSAGE}"
    if why.startswith(CHANGE_REASON):
        return ""
    return f": {POSTPONE_MESSAGE}"


def refusal_fix(why: str, rotm: str, model: str, table: str) -> str:
    """The fix of a refused reply: R-CHANGE's text for a change request, the wait for a reply that postpones, the
    answer to a question, else the fix of a refusal. `rotm` is the rotm.py command line, `model` the model folder
    as it is to be typed."""
    if why.startswith(QUESTION_REASON):
        return (f"answer the user's question, then wait for their reply to contracts/{table}.contract.md and record "
                f"it with {rotm} contract agree {model} --table {table} --reply \"<their words>\" --by <name>")
    if why.startswith(POSTPONE_REASON):
        return (f"wait for the user's reply to contracts/{table}.contract.md and record it with {rotm} contract agree "
                f"{model} --table {table} --reply \"<their words>\" --by <name>")
    if why.startswith(CHANGE_REASON):
        return (f"the reply asks for a change: make the change, run {rotm} contract show {model} --table {table}, "
                "show the user the contract again and record their reply to the new version")
    return (f"make the changes the user asked for ({rotm} contract propose {model} --table {table} --force ...), "
            "show the contract again, and record the reply in which they agree")


def _sentences(body: str) -> list[str]:
    """The sentences of a task file, each with its white space collapsed: split after '.', '!' or '?' followed by
    white space, at a blank line, and before a line that starts a list item, a heading, a quote or a table row.
    Joined with one space they give _collapse(body)."""
    parts = re.split(r"(?<=[.!?])\s+|\n\s*\n|\n(?=[ \t]*(?:[-*+>#|]|\d+[.)])\s?)", body)
    return [s for s in (_collapse(p) for p in parts) if s]


def _names_table(text: str, name: str) -> bool:
    """Whether `text` names the table `name` as a word (any case): 'Sales is agreed' names sales."""
    return bool(re.search(rf"(?<![a-z0-9_]){re.escape(name.lower())}(?![a-z0-9_])", text.lower()))


def _about_another_table(text: str, table: str | None, tables: Sequence[str]) -> bool:
    """Whether a sentence names another contract table of the model than `table` and does not name `table`; such
    a sentence does not count against `table` (round 4 verdict, A-aera-4 and C-sol-2)."""
    if not table:
        return False
    return not _names_table(text, table) and any(_names_table(text, o) for o in tables if o != table)


def refusing_sentence(body: str, reply: str, *, table: str | None = None,
                      tables: Sequence[str] = ()) -> tuple[str, str] | None:
    """(sentences, reason) for the first place in a task file where the reply occurs inside text that reads as a
    refusal or a change request (round 2 verdict, A-aera-3: 'This proposal is not agreed.' holds the reply
    'agreed'); None when every sentence that holds the reply reads as an agreement. A reply that spans several
    sentences is read with all of them. With `table` and `tables` (the model's contract tables), a refusing
    sentence that names another of `tables` and not `table` does not count against `table`."""
    sents = _sentences(body)
    joined = " ".join(sents)
    want = _collapse(reply)
    if not want:
        return None
    starts, pos = [], 0
    for s in sents:
        starts.append(pos)
        pos += len(s) + 1
    i = joined.find(want)
    while i >= 0:
        j = i + len(want)
        held = " ".join(s for s, a in zip(sents, starts) if a < j and a + len(s) > i)
        why = reads_as_refusal(held)
        if why and not _about_another_table(held, table, tables):
            return held, why
        i = joined.find(want, i + 1)
    return None


def reply_refusal(body: str, reply: str, *, table: str | None = None, tables: Sequence[str] = (),
                  whole: bool = True) -> tuple[str, str] | None:
    """(sentences, reason) when a reply taken from a longer text gives no agreement for `table`, else None (round 4
    verdict, A-aera-4 and C-sol-2; the contract gate hook's session channel and the task-file channel use it):
    1. the sentence or sentences that hold the reply read as a refusal, a change request, a postponement or a
       question (refusing_sentence, reads_as_refusal): the reply 'agreed' taken from 'I have not agreed.';
    2. with `whole` (a message the user typed in the session), when the reply is shorter than the text: the whole
       text reads so, through one of its sentences or across them.
    A refusing sentence that names another contract table of the model (`tables`) and not `table` does not count
    against `table`: for 'Sales is agreed. Please change the unit in target.', the reply 'Sales is agreed' is let
    through for sales and refused for target."""
    held = refusing_sentence(body, reply, table=table, tables=tables)
    if held is not None:
        return held
    want, text = _collapse(reply), _collapse(body)
    if not whole or not want or len(want) >= len(text):
        return None
    why = reads_as_refusal(text)
    if not why:
        return None
    refusing = [(s, w) for s, w in ((s, reads_as_refusal(s)) for s in _sentences(body)) if w]
    if not refusing:
        return text, why                         # a refusal read across sentences
    for s, w in refusing:
        if not _about_another_table(s, table, tables):
            return s, w
    return None


def agree(model_root: str | Path, table: str, *, reply: str, by: str, channel: str = "session",
          source_file: str | Path | None = None, at: str | None = None) -> dict[str, Any]:
    """Record the agreement (reply verbatim, by, at, channel, contract_sha256, table); returns the contract.
    An agreement that already stands is returned unchanged. Refuses (common.Refusal, nothing written) when the
    contract is missing or not valid, the reply or the name is empty, the reply reads as a refusal, a change
    request or a postponement (reads_as_refusal), the contract changed after it was last shown (shown_problem,
    refusal F-SHOWN), or
    (channel task-file) the reply does not occur in the task file or a sentence that holds it reads as a refusal
    (refusing_sentence)."""
    _check_table(table)
    root, _ = _model_root(model_root, create=False)
    state, data, problems = modelfolder.read_contract(root, table)
    model = root.as_posix()
    rotm = _rotm()
    if state == "missing":
        raise common.Refusal(f"table '{table}' has no contract (contracts/{table}.contract.json)",
                             fix=f"propose one first: rotm.py contract propose \"{model}\" --table {table} --csv <sample>")
    if state == "invalid" or data is None:
        raise common.Refusal(f"contracts/{table}.contract.json is not valid; nothing was recorded", details=problems,
                             fix=f"correct it, or propose it again with rotm.py contract propose \"{model}\" --table "
                                 f"{table} --force")
    text = (reply or "").strip()
    if not text:
        raise common.Refusal("--reply is empty: an agreement is recorded only with the user's own words",
                             fix="show the user the contract, then pass their reply exactly as they wrote it")
    who = (by or "").strip()
    if not who:
        raise common.Refusal("--by is empty", fix="give the name of the person who agreed, e.g. --by Max")
    why = reads_as_refusal(text)
    if why:
        raise common.Refusal(f"the reply {why}, so it reads as a refusal or a change request{refusal_message(why)}; "
                             "no agreement was recorded", fix=refusal_fix(why, rotm, f'"{model}"', table))
    if channel not in CHANNELS_CLI:
        raise common.Refusal(f"channel '{channel}' is not known", fix="use session, task-file or example")
    if state == "agreed":
        return data
    shown = shown_problem(root, data)
    if shown:
        raise common.Refusal(f"the contract changed after it was last shown: {shown}; no agreement was recorded",
                             fix=f"run {rotm} contract show \"{model}\" --table {table}, show the user "
                                 f"contracts/{table}.contract.md again and record their new reply")
    stored_source = None
    if channel == "task-file":
        if source_file is None:
            raise common.Refusal("channel task-file needs --source-file (the task file that holds the reply)",
                                 fix="pass --source-file <task.md>")
        sf = Path(os.path.abspath(common.native_path(source_file)))
        if not sf.is_file():
            raise common.Refusal(f"{sf.as_posix()}: task file not found", fix="give the path of the task file")
        try:
            body = sf.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise common.Refusal(f"{sf.name}: cannot be read as UTF-8 text ({type(exc).__name__})",
                                 fix="give a UTF-8 text file") from None
        if text not in body and _collapse(text) not in _collapse(body):
            raise common.Refusal(f"the reply is not in {sf.name}; no agreement was recorded",
                                 fix="copy the reply exactly as it is written in the task file, or, when the task "
                                     "file gives no agreement, report the contract as proposed and stop")
        # the function the contract gate hook's session channel uses; a task file is read sentence by sentence
        # around the reply, not as one message (it holds the task's other instructions too)
        held = reply_refusal(body, text, table=table, tables=modelfolder.tables(root), whole=False)
        if held:
            raise common.Refusal(f"{sf.name} holds the reply in '{held[0][:200]}', which {held[1]}, so it reads as "
                                 f"a refusal or a change request{refusal_message(held[1])}; no agreement was recorded",
                                 fix="pass as --reply the sentence of the task file that gives the agreement, copied "
                                     "exactly; when the task file gives no agreement, report the contract as "
                                     "proposed and stop")
        stored_source = common.relpath_posix(sf, root)
    elif channel == "example":
        from . import spec as spec_mod
        if not spec_mod.example_channel_allowed(root):
            raise common.Refusal("channel example is only for the examples and templates shipped inside the plugin",
                                 fix="record the user's own reply with --channel session (or task-file in a "
                                     "headless test)")
    contract = dict(data)
    old = contract.get("agreement") or {}
    history = list(old.get("history") or [])
    if old.get("status") == "agreed":                  # stale: changed after it was agreed
        history.append(_history_entry(contract, state))
    content_hash = modelfolder.contract_hash(contract)
    record: dict[str, Any] = {"status": "agreed", "contract_sha256": content_hash,
                              "table": table, "reply": text, "by": who, "at": at or common.now_iso(), "channel": channel,
                              "source_file": stored_source, "recorded_by": "rotm.py contract agree",
                              "version": int(contract.get("version") or 1),
                              "shown_sha256": content_hash}    # the .md is written below from this content
    # The same words recorded for an earlier version: an agreement belongs to the version it was given for
    # (live test 1). Recorded, and said in the output, so the reader of the contract sees it.
    reused = next((h for h in reversed(history) if isinstance(h, dict) and h.get("status") == "agreed"
                   and _collapse(str(h.get("reply") or "")) == _collapse(text)), None)
    if reused is not None:
        record["reply_reused_from"] = int(reused.get("version") or 1)
    if history:
        record["history"] = history
    contract["agreement"] = record
    path = modelfolder.contract_path(root, table)
    schemas.require_valid(contract, "contract", path.name)
    common.write_json(path, contract)
    write_md(root, contract)
    if modelfolder.contract_state(root, table) != "agreed":    # pragma: no cover - the hash is computed above
        raise common.RotmError(f"contracts/{table}.contract.json was written but does not read back as agreed")
    return contract


# ----------------------------------------------------------------------------- status and show

def status_all(model_root: str | Path) -> list[dict[str, Any]]:
    """[{table, state, by, at, channel, problems}] for every contract of the model folder."""
    root, _ = _model_root(model_root, create=False)
    out = []
    for t in modelfolder.tables(root):
        state, data, problems = modelfolder.read_contract(root, t)
        ag = (data or {}).get("agreement") or {}
        agreed = state == "agreed"
        out.append({"table": t, "state": state, "by": ag.get("by") if agreed else None,
                    "at": ag.get("at") if agreed else None, "channel": ag.get("channel") if agreed else None,
                    "version": (data or {}).get("version") or 1,
                    "agreed_version": ag.get("version") if agreed else None,
                    "columns": len((data or {}).get("columns") or []), "problems": problems,
                    "path": f"contracts/{t}.contract.json"})
    return out


def show(model_root: str | Path, table: str) -> tuple[str, bool]:
    """(readable contract, whether the .md file was rewritten because it differed from the JSON). Records in the
    JSON agreement.shown_sha256, the content hash of the JSON the .md shows (round 2 verdict, C-sol-3); the JSON is
    written only when that field differs."""
    _check_table(table)
    root, _ = _model_root(model_root, create=False)
    state, data, problems = modelfolder.read_contract(root, table)
    if state == "missing":
        raise common.Refusal(f"table '{table}' has no contract", fix=f"propose one: rotm.py contract propose "
                             f"\"{root.as_posix()}\" --table {table} --csv <sample>")
    if state == "invalid" or data is None:
        raise common.Refusal(f"contracts/{table}.contract.json is not valid", details=problems,
                             fix="correct the keys named below, or propose it again with --force")
    text = render_md(data)
    path = md_path(root, table)
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    rewritten = current != text
    if rewritten:
        write_md(root, data)
    h = shown_hash(data)
    ag = data.get("agreement")
    if isinstance(ag, dict) and ag.get("shown_sha256") != h:
        updated = dict(data)
        updated["agreement"] = dict(ag, shown_sha256=h)
        cpath = modelfolder.contract_path(root, table)
        schemas.require_valid(updated, "contract", cpath.name)
        common.write_json(cpath, updated)
    return text, rewritten


# ----------------------------------------------------------------------------- table names and remove (J-4)

def name_note(table: str) -> str | None:
    """Note N-NAME for a table name longer than NAME_NOTE_MAX characters (round 2 verdict, J-4), or None."""
    if len(table) <= NAME_NOTE_MAX:
        return None
    return (f"table name '{table}' has {len(table)} characters: it is part of the sheet names raw_{table} and "
            f"h_{table} and of every formula that reads them, and counts toward the 100-character formula length "
            f"limit; a name of at most {NAME_NOTE_MAX} characters keeps lookup formulas short")


def helper_name_clashes(columns: Sequence[dict[str, Any]]) -> list[tuple[str, str]]:
    """(column name, helper) for every contract column whose name is a helper column build adds when the column and
    the others are fetched on the helper sheet (round 3 verdict, A-aera-12; spec.helper_name_clashes, the rule build
    refuses with): one of row, in_raw, dup_key and dup, or the null flag <c>_null of another column <c>."""
    from . import spec as spec_mod
    cols = list(columns)
    return [(name, helper) for _i, name, helper in
            spec_mod.helper_name_clashes({"columns": cols}, [c["name"] for c in cols])]


def helper_name_note(table: str, name: str, helper: str) -> str:
    """Note N-HELPER-NAME of `contract propose`, with the suggested destination name <name>_src."""
    return (f"column '{name}' of '{table}' has the name of a helper column build adds ({helper}); suggested "
            f"destination name: '{name}_src'")


def _spec_name(root: Path) -> str:
    try:
        name = modelfolder.read_marker(root).get("spec")
    except common.Refusal:
        name = None
    return name if isinstance(name, str) and name else "model.spec.json"


def remove(model_root: str | Path, table: str, *, now: _dt.datetime | None = None) -> dict[str, Any]:
    """Move the files of one table out of the model folder (round 2 verdict, J-4): contracts/<t>.contract.json and
    .contract.md, queries/<t>.* (with their .bak_ copies), raw/<t>.csv and raw/<t>.controls.json, each that exists,
    into MODEL/removed/<t>_<YYYYMMDD_HHMMSS>/ under the same relative paths. Returns {table, folder, moved: [{from,
    to}]} (paths relative to the model folder). Refuses (nothing moved) while the specification has a sheet of the
    table, when the specification cannot be read, and when the table has none of these files."""
    _check_table(table)
    root, _ = _model_root(model_root, create=False)
    model = root.as_posix()
    spec_name = _spec_name(root)
    spec_path = root / spec_name
    if spec_path.is_file():
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise common.Refusal(f"{spec_name} cannot be read as JSON ({type(exc).__name__}), so it is not known "
                                 f"whether it names table '{table}'; nothing was moved",
                                 fix=f"correct {spec_name}, then run contract remove again") from None
        sheets: list[str] = []
        if isinstance(spec, dict) and isinstance(spec.get("sheets"), list):
            sheets = [str(s.get("name") or "?") for s in spec["sheets"] if isinstance(s, dict)
                      and s.get("table") == table]
        if sheets:
            raise common.Refusal(f"{spec_name} names table '{table}' ({'sheet' if len(sheets) == 1 else 'sheets'} "
                                 f"{', '.join(sheets)}); nothing was moved",
                                 fix=f"change {spec_name} so that no sheet reads table '{table}' (for example give "
                                     "those sheets the new table), then run "
                                     f"{_rotm()} contract remove \"{model}\" --table {table} again")
    files = [p for p in (root / "contracts" / f"{table}{modelfolder.CONTRACT_SUFFIX}",
                         md_path(root, table)) if p.is_file()]
    qdir = root / "queries"
    if qdir.is_dir():
        files += sorted((p for p in qdir.glob(f"{table}.*") if p.is_file()), key=lambda p: p.name)
    files += sorted((p for p in (root / "raw" / f"{table}.csv", root / "raw" / f"{table}.controls.json")
                     if p.is_file()), key=lambda p: p.name)
    if not files:
        raise common.Refusal(f"table '{table}' has no files in contracts/, queries/ or raw/; nothing was moved",
                             fix=f"check the table name: {_rotm()} contract status \"{model}\" lists the contracts")
    stamp = (now or _dt.datetime.now()).strftime("%Y%m%d_%H%M%S")
    dest = root / "removed" / f"{table}_{stamp}"
    k = 2
    while dest.exists():
        dest = root / "removed" / f"{table}_{stamp}_{k}"
        k += 1
    moved: list[dict[str, str]] = []
    for p in files:
        rel = p.relative_to(root)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(p, target)
        moved.append({"from": rel.as_posix(), "to": target.relative_to(root).as_posix()})
    return {"table": table, "folder": dest.relative_to(root).as_posix(), "moved": moved}


# ----------------------------------------------------------------------------- command line

def add_contract_arguments(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="action", required=True, metavar="propose|agree|show|status|remove")
    pr = sub.add_parser("propose", help="propose a contract for one raw table")
    pr.add_argument("dir", help="the model folder (its marker is written when absent)")
    pr.add_argument("--table", required=True)
    pr.add_argument("--csv", help="a sample CSV to infer the columns from")
    pr.add_argument("--columns", help="comma-separated column names, each name or name:type (when there is no CSV)")
    pr.add_argument("--key", help="comma-separated key columns")
    pr.add_argument("--grain", help="what one row is, e.g. 'one row per invoice line'")
    pr.add_argument("--source", help="source object or FROM clause text")
    pr.add_argument("--rows", help="expected rows: N or MIN:MAX")
    pr.add_argument("--force", action="store_true", help="replace an existing contract (resets an agreement)")
    pr.add_argument("--purpose", help="what the table is for, in one sentence")
    pr.add_argument("--connector", choices=schemas.CONNECTORS, help="how the table is landed")
    pr.add_argument("--system", help="source system, e.g. BigQuery")
    pr.add_argument("--where", help="the WHERE clause text that scopes the rows")
    pr.add_argument("--unit", action="append", default=[], metavar="COLUMN=UNIT", help="unit of a column, e.g. amount=KRW")
    pr.add_argument("--type", action="append", default=[], metavar="COLUMN=TYPE", help="override an inferred type")
    pr.add_argument("--null", action="append", default=[], metavar="COLUMN=never|allowed", help="override a null rule")
    pr.add_argument("--total", action="append", default=[], metavar="COLUMN=VALUE[:TOL]",
                    help="a source-side control total of a number column")
    pr.add_argument("--key-repeats", type=int, metavar="N",
                    help="known exception: at most N rows repeat an earlier key; they are landed and counted by the "
                         "key check (default: the repeats in the sample, when --key is given)")
    pr.add_argument("--known-nulls", action="append", default=[], metavar="COLUMN=N",
                    help="known exception: a never-null column is empty in at most N rows; they are landed and "
                         "counted by the null check")
    pr.add_argument("--capacity", type=int, help="helper rows for this table, at most the landed rows (more is "
                                                 "refused by build) (refresh.capacity_rows)")
    pr.add_argument("--allow-empty", action="store_true", help="an empty result is allowed")
    pr.add_argument("--max-rows", type=int, default=PROFILE_MAX_ROWS, help="sample rows to profile (default 100,000)")
    ag = sub.add_parser("agree", help="record the user's agreement with their own words")
    ag.add_argument("dir")
    ag.add_argument("--table", required=True)
    ag.add_argument("--reply", required=True, help="the user's reply, verbatim")
    ag.add_argument("--by", required=True, help="who agreed")
    ag.add_argument("--channel", choices=CHANNELS_CLI, default="session",
                    help="session (the user replied in this session), task-file (headless test: the reply is in "
                         "--source-file), example (shipped examples and templates only)")
    ag.add_argument("--source-file", help="the task file that holds the reply (channel task-file)")
    sh = sub.add_parser("show", help="print the readable contract")
    sh.add_argument("dir")
    sh.add_argument("--table", required=True)
    st = sub.add_parser("status", help="the state of every contract")
    st.add_argument("dir")
    rm = sub.add_parser("remove", help="move the contract, query, snapshot and controls files of one table (--table "
                                       "T) into MODEL/removed/T_<time>/; refused while model.spec.json names T")
    rm.add_argument("dir", help="the model folder")
    rm.add_argument("--table", required=True, help="the table whose files are moved")
    for q in (pr, ag, sh, st, rm):
        q.add_argument("--json", metavar="FILE", help="write the result as JSON ('-' = stdout)")


def _out(args: argparse.Namespace) -> Any:
    return sys.stderr if getattr(args, "json", None) == "-" else sys.stdout


def _write_result(args: argparse.Namespace, obj: Any) -> None:
    if not getattr(args, "json", None):
        return
    if args.json == "-":
        sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    else:
        common.write_json(args.json, obj)


def run_contract(args: argparse.Namespace) -> int:
    out = _out(args)
    rotm = _rotm()
    if args.action == "propose":
        root = Path(os.path.abspath(common.native_path(args.dir)))
        had_marker = modelfolder.is_model_root(root)
        path = propose(root, args.table, csv=args.csv, columns=args.columns, key=args.key, grain=args.grain,
                       source=args.source, rows=args.rows, force=args.force, purpose=args.purpose,
                       connector=args.connector, system=args.system, where=args.where, units=args.unit,
                       types=args.type, nulls=args.null, totals=args.total, capacity=args.capacity,
                       allow_empty=args.allow_empty, max_rows=args.max_rows, key_repeats=args.key_repeats,
                       known_nulls=args.known_nulls)
        data = common.read_json(path)
        text = render_md(data)
        if not had_marker:
            out.write(f"created model folder {root.as_posix()} (marker {modelfolder.MARKER})\n")
        out.write(f"proposed: {common.relpath_posix(path, root)} and contracts/{args.table}.contract.md "
                  "(status proposed, not agreed)\n")
        note = name_note(args.table)
        json_notes = [f"N-NAME: {note}"] if note else []
        if note:
            out.write(f"note (N-NAME): {note}. To use a shorter name, propose the table again under that name and "
                      f"move this one's files away with {rotm} contract remove \"{root.as_posix()}\" --table "
                      f"{args.table}\n")
        for name, helper in helper_name_clashes(data["columns"]):
            hnote = helper_name_note(args.table, name, helper)
            json_notes.append(f"{HELPER_NAME_NOTE}: {hnote}")
            out.write(f"note ({HELPER_NAME_NOTE}): {hnote}. build refuses this name: rename the destination column in "
                      f"contracts/{args.table}.contract.json (keep its source expression) and run {rotm} contract "
                      f"show \"{root.as_posix()}\" --table {args.table} before the user agrees\n")
        out.write("\n")
        out.write(text)
        out.write("\nnext: show the user the contract above and wait for their reply. When they agree, record their "
                  f"words: {rotm} contract agree \"{root.as_posix()}\" --table {args.table} --reply \"<their words>\" "
                  "--by <name>. When they ask for changes, propose again with --force and the changes, or edit "
                  f"contracts/{args.table}.contract.json and run {rotm} contract show \"{root.as_posix()}\" --table "
                  f"{args.table}; then show the user the new text.\n")
        _write_result(args, {"action": "propose", "table": args.table, "state": "proposed",
                             "path": path.as_posix(), "md": md_path(root, args.table).as_posix(),
                             "notes": json_notes, "contract": data})
        return common.EXIT_OK
    if args.action == "agree":
        root, _ = _model_root(args.dir, create=False)
        before = modelfolder.contract_state(root, args.table)
        data = agree(root, args.table, reply=args.reply, by=args.by, channel=args.channel,
                     source_file=args.source_file)
        ag = data["agreement"]
        version = ag.get("version") or data.get("version") or 1
        if before == "agreed":
            out.write(f"already agreed: contract '{args.table}' version {version} was agreed by {ag['by']} on "
                      f"{ag['at']} ('{ag['reply']}'); nothing was changed\n")
        else:
            out.write(f"recorded: contract '{args.table}' version {version} agreed by {ag['by']} on {ag['at']} "
                      f"through {ag['channel']}: '{ag['reply']}'\n")
            prev = last_agreed(data)
            if prev is not None:
                changes = changes_between(prev["agreed_content"], data)
                out.write(f"changes since version {prev.get('version') or 1} (agreed by {prev.get('by')} on "
                          f"{prev.get('at')}): " + ("; ".join(changes) if changes else "none in the agreed parts")
                          + "\n")
            if ag.get("reply_reused_from"):
                out.write(f"note: these are the same words that were recorded for version {ag['reply_reused_from']}. "
                          f"Tell the user that version {version} was recorded with them"
                          + (", and list the changes above in the report" if ag.get("channel") == "task-file" else
                             "; record them only if the user gave them after seeing this version") + "\n")
            out.write(f"next: write the SQL in queries/{args.table}.sql (Connected Sheets or Power Query), or land a "
                      f"CSV: {rotm} land \"{root.as_posix()}\" --table {args.table} --csv <file>\n")
        _write_result(args, {"action": "agree", "table": args.table, "state": "agreed",
                             "already_agreed": before == "agreed", "contract": data})
        return common.EXIT_OK
    if args.action == "show":
        root, _ = _model_root(args.dir, create=False)
        text, rewritten = show(root, args.table)
        out.write(text)
        if rewritten:
            out.write(f"\nnote: contracts/{args.table}.contract.md was rewritten from the JSON file\n")
        state = modelfolder.contract_state(root, args.table)
        _write_result(args, {"action": "show", "table": args.table, "state": state, "md": text})
        return common.EXIT_OK
    if args.action == "status":
        root, _ = _model_root(args.dir, create=False)
        rows = status_all(root)
        if not rows:
            out.write(f"no contracts yet in {root.as_posix()}/contracts (run rotm.py contract propose)\n")
        else:
            width = max(len(r["table"]) for r in rows)
            for r in rows:
                extra = f"  by {r['by']} on {r['at']} ({r['channel']})" if r["state"] == "agreed" else ""
                out.write(f"{r['table'].ljust(width)}  {r['state']}  version {r['version']}{extra}\n")
                for prob in r["problems"]:
                    out.write(f"  {prob}\n")
        _write_result(args, {"action": "status", "contracts": rows})
        return common.EXIT_OK
    if args.action == "remove":
        root, _ = _model_root(args.dir, create=False)
        res = remove(root, args.table)
        out.write(f"removed table '{args.table}': {len(res['moved'])} file(s) moved into {res['folder']}/\n")
        for m in res["moved"]:
            out.write(f"  {m['from']} -> {m['to']}\n")
        _write_result(args, dict(res, action="remove"))
        return common.EXIT_OK
    raise common.Refusal(f"unknown action '{args.action}'", fix="use propose, agree, show, status or remove")
