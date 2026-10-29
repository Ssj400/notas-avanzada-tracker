"""Read the public grade sheet without inventing official grading formulas."""

import csv
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation

SHEET_ID = "1xOywnk0h-9JFxPDQKpZmB0jL-rQOKxdeBnGg3V4KBEA"
SOURCE_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid=579970152"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=579970152"


class SheetError(ValueError):
    """The downloaded document does not match the expected public sheet."""


def normalize(value):
    text = unicodedata.normalize("NFKD", value)
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).lower().split())


def number(value, row, column):
    value = value.strip()
    if not value:
        return None
    try:
        result = Decimal(value.replace(",", "."))
    except InvalidOperation as exc:
        raise SheetError(f"Valor no numérico en fila {row}, columna {column}.") from exc
    if not result.is_finite() or not 0 <= result <= 100:
        raise SheetError(f"Valor fuera de rango en fila {row}, columna {column}.")
    return result


def parse_sheet(text):
    if text.lstrip().startswith("<"):
        raise SheetError("Google devolvió una página HTML, no el CSV público. Revisa el acceso a la planilla.")
    try:
        rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff")), strict=True))
    except csv.Error as exc:
        raise SheetError("El CSV recibido no es válido.") from exc
    header_index = next((i for i, row in enumerate(rows[:10]) if row and normalize(row[0]) == "n° alumno"), None)
    if header_index is None:
        raise SheetError("No se encontró el encabezado N° Alumno. La estructura puede haber cambiado.")
    headers = [normalize(h) for h in rows[header_index]]
    used_headers = [h for h in headers if h]
    if len(set(used_headers)) != len(used_headers):
        raise SheetError("Hay encabezados repetidos en la planilla.")
    definitions = [
        ("final", "Nota final", "Totales", "nota final"),
        ("banner", "Nota Banner", "Totales", "nota banner"),
        ("presentation", "Presentación", "Totales", "nota presentacion"),
        ("catedra", "Cátedra", "Categorías", "nota catedra"),
        ("written", "Evaluaciones escritas", "Categorías", "nota e. escritas"),
        ("i1", "Interrogación 1", "Evaluaciones escritas", "i1"),
        ("i2", "Interrogación 2", "Evaluaciones escritas", "i2"),
        ("exam", "Examen", "Evaluaciones escritas", "examen"),
        ("bonus", "Bonificación ECA", "Otros", "bonificacion eca"),
    ]
    activities = sorted((h for h in headers if re.fullmatch(r"ac\d+", h)), key=lambda h: int(h[2:]))
    if not activities:
        raise SheetError("La planilla no contiene columnas de actividades.")
    definitions += [(h, h.upper(), "Actividades", h) for h in activities]
    missing = [h for _, _, _, h in definitions if h not in headers]
    if missing:
        raise SheetError("Faltan columnas esperadas: " + ", ".join(missing))
    indices = {key: headers.index(header) for key, _, _, header in definitions}
    students, seen = [], set()
    for line, row in enumerate(rows[header_index + 1:], header_index + 2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(headers):
            raise SheetError(f"La fila {line} tiene una cantidad inesperada de columnas.")
        student_id = row[0].strip()
        if not re.fullmatch(r"[0-9A-Za-z]{5,20}", student_id):
            raise SheetError(f"Identificador inválido en fila {line}.")
        if student_id in seen:
            raise SheetError(f"Identificador duplicado en fila {line}.")
        seen.add(student_id)
        students.append({"id": student_id, "grades": {key: number(row[index], line, headers[index]) for key, index in indices.items()}, "changes": {}})
    if not students:
        raise SheetError("La planilla no contiene alumnos.")
    active = [key for key in activities if any((s["grades"][key] or 0) > 0 for s in students)]
    for student in students:
        values = [student["grades"][key] for key in active]
        student["grades"]["activities"] = sum(values) / len(values) if values and all(v is not None for v in values) else None
        student["grades"] = {key: float(value) if value is not None else None for key, value in student["grades"].items()}
    metrics = []
    for key, label, group, _ in definitions:
        metrics.append({
            "key": key, "label": label, "group": group,
            "unit": "points" if key in activities or key == "bonus" else "grade",
            "available": any((s["grades"][key] or 0) > 0 for s in students),
            "description": ("Puntos publicados en la planilla; no convertidos a nota." if key in activities else "Valor publicado por la planilla; no recalculado por esta aplicación."),
        })
    metrics.insert(5, {
        "key": "activities", "label": "Promedio actividades", "group": "Categorías", "unit": "points",
        "available": bool(active),
        "description": "Media simple de puntos de " + (", ".join(k.upper() for k in active) or "ninguna actividad") + ". Incluye ceros individuales; excluye columnas sin valores positivos. No es la nota oficial de Cátedra.",
    })
    inactive = [m["label"] for m in metrics if not m["available"]]
    warnings = [
        "Las notas finales, de presentación y por categoría son las publicadas por la planilla y pueden ser provisionales. No se infieren ponderaciones ni aprobación.",
        "Una columna sin valores positivos se marca sin resultados detectados: no confirma si está pendiente o si todos obtuvieron cero. Los ceros individuales sí se conservan en columnas disponibles.",
        "El promedio de actividades usa puntos, no notas. Los valores vacíos se excluyen del ranking; un promedio con algún dato vacío queda sin calcular.",
    ]
    if inactive:
        warnings.append("Sin resultados positivos detectados: " + ", ".join(inactive) + ".")
    return {"source_url": SOURCE_URL, "students": students, "metrics": metrics, "warnings": warnings}
