# -*- coding: utf-8 -*-
"""État réel des deux buckets MinIO. Strictement en lecture.

Ce script n'écrit rien, ne supprime rien, ne régénère rien. Il répond à trois
questions :

  1. quels objets se trouvent réellement dans le bucket d'export SAP et dans
     celui de Power BI, avec taille et date ;
  2. combien d'articles de tête chaque fichier Excel contient — un seul fichier
     peut en porter plusieurs, c'est la première explication à écarter quand
     des articles supprimés réapparaissent ;
  3. ce que contient le CSV en cache, et s'il correspond aux fichiers présents.

    python3 diagnostic_minio.py
    python3 diagnostic_minio.py --config Config/configApp.ini
    python3 diagnostic_minio.py --anonyme     # masque les références

À lancer depuis la racine de l'application, là où `Core/` est importable.
"""

import argparse
import hashlib
import io
import sys

import pandas as pd

from Core.Config import init_config
from Core.ConnexionMinIO import ConnexionMinIO


def attribut(obj, nom, defaut="?"):
    """Tolérant aux variantes du client MinIO selon la version."""
    valeur = getattr(obj, nom, None)
    return defaut if valeur is None else valeur


def masquer(valeur, actif):
    if not actif:
        return str(valeur)
    empreinte = hashlib.sha1(str(valeur).encode("utf-8")).hexdigest()[:8]
    return "<{}>".format(empreinte)


def lister(client, bucket):
    try:
        return sorted(
            client.list_objects(bucket, prefix="", recursive=True),
            key=lambda o: o.object_name,
        )
    except Exception as erreur:  # bucket absent, droits, etc.
        print("    (listing impossible : {})".format(erreur))
        return []


def section(titre):
    print()
    print("=" * 78)
    print(titre)
    print("=" * 78)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="Config/configApp.ini")
    p.add_argument("--anonyme", action="store_true",
                   help="remplace les références par une empreinte")
    args = p.parse_args()

    config = init_config(args.config)
    bucket_sap = config["MINIO"]["BucketExportSAP"]
    bucket_pbi = config["MINIO"]["BucketPowerBI"]

    section("0. CONFIGURATION")
    print("    BucketExportSAP : {}".format(bucket_sap))
    print("    BucketPowerBI   : {}".format(bucket_pbi))
    if bucket_sap == bucket_pbi:
        print("    /!\\ Les deux pointent le même bucket.")

    with ConnexionMinIO(config) as client:
        existants = [b.name for b in client.list_buckets()]
        print("    buckets du serveur : {}".format(existants))

        for bucket in (bucket_sap, bucket_pbi):
            try:
                versioning = client.get_bucket_versioning(bucket)
                statut = getattr(versioning, "status", versioning)
                if str(statut).lower() in ("enabled", "suspended"):
                    print("    /!\\ versioning « {} » sur {} : une suppression "
                          "peut n'être qu'un marqueur.".format(statut, bucket))
            except Exception:
                pass

        section("1. BUCKET D'EXPORT SAP — {}".format(bucket_sap))
        objets = lister(client, bucket_sap)
        print("    {} objet(s)".format(len(objets)))
        for obj in objets:
            print("      {:<50s} {:>10} o   {}".format(
                masquer(obj.object_name, args.anonyme),
                attribut(obj, "size"), attribut(obj, "last_modified")))

        section("2. ARTICLES DE TÊTE PAR FICHIER")
        print("    Un seul fichier peut porter plusieurs articles de tête :")
        print("    c'est la première explication à écarter quand des articles")
        print("    supprimés réapparaissent.")
        print()

        total_couples = set()
        for obj in objets:
            reponse = client.get_object(bucket_sap, obj.object_name)
            try:
                dff = pd.read_excel(
                    io.BytesIO(reponse.read()), sheet_name="export",
                    dtype={"refe_cpse": str, "Article": str, "Niveau": str},
                )
            except Exception as erreur:
                print("    {} : lecture impossible ({})".format(
                    masquer(obj.object_name, args.anonyme), erreur))
                continue

            print("    {} — {} lignes".format(
                masquer(obj.object_name, args.anonyme), len(dff)))

            if {"article de tête", "désignation article de tête"} <= set(dff.columns):
                couples = (dff[["article de tête", "désignation article de tête"]]
                           .drop_duplicates().values)
                print("      colonnes « article de tête » présentes -> "
                      "{} couple(s)".format(len(couples)))
                for article, designation in couples:
                    total_couples.add((str(article), str(designation)))
                    print("        {} / {}".format(
                        masquer(article, args.anonyme),
                        masquer(designation, args.anonyme)))
            else:
                tetes = dff[dff["Niveau"] == "0"]
                print("      pas de colonne « article de tête » -> tête déduite "
                      "du Niveau 0 : {} ligne(s)".format(len(tetes)))
                for _, ligne in tetes.iterrows():
                    couple = (str(ligne["Article"]).lstrip("0"),
                              str(ligne.get("Designatin", "")))
                    total_couples.add(couple)
                    print("        {} / {}".format(
                        masquer(couple[0], args.anonyme),
                        masquer(couple[1], args.anonyme)))

        print()
        print("    TOTAL : {} article(s) de tête distinct(s) dans le "
              "bucket".format(len(total_couples)))

        section("3. BUCKET POWER BI — {}".format(bucket_pbi))
        objets_pbi = lister(client, bucket_pbi)
        print("    {} objet(s)".format(len(objets_pbi)))
        for obj in objets_pbi:
            print("      {:<50s} {:>10} o   {}".format(
                obj.object_name,
                attribut(obj, "size"), attribut(obj, "last_modified")))

        noms = [o.object_name for o in objets_pbi]
        if "export_power_bi.csv" not in noms:
            print()
            print("    export_power_bi.csv absent.")
            print("    ATTENTION : la prochaine ouverture du graphique va le")
            print("    RECRÉER. get_donnee_power_bi régénère et réécrit le CSV")
            print("    dès qu'il ne le trouve pas — sans passer par")
            print("    /update_info_cascade.")
        else:
            reponse = client.get_object(bucket_pbi, "export_power_bi.csv")
            df = pd.read_csv(io.BytesIO(reponse.read()))
            print()
            print("    {} lignes".format(len(df)))
            colonne = "désignation article de tête"
            if colonne in df.columns:
                comptes = df[colonne].value_counts(dropna=False)
                print("    {} désignation(s) :".format(len(comptes)))
                for valeur, n in comptes.items():
                    etiquette = ("(vide)" if pd.isna(valeur)
                                 else masquer(valeur, args.anonyme))
                    print("      {:<40s} {:>5d} lignes".format(etiquette, n))

                section("4. RAPPROCHEMENT")
                du_csv = {str(v) for v in comptes.index if not pd.isna(v)}
                du_bucket = {d for _, d in total_couples}
                fantomes = du_csv - du_bucket
                absents = du_bucket - du_csv
                if fantomes:
                    print("    Désignations présentes dans le CSV mais dans AUCUN")
                    print("    fichier du bucket : {}".format(len(fantomes)))
                    for d in sorted(fantomes):
                        print("      {}".format(masquer(d, args.anonyme)))
                    print("    -> le CSV est un cache périmé : le supprimer puis")
                    print("       appeler /update_info_cascade.")
                if absents:
                    print("    Désignations du bucket absentes du CSV : {}".format(
                        len(absents)))
                    for d in sorted(absents):
                        print("      {}".format(masquer(d, args.anonyme)))
                if not fantomes and not absents:
                    print("    Le CSV correspond exactement aux fichiers présents.")

    print()
    print("Aucune écriture n'a été faite par ce script.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
