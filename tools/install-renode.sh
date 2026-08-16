#!/usr/bin/env bash
#
# Installe Renode (version épinglée) en local dans .renode/, sans droits root.
# L'archive « linux-portable-dotnet » embarque son propre runtime .NET :
# ni mono, ni dotnet, ni paquet système ne sont requis.
#
# La version épinglée est une nightly : la 1.16.1 stable ne fait pas
# implémenter IDMA à DMA.STM32LDMA, or ce lien est indispensable pour que la
# chaîne ADC1 -> DMA1_Channel1 du firmware IER fonctionne (sans lui, les
# valeurs de capteurs injectées n'atteignent jamais adc1_dma_value).
#
set -euo pipefail

RENODE_VERSION="${RENODE_VERSION:-1.16.1+20260623git3f5a91013}"
ARCHIVE="renode-${RENODE_VERSION}.linux-portable-dotnet.tar.gz"

case "${RENODE_VERSION}" in
    *+*git*) URL="https://builds.renode.io/${ARCHIVE}" ;;         # nightly
    *)       URL="https://github.com/renode/renode/releases/download/v${RENODE_VERSION}/${ARCHIVE}" ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${HERE}/.renode"
STAMP="${DEST}/.installed-version"

if [ -x "${DEST}/renode" ] && [ "$(cat "${STAMP}" 2>/dev/null)" = "${RENODE_VERSION}" ]; then
    echo "Renode ${RENODE_VERSION} déjà installé dans ${DEST}"
    exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

echo "Téléchargement de ${ARCHIVE}..."
curl -fL --progress-bar -o "${TMP}/${ARCHIVE}" "${URL}"

echo "Extraction vers ${DEST}..."
rm -rf "${DEST}"
mkdir -p "${DEST}"
# L'archive contient un unique répertoire racine renode_<version>_portable/
tar -xzf "${TMP}/${ARCHIVE}" -C "${DEST}" --strip-components=1

if [ ! -x "${DEST}/renode" ]; then
    echo "Erreur : ${DEST}/renode introuvable après extraction." >&2
    exit 1
fi
echo "${RENODE_VERSION}" > "${STAMP}"

echo
echo "Renode installé :"
"${DEST}/renode" --version
echo
echo "Lancement de l'émulateur IER :  ./run.sh"
