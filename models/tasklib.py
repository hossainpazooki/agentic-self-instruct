"""Task library backing the deterministic fake model backend.

TEST SCAFFOLDING -- not part of the reconstructed method. It exists so the
judge, the differential fuzzer, and the mutation runner execute real Python
instead of being mocked past. Every flaw mode below is one the controller is
supposed to catch:

``sound``        strong tests, correct reference          -> controller accepts
``weak_tests``   correct reference, near-vacuous tests    -> mutation score fails
``wrong_ref``    strong tests, subtly wrong reference     -> differential fuzz fails
``hardcoded``    reference memorises the visible tests    -> differential fuzz fails
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    key: str
    signature: str
    problem_statement: str
    correct_solution: str
    wrong_solution: str
    hardcoded_solution: str
    strong_tests: tuple[str, ...]
    weak_tests: tuple[str, ...]


TASKS: tuple[Task, ...] = (
    Task(
        key="sum_even",
        signature="def sum_even(nums: list[int]) -> int:",
        problem_statement=(
            "Given a list of integers, return the sum of the even values. "
            "An empty list sums to 0. Negative even numbers count."
        ),
        correct_solution=(
            "def sum_even(nums: list[int]) -> int:\n"
            "    return sum(n for n in nums if n % 2 == 0)\n"
        ),
        # Subtly wrong: silently drops negative even values.
        # (An earlier draft used `abs(n) % 2 != 1`, which differential fuzzing
        # showed is *equivalent* to the correct version -- a useful reminder
        # that "looks wrong" is not the same as "is wrong".)
        wrong_solution=(
            "def sum_even(nums: list[int]) -> int:\n"
            "    return sum(n for n in nums if n > 0 and n % 2 == 0)\n"
        ),
        hardcoded_solution=(
            "def sum_even(nums: list[int]) -> int:\n"
            "    table = {(): 0, (1, 2, 3, 4): 6, (2,): 2, (-2, -4): -6}\n"
            "    return table.get(tuple(nums), 0)\n"
        ),
        strong_tests=(
            "assert sum_even([]) == 0",
            "assert sum_even([1, 2, 3, 4]) == 6",
            "assert sum_even([2]) == 2",
            "assert sum_even([-2, -4]) == -6",
            "assert sum_even([1, 3, 5]) == 0",
            "assert sum_even([-3, -2, 7, 8]) == 6",
        ),
        weak_tests=("assert sum_even([2]) == 2",),
    ),
    Task(
        key="reverse_words",
        signature="def reverse_words(s: str) -> str:",
        problem_statement=(
            "Return the words of the input string in reverse order, separated by "
            "single spaces. Runs of whitespace are treated as one separator, and "
            "leading and trailing whitespace is dropped."
        ),
        correct_solution=(
            "def reverse_words(s: str) -> str:\n"
            "    return ' '.join(reversed(s.split()))\n"
        ),
        # Subtly wrong: split(' ') keeps empty fields on repeated spaces.
        wrong_solution=(
            "def reverse_words(s: str) -> str:\n"
            "    return ' '.join(reversed(s.split(' ')))\n"
        ),
        hardcoded_solution=(
            "def reverse_words(s: str) -> str:\n"
            "    table = {'a b': 'b a', '': '', 'one two three': 'three two one'}\n"
            "    return table.get(s, '')\n"
        ),
        strong_tests=(
            "assert reverse_words('a b') == 'b a'",
            "assert reverse_words('') == ''",
            "assert reverse_words('one two three') == 'three two one'",
            "assert reverse_words('  padded  words  ') == 'words padded'",
            "assert reverse_words('single') == 'single'",
        ),
        weak_tests=("assert reverse_words('a b') == 'b a'",),
    ),
    Task(
        key="count_vowels",
        signature="def count_vowels(s: str) -> int:",
        problem_statement=(
            "Count the vowels (a, e, i, o, u) in the input string, case-insensitively. "
            "The letter y is not a vowel."
        ),
        correct_solution=(
            "def count_vowels(s: str) -> int:\n"
            "    return sum(1 for ch in s.lower() if ch in 'aeiou')\n"
        ),
        # Subtly wrong: case-sensitive, so uppercase vowels are missed.
        wrong_solution=(
            "def count_vowels(s: str) -> int:\n"
            "    return sum(1 for ch in s if ch in 'aeiou')\n"
        ),
        hardcoded_solution=(
            "def count_vowels(s: str) -> int:\n"
            "    table = {'hello': 2, '': 0, 'AEIOU': 5}\n"
            "    return table.get(s, 0)\n"
        ),
        strong_tests=(
            "assert count_vowels('hello') == 2",
            "assert count_vowels('') == 0",
            "assert count_vowels('AEIOU') == 5",
            "assert count_vowels('rhythm') == 0",
            "assert count_vowels('Yellow') == 2",
        ),
        weak_tests=("assert count_vowels('hello') == 2",),
    ),
    Task(
        key="max_subarray_sum",
        signature="def max_subarray_sum(nums: list[int]) -> int:",
        problem_statement=(
            "Return the largest sum obtainable from a contiguous non-empty subarray. "
            "For an empty list return 0."
        ),
        correct_solution=(
            "def max_subarray_sum(nums: list[int]) -> int:\n"
            "    if not nums:\n"
            "        return 0\n"
            "    best = current = nums[0]\n"
            "    for n in nums[1:]:\n"
            "        current = max(n, current + n)\n"
            "        best = max(best, current)\n"
            "    return best\n"
        ),
        # Subtly wrong: clamps at 0, so all-negative inputs return 0.
        wrong_solution=(
            "def max_subarray_sum(nums: list[int]) -> int:\n"
            "    best = current = 0\n"
            "    for n in nums:\n"
            "        current = max(0, current + n)\n"
            "        best = max(best, current)\n"
            "    return best\n"
        ),
        hardcoded_solution=(
            "def max_subarray_sum(nums: list[int]) -> int:\n"
            "    table = {(): 0, (1, 2, 3): 6, (-1, -2): -1}\n"
            "    return table.get(tuple(nums), 0)\n"
        ),
        strong_tests=(
            "assert max_subarray_sum([]) == 0",
            "assert max_subarray_sum([1, 2, 3]) == 6",
            "assert max_subarray_sum([-1, -2]) == -1",
            "assert max_subarray_sum([-2, 1, -3, 4, -1, 2, 1, -5, 4]) == 6",
            "assert max_subarray_sum([5]) == 5",
        ),
        weak_tests=("assert max_subarray_sum([1, 2, 3]) == 6",),
    ),
    Task(
        key="is_balanced",
        signature="def is_balanced(s: str) -> bool:",
        problem_statement=(
            "Return True when every round bracket in the string is matched and "
            "properly nested. Characters other than ( and ) are ignored."
        ),
        correct_solution=(
            "def is_balanced(s: str) -> bool:\n"
            "    depth = 0\n"
            "    for ch in s:\n"
            "        if ch == '(':\n"
            "            depth += 1\n"
            "        elif ch == ')':\n"
            "            depth -= 1\n"
            "            if depth < 0:\n"
            "                return False\n"
            "    return depth == 0\n"
        ),
        # Subtly wrong: counts only, so ')(' passes.
        wrong_solution=(
            "def is_balanced(s: str) -> bool:\n"
            "    return s.count('(') == s.count(')')\n"
        ),
        hardcoded_solution=(
            "def is_balanced(s: str) -> bool:\n"
            "    table = {'()': True, '': True, '(()': False}\n"
            "    return table.get(s, False)\n"
        ),
        strong_tests=(
            "assert is_balanced('()') is True",
            "assert is_balanced('') is True",
            "assert is_balanced('(()') is False",
            "assert is_balanced(')(') is False",
            "assert is_balanced('a(b)c') is True",
        ),
        weak_tests=("assert is_balanced('()') is True",),
    ),
    # The two tasks below are multi-branch on purpose. Mutation score barely
    # discriminates a one-assert suite from a six-assert suite on a one-line
    # function -- almost any mutation of a one-liner changes almost any input,
    # so a single test kills everything. Discrimination needs branches whose
    # behaviour only specific inputs reveal. See docs/fidelity.md.
    Task(
        key="grade_bucket",
        signature="def grade_bucket(score: int) -> str:",
        problem_statement=(
            "Map a numeric score to a letter grade: 'A' for 90 and above, 'B' for "
            "80 to 89, 'C' for 70 to 79, and 'F' below 70. Scores are integers and "
            "may be negative."
        ),
        correct_solution=(
            "def grade_bucket(score: int) -> str:\n"
            "    if score >= 90:\n"
            "        return 'A'\n"
            "    if score >= 80:\n"
            "        return 'B'\n"
            "    if score >= 70:\n"
            "        return 'C'\n"
            "    return 'F'\n"
        ),
        # Subtly wrong: the B boundary sits at 85, so 80-84 grade as C.
        wrong_solution=(
            "def grade_bucket(score: int) -> str:\n"
            "    if score >= 90:\n"
            "        return 'A'\n"
            "    if score >= 85:\n"
            "        return 'B'\n"
            "    if score >= 70:\n"
            "        return 'C'\n"
            "    return 'F'\n"
        ),
        hardcoded_solution=(
            "def grade_bucket(score: int) -> str:\n"
            "    table = {95: 'A', 85: 'B', 75: 'C', 10: 'F'}\n"
            "    return table.get(score, 'F')\n"
        ),
        strong_tests=(
            "assert grade_bucket(95) == 'A'",
            "assert grade_bucket(90) == 'A'",
            "assert grade_bucket(89) == 'B'",
            "assert grade_bucket(80) == 'B'",
            "assert grade_bucket(79) == 'C'",
            "assert grade_bucket(70) == 'C'",
            "assert grade_bucket(69) == 'F'",
            "assert grade_bucket(-5) == 'F'",
        ),
        weak_tests=("assert grade_bucket(95) == 'A'",),
    ),
    Task(
        key="is_valid_password",
        signature="def is_valid_password(s: str) -> bool:",
        problem_statement=(
            "Return True when the string is at least 8 characters long AND contains "
            "at least one digit AND contains at least one uppercase letter. All "
            "three conditions must hold."
        ),
        correct_solution=(
            "def is_valid_password(s: str) -> bool:\n"
            "    if len(s) < 8:\n"
            "        return False\n"
            "    if not any(ch.isdigit() for ch in s):\n"
            "        return False\n"
            "    if not any(ch.isupper() for ch in s):\n"
            "        return False\n"
            "    return True\n"
        ),
        # Subtly wrong: drops the uppercase requirement entirely.
        wrong_solution=(
            "def is_valid_password(s: str) -> bool:\n"
            "    if len(s) < 8:\n"
            "        return False\n"
            "    if not any(ch.isdigit() for ch in s):\n"
            "        return False\n"
            "    return True\n"
        ),
        hardcoded_solution=(
            "def is_valid_password(s: str) -> bool:\n"
            "    return s in ('Passw0rd', 'A1b2c3d4')\n"
        ),
        strong_tests=(
            "assert is_valid_password('Passw0rd') is True",
            "assert is_valid_password('') is False",
            "assert is_valid_password('Short1A') is False",
            "assert is_valid_password('alllower1') is False",
            "assert is_valid_password('NoDigitsHere') is False",
            "assert is_valid_password('A1b2c3d4') is True",
        ),
        weak_tests=("assert is_valid_password('Passw0rd') is True",),
    ),
)

TASKS_BY_KEY = {t.key: t for t in TASKS}
FLAW_MODES = ("sound", "weak_tests", "wrong_ref", "hardcoded")


# A weak model does not only make subtle mistakes -- it also produces answers
# that are simply poor. Without this third behaviour the fake weak solver scores
# 0.80+ on everything (a "subtly wrong" solution still passes most tests), the
# weak gate never opens, and no candidate is ever accepted.
#
# Each poor solution still passes at least one visible test, so that the
# deployed predicate's "no zeros" rule is exercised rather than trivially
# violated by every weak attempt.
POOR_SOLUTIONS: dict[str, str] = {
    "sum_even": "def sum_even(nums: list[int]) -> int:\n    return 0\n",
    "reverse_words": "def reverse_words(s: str) -> str:\n    return s\n",
    "count_vowels": "def count_vowels(s: str) -> int:\n    return 0\n",
    "max_subarray_sum": "def max_subarray_sum(nums: list[int]) -> int:\n    return 0\n",
    "is_balanced": "def is_balanced(s: str) -> bool:\n    return True\n",
    "grade_bucket": "def grade_bucket(score: int) -> str:\n    return 'F'\n",
    "is_valid_password": "def is_valid_password(s: str) -> bool:\n    return False\n",
}
