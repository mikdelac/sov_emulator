"""Composants d'interface de la console, dessinés d'après le design system.

Qt ne sait pas rendre les interrupteurs à bascule ni les voyants du design
system avec une feuille de style seule : ces deux-là sont peints à la main,
tout le reste s'assemble à partir de widgets standards habillés par
`theme.stylesheet()`.
"""

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QSizePolicy, QSlider,
    QVBoxLayout, QWidget,
)

from . import theme


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------
def label(text: str, role: str = "", *, wrap: bool = False,
          align=None) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setObjectName(role)
    widget.setWordWrap(wrap)
    if align is not None:
        widget.setAlignment(align)
    return widget


def separator() -> QFrame:
    line = QFrame()
    line.setObjectName("separator")
    line.setFixedHeight(1)
    return line


class Card(QFrame):
    """Surface blanche à bord fin, brique de base de toutes les sections."""

    def __init__(self, title: str = "", *, variant: str = "card",
                 spacing: int = theme.S3):
        super().__init__()
        self.setObjectName(variant)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(theme.S4, theme.S3, theme.S4, theme.S3)
        self.body.setSpacing(spacing)
        if title:
            self.body.addWidget(label(title, "sectionTitle"))

    def add(self, widget) -> None:
        self.body.addWidget(widget)


class Lamp(QWidget):
    """Voyant rond : éteint, allumé, ou allumé en rouge pour une anomalie."""

    def __init__(self, warning: bool = False, diameter: int = 14):
        super().__init__()
        self._on = False
        self._warning = warning
        self._diameter = diameter
        self.setFixedSize(diameter + 8, diameter + 8)

    def set_on(self, value: bool) -> None:
        if value != self._on:
            self._on = value
            self.update()

    def paintEvent(self, event) -> None:      # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centre = QRectF(4, 4, self._diameter, self._diameter)
        if self._on:
            fill = theme.ERROR if self._warning else theme.PRIMARY[500]
            halo = theme.ERROR_BG if self._warning else theme.PRIMARY[50]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(halo))
            painter.drawEllipse(centre.adjusted(-4, -4, 4, 4))
        else:
            fill = theme.GREY[200]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawEllipse(centre)


class ToggleSwitch(QAbstractButton):
    """Interrupteur à bascule du design system : piste 48×26, bouton 20 px."""

    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(48, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:      # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        track = theme.PRIMARY[500] if self.isChecked() else theme.GREY[300]
        painter.setBrush(QColor(track))
        painter.drawRoundedRect(QRectF(0, 0, 48, 26), 13, 13)
        x = 25.0 if self.isChecked() else 3.0
        painter.setBrush(QColor(theme.SURFACE))
        painter.drawEllipse(QRectF(x, 3, 20, 20))


class Meter(QWidget):
    """Barre de remplissage horizontale, pour un rapport cyclique ou un %."""

    def __init__(self, height: int = 8):
        super().__init__()
        self._ratio = 0.0
        self._active = True
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

    def set_ratio(self, ratio, active: bool = True) -> None:
        self._ratio = max(0.0, min(1.0, ratio or 0.0))
        self._active = active
        self.update()

    def paintEvent(self, event) -> None:      # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = self.height() / 2
        painter.setBrush(QColor(theme.GREY[100]))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()),
                                radius, radius)
        if self._ratio > 0:
            colour = theme.PRIMARY[500] if self._active else theme.GREY[400]
            painter.setBrush(QColor(colour))
            painter.drawRoundedRect(
                QRectF(0, 0, self.width() * self._ratio, self.height()),
                radius, radius)


# ---------------------------------------------------------------------------
# Lignes d'entrée
# ---------------------------------------------------------------------------
class DigitalInputRow(QWidget):
    """Une entrée tout ou rien : broche, libellé, niveau et bascule."""

    toggled_to = pyqtSignal(str, bool)

    def __init__(self, digital):
        super().__init__()
        self.digital = digital
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S3)

        pin = label(digital.pin, "pin")
        pin.setFixedWidth(42)
        row.addWidget(pin)

        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label(digital.label))
        text.addWidget(label(f"alimente {digital.alarm}", "muted"))
        row.addLayout(text, 1)

        self.level = label("0", "value")
        self.level.setFixedWidth(16)
        row.addWidget(self.level)

        self.switch = ToggleSwitch()
        self.switch.toggled.connect(self._emit)
        row.addWidget(self.switch)

        self.setToolTip(
            f"{digital.pin} → {digital.flag}\n"
            f"La polarité (contact NO ou NC) est configurée en flash : "
            f"c'est l'alarme {digital.alarm} qui dit ce que le firmware en fait.")

    def _emit(self, checked: bool) -> None:
        self.level.setText("1" if checked else "0")
        self.toggled_to.emit(self.digital.key, checked)

    def set_value(self, closed: bool) -> None:
        self.switch.blockSignals(True)
        self.switch.setChecked(closed)
        self.switch.blockSignals(False)
        self.level.setText("1" if closed else "0")


class AnalogInputRow(QWidget):
    """Une entrée analogique : curseur, grandeur physique et tension ADC."""

    value_changed = pyqtSignal(str, float)

    def __init__(self, analog):
        super().__init__()
        self.analog = analog
        self._steps = max(1, round((analog.maximum - analog.minimum) / analog.step))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.S1)

        head = QHBoxLayout()
        head.setSpacing(theme.S2)
        pin = label(analog.pin, "pin")
        pin.setFixedWidth(42)
        head.addWidget(pin)
        head.addWidget(label(f"{analog.label} · ch {analog.channel}"), 1)
        self.value = label("", "value")
        head.addWidget(self.value)
        self.volts = label("", "monoMuted")
        self.volts.setFixedWidth(78)
        self.volts.setAlignment(Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.volts)
        outer.addLayout(head)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, self._steps)
        self.slider.valueChanged.connect(self._emit)
        outer.addWidget(self.slider)

        if analog.note:
            outer.addWidget(label(analog.note, "muted", wrap=True))

        self.set_value(analog.default)

    def _to_physical(self, index: int) -> float:
        return self.analog.minimum + index * self.analog.step

    def _emit(self, index: int) -> None:
        value = self._to_physical(index)
        self._refresh(value)
        self.value_changed.emit(self.analog.key, value)

    def _refresh(self, value: float) -> None:
        self.value.setText(f"{value:.{self.analog.decimals}f} {self.analog.unit}")
        self.volts.setText(f"{self.analog.to_volts(value):.3f} V")

    def set_value(self, value: float) -> None:
        index = round((value - self.analog.minimum) / self.analog.step)
        self.slider.blockSignals(True)
        self.slider.setValue(max(0, min(self._steps, index)))
        self.slider.blockSignals(False)
        self._refresh(value)


# ---------------------------------------------------------------------------
# Lignes de sortie
# ---------------------------------------------------------------------------
class OutputRow(QWidget):
    """Une sortie tout ou rien lue dans le registre ODR du port."""

    def __init__(self, output):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S3)

        self.lamp = Lamp(warning=output.warning)
        row.addWidget(self.lamp)

        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label(output.label))
        if output.note:
            text.addWidget(label(output.note, "muted"))
        row.addLayout(text, 1)

        self.state = label("repos", "mono")
        row.addWidget(self.state)
        pin = label(output.pin, "pin")
        pin.setFixedWidth(42)
        pin.setAlignment(Qt.AlignmentFlag.AlignRight
                         | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(pin)

        self.setToolTip(f"{output.pin} · {output.flag} · gpo_update (hal.c)")
        self._warning = output.warning

    def set_value(self, active) -> None:
        if active is None:
            self.lamp.set_on(False)
            self.state.setText("—")
            return
        self.lamp.set_on(bool(active))
        self.state.setText("ACTIF" if active else "repos")
        colour = (theme.ERROR if self._warning else theme.PRIMARY[600]) \
            if active else theme.GREY[600]
        self.state.setStyleSheet(f"color: {colour}; font-weight: 600;")


class PwmRow(QWidget):
    """Une sortie PWM : rapport cyclique lu dans TIMx_CCR rapporté à ARR."""

    def __init__(self, pwm):
        super().__init__()
        self.pwm = pwm
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.S1)

        head = QHBoxLayout()
        head.setSpacing(theme.S2)
        self.lamp = Lamp()
        head.addWidget(self.lamp)
        head.addWidget(label(pwm.label), 1)
        self.value = label("—", "value")
        head.addWidget(self.value)
        pin = label(pwm.pin, "pin")
        pin.setFixedWidth(42)
        pin.setAlignment(Qt.AlignmentFlag.AlignRight
                         | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(pin)
        outer.addLayout(head)

        self.meter = Meter()
        outer.addWidget(self.meter)
        self.detail = label(pwm.note, "muted")
        outer.addWidget(self.detail)

    def set_value(self, entry) -> None:
        if entry is None:
            self.value.setText("—")
            self.meter.set_ratio(0)
            self.lamp.set_on(False)
            return
        duty, enabled = entry["duty"], entry["enabled"]
        if duty is None:
            self.value.setText("—")
            self.meter.set_ratio(0)
            self.lamp.set_on(False)
            self.detail.setText(f"{self.pwm.note} · base de temps à l'arrêt")
            return
        self.value.setText(f"{duty * 100:.1f} %")
        self.meter.set_ratio(duty, enabled)
        self.lamp.set_on(duty > 0 and enabled)
        detail = f"{self.pwm.note} · CCR {entry['ccr']} / ARR {entry['arr']}"
        if entry["saturated"]:
            detail += " · CCR > ARR, sortie bloquée à 100 %"
        if not enabled:
            detail += " · sortie désactivée (CCER)"
        self.detail.setText(detail)


# ---------------------------------------------------------------------------
# Vues d'état du firmware
# ---------------------------------------------------------------------------
class StateList(QWidget):
    """Les onze états de `ier_state_e`, celui en cours mis en évidence."""

    def __init__(self, states):
        super().__init__()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        self._rows = {}
        for number, name, hint in states:
            row = QFrame()
            row.setObjectName("stateRow")
            inner = QHBoxLayout(row)
            inner.setContentsMargins(theme.S3, 5, theme.S3, 5)
            inner.setSpacing(theme.S2)
            dot = Lamp(diameter=8)
            inner.addWidget(dot)
            number_label = label(str(number), "monoMuted")
            number_label.setFixedWidth(18)
            inner.addWidget(number_label)
            name_label = label(name, "mono")
            inner.addWidget(name_label, 1)
            inner.addWidget(label(hint, "muted"))
            column.addWidget(row)
            self._rows[name] = (row, dot, name_label)
        self.set_active(None)

    def set_active(self, active_name) -> None:
        for name, (row, dot, name_label) in self._rows.items():
            active = name == active_name
            alarm = active and name == "ALARMS_STATE"
            border = (theme.ERROR if alarm
                      else theme.PRIMARY[500] if active else "transparent")
            background = (theme.ERROR_BG if alarm
                          else theme.PRIMARY[50] if active else "transparent")
            row.setStyleSheet(
                f"QFrame#stateRow {{ border: 1px solid {border};"
                f" border-radius: {theme.R_SM}px; background: {background}; }}")
            dot._warning = alarm
            dot.set_on(active)
            name_label.setStyleSheet(
                f"color: {theme.SECONDARY[500] if active else theme.GREY[700]};"
                f" font-weight: {600 if active else 400};")


class FlagList(QWidget):
    """Liste de drapeaux levés — alarmes ou bits de statut."""

    def __init__(self, empty_text: str, *, warning: bool = False):
        super().__init__()
        self._empty = empty_text
        self._warning = warning
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(theme.S1)
        self.set_flags([])

    def set_flags(self, flags) -> None:
        while self._column.count():
            item = self._column.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not flags:
            self._column.addWidget(label(self._empty, "muted"))
            return
        for flag in flags:
            chip = QLabel(flag)
            chip.setObjectName("mono")
            colour = theme.ERROR if self._warning else theme.PRIMARY[700]
            background = theme.ERROR_BG if self._warning else theme.PRIMARY[50]
            chip.setStyleSheet(
                f"color: {colour}; background: {background}; font-weight: 600;"
                f" padding: 3px 8px; border-radius: {theme.R_SM}px;"
                f" font-size: {theme.SIZE_SMALL}px;")
            self._column.addWidget(chip)


class ReadbackTable(QWidget):
    """Ce que le firmware a réellement lu après conversion et filtrage."""

    def __init__(self, readbacks):
        super().__init__()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.S1)
        self._values = {}
        for readback in readbacks:
            row = QHBoxLayout()
            row.setSpacing(theme.S2)
            row.addWidget(label(readback.label, "muted"), 1)
            value = label("—", "value")
            value.setAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)
            value.setFixedWidth(96)
            row.addWidget(value)
            column.addLayout(row)
            self._values[readback.expr] = (value, readback)

    def update_values(self, readbacks) -> None:
        for expr, (widget, meta) in self._values.items():
            value = readbacks.get(expr)
            widget.setText("—" if value is None
                           else f"{value:.{meta.decimals}f} {meta.unit}")


class LogView(QWidget):
    """Journal horodaté en temps simulé, comme la maquette.

    Les marques reprennent celles du design : « — » information, « · »
    commande envoyée, « › » transition observée, « ! » erreur.
    """

    MARKS = {"info": ("—", theme.GREY[500]),
             "cmd": ("·", theme.GREY[600]),
             "event": ("›", theme.PRIMARY[600]),
             "error": ("!", theme.ERROR)}

    def __init__(self):
        super().__init__()
        from PyQt6.QtWidgets import QPlainTextEdit
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(500)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.SURFACE}; border: none;"
            f" font-family: {theme.MONO_STACK};"
            f" font-size: {theme.SIZE_SMALL}px; }}")
        column.addWidget(self.view)

    def append(self, clock: float, text: str, kind: str = "info") -> None:
        mark, colour = self.MARKS.get(kind, self.MARKS["info"])
        escaped = (text.replace("&", "&amp;").replace("<", "&lt;")
                   .replace(">", "&gt;"))
        self.view.appendHtml(
            f'<span style="color:{theme.GREY[600]}">{clock:8.2f} s</span>'
            f' <span style="color:{colour};font-weight:600">{mark}</span>'
            f' <span style="color:{theme.SECONDARY[900]}">{escaped}</span>')

    def clear(self) -> None:
        self.view.clear()
