"""Console d'entrées / sorties du firmware IER émulé dans Renode.

Pilote les capteurs et les entrées tout ou rien de la carte IERPCB001 v3.2, et
observe en retour l'état réel du firmware — machine à états, alarmes, sorties,
relectures converties — lu dans sa mémoire et ses registres à chaque sondage.
Rien n'est recalculé côté interface.

    ./console.sh                 démarre Renode et ouvre la fenêtre
    ./console.sh --self-check    vérifie la cohérence avec les scripts Renode
"""
