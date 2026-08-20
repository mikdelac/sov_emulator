"""Client du Monitor Renode exposé par « renode -P <port> ».

Renode accepte les commandes du Monitor sur un socket TCP en clair. Le flux
n'est pas structuré : chaque commande est renvoyée en écho, suivie de son
résultat puis d'une invite « (machine) ». Se caler sur l'invite est fragile —
n'importe quelle sortie contenant des parenthèses la simule. On encadre donc
chaque commande d'un marqueur unique émis par `echo`, ce qui donne un
découpage sans ambiguïté et permet d'enchaîner les commandes en pipeline.

Le pipeline est ce qui rend le sondage périodique viable : mesuré sur cette
plateforme, 12 lectures coûtent 14,9 ms en séquentiel contre 5,2 ms groupées,
soit ~270 rafales par seconde. Sonder à 10 Hz reste sous 5 % du temps.
"""

import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Za-z0-9]")
_PROMPT = re.compile(r"^\([^()\n]{0,40}\)\s*")
_MARKER = "@@RC{}@@"


class MonitorError(RuntimeError):
    """Le Monitor n'a pas répondu, ou a répondu une erreur."""


def _clean(text: str) -> list[str]:
    """Lignes utiles : sans codes ANSI, sans invite, sans lignes vides."""
    lines = []
    for raw in _ANSI.sub("", text).replace("\r", "").split("\n"):
        line = _PROMPT.sub("", raw).strip()
        if line:
            lines.append(line)
    return lines


class RenodeMonitor:
    """Connexion au Monitor d'une instance Renode déjà démarrée."""

    def __init__(self, host: str = "127.0.0.1", port: int = 12345,
                 timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self._sequence = 0
        self._buffer = ""

    # -- cycle de vie ------------------------------------------------------
    def connect(self, retries: int = 60, delay: float = 0.5) -> None:
        """Se connecte, en réessayant le temps que Renode ouvre son port."""
        last = None
        for _ in range(retries):
            try:
                self._socket = socket.create_connection(
                    (self.host, self.port), timeout=self.timeout)
                self._socket.settimeout(self.timeout)
                self._buffer = ""
                self._drain_banner()
                return
            except OSError as error:
                last = error
                time.sleep(delay)
        raise MonitorError(
            f"connexion impossible à {self.host}:{self.port} — {last}")

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def _drain_banner(self) -> None:
        """Jette la bannière et la sortie du script de démarrage.

        S'arrêter à la première invite ne suffit pas : le script passé par
        « -e include » continue d'écrire après elle, et ce reliquat se
        retrouverait collé au résultat de la première commande. On pousse donc
        un marqueur et on jette tout ce qui le précède — il arrive forcément
        après, le Monitor traitant les commandes dans l'ordre.
        """
        assert self._socket is not None
        self._sequence += 1
        marker = _MARKER.format(self._sequence)
        self._socket.sendall(f'echo "{marker}"\n'.encode())
        self._read_until(marker)

    # -- exécution ---------------------------------------------------------
    def execute(self, command: str) -> str:
        return self.execute_many([command])[0]

    def execute_many(self, commands) -> list[str]:
        """Exécute les commandes en pipeline et renvoie un résultat par commande."""
        commands = list(commands)
        if not commands:
            return []
        if self._socket is None:
            raise MonitorError("Monitor non connecté")

        with self._lock:
            payload, markers = [], []
            for command in commands:
                self._sequence += 1
                marker = _MARKER.format(self._sequence)
                markers.append(marker)
                payload.append(command)
                payload.append(f'echo "{marker}"')
            try:
                self._socket.sendall(("\n".join(payload) + "\n").encode())
                raw = self._read_until(markers[-1])
            except (OSError, socket.timeout) as error:
                raise MonitorError(f"échange interrompu : {error}") from error
        return self._split(raw, markers, commands)

    def _read_until(self, marker: str) -> str:
        """Lit jusqu'au marqueur inclus et garde le reste pour l'appel suivant.

        Ce qui suit le marqueur est l'invite « (machine) » que Renode réémet
        après chaque commande. Elle arrive souvent à cheval sur deux paquets ;
        la jeter reviendrait à coller sa fin au début de la rafale suivante.
        """
        assert self._socket is not None
        deadline = time.monotonic() + self.timeout
        while True:
            end = self._marker_end(self._buffer, marker)
            if end is not None:
                consumed, self._buffer = self._buffer[:end], self._buffer[end:]
                return consumed
            if time.monotonic() >= deadline:
                raise MonitorError(
                    f"pas de réponse du Monitor avant {self.timeout} s")
            chunk = self._socket.recv(65536)
            if not chunk:
                raise MonitorError("le Monitor a fermé la connexion")
            self._buffer += chunk.decode(errors="replace")

    @staticmethod
    def _marker_end(buffer: str, marker: str) -> int | None:
        """Fin de la ligne portant le marqueur, hors écho de « echo "…" »."""
        start = 0
        while True:
            found = buffer.find(marker, start)
            if found < 0:
                return None
            # Dans l'écho de la commande, le marqueur est entre guillemets.
            if found == 0 or buffer[found - 1] != '"':
                return found + len(marker)
            start = found + len(marker)

    @staticmethod
    def _split(raw: str, markers, commands) -> list[str]:
        lines = _clean(raw)
        results, block, index = [], [], 0
        for line in lines:
            if index < len(markers) and line == markers[index]:
                results.append("\n".join(block))
                block = []
                index += 1
                continue
            if line.startswith('echo "@@RC'):
                continue                       # écho de notre propre marqueur
            if index < len(commands) and line == commands[index] and not block:
                continue                       # écho de la commande elle-même
            block.append(line)
        while len(results) < len(commands):
            results.append("")
        return results

    # -- accès mémoire -----------------------------------------------------
    _WIDTH_COMMAND = {1: "ReadByte", 2: "ReadWord", 4: "ReadDoubleWord",
                      8: "ReadQuadWord"}

    def read_commands(self, addresses) -> list[str]:
        """Commandes de lecture pour une liste de couples (adresse, largeur)."""
        return [f"sysbus {self._WIDTH_COMMAND.get(width, 'ReadDoubleWord')} "
                f"0x{address:08X}" for address, width in addresses]

    _ERROR = re.compile(r"There was an error executing command|"
                        r"^Could not|does not provide a field")

    @classmethod
    def is_error(cls, result: str) -> bool:
        """Le Monitor signale ses erreurs en clair plutôt qu'en les levant."""
        return bool(result) and bool(cls._ERROR.search(result))

    @staticmethod
    def parse_integer(result: str):
        """Entier renvoyé par sysbus Read* / GetSymbolAddress, sinon None."""
        match = re.search(r"0x([0-9A-Fa-f]+)", result)
        if match:
            return int(match.group(1), 16)
        try:
            return int(result.strip())
        except (TypeError, ValueError):
            return None


class RenodeProcess:
    """Instance Renode lancée par la console, avec son Monitor sur un port libre."""

    def __init__(self, renode: Path, script: Path, log_path: Path,
                 port: int | None = None, extra_args=()):
        self.renode = Path(renode)
        self.script = Path(script)
        self.log_path = Path(log_path)
        self.port = port or self._free_port()
        self.extra_args = list(extra_args)
        self._process: subprocess.Popen | None = None
        self._stdout = None

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    @staticmethod
    def _die_with_parent() -> None:
        """Demande au noyau de tuer Renode si la console disparaît.

        Sans cela, une console tuée brutalement laisse derrière elle un
        émulateur qui continue de consommer un cœur — et fausse la vitesse de
        simulation de la session suivante.
        """
        try:
            import ctypes
            pr_set_pdeathsig, sigterm = 1, 15
            ctypes.CDLL("libc.so.6").prctl(pr_set_pdeathsig, sigterm, 0, 0, 0)
        except Exception:            # noqa: BLE001 — au mieux, ailleurs qu'ici
            pass

    def start(self) -> None:
        if not self.renode.is_file():
            raise MonitorError(
                f"Renode introuvable : {self.renode} — lancez "
                f"tools/install-renode.sh")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._stdout = self.log_path.open("w", encoding="utf-8")
        self._process = subprocess.Popen(
            [str(self.renode), "--disable-xwt", "--plain", "--hide-log",
             "--hide-analyzers", "-P", str(self.port),
             "-e", f"include @{self.script}", *self.extra_args],
            stdout=self._stdout, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            preexec_fn=(self._die_with_parent
                        if sys.platform == "linux" else None),
        )

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self, timeout: float = 5.0) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        if self._stdout is not None:
            self._stdout.close()
            self._stdout = None
