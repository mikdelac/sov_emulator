"""Fenêtre principale de la console d'entrées / sorties IER.

Reprend l'organisation de la maquette « Console IO Renode v2 » — entrées à
gauche, carte et sorties au centre, état du firmware à droite — en la rendant
redimensionnable, ce qu'un plan de 1936 px de large ne permettait pas.

Différence de fond avec la maquette : celle-ci recalculait la machine à états
en JavaScript pour animer le rendu. Ici il n'y a aucun modèle local. Chaque
voyant, chaque état, chaque alarme est lu dans la mémoire du firmware qui
tourne réellement sous Renode. Une conséquence visible : les changements ne
sont pas instantanés — le firmware filtre les entrées tout ou rien sur 25
échantillons et lisse les analogiques, il faut donc quelques secondes de temps
simulé avant qu'une bascule se répercute. Les commandes envoyées apparaissent
immédiatement dans le journal, leurs effets quand le firmware les a vus.
"""

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QStatusBar, QVBoxLayout, QWidget,
)

from . import firmware as fw
from . import theme
from .session import Session
from .widgets import (
    AnalogInputRow, Card, DigitalInputRow, FlagList, LogView, OutputRow,
    PwmRow, ReadbackTable, StateList, label, separator,
)
from .worker import SessionWorker

_ANALOG_DEBOUNCE_MS = 60


def _scroll(widget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return area


def _column(spacing: int = theme.S4) -> tuple[QWidget, QVBoxLayout]:
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, theme.S2, 0)
    layout.setSpacing(spacing)
    return holder, layout


class ConsoleWindow(QMainWindow):
    """Fenêtre unique : pilote les entrées, observe l'état réel du firmware."""

    request_analog = pyqtSignal(str, float)
    request_digital = pyqtSignal(str, bool)
    request_macro = pyqtSignal(str)
    request_start = pyqtSignal()
    request_pause = pyqtSignal()
    request_reset = pyqtSignal()
    request_open = pyqtSignal()
    request_close = pyqtSignal()

    def __init__(self, session: Session, interval_ms: int = 150):
        super().__init__()
        self.setWindowTitle("Console d'entrées / sorties IER — émulation Renode")
        self.resize(1560, 940)
        self.setStyleSheet(theme.stylesheet())

        self._clock = 0.0
        self._running = False
        self._last_state = None
        self._last_alarms = None
        self._pending_analog: dict = {}

        self._build()

        self.thread = QThread(self)
        self.worker = SessionWorker(session, interval_ms)
        self.worker.moveToThread(self.thread)
        self._connect(session)
        self.thread.start()
        self.request_open.emit()

    # ------------------------------------------------------------------ vue
    def _build(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(theme.S5, theme.S4, theme.S5, theme.S3)
        outer.setSpacing(theme.S4)
        outer.addWidget(self._header())
        outer.addWidget(separator())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        for pane, stretch in ((self._inputs_pane(), 4),
                              (self._board_pane(), 4),
                              (self._firmware_pane(), 5)):
            splitter.addWidget(pane)
            splitter.setStretchFactor(splitter.count() - 1, stretch)
        outer.addWidget(splitter, 1)

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.status = QLabel("Démarrage de Renode…")
        self.statusBar().addWidget(self.status)
        self.cost = QLabel("")
        self.statusBar().addPermanentWidget(self.cost)

    def _header(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S5)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(
            "ÉMULATION RENODE · CARTE IERPCB001 v3.2 · STM32F303xC", "eyebrow"))
        titles.addWidget(label("Console d'entrées / sorties IER", "title"))
        row.addLayout(titles, 1)

        clock_box = QVBoxLayout()
        clock_box.setSpacing(0)
        clock_box.addWidget(label("TEMPS SIMULÉ", "eyebrow",
                                  align=Qt.AlignmentFlag.AlignRight))
        self.clock_label = label("0.00 s", "clock",
                                 align=Qt.AlignmentFlag.AlignRight)
        clock_box.addWidget(self.clock_label)
        row.addLayout(clock_box)

        self.run_button = QPushButton("Start")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self._toggle_run)
        self.run_button.setEnabled(False)
        row.addWidget(self.run_button)

        self.reset_button = QPushButton("Reset")
        self.reset_button.clicked.connect(self._reset)
        self.reset_button.setEnabled(False)
        row.addWidget(self.reset_button)
        return bar

    def _inputs_pane(self) -> QWidget:
        holder, column = _column()

        digital = Card("Entrées tout ou rien")
        digital.add(label(
            "gpi_update · la polarité NO/NC est un paramètre en flash, "
            "l'effet réel se lit dans les alarmes", "muted", wrap=True))
        self.digital_rows = {}
        for index, entry in enumerate(fw.DIGITAL_INPUTS):
            if index:
                digital.add(separator())
            row = DigitalInputRow(entry)
            row.toggled_to.connect(self.request_digital.emit)
            digital.add(row)
            self.digital_rows[entry.key] = row
        column.addWidget(digital)

        analog = Card("Entrées analogiques")
        analog.add(label("séquence ADC1 · valeurs converties par adc_conv",
                         "muted", wrap=True))
        self.analog_rows = {}
        for index, entry in enumerate(fw.ANALOG_INPUTS):
            if index:
                analog.add(separator())
            row = AnalogInputRow(entry)
            row.value_changed.connect(self._queue_analog)
            analog.add(row)
            self.analog_rows[entry.key] = row
        column.addWidget(analog)

        macros = Card("Macros")
        macros.add(label("exécutées par Renode depuis scripts/sensors.resc",
                         "muted", wrap=True))
        grid_holder = QWidget()
        grid = QGridLayout(grid_holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.S2)
        for index, macro in enumerate(fw.MACROS):
            button = QPushButton(macro.name)
            button.setObjectName("macro")
            button.clicked.connect(
                lambda _, m=macro: self._run_macro(m))
            grid.addWidget(button, index // 2, index % 2)
        macros.add(grid_holder)
        column.addWidget(macros)

        column.addStretch(1)
        return _scroll(holder)

    def _board_pane(self) -> QWidget:
        holder, column = _column()
        column.addWidget(self._board_card())

        outputs = Card("Sorties tout ou rien")
        outputs.add(label("gpo_update · état lu dans les registres GPIOx_ODR",
                          "muted", wrap=True))
        self.output_rows = {}
        for index, entry in enumerate(fw.DIGITAL_OUTPUTS):
            if index:
                outputs.add(separator())
            row = OutputRow(entry)
            outputs.add(row)
            self.output_rows[entry.key] = row
        column.addWidget(outputs)

        pwm = Card("Sorties PWM")
        pwm.add(label("rapport cyclique = TIMx_CCR / (ARR + 1) · "
                      "TIM8 BDTR.MOE n'est pas modélisé par Renode",
                      "muted", wrap=True))
        self.pwm_rows = {}
        for index, entry in enumerate(fw.PWM_OUTPUTS):
            if index:
                pwm.add(separator())
            row = PwmRow(entry)
            pwm.add(row)
            self.pwm_rows[entry.key] = row
        column.addWidget(pwm)

        column.addStretch(1)
        return _scroll(holder)

    def _board_card(self) -> QWidget:
        card = Card(variant="board", spacing=theme.S3)
        card.body.setContentsMargins(theme.S4, theme.S4, theme.S4, theme.S4)

        chip = QFrame()
        chip.setObjectName("chip")
        chip_layout = QVBoxLayout(chip)
        chip_layout.setContentsMargins(theme.S4, theme.S3, theme.S4, theme.S3)
        chip_layout.setSpacing(2)
        name = label("STM32F303xC", "boardText",
                     align=Qt.AlignmentFlag.AlignCenter)
        name.setStyleSheet("font-size: 18px; font-weight: 600; color: #fff;")
        chip_layout.addWidget(name)
        chip_layout.addWidget(label(
            "Cortex-M4F · 72 MHz · 256 Ko flash · 40 Ko SRAM · 8 Ko CCMRAM",
            "boardMono", align=Qt.AlignmentFlag.AlignCenter, wrap=True))
        card.add(chip)

        self.board_state = label("—", "boardText",
                                 align=Qt.AlignmentFlag.AlignCenter)
        self.board_state.setStyleSheet(
            f"font-family: {theme.MONO_STACK}; font-size: 15px;"
            f" font-weight: 600; color: {theme.LIME[500]};")
        card.add(self.board_state)

        peripherals = QLabel(
            "ADC1 + DMA1 ch1   ·   TIM8 / TIM4 PWM   ·   USART1 Modbus\n"
            "IWDG · RTC · CRC   ·   SPI2 esclave non simulé")
        peripherals.setObjectName("boardMono")
        peripherals.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.add(peripherals)
        return card

    def _firmware_pane(self) -> QWidget:
        holder, column = _column()

        states = Card("Machine à états")
        states.add(label("ier_core.ier_state · lu à chaque sondage",
                         "muted", wrap=True))
        self.state_list = StateList(fw.STATES)
        states.add(self.state_list)
        self.last_state = label("précédent : —", "muted")
        states.add(self.last_state)
        column.addWidget(states)

        alarms = Card("Alarmes et statut")
        self.control_source = label("source de commande : —", "muted", wrap=True)
        alarms.add(self.control_source)
        alarms.add(label("alarm1 + alarm2", "muted"))
        self.alarm_list = FlagList("aucune alarme levée", warning=True)
        alarms.add(self.alarm_list)
        alarms.add(separator())
        alarms.add(label("status", "muted"))
        self.status_list = FlagList("aucun bit de statut")
        alarms.add(self.status_list)
        column.addWidget(alarms)

        readbacks = Card("Relectures du firmware")
        readbacks.add(label(
            "ce que le firmware a lu après conversion, à comparer aux valeurs "
            "injectées · les lignes « échantillon » sont brutes, les autres "
            "sortent d'une moyenne glissante sur 20 points · l'écart de 10 °C "
            "sur l'eau vient de wt_offset, nul en flash non programmée",
            "muted", wrap=True))
        self.readback_table = ReadbackTable(
            list(fw.READBACKS) + list(fw.LIVE_READBACKS))
        readbacks.add(self.readback_table)
        column.addWidget(readbacks)

        journal = Card("Journal")
        header = QHBoxLayout()
        header.addWidget(label("horodaté en temps simulé", "muted"), 1)
        clear = QPushButton("Vider")
        clear.clicked.connect(lambda: self.log.clear())
        header.addWidget(clear)
        holder_header = QWidget()
        holder_header.setLayout(header)
        journal.add(holder_header)
        self.log = LogView()
        self.log.setMinimumHeight(180)
        self.log.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Expanding)
        journal.add(self.log)
        column.addWidget(journal, 1)
        return _scroll(holder)

    # -------------------------------------------------------------- câblage
    def _connect(self, session: Session) -> None:
        self.request_open.connect(self.worker.open)
        self.request_close.connect(self.worker.close)
        self.request_analog.connect(self.worker.set_analog)
        self.request_digital.connect(self.worker.set_digital)
        self.request_macro.connect(self.worker.run_macro)
        self.request_start.connect(self.worker.start)
        self.request_pause.connect(self.worker.pause)
        self.request_reset.connect(self.worker.reset)

        self.worker.opened.connect(self._on_opened)
        self.worker.failed.connect(self._on_failed)
        self.worker.polled.connect(self._on_snapshot)
        self.worker.executed.connect(self._on_executed)
        self.worker.errored.connect(self._on_error)
        self.worker.finished.connect(self.thread.quit)

        self._analog_timer = QTimer(self)
        self._analog_timer.setSingleShot(True)
        self._analog_timer.setInterval(_ANALOG_DEBOUNCE_MS)
        self._analog_timer.timeout.connect(self._flush_analog)

    # -------------------------------------------------------------- actions
    def _queue_analog(self, key: str, value: float) -> None:
        """Regroupe les crans d'un glissement en une seule commande.

        Un curseur traversé à la souris émet des dizaines de valeurs ; les
        envoyer toutes saturerait le Monitor sans rien apporter, seule la
        dernière compte.
        """
        self._pending_analog[key] = value
        self._analog_timer.start()

    def _flush_analog(self) -> None:
        pending, self._pending_analog = self._pending_analog, {}
        for key, value in pending.items():
            self.request_analog.emit(key, value)

    def _run_macro(self, macro) -> None:
        self.request_macro.emit(macro.name)
        for key, value in macro.patch.items():
            if key in self.analog_rows:
                self.analog_rows[key].set_value(float(value))
            elif key in self.digital_rows:
                self.digital_rows[key].set_value(bool(value))

    def _toggle_run(self) -> None:
        (self.request_pause if self._running else self.request_start).emit()
        self._running = not self._running
        self._refresh_run_button()

    def _reset(self) -> None:
        self.request_reset.emit()
        self._running = False
        self._refresh_run_button()
        for key, row in self.analog_rows.items():
            row.set_value(fw.ANALOG_BY_KEY[key].default)
        for row in self.digital_rows.values():
            row.set_value(False)

    def _refresh_run_button(self) -> None:
        self.run_button.setText("Pause" if self._running else "Start")
        self.run_button.setObjectName("secondary" if self._running else "primary")
        self.run_button.setStyleSheet("")      # force la réévaluation du QSS
        self.run_button.style().polish(self.run_button)

    # -------------------------------------------------------------- retours
    def _on_opened(self, info: dict) -> None:
        origin = ("Renode lancé par la console" if info["spawned"]
                  else "instance Renode existante")
        self.status.setText(
            f"{origin} · Monitor :{info['port']} · {info['elf']}")
        self.run_button.setEnabled(True)
        self.reset_button.setEnabled(True)
        self.log.append(0.0, f"session ouverte sur le port {info['port']}")
        self.log.append(0.0, f"journal Renode : {info['log']}")
        if info["missing"]:
            self.log.append(
                0.0, "symboles absents de l'ELF : "
                     + ", ".join(info["missing"]), "error")
        self.log.append(0.0, "« Start » lance l'exécution du firmware")

    def _on_failed(self, message: str) -> None:
        self.status.setText(f"échec de l'ouverture — {message}")
        self.log.append(0.0, message, "error")

    def _on_executed(self, command: str) -> None:
        self.log.append(self._clock, command, "cmd")

    def _on_error(self, message: str) -> None:
        self.log.append(self._clock, message, "error")

    def _on_snapshot(self, snapshot) -> None:
        self._clock = snapshot.virtual_time
        self.clock_label.setText(f"{snapshot.virtual_time:.2f} s")

        state = snapshot.state_name
        self.state_list.set_active(state)
        self.board_state.setText(state)
        self.last_state.setText(f"précédent : {snapshot.last_state_name}")
        if state != self._last_state:
            if self._last_state is not None:
                self.log.append(self._clock, f"ier_state → {state}", "event")
            self._last_state = state

        alarms = snapshot.alarms
        self.alarm_list.set_flags(alarms)
        if alarms != self._last_alarms:
            if self._last_alarms is not None:
                raised = [a for a in alarms if a not in self._last_alarms]
                cleared = [a for a in self._last_alarms if a not in alarms]
                for name in raised:
                    self.log.append(self._clock, f"alarme levée : {name}", "error")
                for name in cleared:
                    self.log.append(self._clock, f"alarme retombée : {name}",
                                    "event")
            self._last_alarms = alarms

        self.status_list.set_flags(snapshot.status_flags)
        self.control_source.setText(
            f"source de commande : {snapshot.control_source}")
        self.readback_table.update_values(snapshot.readbacks)

        for key, row in self.output_rows.items():
            row.set_value(snapshot.outputs.get(key))
        for key, row in self.pwm_rows.items():
            row.set_value(snapshot.pwm.get(key))

        self.cost.setText(f"{len(snapshot.errors)} erreur(s) de lecture"
                          if snapshot.errors else "lectures nominales")
        for message in snapshot.errors[:2]:
            self.log.append(self._clock, message, "error")

    # ---------------------------------------------------------------- sortie
    def closeEvent(self, event) -> None:       # noqa: N802 (API Qt)
        """Laisse le fil de travail fermer sa session avant de le terminer.

        Le QTimer de sondage appartient au fil de travail et ne peut être
        arrêté que par lui ; couper le fil d'abord ferait détruire le timer
        depuis le fil de l'interface, ce que Qt refuse bruyamment. On envoie
        donc la demande de fermeture et on attend que `finished` déclenche
        `quit()`, déjà câblé.
        """
        self.request_close.emit()
        if not self.thread.wait(5000):
            self.thread.quit()
            self.thread.wait(2000)
        super().closeEvent(event)
