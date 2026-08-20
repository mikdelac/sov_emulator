"""Sondage et commandes exécutés hors du fil d'exécution de l'interface.

Le socket du Monitor n'est touché que d'ici. Toutes les actions de la fenêtre
arrivent par des signaux Qt mis en file, ce qui garantit qu'aucune commande ne
s'entrelace avec une rafale de lecture en cours — le Monitor Renode n'a pas de
notion de requête concurrente.
"""

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from .monitor import MonitorError
from .session import Session
from .symbols import SymbolError


class SessionWorker(QObject):
    """Propriétaire exclusif de la session Renode, vit dans son propre fil."""

    opened = pyqtSignal(object)      # dict d'informations d'ouverture
    failed = pyqtSignal(str)
    polled = pyqtSignal(object)      # Snapshot
    executed = pyqtSignal(str)       # commande effectivement envoyée
    errored = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, session: Session, interval_ms: int = 150):
        super().__init__()
        self.session = session
        self.interval_ms = interval_ms
        self._timer: QTimer | None = None
        self._busy = False

    # -- cycle de vie ------------------------------------------------------
    @pyqtSlot()
    def open(self) -> None:
        try:
            self.session.open()
        except (MonitorError, SymbolError, OSError) as error:
            self.failed.emit(str(error))
            return
        self.opened.emit({
            "port": self.session.monitor.port,
            "elf": str(self.session.elf),
            "script": str(self.session.script),
            "log": str(self.session.log_path),
            "spawned": self.session.process is not None,
            "missing": self.session.missing_symbols,
        })
        self._timer = QTimer(self)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    @pyqtSlot()
    def close(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.session.close()
        self.finished.emit()

    # -- sondage -----------------------------------------------------------
    def _tick(self) -> None:
        # Une rafale coûte une quinzaine de millisecondes ; si la machine hôte
        # ralentit, mieux vaut sauter un tour que d'empiler les rafales.
        if self._busy:
            return
        self._busy = True
        try:
            self.polled.emit(self.session.poll())
        except MonitorError as error:
            self.errored.emit(f"sondage interrompu : {error}")
            if self._timer is not None:
                self._timer.stop()
        finally:
            self._busy = False

    # -- commandes ---------------------------------------------------------
    def _run(self, action) -> None:
        try:
            self.executed.emit(action())
        except MonitorError as error:
            self.errored.emit(str(error))

    @pyqtSlot(str, float)
    def set_analog(self, key: str, value: float) -> None:
        self._run(lambda: self.session.set_analog(key, value))

    @pyqtSlot(str, bool)
    def set_digital(self, key: str, closed: bool) -> None:
        self._run(lambda: self.session.set_digital(key, closed))

    @pyqtSlot(str)
    def run_macro(self, name: str) -> None:
        self._run(lambda: self.session.run_macro(name))

    @pyqtSlot()
    def start(self) -> None:
        self._run(self.session.start)

    @pyqtSlot()
    def pause(self) -> None:
        self._run(self.session.pause)

    @pyqtSlot()
    def reset(self) -> None:
        self._run(self.session.reset)
