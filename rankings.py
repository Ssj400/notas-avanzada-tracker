"""Pure ranking and alias helpers, independent of the graphical interface."""

import math
import re
import unicodedata

MY_ID = "26663279"
DEFAULT_ALIASES = {MY_ID: "José Garrillo"}


def validate_student_id(value):
    if not isinstance(value, str):
        raise ValueError("El número de alumno debe ser texto.")
    value = value.strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{5,20}", value):
        raise ValueError("Ingresa un número de alumno de 5 a 20 caracteres alfanuméricos.")
    return value


def rank_students(students, metric_key):
    """Competition ranking: 1, 2, 2, 4. Missing values do not participate."""
    candidates = []
    for student in students:
        value = student["grades"].get(metric_key)
        if type(value) in (int, float) and math.isfinite(value):
            candidates.append((student, value))
    candidates.sort(key=lambda pair: (-pair[1], pair[0]["id"]))
    result, previous, rank = [], None, 0
    for index, (student, value) in enumerate(candidates, 1):
        if previous is None or value != previous:
            rank = index
        result.append({"student": student, "rank": rank, "value": value})
        previous = value
    return result


def validate_aliases(value):
    if not isinstance(value, dict) or len(value) > 2000:
        raise ValueError("Los nombres deben ser un objeto JSON con un máximo de 2000 alumnos.")
    cleaned = {}
    for student_id, name in value.items():
        if not isinstance(student_id, str) or not re.fullmatch(r"[0-9A-Za-z]{5,20}", student_id):
            raise ValueError("Cada número de alumno debe tener entre 5 y 20 caracteres alfanuméricos.")
        if not isinstance(name, str):
            raise ValueError("Cada nombre debe ser texto.")
        name = name.strip()
        if not name or len(name) > 80 or any(unicodedata.category(c).startswith("C") for c in name):
            raise ValueError("Cada nombre debe tener entre 1 y 80 caracteres, sin caracteres de control.")
        cleaned[student_id] = name
    return cleaned


def display_name(student_id, aliases):
    return aliases.get(student_id, DEFAULT_ALIASES.get(student_id, student_id))
