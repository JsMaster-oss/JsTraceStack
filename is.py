#!/usr/bin/env python3
"""
inspecter_mappings.py — que contient mappings_valides.json, et que perdrait-on ?

Lecture seule par défaut. Avec --nettoyer, retire UNIQUEMENT les entrées de
chaîne devenues inertes (clé au format antérieur à l'ajout de la famille), après
sauvegarde du fichier. Tout le reste est conservé : correspondances wono, pays,
scénario, activité, produit, produits à créer, lignes En cours, et arbitrages
manuels encore valides.

Usage :
    python inspecter_mappings.py [chemin]            # inspection
    python inspecter_mappings.py [chemin] --nettoyer # + purge des clés mortes

Sans chemin : $IMPORT_DIR/mappings_valides.json (défaut ./import/).
"""
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

# Clé de chaîne actuelle : version|wono|libellé|famille|activité|produit|scénario
SEPARATEURS_ATTENDUS = 6

LIBELLES = {
    'wono':           "num_wono trop longs → num_wono en base",
    'pays':           "noms de pays corrigés",
    'scenario':       "noms de scénario corrigés",
    'activite':       "noms d'activité corrigés",
    'produit':        "produits mappés vers un produit existant",
    'produit_create': "produits à créer, confirmés",
    'encours':        "lignes En cours rattachées à une identité XLSX",
}


def charger(chemin):
    if not chemin.exists():
        print(f"[ERREUR] Fichier introuvable : {chemin}")
        sys.exit(1)
    with open(chemin, encoding='utf-8') as f:
        m = json.load(f)
    if not isinstance(m, dict):
        print(f"[ERREUR] {chemin} ne contient pas un objet JSON.")
        sys.exit(1)
    return m


def inspecter(m):
    """Affiche le contenu et retourne la liste des clés de chaîne inertes."""
    print("\nCorrespondances de référentiel — PERDUES si tu supprimes le fichier,")
    print("à reconfirmer une par une en staging interactif :\n")
    total_ref = 0
    for ns, libelle in LIBELLES.items():
        n = len(m.get(ns) or {})
        total_ref += n
        marque = '·' if n else ' '
        print(f"  {marque} {ns:16} {n:4}   {libelle}")
    print(f"\n    → {total_ref} correspondance(s) de référentiel au total.")

    chaine = m.get('chaine') or {}
    print(f"\nLiens de chaîne entre versions : {len(chaine)}\n")
    par_source = Counter((v or {}).get('source') or '(sans source — run ancien)'
                         for v in chaine.values())
    for source, n in sorted(par_source.items(), key=lambda kv: -kv[1]):
        if source == 'manuel':
            note = "recalculés à chaque run depuis le tableau — rien à perdre"
        elif source == 'manuel-choisi':
            note = "TES ARBITRAGES sur cas ambigus — à conserver"
        else:
            note = "liens d'un run antérieur — à examiner"
        print(f"      {n:4}  {source:28} {note}")

    inertes = [k for k in chaine if k.count('|') != SEPARATEURS_ATTENDUS]
    if inertes:
        print(f"\n  /!\\ {len(inertes)} clé(s) au format antérieur à l'ajout de la famille.")
        print("      Elles ne correspondent plus à aucune clé calculée : elles sont")
        print("      déjà SANS EFFET, que tu les gardes ou non.")
        for k in inertes[:15]:
            print(f"        {k}")
        if len(inertes) > 15:
            print(f"        … et {len(inertes) - 15} autre(s)")
    return inertes


def nettoyer(chemin, m, inertes):
    if not inertes:
        print("\nRien à nettoyer : aucune clé inerte.")
        return
    sauvegarde = chemin.with_suffix('.json.bak')
    shutil.copy2(chemin, sauvegarde)
    for k in inertes:
        del m['chaine'][k]
    with open(chemin, 'w', encoding='utf-8') as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    print(f"\n{len(inertes)} clé(s) inerte(s) retirée(s).")
    print(f"Sauvegarde : {sauvegarde}")
    print("Tout le reste est conservé.")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    import_dir = Path(os.environ.get('IMPORT_DIR', Path(__file__).parent / 'import'))
    chemin = Path(args[0]) if args else import_dir / 'mappings_valides.json'

    m = charger(chemin)
    print(f"=== {chemin} ===")
    inertes = inspecter(m)

    if '--nettoyer' in sys.argv[1:]:
        nettoyer(chemin, m, inertes)
    elif inertes:
        print("\n(relance avec --nettoyer pour les retirer, sauvegarde automatique)")


if __name__ == '__main__':
    main()
