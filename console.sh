#!/usr/bin/env bash
#
# Lance la console d'entrées / sorties IER (PyQt6 + Renode).
#
#   ./console.sh                     démarre Renode et ouvre la console
#   ./console.sh --attach 12345      rejoint un Renode lancé avec « -P 12345 »
#   ./console.sh --self-check        vérifie la cohérence, sans fenêtre
#   ./console.sh --bin autre.elf     autre firmware (doit contenir le DWARF)
#
# Les dépendances Python vivent dans .venv-console/, séparé de .venv/ qui sert
# aux tests Robot : la console n'a pas à tirer Qt dans l'environnement de test.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${HERE}/.venv-console"
REQUIREMENTS="${HERE}/console/requirements.txt"

ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --bin)  ARGS+=(--elf "$2"); shift 2 ;;
        -h|--help)
            sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
            echo
            ARGS+=(--help); shift ;;
        *) ARGS+=("$1"); shift ;;
    esac
done

if [ ! -x "${VENV}/bin/python3" ]; then
    echo "Création de l'environnement de la console dans ${VENV}..."
    python3 -m venv "${VENV}"
    "${VENV}/bin/pip" install --quiet --upgrade pip
    "${VENV}/bin/pip" install --quiet -r "${REQUIREMENTS}"
fi

if [ ! -x "${HERE}/.renode/renode" ]; then
    echo "Renode n'est pas installé — lancement de tools/install-renode.sh"
    "${HERE}/tools/install-renode.sh"
fi

cd "${HERE}"
exec "${VENV}/bin/python3" -m console "${ARGS[@]}"
