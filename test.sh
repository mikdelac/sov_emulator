#!/usr/bin/env bash
#
# Lance la suite de non-régression Renode (Robot Framework).
#
#   ./test.sh                      toute la suite tests/
#   ./test.sh tests/ier-boot.robot un seul fichier
#
# Robot Framework est installé dans un environnement virtuel local (.venv/),
# pour ne rien ajouter à l'installation Python du système.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${HERE}/.venv"

if [ ! -x "${HERE}/.renode/renode-test" ]; then
    "${HERE}/tools/install-renode.sh"
fi

if [ ! -x "${VENV}/bin/python3" ]; then
    echo "Création de l'environnement de test dans ${VENV}..."
    python3 -m venv "${VENV}"
    "${VENV}/bin/pip" install --quiet --upgrade pip
    "${VENV}/bin/pip" install --quiet -r "${HERE}/.renode/tests/requirements.txt"
fi

export PATH="${VENV}/bin:${PATH}"

TARGETS=("$@")
if [ ${#TARGETS[@]} -eq 0 ]; then
    TARGETS=("${HERE}/tests/ier-boot.robot" "${HERE}/tests/ier-eeprom.robot"
             "${HERE}/tests/ier-pwm.robot")
fi

exec "${HERE}/.renode/renode-test" --results-dir "${HERE}/tests/results" "${TARGETS[@]}"
