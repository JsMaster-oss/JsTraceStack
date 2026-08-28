# -*- coding: utf-8 -*-
"""Contrôle statique des index du survol dans les routes de tracé.

Aucune donnée, aucune exécution : le script lit le fichier des routes et vérifie
que chaque `%{customdata[N]}` pointe bien la colonne que son libellé annonce.

Un index décalé d'un cran — parce qu'une entrée a été insérée au mauvais endroit
dans `liste_hover_template_analyse` — donne un survol qui affiche des chiffres
justes mais mal étiquetés. C'est indétectable à l'œil et ça ne lève aucune
erreur.

    python3 verifier_hovertemplate.py routes_graphique.py
"""

import ast
import re
import sys
import unicodedata


def normaliser(texte):
    sans_accent = unicodedata.normalize("NFKD", str(texte))
    sans_accent = "".join(c for c in sans_accent if not unicodedata.combining(c))
    return "".join(c for c in sans_accent.lower() if c.isalnum())


# libellé du survol -> colonne attendue, quand les deux ne sont pas identiques
SYNONYMES = {
    "designationarticle": "designation",
    "referencearticle": "article",
    "datededernieremiseajour": "datedelavaliditeducyclecontractuelequipementiers",
    "risquemajorant": "risquemajorantmois",
    "sommedesretardsdemontres": "sommedesretardsdemontresmois",
    "cyclesapvsretarddemontre": "cyclesapmois",
    "approslongslli": "approslongsllimois",
    "autresapprosousemifinis": "autresapprosousemifinismois",
    "cycleindustrielfournisseur": "cycleindustrielmois",
    "delaidesecurite": "delaisecumois",
    "tempsdereception": "tmpsrecepmois",
    "decalagedet0": "datedebutt0mois",
    "delaisapnonanalyse": "delaissapnonanalysemois",
    "delaitotal": "delaitotalmois",
    "margeappro": "margeappromois",
    "fournisseur": "fournisseur",
}


def routes(source):
    """Découpe le fichier par route de tracé."""
    bornes = [(m.group(1), m.start()) for m in re.finditer(r"\ndef (\w+)\(\):", source)]
    bornes.append(("<fin>", len(source)))
    for (nom, debut), (_, fin) in zip(bornes, bornes[1:]):
        bloc = source[debut:fin]
        if "liste_hover_template_analyse" in bloc and "customdata" in bloc:
            yield nom, bloc


def colonnes_du_survol(bloc):
    m = re.search(r"liste_hover_template_analyse = (\[.*?\n    \])", bloc, re.S)
    if not m:
        return None
    return ast.literal_eval(re.sub(r"#.*", "", m.group(1)))


def main():
    chemin = sys.argv[1] if len(sys.argv) > 1 else "routes_graphique.py"
    source = open(chemin, encoding="utf-8").read()

    anomalies = 0
    for nom, bloc in routes(source):
        colonnes = colonnes_du_survol(bloc)
        print()
        print("=" * 78)
        print("{}  —  {} colonnes dans liste_hover_template_analyse".format(
            nom, len(colonnes) if colonnes else 0))
        print("=" * 78)
        if not colonnes:
            print("  liste introuvable")
            anomalies += 1
            continue

        if colonnes[-1] != "Délai total (mois)":
            print("  ALERTE  la dernière entrée est « {} » et non "
                  "« Délai total (mois) ».".format(colonnes[-1]))
            print("          Plusieurs contrôles lisent le total par l'index -1.")
            anomalies += 1

        # chaque ligne de survol : un libellé, puis un ou plusieurs index
        for ligne in re.findall(r'hovertemplate \+= (".*?")', bloc):
            texte = ast.literal_eval(ligne)
            index = [int(i) for i in re.findall(r"%\{customdata\[(\d+)\]\}", texte)]
            if not index:
                continue
            libelle = re.sub(r"<[^>]+>|&#\d+;", "", texte)
            libelle = re.sub(r"%\{customdata\[\d+\]\}", "", libelle)
            libelle = libelle.strip(" :<>-").split(":")[0].strip()

            premier = index[0]
            if premier >= len(colonnes):
                print("  ECHEC   [{}] index {} hors liste (max {})".format(
                    libelle, premier, len(colonnes) - 1))
                anomalies += 1
                continue

            cible = colonnes[premier]
            attendu = SYNONYMES.get(normaliser(libelle), normaliser(libelle))
            ok = attendu in normaliser(cible) or normaliser(cible) in attendu

            marque = "OK     " if ok else "ECHEC  "
            if not ok:
                anomalies += 1
            print("  {} {:<34s} [{:>2d}] -> {}".format(
                marque, libelle[:34], premier, cible))
            if not ok:
                bons = [i for i, c in enumerate(colonnes)
                        if attendu in normaliser(c) or normaliser(c) in attendu]
                if bons:
                    print("           l'index attendu serait {} ({})".format(
                        bons[0], colonnes[bons[0]]))

    print()
    print("=" * 78)
    print("{} anomalie(s)".format(anomalies) if anomalies
          else "Tous les index du survol pointent la bonne colonne.")
    return 1 if anomalies else 0


if __name__ == "__main__":
    sys.exit(main())
