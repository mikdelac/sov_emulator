#!/usr/bin/env bash
#
# Lance l'émulation du firmware IER dans Renode.
#
#   ./run.sh                        console interactive
#   ./run.sh --debug                idem + serveur GDB sur :3333
#   ./run.sh --bin ../ier/Release/ier.elf     autre binaire
#   ./run.sh --start                démarre l'exécution immédiatement
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RENODE="${HERE}/.renode/renode"

SCRIPT="${HERE}/scripts/ier.resc"
BIN=""
AUTOSTART=0

while [ $# -gt 0 ]; do
    case "$1" in
        --debug)  SCRIPT="${HERE}/scripts/ier-debug.resc"; shift ;;
        --bin)    BIN="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2 ;;
        --start)  AUTOSTART=1; shift ;;
        -h|--help)
            sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
            exit 0 ;;
        *) echo "Option inconnue : $1" >&2; exit 1 ;;
    esac
done

if [ ! -x "${RENODE}" ]; then
    echo "Renode n'est pas installé — lancement de tools/install-renode.sh"
    "${HERE}/tools/install-renode.sh"
fi

ELF="${BIN:-${HERE}/../ier/Debug/ier.elf}"
if [ ! -f "${ELF}" ]; then
    echo "Firmware introuvable : ${ELF}" >&2
    echo "Compilez-le d'abord :  make -C ../ier/Debug" >&2
    exit 1
fi

ARGS=(--disable-xwt --console)
if [ -n "${BIN}" ]; then
    ARGS+=(-e "\$bin=@${BIN}")
fi
ARGS+=(-e "include @${SCRIPT}")
if [ "${AUTOSTART}" = "1" ]; then
    ARGS+=(-e "start")
fi

exec "${RENODE}" "${ARGS[@]}"
