#!/usr/bin/env python3
"""Panel nativo de notas. Los nombres se guardan solo en este equipo."""

import argparse
import copy
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import (
    QAbstractTableModel, QModelIndex, QObject, QRunnable, QSortFilterProxyModel,
    Qt, QThreadPool, QTimer, QUrl, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QPalette
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QTableView, QVBoxLayout, QWidget,
    QAbstractItemView,
)

from grades import SOURCE_URL
from rankings import DEFAULT_ALIASES, MY_ID, display_name, rank_students, validate_aliases, validate_student_id
from tracker import Store


ROOT = Path(__file__).resolve().parent
MAX_ALIAS_BYTES = 1024 * 1024
NUMBER_ROLE = int(Qt.ItemDataRole.UserRole) + 1
ID_ROLE = NUMBER_ROLE + 1
# Retain running Python wrappers even if the window closes during a download.
_RUNNING_JOBS = set()


def finish_background_jobs():
    """Finish bounded network work before Qt/Python tear down signal objects."""
    QThreadPool.globalInstance().waitForDone()


def read_aliases(path):
    """Read a bounded, validated alias map; never read spreadsheet data here."""
    with Path(path).open("rb") as source:
        raw = source.read(MAX_ALIAS_BYTES + 1)
    if len(raw) > MAX_ALIAS_BYTES:
        raise ValueError("El archivo de nombres supera el límite de 1 MB.")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("El archivo no contiene un JSON UTF-8 válido.") from exc
    return validate_aliases(value)


def write_local_json(path, value):
    """Atomically save local settings with owner-only permissions."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_aliases(path, aliases):
    """Save only explicit aliases, never spreadsheet data or selected user."""
    write_local_json(path, validate_aliases(aliases))


def read_student_id(path):
    with Path(path).open("rb") as source:
        raw = source.read(4097)
    if len(raw) > 4096:
        raise ValueError("El archivo de usuario es demasiado grande.")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("El archivo de usuario no es JSON válido.") from exc
    if not isinstance(value, dict) or set(value) != {"student_id"}:
        raise ValueError("El archivo de usuario no tiene el formato esperado.")
    return validate_student_id(value["student_id"])


def choose_student_id(parent, current):
    """Ask on every launch, prefilled with the last selected student."""
    dialog = ProfileDialog(current, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.student_id
    return None


def format_number(value, signed=False):
    if value is None:
        return "—"
    return (f"{value:+.2f}" if signed else f"{value:.2f}").replace(".", ",")


def format_time(value):
    if not value:
        return "Sin consulta todavía"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%d/%m/%Y · %H:%M:%S")
    except (ValueError, AttributeError):
        return "Fecha no disponible"


def label(text="", name=None, wrap=False):
    widget = QLabel()
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setText(text)
    widget.setWordWrap(wrap)
    if name:
        widget.setObjectName(name)
    return widget


class ProfileDialog(QDialog):
    """A local profile selector, not authentication to the public spreadsheet."""

    def __init__(self, current, parent=None):
        super().__init__(parent)
        self.student_id = current
        self.aliases = getattr(parent, "aliases", DEFAULT_ALIASES)
        self.setWindowTitle("Entrar · Notas IIC2233")
        self.setMinimumWidth(440)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(14)
        layout.addWidget(label("IIC2233 · PERFIL LOCAL", "eyebrow"))
        layout.addWidget(label("Entrar al panel", "heading"))
        layout.addWidget(label("Elige tu número de alumno para ver tus posiciones y destacar tu fila.", "muted", wrap=True))
        layout.addWidget(label("Número de alumno"))
        self.student_input = QLineEdit(current)
        self.student_input.setAccessibleName("Número de alumno del perfil")
        self.student_input.setPlaceholderText("Ej. 26663279")
        self.student_input.setMaxLength(20)
        self.student_input.selectAll()
        layout.addWidget(self.student_input)
        self.preview = label("", "muted", wrap=True)
        layout.addWidget(self.preview)
        self.error = label("", "error", wrap=True)
        self.error.hide()
        layout.addWidget(self.error)
        layout.addWidget(label("Se recuerda el último usuario, pero puedes cambiarlo en cada inicio o desde el panel. No es autenticación: la planilla es pública.", "muted", wrap=True))
        buttons = QDialogButtonBox()
        enter = buttons.addButton("Entrar", QDialogButtonBox.ButtonRole.AcceptRole)
        enter.setObjectName("primary")
        enter.setDefault(True)
        buttons.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.student_input.textChanged.connect(self.update_preview)
        self.update_preview(current)

    def update_preview(self, text):
        try:
            student_id = validate_student_id(text)
        except ValueError:
            self.preview.setText("Ingresa un número de alumno válido.")
        else:
            self.preview.setText(display_name(student_id, self.aliases))

    def accept(self):
        try:
            self.student_id = validate_student_id(self.student_input.text())
        except ValueError as exc:
            self.error.setText(str(exc))
            self.error.show()
            self.student_input.setFocus()
            return
        super().accept()


class FetchSignals(QObject):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()


class FetchJob(QRunnable):
    """No widgets or window references: safe to finish after a window closes."""

    def __init__(self, store, refresh):
        super().__init__()
        self.store = store
        self.refresh = refresh
        self.signals = FetchSignals()

    @pyqtSlot()
    def run(self):
        try:
            try:
                payload = self.store.get(refresh=self.refresh)
            except Exception:
                # Never expose a traceback, fetched notes or arbitrary remote text.
                self.signals.failed.emit(
                    "No se pudo consultar la planilla. Revisa tu conexión y vuelve a intentar."
                )
            else:
                self.signals.succeeded.emit(payload)
        finally:
            self.signals.finished.emit()
            _RUNNING_JOBS.discard(self)


class MetricCard(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("metricCard")
        self.setMinimumWidth(140)
        self.setMinimumHeight(138)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(6)
        self.title = label(title, "cardTitle", wrap=True)
        self.value = label("—", "cardValue")
        self.detail = label("Esperando datos", "muted", wrap=True)
        self.detail.setMinimumHeight(36)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)

    def update_metric(self, metric, ranked, student_id=MY_ID):
        if metric is None or not metric.get("available"):
            self.value.setText("—")
            self.detail.setText("Sin resultados disponibles")
            return
        own = next((entry for entry in ranked if entry["student"]["id"] == student_id), None)
        if own is None:
            self.value.setText("—")
            self.detail.setText("Sin valor publicado para tu ID")
            return
        self.value.setText(f"#{own['rank']}")
        unit = "ptos." if metric["unit"] == "points" else "nota"
        self.detail.setText(f"{format_number(own['value'])} {unit} · entre {len(ranked)} estudiantes")


class RankingModel(QAbstractTableModel):
    HEADERS = ("Pos.", "Estudiante", "ID", "Valor", "Final¹", "Cátedra¹", "Actividades²", "Δ valor³")

    def __init__(self, parent=None, student_id=MY_ID):
        super().__init__(parent)
        self.student_id = student_id
        self.rows = []
        self.aliases = dict(DEFAULT_ALIASES)
        self.metric_key = "final"
        self.metric_unit = "grade"

    def set_ranking(self, rows, aliases, key, unit):
        self.beginResetModel()
        self.rows = rows
        self.aliases = aliases
        self.metric_key = key
        self.metric_unit = unit
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section == 3:
                return "Valor · " + ("puntos" if self.metric_unit == "points" else "nota")
            return self.HEADERS[section]
        if role == Qt.ItemDataRole.ToolTipRole:
            return {
                0: "Posición de competición: 1, 2, 2, 4. Los empates conservan posición.",
                1: "Doble clic para cambiar el nombre solo en este equipo.",
                4: "Total publicado; puede ser provisional.",
                5: "Cátedra publicada; puede ser provisional.",
                6: "Media de puntos en actividades disponibles, no una nota.",
                7: "Cambio de la métrica desde la consulta anterior al último cambio detectado.",
            }.get(section)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        entry = self.rows[index.row()]
        student = entry["student"]
        student_id = student["id"]
        col = index.column()
        numeric = {
            0: entry["rank"], 3: entry["value"],
            4: student["grades"].get("final"),
            5: student["grades"].get("catedra"),
            6: student["grades"].get("activities"),
            7: student.get("changes", {}).get(self.metric_key),
        }
        if role == ID_ROLE:
            return student_id
        if role == NUMBER_ROLE:
            if col in numeric:
                return numeric[col]
            return display_name(student_id, self.aliases).casefold() if col == 1 else student_id
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return {1: "🥇 1", 2: "🥈 2", 3: "🥉 3"}.get(entry["rank"], str(entry["rank"]))
            if col == 1:
                return display_name(student_id, self.aliases) + (" · Tú" if student_id == self.student_id else "")
            if col == 2:
                return student_id
            return format_number(numeric[col], signed=col == 7)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if col in (1, 2) else Qt.AlignmentFlag.AlignRight))
        if role == Qt.ItemDataRole.BackgroundRole and student_id == self.student_id:
            return QColor("#193a3b")
        if role == Qt.ItemDataRole.ForegroundRole:
            if student_id == self.student_id:
                return QColor("#d7f4a3")
            if col == 7 and numeric[7] is not None:
                return QColor("#72dfc1" if numeric[7] >= 0 else "#ffb5a9")
        if role == Qt.ItemDataRole.FontRole and (student_id == self.student_id or col == 3):
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{self.headerData(col, Qt.Orientation.Horizontal)}: {self.data(index)}"
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{display_name(student_id, self.aliases)} · ID {student_id} · posición {entry['rank']}" + (" · Tú" if student_id == self.student_id else "")
        return None


class RankingProxy(QSortFilterProxyModel):
    """Top refers to original ranking, never to a sorted/filtered table index."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = ""
        self.limit = 25
        self.setSortRole(NUMBER_ROLE)

    def set_filters(self, query, limit):
        self.query = query.strip().casefold()
        self.limit = limit
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        entry = model.rows[row]
        if self.limit and entry["rank"] > self.limit:
            return False
        student_id = entry["student"]["id"]
        return not self.query or self.query in student_id.casefold() or self.query in display_name(student_id, model.aliases).casefold()

    def lessThan(self, left, right):
        lhs = self.sourceModel().data(left, NUMBER_ROLE)
        rhs = self.sourceModel().data(right, NUMBER_ROLE)
        if lhs is None:
            return rhs is not None
        if rhs is None:
            return False
        if lhs == rhs:
            return self.sourceModel().data(left, ID_ROLE) < self.sourceModel().data(right, ID_ROLE)
        return lhs < rhs


STYLE = """
QWidget { background: #0e1726; color: #e5edf5; font-size: 13px; }
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget { background: #0e1726; }
QFrame#sidebar { background: #101e30; border-right: 1px solid #253349; }
QFrame#sidebar QLabel { background: transparent; }
QLabel#brand { font-size: 22px; font-weight: 800; color: #d7f4a3; }
QLabel#eyebrow { color: #72dfc1; font-size: 11px; font-weight: 700; }
QLabel#heading { font-size: 29px; font-weight: 800; }
QLabel#sectionTitle { font-size: 19px; font-weight: 700; }
QLabel#muted, QLabel#cardTitle { color: #b0bfd0; }
QLabel#cardValue { color: #d7f4a3; font-size: 32px; font-weight: 800; }
QFrame#metricCard, QFrame#podium { background: #152237; border: 1px solid #2b3a50; border-radius: 12px; }
QFrame#metricCard QLabel, QFrame#podium QLabel { background: transparent; }
QLabel#notice { background: #152d32; color: #c4eadf; border: 1px solid #2e615e; border-radius: 8px; padding: 12px; }
QLabel#warning { background: #352a22; color: #ffdcaa; border: 1px solid #766044; border-radius: 8px; padding: 12px; }
QLabel#error { background: #37252d; color: #ffd0c8; border: 1px solid #815151; border-radius: 8px; padding: 12px; }
QPushButton, QComboBox, QLineEdit { background: #18273b; border: 1px solid #45566c; border-radius: 7px; min-height: 26px; padding: 6px 10px; }
QPushButton:hover { background: #243a50; border-color: #72dfc1; }
QPushButton:pressed { background: #30465c; }
QPushButton#primary { background: #d7f4a3; color: #122328; font-weight: 700; border-color: #d7f4a3; }
QPushButton#primary:hover { background: #e6ffc0; }
QPushButton:disabled { color: #9aabbc; background: #263347; border-color: #39495f; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QTableView:focus, QCheckBox:focus { border: 2px solid #72dfc1; }
QComboBox QAbstractItemView { background: #18273b; selection-background-color: #315653; color: #e5edf5; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 18px; height: 18px; }
QCheckBox::indicator:unchecked { background: #18273b; border: 1px solid #71839b; border-radius: 4px; }
QCheckBox::indicator:checked { background: #72dfc1; border: 4px solid #315653; border-radius: 4px; }
QTableView { background: #111e30; alternate-background-color: #152237; border: 1px solid #34445b; border-radius: 8px; gridline-color: #28374c; selection-background-color: #315653; selection-color: #ffffff; }
QTableView::item { padding: 9px; border-bottom: 1px solid #243348; }
QHeaderView::section { background: #1c2c42; color: #cbd9e7; padding: 10px 8px; border: 0; border-bottom: 1px solid #45566c; font-weight: 600; }
QToolTip { background: #24374d; color: #ffffff; border: 1px solid #72dfc1; padding: 6px; }
QScrollBar:vertical { background: #111e30; width: 12px; }
QScrollBar::handle:vertical { background: #536780; min-height: 24px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QStatusBar { color: #b0bfd0; border-top: 1px solid #253349; }
QSplitter::handle { background: #253349; width: 2px; }
"""


class MainWindow(QMainWindow):
    def __init__(self, data_dir=None, store=None, autoload=True, student_id=None):
        super().__init__()
        self.setWindowTitle("IIC2233 · Mis notas")
        self.resize(1360, 940)
        self.setMinimumSize(880, 660)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.data_dir = Path(data_dir) if data_dir is not None else ROOT / ".data"
        self.profile_path = self.data_dir / "profile.json"
        profile_error = None
        if student_id is None:
            try:
                student_id = read_student_id(self.profile_path)
            except FileNotFoundError:
                student_id = MY_ID
            except (OSError, ValueError):
                student_id = MY_ID
                profile_error = "No se pudo leer el último usuario. Puedes elegirlo con Cambiar usuario."
        self.student_id = validate_student_id(student_id)
        self.alias_path = self.data_dir / "aliases.json"
        self.store = store if store is not None else Store(cache_path=self.data_dir / "grades.json")
        self.payload = None
        self.busy = False
        self._closed = False
        self._job = None
        self._initial_request = True
        self._refresh_cached = False
        app = QApplication.instance()
        if app is not None and not app.property("gradePoolShutdownConnected"):
            app.aboutToQuit.connect(finish_background_jobs)
            app.setProperty("gradePoolShutdownConnected", True)
        self.explicit_aliases = {}
        alias_error = None
        try:
            self.explicit_aliases = read_aliases(self.alias_path)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            alias_error = "No se pudieron cargar los nombres locales. Se usan los predeterminados. " + str(exc)
        self.aliases = dict(DEFAULT_ALIASES, **self.explicit_aliases)
        self._build_ui()
        self.setStyleSheet(STYLE)
        self.timer = QTimer(self)
        self.timer.setInterval(5 * 60 * 1000)
        self.timer.timeout.connect(self.refresh)
        self.auto_checkbox.toggled.connect(self._auto_changed)
        self.auto_checkbox.setChecked(True)
        if alias_error:
            self.persistence_warning.setText(alias_error)
            self.persistence_warning.show()
        if profile_error:
            self.profile_warning.setText(profile_error)
            self.profile_warning.show()
        if autoload:
            QTimer.singleShot(0, self.load_initial)

    def _build_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(208)
        sidebar.setMaximumWidth(270)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 28, 22, 24)
        side.setSpacing(14)
        side.addWidget(label("IIC2233", "brand"))
        side.addWidget(label("PROGRAMACIÓN\nAVANZADA", "eyebrow"))
        side.addSpacing(25)
        side.addWidget(label("TU ESPACIO", "eyebrow"))
        self.identity = label(display_name(self.student_id, self.aliases), wrap=True)
        side.addWidget(self.identity)
        self.identity_id = label("ID " + self.student_id, "muted")
        side.addWidget(self.identity_id)
        self.user_button = QPushButton("Cambiar &usuario…")
        self.user_button.clicked.connect(self.change_user)
        side.addWidget(self.user_button)
        self.position_button = QPushButton("Ver &mi posición")
        self.position_button.clicked.connect(self.show_my_position)
        side.addWidget(self.position_button)
        side.addSpacing(14)
        side.addWidget(label("NOMBRES LOCALES", "eyebrow"))
        self.rename_button = QPushButton("Cambiar &nombre")
        self.rename_button.setToolTip("Selecciona una fila o haz doble clic en un estudiante. Un nombre vacío restaura su ID.")
        self.rename_button.clicked.connect(self.rename_selected)
        self.rename_button.setEnabled(False)
        side.addWidget(self.rename_button)
        self.import_button = QPushButton("Importar JSON…")
        self.import_button.setToolTip("Fusionar nombres; los importados reemplazan los IDs coincidentes. Máximo 2000 nombres / 1 MB.")
        self.import_button.clicked.connect(self.import_aliases)
        side.addWidget(self.import_button)
        self.export_button = QPushButton("Exportar JSON…")
        self.export_button.setToolTip("Exporta solo nombres guardados explícitamente, nunca las notas.")
        self.export_button.clicked.connect(self.export_aliases)
        side.addWidget(self.export_button)
        side.addWidget(label("Los nombres viven en tu equipo.\nNo se modifica Google Sheets.", "muted", wrap=True))
        side.addStretch()
        source = QPushButton("Abrir planilla fuente ↗")
        source.clicked.connect(self.open_source)
        side.addWidget(source)
        side.addWidget(label("Solo lectura · datos públicos\nSin servidor local", "muted", wrap=True))
        splitter.addWidget(sidebar)

        scroll = QScrollArea()
        self.scroll_area = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(26, 26, 26, 22)
        self.content_layout.setSpacing(15)
        scroll.setWidget(content)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 1140])

        heading = QHBoxLayout()
        headings = QVBoxLayout()
        headings.addWidget(label("PANORAMA DEL CURSO", "eyebrow"))
        headings.addWidget(label("Tus notas, en perspectiva", "heading", wrap=True))
        self.timestamp = label("Sin consulta todavía", "muted")
        headings.addWidget(self.timestamp)
        heading.addLayout(headings, 1)
        actions = QVBoxLayout()
        self.refresh_button = QPushButton("↻ &Actualizar")
        self.refresh_button.setObjectName("primary")
        self.refresh_button.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_button)
        self.auto_checkbox = QCheckBox("Cada 5 minutos")
        self.auto_checkbox.setMinimumWidth(165)
        self.auto_checkbox.setAccessibleName("Actualizar automáticamente cada cinco minutos")
        actions.addWidget(self.auto_checkbox)
        heading.addLayout(actions)
        self.content_layout.addLayout(heading)

        self.fetch_warning = label("", "error", wrap=True)
        self.fetch_warning.hide()
        self.content_layout.addWidget(self.fetch_warning)
        self.storage_warning = label("", "warning", wrap=True)
        self.storage_warning.hide()
        self.content_layout.addWidget(self.storage_warning)
        self.persistence_warning = label("", "warning", wrap=True)
        self.persistence_warning.hide()
        self.content_layout.addWidget(self.persistence_warning)
        self.profile_warning = label("", "warning", wrap=True)
        self.profile_warning.hide()
        self.content_layout.addWidget(self.profile_warning)
        self.notice = label(
            "Totales provisionales: no se infieren ponderaciones ni aprobación. "
            "Actividades se expresa en puntos, no en nota. Los vacíos no participan del ranking.",
            "notice", wrap=True,
        )
        self.content_layout.addWidget(self.notice)

        self.card_layout = QGridLayout()
        self.card_layout.setSpacing(12)
        self._card_columns = 4
        self.cards = {}
        for index, (key, title) in enumerate((("final", "TU POSICIÓN · FINAL¹"), ("catedra", "CÁTEDRA¹"), ("activities", "ACTIVIDADES²"), ("i1", "INTERROGACIÓN 1"))):
            self.cards[key] = MetricCard(title)
            self.card_layout.addWidget(self.cards[key], 0, index)
            self.card_layout.setColumnStretch(index, 1)
        self.content_layout.addLayout(self.card_layout)

        title_row = QHBoxLayout()
        title_row.addWidget(label("Ranking del curso", "sectionTitle"))
        title_row.addStretch()
        self.count_label = label("Sin datos", "muted")
        title_row.addWidget(self.count_label)
        self.content_layout.addLayout(title_row)
        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.metric_combo = QComboBox()
        self.metric_combo.setMinimumWidth(210)
        self.metric_combo.setAccessibleName("Métrica y unidad del ranking")
        self.metric_combo.currentIndexChanged.connect(self.render_ranking)
        controls.addWidget(self.metric_combo, 3)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar nombre o ID…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Buscar estudiante por nombre o ID")
        self.search.textChanged.connect(self.apply_filters)
        controls.addWidget(self.search, 2)
        self.limit_combo = QComboBox()
        for text, limit in (("Top 10", 10), ("Top 25", 25), ("Top 50", 50), ("Todos", None)):
            self.limit_combo.addItem(text, limit)
        self.limit_combo.setCurrentIndex(1)
        self.limit_combo.setAccessibleName("Límite del ranking, incluidos empates")
        self.limit_combo.setToolTip("Top incluye todos los empates en la posición límite.")
        self.limit_combo.currentIndexChanged.connect(self.apply_filters)
        controls.addWidget(self.limit_combo)
        self.content_layout.addLayout(controls)
        self.metric_description = label("Esperando consulta de la planilla…", "muted", wrap=True)
        self.content_layout.addWidget(self.metric_description)

        self.podium = QFrame()
        self.podium.setObjectName("podium")
        podium_layout = QHBoxLayout(self.podium)
        podium_layout.setContentsMargins(15, 12, 15, 12)
        self.podium_labels = []
        for _ in range(3):
            item = label("", wrap=True)
            item.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            podium_layout.addWidget(item, 1)
            self.podium_labels.append(item)
        self.podium.hide()
        self.content_layout.addWidget(self.podium)

        self.model = RankingModel(self, student_id=self.student_id)
        self.proxy = RankingProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setAccessibleName("Ranking de estudiantes. Selecciona una fila para cambiar su nombre local.")
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col, width in ((0, 74), (1, 180), (2, 108), (3, 116), (4, 85), (5, 95), (6, 120), (7, 100)):
            self.table.setColumnWidth(col, width)
        self.table.setMinimumHeight(290)
        self.table.doubleClicked.connect(self._double_clicked)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.content_layout.addWidget(self.table, 1)
        self.empty_label = label("Consultando datos…", "muted", wrap=True)
        self.content_layout.addWidget(self.empty_label)
        self.content_layout.addWidget(label(
            "¹ Publicado por la planilla, posiblemente provisional.  ² Promedio de puntos de actividades disponibles.\n"
            "³ Δ de la métrica seleccionada; — indica que no hay cambio comparable registrado. "
            "Empates: 1, 2, 2, 4. Puedes ordenar las columnas sin alterar las posiciones.",
            "muted", wrap=True,
        ))
        self.statusBar().showMessage("Listo para consultar. No se modifica la planilla.")

    def _auto_changed(self, enabled):
        if enabled:
            self.timer.start()
        else:
            self.timer.stop()

    @pyqtSlot()
    def load_initial(self):
        self._start_request(refresh=False)

    @pyqtSlot()
    def refresh(self):
        self._start_request(refresh=True)

    def _start_request(self, refresh):
        if self.busy or self._closed:
            return
        self.busy = True
        self._initial_request = not refresh
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Consultando…")
        self.statusBar().showMessage("Consultando Google Sheets en segundo plano…")
        job = FetchJob(self.store, refresh)
        self._job = job
        job.signals.succeeded.connect(self._received)
        job.signals.failed.connect(self._failed)
        job.signals.finished.connect(self._finished)
        _RUNNING_JOBS.add(job)
        QThreadPool.globalInstance().start(job)

    @pyqtSlot(object)
    def _received(self, payload):
        if self._closed:
            return
        self.payload = payload
        self._refresh_cached = bool(self._initial_request and payload.get("stale"))
        self.timestamp.setText("Última consulta · " + format_time(payload.get("fetched_at")))
        self.timestamp.setToolTip(
            "Último cambio detectado · " + format_time(payload.get("changed_at"))
            + "\nComparación de cambios · " + format_time(payload.get("previous_at"))
        )
        if payload.get("stale"):
            self.fetch_warning.setText("COPIA ANTIGUA · " + (payload.get("error") or "No se pudo actualizar.") + " Usa Actualizar para reintentar.")
            self.fetch_warning.show()
        elif payload.get("error"):
            self.fetch_warning.setText(payload["error"])
            self.fetch_warning.show()
        else:
            self.fetch_warning.hide()
        self.storage_warning.setText(payload.get("storage_warning") or "")
        self.storage_warning.setVisible(bool(payload.get("storage_warning")))
        warnings = payload.get("warnings", [])
        self.notice.setText("\n".join(warnings) if warnings else "Totales provisionales. Actividades: puntos, no nota. Vacíos excluidos del ranking.")
        previous_key = self.metric_combo.currentData()
        self.metric_combo.blockSignals(True)
        self.metric_combo.clear()
        for metric in payload["metrics"]:
            unit = "puntos" if metric["unit"] == "points" else "nota"
            suffix = " · sin resultados" if not metric["available"] else ""
            self.metric_combo.addItem(f"{metric['group']} / {metric['label']} · {unit}{suffix}", metric["key"])
            self.metric_combo.setItemData(self.metric_combo.count() - 1, metric["description"], Qt.ItemDataRole.ToolTipRole)
        target = self.metric_combo.findData(previous_key or "final")
        self.metric_combo.setCurrentIndex(max(0, target))
        self.metric_combo.blockSignals(False)
        metrics = {m["key"]: m for m in payload["metrics"]}
        for key, card in self.cards.items():
            metric = metrics.get(key)
            ranking = rank_students(payload["students"], key) if metric and metric["available"] else []
            card.update_metric(metric, ranking, self.student_id)
        self.render_ranking()
        self.statusBar().showMessage("Copia antigua: reintenta actualizar." if payload.get("stale") else "Consulta completada · solo lectura · nombres locales")

    @pyqtSlot(str)
    def _failed(self, message):
        if self._closed:
            return
        self._refresh_cached = False
        if self.payload is not None:
            old = copy.deepcopy(self.payload)
            old.update(stale=True, error=message)
            self._received(old)
            self._refresh_cached = False
        else:
            self.fetch_warning.setText(message + " Pulsa Actualizar para reintentar.")
            self.fetch_warning.show()
            self.empty_label.setText("No hay datos disponibles todavía. Puedes volver a intentar con Actualizar.")
        self.statusBar().showMessage("No se pudo actualizar. Puedes reintentar.")

    @pyqtSlot()
    def _finished(self):
        if self._closed:
            return
        self.busy = False
        self._job = None
        self.refresh_button.setText("↻ &Actualizar")
        self.refresh_button.setEnabled(True)
        if self._refresh_cached:
            self._refresh_cached = False
            QTimer.singleShot(0, self.refresh)

    def current_metric(self):
        if not self.payload:
            return None
        return next((m for m in self.payload["metrics"] if m["key"] == self.metric_combo.currentData()), None)

    def render_ranking(self, *_):
        metric = self.current_metric()
        if metric is None:
            return
        selected = self.selected_id()
        ranking = rank_students(self.payload["students"], metric["key"]) if metric["available"] else []
        self.model.set_ranking(ranking, self.aliases, metric["key"], metric["unit"])
        self.metric_description.setText(metric["description"] + (" · Sin resultados positivos detectados; ranking no disponible." if not metric["available"] else ""))
        self.apply_filters()
        self._update_podium()
        if selected:
            self.select_id(selected)

    def apply_filters(self, *_):
        if not hasattr(self, "proxy"):
            return
        self.proxy.set_filters(self.search.text(), self.limit_combo.currentData())
        shown = self.proxy.rowCount()
        self.count_label.setText(f"{shown} de {self.model.rowCount()} con valor")
        self.empty_label.setVisible(shown == 0)
        metric = self.current_metric()
        if not self.payload:
            self.empty_label.setText("Consultando datos…")
        elif metric and not metric["available"]:
            self.empty_label.setText("Esta métrica no tiene resultados positivos detectados. Elige otra métrica.")
        elif not self.model.rows:
            self.empty_label.setText("No hay valores publicados para esta métrica.")
        else:
            self.empty_label.setText("Ningún estudiante coincide. Borra la búsqueda o elige Todos.")
        self._selection_changed()

    def _update_podium(self):
        entries = [entry for entry in self.model.rows if entry["rank"] <= 3]
        # Never show an arbitrary subset of a tied podium.
        show = len(entries) == 3 and self.width() >= 1160
        self.podium.setVisible(show)
        if show:
            for widget, entry in zip(self.podium_labels, entries):
                student_id = entry["student"]["id"]
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}[entry["rank"]]
                widget.setText(f"{medal} #{entry['rank']} · {display_name(student_id, self.aliases)}\n{format_number(entry['value'])}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "model"):
            columns = 4 if self.width() >= 1160 else 2
            if columns != self._card_columns:
                self._card_columns = columns
                for card in self.cards.values():
                    self.card_layout.removeWidget(card)
                for index, card in enumerate(self.cards.values()):
                    self.card_layout.addWidget(card, index // columns, index % columns)
                for col in range(4):
                    self.card_layout.setColumnStretch(col, 1 if col < columns else 0)
            self._update_podium()

    def selected_id(self):
        if not hasattr(self, "table"):
            return None
        rows = self.table.selectionModel().selectedRows()
        return self.proxy.data(rows[0], ID_ROLE) if rows else None

    def select_id(self, student_id):
        for row, entry in enumerate(self.model.rows):
            if entry["student"]["id"] == student_id:
                index = self.proxy.mapFromSource(self.model.index(row, 0))
                if index.isValid():
                    self.table.selectRow(index.row())
                    self.table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
                    return True
        return False

    def _selection_changed(self, *_):
        self.rename_button.setEnabled(self.selected_id() is not None)

    @pyqtSlot()
    def show_my_position(self):
        self.search.clear()
        self.limit_combo.setCurrentIndex(self.limit_combo.findText("Todos"))
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        if self.select_id(self.student_id):
            self.scroll_area.ensureWidgetVisible(self.table, 0, 20)
            self.table.setFocus()
            self.statusBar().showMessage("Tu fila está seleccionada. Búsqueda y límite despejados.")
        else:
            self.statusBar().showMessage("Tu ID no tiene valor disponible en esta métrica. Prueba otra métrica.")

    def _double_clicked(self, index):
        if index.column() in (1, 2):
            self.rename_selected()

    @pyqtSlot()
    def change_user(self):
        student_id = choose_student_id(self, self.student_id)
        if student_id is not None:
            self.set_student_id(student_id)
            if self.payload:
                self.show_my_position()

    def set_student_id(self, student_id):
        self.student_id = validate_student_id(student_id)
        self.model.student_id = self.student_id
        self.identity.setText(display_name(self.student_id, self.aliases))
        self.identity_id.setText("ID " + self.student_id)
        if self.payload:
            metrics = {metric["key"]: metric for metric in self.payload["metrics"]}
            for key, card in self.cards.items():
                metric = metrics.get(key)
                ranked = rank_students(self.payload["students"], key) if metric and metric["available"] else []
                card.update_metric(metric, ranked, self.student_id)
            self.render_ranking()
        try:
            write_local_json(self.profile_path, {"student_id": self.student_id})
        except (OSError, ValueError):
            self.profile_warning.setText("Usuario cambiado en esta sesión, pero no se pudo recordar en disco.")
            self.profile_warning.show()
        else:
            self.profile_warning.hide()
            self.statusBar().showMessage("Usuario seleccionado y guardado. Puedes volver a cambiarlo al iniciar.")

    @pyqtSlot()
    def rename_selected(self):
        student_id = self.selected_id()
        if student_id is None:
            self.statusBar().showMessage("Selecciona un estudiante para cambiar su nombre local.")
            return
        text, accepted = QInputDialog.getText(
            self, "Nombre local", f"Nombre para ID {student_id} (vacío para restaurar):",
            QLineEdit.EchoMode.Normal, display_name(student_id, self.aliases) if student_id in self.aliases else "",
        )
        if not accepted:
            return
        try:
            aliases = dict(self.explicit_aliases)
            if text.strip():
                aliases.update(validate_aliases({student_id: text}))
            else:
                aliases.pop(student_id, None)
            self.set_aliases(validate_aliases(aliases))
        except ValueError as exc:
            self.statusBar().showMessage("Nombre no válido: " + str(exc))

    def set_aliases(self, aliases):
        self.explicit_aliases = validate_aliases(aliases)
        self.aliases = dict(DEFAULT_ALIASES, **self.explicit_aliases)
        self.identity.setText(display_name(self.student_id, self.aliases))
        self.render_ranking()
        try:
            write_aliases(self.alias_path, self.explicit_aliases)
        except (OSError, ValueError):
            self.persistence_warning.setText(
                "No se pudieron guardar los nombres en disco. Los cambios siguen en memoria durante esta sesión. "
                "Puedes exportarlos o intentar cambiarlos nuevamente."
            )
            self.persistence_warning.show()
            self.statusBar().showMessage("Nombres actualizados en memoria; persistencia pendiente.")
        else:
            self.persistence_warning.hide()
            self.statusBar().showMessage("Nombres guardados localmente. Google Sheets no se modifica.")

    @pyqtSlot()
    def import_aliases(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar nombres locales", "", "Nombres JSON (*.json);;Todos los archivos (*)")
        if not path:
            return
        try:
            imported = read_aliases(path)
            combined = dict(self.explicit_aliases)
            combined.update(imported)
            combined = validate_aliases(combined)
        except (OSError, ValueError) as exc:
            self.persistence_warning.setText("No se importaron nombres: " + str(exc))
            self.persistence_warning.show()
            return
        self.set_aliases(combined)

    @pyqtSlot()
    def export_aliases(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportar solo nombres locales", "nombres-locales.json", "Nombres JSON (*.json)")
        if not path:
            return
        destination = Path(path).expanduser()
        # Do not let the aliases-only exporter overwrite the application/cache.
        resolved = destination.resolve()
        cache_path = getattr(self.store, "cache_path", None)
        protected = {self.alias_path.resolve(), self.profile_path.resolve(), Path(__file__).resolve(), (ROOT / "grades.py").resolve(), (ROOT / "tracker.py").resolve(), (ROOT / "rankings.py").resolve()}
        if cache_path is not None:
            protected.add(Path(cache_path).resolve())
        if resolved in protected:
            self.persistence_warning.setText("Elige otro archivo para exportar; esta ruta está reservada por la aplicación.")
            self.persistence_warning.show()
            return
        try:
            write_aliases(destination, self.explicit_aliases)
        except (OSError, ValueError):
            self.persistence_warning.setText("No se pudo exportar el archivo. Los nombres siguen disponibles en memoria.")
            self.persistence_warning.show()
        else:
            self.statusBar().showMessage("Se exportaron únicamente los nombres guardados explícitamente, sin notas.")

    @pyqtSlot()
    def open_source(self):
        # Never open a URL supplied by aliases, file input or a remote payload.
        url = QUrl(SOURCE_URL)
        if url.scheme() == "https" and url.host() == "docs.google.com":
            if not QDesktopServices.openUrl(url):
                self.statusBar().showMessage("No se pudo abrir la planilla con la aplicación predeterminada.")

    def closeEvent(self, event):
        self._closed = True
        self.timer.stop()
        # The window closes immediately. aboutToQuit drains bounded network
        # work before Qt/Python destruction; deleted widgets disconnect safely.
        super().closeEvent(event)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".data", help="Directorio local para caché y nombres (predeterminado: .data junto a la aplicación)")
    args = parser.parse_args(argv)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Notas IIC2233")
    app.setOrganizationName("LocalGradeTracker")
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#0e1726"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e5edf5"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#152237"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#18273b"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e5edf5"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#18273b"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e5edf5"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#315653"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    window = MainWindow(data_dir=args.data_dir.expanduser(), autoload=False)
    selected = choose_student_id(window, window.student_id)
    if selected is None:
        window.close()
        return 0
    window.set_student_id(selected)
    window.show()
    QTimer.singleShot(0, window.load_initial)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
