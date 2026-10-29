"""Offline tests for the native interface; no Google requests or HTTP server."""

import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

try:
    from PyQt6.QtCore import QCoreApplication, QEvent, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QDialog
    from desktop import (
        ID_ROLE, MAX_ALIAS_BYTES, NUMBER_ROLE, MainWindow, ProfileDialog, RankingModel,
        RankingProxy, read_aliases, read_student_id, write_aliases,
    )
    from rankings import MY_ID, rank_students
except ModuleNotFoundError as exc:
    if exc.name and exc.name.startswith("PyQt6"):
        QApplication = None
    else:
        raise


def sample_payload():
    metrics = []
    for key, unit in (("final", "grade"), ("catedra", "grade"), ("activities", "points"), ("i1", "grade"), ("exam", "grade")):
        metrics.append({
            "key": key, "label": key, "group": "Prueba", "unit": unit,
            "available": key != "exam", "description": "Dato de prueba: " + key,
        })
    students = []
    for student_id, value in (("100001", 7.0), ("100002", 6.5), ("100003", 6.5), (MY_ID, 0.0), ("100004", None)):
        students.append({
            "id": student_id,
            "grades": {m["key"]: (0.0 if m["key"] == "exam" else value) for m in metrics},
            "changes": {"final": 0.25} if student_id == MY_ID else {},
        })
    return {
        "students": students, "metrics": metrics, "warnings": ["Totales provisionales."],
        "fetched_at": "2026-10-29T12:30:00+00:00", "changed_at": "2026-10-29T12:30:00+00:00",
        "previous_at": "2026-10-28T12:30:00+00:00", "stale": False,
        "error": None, "source_url": "https://invalid.example/not-used",
    }


@unittest.skipIf(QApplication is None, "PyQt6 no está instalado")
class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def window(self, store=None):
        window = MainWindow(data_dir=self.directory, store=store, autoload=False)
        self.windows.append(window)
        return window

    def wait_until(self, condition, timeout=3000):
        end = time.monotonic() + timeout / 1000
        while not condition() and time.monotonic() < end:
            QTest.qWait(10)
        self.assertTrue(condition(), "La operación Qt no finalizó dentro del límite")

    def test_atomic_explicit_aliases_and_permissions(self):
        path = self.directory / "new" / "aliases.json"
        write_aliases(path, {MY_ID: " Otro nombre "})
        self.assertEqual(read_aliases(path), {MY_ID: "Otro nombre"})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        path.chmod(0o644)
        write_aliases(path, {"100001": "Compañero"})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_failed_atomic_replace_preserves_previous_file(self):
        path = self.directory / "aliases.json"
        write_aliases(path, {MY_ID: "Anterior"})
        with patch("desktop.os.replace", side_effect=OSError("Disco no disponible")):
            with self.assertRaises(OSError):
                write_aliases(path, {MY_ID: "Nuevo"})
        self.assertEqual(read_aliases(path), {MY_ID: "Anterior"})
        self.assertEqual(list(self.directory.iterdir()), [path])

    def test_oversized_invalid_and_non_object_import(self):
        path = self.directory / "aliases.json"
        for data in (b"x" * (MAX_ALIAS_BYTES + 1), b"[]", b"{", b' {"12345": ""}', b"\xff", b"[" * 2000 + b"]" * 2000):
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                read_aliases(path)

    def test_default_not_persisted_and_alias_html_is_plain_text(self):
        window = self.window()
        self.assertEqual(window.identity.text(), "José Garrillo")
        window.set_aliases({})
        self.assertEqual(json.loads(window.alias_path.read_text()), {})
        window.set_aliases({MY_ID: "<b>Nombre local</b>"})
        self.assertEqual(window.identity.text(), "<b>Nombre local</b>")
        self.assertEqual(window.identity.textFormat(), Qt.TextFormat.PlainText)
        window.set_aliases({})
        self.assertEqual(window.identity.text(), "José Garrillo")

    def test_switch_user_updates_cards_highlight_and_saves_profile(self):
        window = self.window()
        window._received(sample_payload())
        window.set_student_id("100001")
        self.assertEqual(window.student_id, "100001")
        self.assertEqual(window.cards["final"].value.text(), "#1")
        self.assertEqual(window.cards["catedra"].value.text(), "#1")
        self.assertEqual(window.identity.text(), "100001")
        self.assertEqual(window.identity_id.text(), "ID 100001")
        self.assertEqual(read_student_id(window.profile_path), "100001")
        self.assertIn("Tú", window.model.data(window.model.index(0, 1)))
        self.assertNotIn("Tú", window.model.data(window.model.index(3, 1)))
        window.show_my_position()
        self.assertEqual(window.selected_id(), "100001")
        window.set_aliases({"100001": "Otro estudiante"})
        self.assertEqual(window.identity.text(), "Otro estudiante")
        self.assertEqual(window.aliases[MY_ID], "José Garrillo")
        reopened = self.window()
        self.assertEqual(reopened.student_id, "100001")
        self.assertEqual(reopened.identity.text(), "Otro estudiante")

    def test_switch_user_dialog_cancel_keeps_current_profile(self):
        window = self.window()
        window.set_student_id("100001")
        with patch("desktop.choose_student_id", return_value=None) as choose:
            window.change_user()
        choose.assert_called_once_with(window, "100001")
        self.assertEqual(window.student_id, "100001")
        self.assertEqual(read_student_id(window.profile_path), "100001")

    def test_switch_user_dialog_accept_selects_new_row(self):
        window = self.window()
        window._received(sample_payload())
        window.search.setText("does not match")
        with patch("desktop.choose_student_id", return_value="100002"):
            window.change_user()
        self.assertEqual(window.student_id, "100002")
        self.assertEqual(window.selected_id(), "100002")
        self.assertEqual(window.search.text(), "")
        self.assertEqual(window.cards["final"].value.text(), "#2")

    def test_invalid_id_does_not_change_profile_and_save_failure_is_visible(self):
        window = self.window()
        with self.assertRaises(ValueError):
            window.set_student_id("bad!")
        self.assertEqual(window.student_id, MY_ID)
        with patch("desktop.write_local_json", side_effect=OSError("read-only")):
            window.set_student_id("100001")
        self.assertEqual(window.student_id, "100001")
        self.assertFalse(window.profile_warning.isHidden())
        window.set_student_id("100001")
        self.assertTrue(window.profile_warning.isHidden())

    def test_profile_dialog_prefills_previous_id_validates_and_accepts(self):
        dialog = ProfileDialog("100002")
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.student_input.text(), "100002")
        dialog.student_input.setText("bad!")
        dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertFalse(dialog.error.isHidden())
        dialog.student_input.setText(MY_ID)
        self.assertEqual(dialog.preview.text(), "José Garrillo")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.student_id, MY_ID)

    def test_corrupt_profile_is_ignored_and_warned(self):
        (self.directory / "profile.json").write_text('{"student_id": "bad!"}')
        window = self.window()
        self.assertEqual(window.student_id, MY_ID)
        self.assertFalse(window.profile_warning.isHidden())

    def test_startup_always_prompts_and_remembers_last_profile(self):
        script = """
import sys
from PyQt6.QtCore import QTimer
import desktop
def choose(parent, current):
    print('PREFILLED:' + current, flush=True)
    return sys.argv[2]
desktop.choose_student_id = choose
desktop.MainWindow.load_initial = lambda self: QTimer.singleShot(0, self.close)
sys.exit(desktop.main(['--data-dir', sys.argv[1]]))
"""
        for chosen, expected in (("100001", MY_ID), ("100002", "100001")):
            result = subprocess.run(
                [sys.executable, "-c", script, str(self.directory), chosen],
                cwd=Path(__file__).resolve().parent,
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PREFILLED:" + expected, result.stdout)
            self.assertEqual(read_student_id(self.directory / "profile.json"), chosen)

    def test_persistence_failure_keeps_in_memory_and_can_retry(self):
        window = self.window()
        with patch("desktop.write_aliases", side_effect=OSError("Solo lectura")):
            window.set_aliases({MY_ID: "En memoria"})
        self.assertEqual(window.aliases[MY_ID], "En memoria")
        self.assertFalse(window.persistence_warning.isHidden())
        window.set_aliases(window.explicit_aliases)
        self.assertTrue(window.persistence_warning.isHidden())
        self.assertEqual(read_aliases(window.alias_path), {MY_ID: "En memoria"})

    def test_ranking_competition_zero_null_numeric_roles(self):
        payload = sample_payload()
        model = RankingModel()
        model.set_ranking(rank_students(payload["students"], "final"), {}, "final", "grade")
        self.assertEqual([entry["rank"] for entry in model.rows], [1, 2, 2, 4])
        self.assertEqual(model.rowCount(), 4)
        self.assertEqual(model.data(model.index(3, 3), NUMBER_ROLE), 0.0)
        self.assertEqual(model.data(model.index(3, 3)), "0,00")
        self.assertEqual(model.data(model.index(3, 7)), "+0,25")
        proxy = RankingProxy()
        proxy.setSourceModel(model)
        proxy.set_filters("", 2)
        self.assertEqual(proxy.rowCount(), 3)  # include tie at the cutoff
        proxy.sort(3, Qt.SortOrder.DescendingOrder)
        self.assertEqual(proxy.data(proxy.index(0, 0), ID_ROLE), "100001")

    def test_my_position_clears_filters_selects_zero_and_scrolls(self):
        window = self.window()
        window._received(sample_payload())
        window.search.setText("not-found")
        self.assertEqual(window.proxy.rowCount(), 0)
        window.show_my_position()
        self.assertEqual(window.search.text(), "")
        self.assertIsNone(window.limit_combo.currentData())
        self.assertEqual(window.selected_id(), MY_ID)
        self.assertTrue(window.rename_button.isEnabled())
        self.assertEqual(window.cards["final"].value.text(), "#4")

    def test_unavailable_metric_never_ranks_all_zero_column(self):
        window = self.window()
        window._received(sample_payload())
        window.metric_combo.setCurrentIndex(window.metric_combo.findData("exam"))
        self.assertEqual(window.model.rowCount(), 0)
        self.assertIn("no tiene resultados", window.empty_label.text())

    def test_metric_and_search_survive_refresh(self):
        window = self.window()
        payload = sample_payload()
        window._received(payload)
        window.metric_combo.setCurrentIndex(window.metric_combo.findData("activities"))
        window.search.setText("José")
        window._received(copy.deepcopy(payload))
        self.assertEqual(window.metric_combo.currentData(), "activities")
        self.assertEqual(window.search.text(), "José")
        self.assertEqual(window.proxy.rowCount(), 1)
        self.assertIn("puntos", window.model.headerData(3, Qt.Orientation.Horizontal))

    def test_cache_storage_warning_does_not_make_source_stale(self):
        window = self.window()
        payload = sample_payload()
        payload["storage_warning"] = "No se pudo guardar la copia local. Datos en memoria."
        window._received(payload)
        self.assertFalse(window.storage_warning.isHidden())
        self.assertEqual(window.storage_warning.text(), payload["storage_warning"])
        self.assertIn("Totales provisionales", window.notice.text())
        self.assertTrue(window.fetch_warning.isHidden())
        self.assertFalse(window.payload["stale"])
        self.assertIsNone(window.payload["error"])
        self.assertTrue(window.persistence_warning.isHidden())
        payload["storage_warning"] = None
        window._received(payload)
        self.assertTrue(window.storage_warning.isHidden())

    def test_export_only_explicit_aliases_not_grades_or_defaults(self):
        window = self.window()
        window._received(sample_payload())
        window.set_aliases({"100001": "Un nombre"})
        destination = self.directory / "export.json"
        with patch("desktop.QFileDialog.getSaveFileName", return_value=(str(destination), "")):
            window.export_aliases()
        self.assertEqual(json.loads(destination.read_text()), {"100001": "Un nombre"})

    def test_import_merges_validates_combined_limit(self):
        window = self.window()
        window.set_aliases({MY_ID: "Mío"})
        path = self.directory / "import.json"
        path.write_text(json.dumps({"100001": "Amiga"}))
        with patch("desktop.QFileDialog.getOpenFileName", return_value=(str(path), "")):
            window.import_aliases()
        self.assertEqual(window.explicit_aliases, {MY_ID: "Mío", "100001": "Amiga"})
        path.write_text(json.dumps({f"{i:05d}": "Alumno" for i in range(2000)}))
        before = dict(window.explicit_aliases)
        with patch("desktop.QFileDialog.getOpenFileName", return_value=(str(path), "")):
            window.import_aliases()
        self.assertEqual(window.explicit_aliases, before)
        self.assertFalse(window.persistence_warning.isHidden())

    def test_source_uses_fixed_url_not_payload(self):
        from grades import SOURCE_URL

        window = self.window()
        window._received(sample_payload())
        with patch("desktop.QDesktopServices.openUrl", return_value=True) as opened:
            window.open_source()
        self.assertEqual(opened.call_args.args[0].toString(), SOURCE_URL)

    def test_async_no_overlap_failure_then_retry(self):
        main_thread = threading.get_ident()
        release = threading.Event()

        class FakeStore:
            def __init__(self):
                self.calls = 0
                self.threads = []

            def get(self, refresh=False):
                self.calls += 1
                self.threads.append(threading.get_ident())
                release.wait(2)
                if self.calls == 1:
                    raise OSError("No network")
                return sample_payload()

        store = FakeStore()
        window = self.window(store)
        window.refresh()
        self.wait_until(lambda: store.calls == 1)
        window.refresh()
        self.assertFalse(window.refresh_button.isEnabled())
        self.assertEqual(store.calls, 1)
        release.set()
        self.wait_until(lambda: not window.busy)
        self.assertFalse(window.fetch_warning.isHidden())
        self.assertIsNone(window.payload)
        window.refresh()
        self.wait_until(lambda: not window.busy)
        self.assertIsNotNone(window.payload)
        self.assertTrue(window.fetch_warning.isHidden())
        self.assertTrue(all(thread != main_thread for thread in store.threads))

    def test_initial_cache_display_then_background_refresh(self):
        release = threading.Event()

        class FakeStore:
            def __init__(self):
                self.calls = []

            def get(self, refresh=False):
                self.calls.append(refresh)
                data = sample_payload()
                if not refresh:
                    data.update(stale=True, error="Copia local")
                else:
                    release.wait(2)
                return data

        store = FakeStore()
        window = self.window(store)
        window.load_initial()
        self.wait_until(lambda: len(store.calls) == 2)
        self.assertTrue(window.payload["stale"])
        self.assertIn("COPIA ANTIGUA", window.fetch_warning.text())
        release.set()
        self.wait_until(lambda: not window.busy)
        self.assertEqual(store.calls, [False, True])
        self.assertFalse(window.payload["stale"])

    def test_close_while_worker_running_no_thread_destruction(self):
        release = threading.Event()
        completed = threading.Event()

        class FakeStore:
            def get(self, refresh=False):
                release.wait(2)
                completed.set()
                return sample_payload()

        window = self.window(FakeStore())
        window.refresh()
        window.close()
        self.windows.remove(window)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        release.set()
        self.wait_until(completed.is_set)
        QTest.qWait(30)

    def test_automatic_timer_toggle(self):
        window = self.window()
        self.assertTrue(window.timer.isActive())
        self.assertEqual(window.timer.interval(), 300000)
        window.auto_checkbox.setChecked(False)
        self.assertFalse(window.timer.isActive())
        window.auto_checkbox.setChecked(True)
        self.assertTrue(window.timer.isActive())

    def test_cards_reflow_on_small_window(self):
        window = self.window()
        window._received(sample_payload())
        window.resize(880, 700)
        window.show()
        self.app.processEvents()
        self.assertEqual(window._card_columns, 2)
        self.assertTrue(window.podium.isHidden())
        window.resize(1360, 940)
        self.app.processEvents()
        self.assertEqual(window._card_columns, 4)
        self.assertFalse(window.podium.isHidden())

    def test_application_exit_with_running_worker(self):
        script = """
import sys
import time
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from desktop import MainWindow
from test_desktop import sample_payload
class FakeStore:
    def get(self, refresh=False):
        time.sleep(0.2)
        return sample_payload()
app = QApplication([])
window = MainWindow(data_dir=sys.argv[1], store=FakeStore(), autoload=False)
window.show()
window.refresh()
QTimer.singleShot(30, window.close)
app.exec()
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.directory)],
            cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("QThread: Destroyed", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
