"""Grade downloading, local caching and change detection; independent of Qt."""

import copy
import http.client
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from grades import CSV_URL, SOURCE_URL, SheetError, parse_sheet

MAX_DOWNLOAD = 5 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def download():
    request = urllib.request.Request(CSV_URL, headers={"User-Agent": "LocalGradeTracker/1.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise SheetError("La respuesta supera el límite de descarga de 5 MB.")
    return parse_sheet(data.decode("utf-8-sig"))


def valid_cache(payload):
    """Treat even a syntactically valid local cache as untrusted input."""
    try:
        if not isinstance(payload, dict) or payload["source_url"] != SOURCE_URL:
            return False
        for field in ("fetched_at", "changed_at", "previous_at"):
            value = payload[field]
            if value is None and field == "previous_at":
                continue
            if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
                return False
        if not isinstance(payload["stale"], bool) or not (payload["error"] is None or isinstance(payload["error"], str)):
            return False
        if payload.get("storage_warning") is not None and not isinstance(payload["storage_warning"], str):
            return False
        if not isinstance(payload["warnings"], list) or not all(isinstance(w, str) for w in payload["warnings"]):
            return False
        metrics, students = payload["metrics"], payload["students"]
        if not isinstance(metrics, list) or not 1 <= len(metrics) <= 200:
            return False
        keys = set()
        for metric in metrics:
            if not isinstance(metric, dict) or not all(isinstance(metric[f], str) for f in ("key", "label", "group", "unit", "description")):
                return False
            if metric["unit"] not in ("grade", "points") or not isinstance(metric["available"], bool) or metric["key"] in keys:
                return False
            keys.add(metric["key"])
        if not isinstance(students, list) or not 1 <= len(students) <= 10000:
            return False
        seen = set()
        for student in students:
            if not isinstance(student, dict) or not isinstance(student["id"], str) or not re.fullmatch(r"[0-9A-Za-z]{5,20}", student["id"]) or student["id"] in seen:
                return False
            seen.add(student["id"])
            grades, changes = student["grades"], student["changes"]
            if not isinstance(grades, dict) or set(grades) != keys or not isinstance(changes, dict) or not set(changes) <= keys:
                return False
            for value in grades.values():
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100):
                    return False
            for value in changes.values():
                if type(value) not in (int, float) or not math.isfinite(value) or not -100 <= value <= 100:
                    return False
        return True
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


class Store:
    def __init__(self, cache_path=None, loader=download):
        self.cache_path = cache_path
        self.loader = loader
        self.lock = threading.Lock()
        self.payload = None
        self.last_attempt = 0
        if cache_path and cache_path.is_file():
            try:
                if cache_path.stat().st_size > MAX_DOWNLOAD:
                    return
                envelope = json.loads(cache_path.read_text(encoding="utf-8"))
                cached = envelope.get("data")
                if envelope.get("version") == 1 and valid_cache(cached):
                    cached.update(stale=True, error="Copia local de la sesión anterior; actualiza para consultar Google.")
                    self.payload = cached
            except (OSError, ValueError, AttributeError):
                pass

    def get(self, refresh=False):
        with self.lock:
            if self.payload is not None and (not refresh or time.monotonic() - self.last_attempt < 5):
                return copy.deepcopy(self.payload)
            self.last_attempt = time.monotonic()
            try:
                fresh = self.loader()
                timestamp = now()
                old = self.payload
                changed = old is None or any(fresh[key] != old.get(key) for key in ("metrics", "warnings"))
                old_students = {s["id"]: s for s in old["students"]} if old else {}
                changed = changed or set(old_students) != {s["id"] for s in fresh["students"]}
                for student in fresh["students"]:
                    prior = old_students.get(student["id"])
                    if prior and student["grades"] != prior["grades"]:
                        changed = True
                    if prior:
                        student["changes"] = {
                            key: round(value - prior["grades"][key], 8)
                            for key, value in student["grades"].items()
                            if value is not None and prior["grades"].get(key) is not None and value != prior["grades"][key]
                        }
                if not changed and old:
                    # Keep the last actual change visible through unchanged polling cycles.
                    for student in fresh["students"]:
                        student["changes"] = old_students[student["id"]].get("changes", {})
                fresh.update(
                    fetched_at=timestamp, changed_at=timestamp if changed else old["changed_at"],
                    previous_at=old["fetched_at"] if changed and old else (old.get("previous_at") if old else None),
                    stale=False, error=None, storage_warning=None,
                )
                self.payload = fresh
                try:
                    self.save()
                except OSError:
                    fresh["storage_warning"] = "No se pudo guardar la copia local. Los datos actuales siguen disponibles en memoria."
                return copy.deepcopy(fresh)
            except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as exc:
                message = str(exc) if isinstance(exc, SheetError) else "No se pudo consultar Google Sheets. Revisa tu conexión o el acceso público e intenta actualizar nuevamente."
                if self.payload is None:
                    raise SheetError(message) from exc
                self.payload.update(stale=True, error=message)
                return copy.deepcopy(self.payload)

    def save(self):
        if self.cache_path is None:
            return
        self.cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = self.cache_path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"version": 1, "data": self.payload}, output, ensure_ascii=False, allow_nan=False)
        temp.replace(self.cache_path)
