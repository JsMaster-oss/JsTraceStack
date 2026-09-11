"""Created on Sun Mar 5 16:41:06 2023

@author: gicornu"""

import datetime
import json
import logging
from io import BytesIO

import docx
import numpy as np
import pandas as pd
from docx import Document
from docx.shared import Inches
from minio.error import S3Error

from Core.Config import init_config
from Core.ConnexionMinIO import ConnexionMinIO
from Core.ConnexionMongoDB import ConnexionMongoDB

logger = logging.getLogger(__name__)

liste_attribut_export = [
    "article de tête",
    "désignation article de tête",
    "Niveau",
    "refe_cpse",
    "Article",
    "Designatin",
    "Type_appro",
    "Appro_spec",
    "Cyc_Cum",
    "Cyc_Fab.",
    "Delai_appr",
    "Délai_Sécu",
    "Tps_Recep",
    "TyApproSpe",
    # MODIF GEN-5 : colonne FV, requise par RG-040. Si le chargement plante
    # ici, c'est que l'en-tête Excel ne s'appelle pas exactement "MargeAppr" :
    # relever le nom réel dans le fichier et le reporter.
    "MargeAppr",
    # MODIF GEN-6 : colonne ajoutée, requise par la règle « fourni. = L » sur
    # les enfants d'un F/30. Le point final fait partie du nom. Si le
    # chargement plante ici, c'est que l'en-tête Excel ne s'appelle pas
    # exactement « fourni. » : relever le nom réel et le reporter.
    "fourni.",
]

dico_rename_export = {
    "refe_cpse": "article parent",
    "Designatin": "Designation",
}

dico_ecrasement_mongo = {
    "Désignation Article": "Designatin",
}

dico_power_bi = {
    "ID": 0,
    "Analysé": "",
    "ZPIF": 0,
    "202": 0,
    "201": 0,
    "Delta SAP": 0,
    "Démontré": 0,
    "Risques": 0,
    "Date début": datetime.datetime(2023, 1, 1),
    "Délai": 0,
    "Durée restante": 0,
    "date début TO": 0,
    "Délais non analysé (mois)": 0,
    "Délai non analysé": 0,
    "Délais analysé (mois)": 0,
    "date début To (mois)": 0,
    "ZO2": 0,
    "ZO1": 0,
}


def duree_restante(row):
    def num(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return 0.0
        v = str(v).strip()
        return float(v) if v not in ("", "nan") else 0.0

    def txt(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return ""
        try:
            return str(int(float(v)))
        except (ValueError, TypeError):
            return str(v)

    if num(row["Cyc_Cum"]) == 0:
        return 0.0
    if txt(row["Appro_spec"]) == "50" or txt(row["TyApproSpe"]) == "50":
        return 0.0

    type_appro = str(row["Type_appro"]).strip() if row["Type_appro"] is not None else ""
    if type_appro == "E":
        return num(row["Cyc_Fab."])
    if type_appro == "F":
        return num(row["Delai_appr"])
    return max(num(row["Cyc_Fab."]), num(row["Delai_appr"]))


def _num(valeur):
    """Valeur numérique d'une cellule, vide considérée comme 0."""
    if valeur is None or (isinstance(valeur, float) and np.isnan(valeur)):
        return 0.0
    valeur = str(valeur).strip()
    return float(valeur) if valeur not in ("", "nan") else 0.0


# MODIF GEN-5 : RG-040, retrouvée dans le Word.
def delai_lien(row):
    """RG-040 — délai du lien FD vers le successeur, en jours.

    Si colonne AR (Cyc_Cum) = 0  -> 0
    Sinon                        -> AU + AV - FV
                                    Délai_Sécu + Tps_Recep - MargeAppr

    Cellules vides considérées comme 0.

    Deux conséquences par rapport à ce que faisait le code :

    - le terme `- MargeAppr` n'existait pas. La marge est souvent négative
      (médiane -20 jours), auquel cas elle *allonge* le lien ;
    - un article à Cyc_Cum = 0 ne décale plus du tout ses composants. Avant, il
      leur imposait quand même Délai_Sécu + Tps_Recep.
    """
    if _num(row["Cyc_Cum"]) == 0:
        return 0.0
    return (_num(row["Délai_Sécu"]) + _num(row["Tps_Recep"])
            - _num(row.get("MargeAppr") if hasattr(row, "get") else row["MargeAppr"]))


def is_appro_50(value):
    """Teste appro_spec 50 indépendamment du typage de la colonne."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return False
    try:
        return int(float(str(value).strip())) == 50
    except (ValueError, TypeError):
        return str(value).strip() == "50"


def calcul_delai_non_analyse(row, parent_cyc_cum, test_appro_50):
    # MODIF GEN-2 : corps entièrement remplacé, voir MODIFICATIONS.md
    """Délai des articles non analysés — RG-038.

    La durée d'une tâche se lit dans les colonnes de cycle (Cyc_Fab. / Delai_appr),
    elle ne se déduit pas de l'écart de compte à rebours avec le parent.
    `duree_restante` couvre déjà les deux sorties à zéro de l'ancien calcul —
    Cyc_Cum = 0, et Appro_spec ou TyApproSpe à 50 — donc `parent_cyc_cum` et
    `test_appro_50` ne servent plus. La signature est conservée pour laisser les
    points d'appel intacts et garder un seul endroit où basculer.

    Délai_Sécu et Tps_Recep ne sont pas soustraits : ils relèvent du délai du
    lien (RG-040), pas de la durée de la tâche, et le graphique les empile déjà
    en segments distincts.

    Ancien calcul, conservé ici pour mémoire :
        si Cyc_Cum == 0                    -> 0
        si test_appro_50 et Appro_spec 50  -> 0
        si Cyc_Cum == parent_cyc_cum       -> 0
        sinon Cyc_Cum - parent_cyc_cum - Délai_Sécu - Tps_Recep
    """
    return duree_restante(row)  # MODIF GEN-2 : remplace l'écart de Cyc_Cum


# MODIF GEN-4 : remplace `df.loc[df["Article"] == parent].iloc[0]`, qui prenait
# la PREMIÈRE occurrence du parent dans tout le fichier. Deux défauts : un
# composant monté à plusieurs endroits renvoyait toujours le premier montage, et
# la recherche n'était pas limitée à la désignation article de tête alors que la
# boucle appelante l'est — un parent pouvait être capté dans un autre ensemble.
def trouver_parent(df, index_ligne, row):
    """Ligne parente de `row`, avec la même règle que le graphique.

    On retient la dernière occurrence du parent *située avant* la ligne, ce qui
    est la convention d'une nomenclature indentée où le parent précède ses
    composants, et on se rabat sur une occurrence suivante si aucune ne précède.

    Renvoie None quand le parent est introuvable, au lieu de lever une
    IndexError sur un `.iloc[0]` appliqué à un résultat vide.
    """
    candidats = df.index[
        (df["Article"] == row["article parent"])
        & (df["désignation article de tête"] == row["désignation article de tête"])
    ]
    if len(candidats) == 0:
        return None

    avant = candidats[candidats < index_ligne]
    return df.loc[avant[-1]] if len(avant) else df.loc[candidats[0]]


def parent_racine(date_select):
    """Parent fictif pour une ligne dont le parent n'existe pas dans l'export.

    Elle est alors traitée comme une racine : décalage nul et date de début au
    repère. Sans ça la ligne prenait le premier article homonyme venu, ou faisait
    tomber tout le traitement.
    """
    return pd.Series(
        {
            "Analysé": "NON",
            "Cyc_Cum": 0,
            "Date début": date_select,
            "Délai": 0,
            "Délai_Sécu": 0,
            "Tps_Recep": 0,
            "MargeAppr": 0,  # MODIF GEN-5 : lu par delai_lien
            "date début TO": 0,
            "date début To (mois)": 0,
            "Délais analysé (mois)": 0,
            "Délais non analysé (mois)": 0,
        }
    )


def calcul_to(parent, delai_parent_mois):
    """date début to (jours, mois). Identique dans tous les cas.

    MODIF GEN-5 : le décalage apporté par le parent est son délai de lien au
    sens de RG-040, et non plus la simple somme Délai_Sécu + Tps_Recep.
    """
    lien_parent = delai_lien(parent)
    to_jours = round(
        parent["date début TO"]
        + parent["Délai"]
        + lien_parent
    )
    to_mois = (
        delai_parent_mois
        + parent["date début To (mois)"]
        + (lien_parent / 20)
    )
    return to_jours, to_mois


def correction_niveau(x):
    x = str(x).strip()
    nb_points = len(x) - len(x.lstrip("."))
    nbr_niveau = nb_points + 1
    return "." * nbr_niveau + str(nbr_niveau)


config = init_config("Config/configApp.ini")


def filtrage_derniere_version():
    with ConnexionMongoDB(config) as clientMongo:
        db_fiche = clientMongo[config["MONGO"]["BaseFiche"]]
        my_collection = db_fiche[config["MONGO"]["CollectionFiche"]]

        liste_fiche = []
        for doc in my_collection.find(
            filter={},
            projection=[
                "_id",
                "Designation Article",
                "Reference Article",
                "Programme",
                "Categorie Technologique",
                "Auteur",
                "Version",
                "DateCreation",
                "Date_Validite_Cycle_Contractuel_Equipementiers",
            ],
        ):
            doc["_id"] = str(doc["_id"])
            liste_fiche.append(doc)

        df_cycle = pd.DataFrame.from_records(liste_fiche)
        df_cycle.sort_values(
            by=["Designation_Article", "Reference_Article", "Version"],
            inplace=True,
        )
        df_cycle.drop_duplicates(
            subset=["Designation_Article", "Reference_Article"],
            keep="last",
            inplace=True,
        )
    return None


def generate_data_cycle(config):
    """Generation des donnees de cycles"""
    liste_fiche = []

    with ConnexionMongoDB(config) as clientMongo:
        db_fiche = clientMongo[config["MONGO"]["BaseFiche"]]
        my_collection = db_fiche[config["MONGO"]["CollectionFiche"]]

        for doc in my_collection.find(filter={}):
            liste_fiche.append(doc)

    df_cycle = pd.DataFrame.from_records(liste_fiche)
    df_cycle.sort_values(by=["Designation_Article", "Programme", "Version"], inplace=True)
    df_cycle.drop_duplicates(subset="Designation_Article", keep="last", inplace=True)

    dico_columns = json.loads(config["DATA"]["Dictionnaire_cycle"])
    columns_of_interest = list(dico_columns.keys())

    df_cycle_norm = df_cycle[columns_of_interest].rename(columns=dico_columns)
    df_cycle_norm["ZPIF"] = df_cycle_norm["Cycle Industriel Optimal (en mois)"]

    df_cycle_norm["PUMP en €"] = df_cycle_norm["PUMP en €"].apply(
        lambda x: np.nan if x == "" else float(x)
    )

    df_cycle_norm = df_cycle_norm.astype(
        {
            "Cycle Contractuel Equipementiers (en mois)": "float32",
            "Cycle SAP (en mois)": "float32",
            "Cycle Industriel Optimal (en mois)": "float32",
            "Gain Appros Longs (en mois)": "float32",
            "retard démontré vs TdB (en jours) ": "float32",
            "Gain Complément au cycle industriel (en mois)": "float32",
            "Risque majorant (en mois)": "float32",
            "PUMP en €": "float32",
        }
    )
    return df_cycle_norm


def generate_data_export(config):
    """Generation des donnees d export SAP"""
    liste_df_export = []
    liste_couple_A_D = []

    with ConnexionMinIO(config) as clientMinIO:
        objects = clientMinIO.list_objects(
            config["MINIO"]["BucketExportSAP"], prefix="", recursive=True
        )
        for obj in objects:
            pathobject = obj.object_name
            response = clientMinIO.get_object(
                config["MINIO"]["BucketExportSAP"], pathobject
            )

            dff = pd.read_excel(
                response.data,
                sheet_name="export",
                dtype={"refe_cpse": str, "Article": str, "Niveau": str},
            )

            if (
                "article de tête" in dff.columns
                and "désignation article de tête" in dff.columns
            ):
                dff["article de tête"] = dff["article de tête"].astype("string")
                list_couple = (
                    dff[["article de tête", "désignation article de tête"]]
                    .drop_duplicates()
                    .values
                )

                for couple in list_couple:
                    article, designation = couple
                    if (article, designation) not in liste_couple_A_D:
                        liste_couple_A_D.append((article, designation))
                    else:
                        dff.drop(
                            dff[
                                (dff["article de tête"] == article)
                                & (dff["désignation article de tête"] == designation)
                            ].index,
                            axis=0,
                            inplace=True,
                        )
                liste_df_export.append(dff)
            else:
                article, designation = dff[dff["Niveau"] == "0"][["Article", "Designatin"]].values[0]
                if (article, designation) not in liste_couple_A_D:
                    liste_couple_A_D.append((article, designation))

                article = article.lstrip("0")
                dff["article de tête"] = article
                dff["désignation article de tête"] = designation
                dff.drop(columns=["Ligne", "Li_cpse"], inplace=True)
                index_of_head = dff[dff["Niveau"] == "0"].index
                dff.loc[index_of_head, "refe_cpse"] = "SP00035899"
                dff["Niveau"] = dff["Niveau"].apply(correction_niveau)
                liste_df_export.append(dff)

    if liste_df_export:
        return pd.concat(liste_df_export, axis=0, ignore_index=True)
    return None


def generate_data_power_bi(config):
    """Generation des donnees pour Power BI"""
    df_cycle_detail = generate_data_cycle(config)
    if df_cycle_detail is None:
        return None

    df_export = generate_data_export(config)
    if df_export is None:
        return None

    date_select = dico_power_bi["Date début"]
    liste_designation_article = list(df_export["désignation article de tête"].unique())
    liste_ref_article = list(df_cycle_detail["Référence Article"])

    df_out_power_bi = df_export[liste_attribut_export].copy()
    df_out_power_bi.rename(columns=dico_rename_export, inplace=True)

    df_out_power_bi["Délai_Sécu"] = df_out_power_bi["Délai_Sécu"].fillna(0)
    df_out_power_bi["Tps_Recep"] = df_out_power_bi["Tps_Recep"].fillna(0)

    for attribut in dico_power_bi:
        df_out_power_bi[attribut] = dico_power_bi[attribut]

    for designation_article in liste_designation_article:
        index = df_out_power_bi[
            df_out_power_bi["désignation article de tête"] == designation_article
        ].index
        nbr_ligne = len(index)
        df_out_power_bi.loc[index, "ID"] = np.arange(start=1, stop=nbr_ligne + 1)

        for index, row in df_out_power_bi[
            df_out_power_bi["désignation article de tête"] == designation_article
        ].iterrows():
            if row["Article"] != "SP00035899":
                if row["Article"] not in liste_ref_article:
                    df_out_power_bi.at[index, "Analysé"] = "NON"

                    if row["Niveau"] == ".1":
                        delai = calcul_delai_non_analyse(row, 0, False)
                        df_out_power_bi.at[index, "Délai"] = delai
                        df_out_power_bi.at[index, "Date début"] = (
                            date_select + datetime.timedelta(days=int(row["Cyc_Cum"]))
                        )
                        # MODIF GEN-3 : deux lignes ajoutées.
                        # Sans elles la durée propre de la tête reste à 0 dans
                        # le CSV, et le graphique — qui ne lit que
                        # "Délais non analysé (mois)" — comprime toute la
                        # cascade d'autant.
                        df_out_power_bi.at[index, "Délai non analysé"] = delai
                        df_out_power_bi.at[index, "Délais non analysé (mois)"] = delai / 20
                    else:
                        # MODIF GEN-4
                        parent = trouver_parent(df_out_power_bi, index, row)
                        if parent is None:
                            parent = parent_racine(date_select)
                        parent_analyse = parent["Analysé"] == "OUI"

                        delai = calcul_delai_non_analyse(
                            row,
                            parent["Cyc_Cum"],
                            test_appro_50=True,
                        )
                        df_out_power_bi.at[index, "Délai"] = delai
                        # MODIF GEN-5 : délai de lien RG-040
                        df_out_power_bi.at[index, "Date début"] = (
                            parent["Date début"]
                            + datetime.timedelta(days=int(delai + delai_lien(row)))
                        )

                        delai_parent_mois = (
                            parent["Délais analysé (mois)"]
                            if parent_analyse
                            else parent["Délais non analysé (mois)"]
                        )
                        to_j, to_m = calcul_to(parent, delai_parent_mois)
                        df_out_power_bi.at[index, "date début TO"] = to_j
                        df_out_power_bi.at[index, "date début To (mois)"] = (
                            round(to_m) if row["Cyc_Cum"] == 0 else to_m
                        )
                        df_out_power_bi.at[index, "Délai non analysé"] = delai
                        df_out_power_bi.at[index, "Délais non analysé (mois)"] = delai / 20
                else:
                    analyse = df_cycle_detail.loc[
                        df_cycle_detail["Référence Article"] == row["Article"]
                    ].iloc[0]

                    df_out_power_bi.at[index, "Analysé"] = "OUI"
                    df_out_power_bi.at[index, "ZPIF"] = (
                        analyse["Cycle Industriel Optimal (en mois)"] * 20
                    )
                    df_out_power_bi.at[index, "ZO2"] = (
                        analyse["Gain Complément au cycle industriel (en mois)"] * 20
                    )
                    df_out_power_bi.at[index, "ZO1"] = (
                        analyse["Gain Appros Longs (en mois)"] * 20
                    )
                    df_out_power_bi.at[index, "Delta SAP"] = (
                        analyse["Cycle SAP (en mois)"] * 20
                        - analyse["Cycle Industriel Optimal (en mois)"] * 20
                        - analyse["Gain Appros Longs (en mois)"] * 20
                        - analyse["Gain Complément au cycle industriel (en mois)"] * 20
                    )
                    df_out_power_bi.at[index, "Démontré"] = (
                        analyse["retard démontré vs TdB (en jours) "] / 30 * 20
                    )
                    df_out_power_bi.at[index, "Risques"] = (
                        analyse["Risque majorant (en mois)"] * 20
                    )

                    # MODIF GEN-4
                    parent = trouver_parent(df_out_power_bi, index, row)
                    if parent is None:
                        parent = parent_racine(date_select)

                    df_out_power_bi.at[index, "Délai"] = (
                        df_out_power_bi.at[index, "ZPIF"]
                        # MODIF GEN-1 : lisait "202" et "201", jamais renseignées
                        + df_out_power_bi.at[index, "ZO2"]
                        + df_out_power_bi.at[index, "ZO1"]
                        + df_out_power_bi.at[index, "Delta SAP"]
                        + df_out_power_bi.at[index, "Démontré"]
                        + df_out_power_bi.at[index, "Risques"]
                    )
                    # MODIF GEN-5 : délai de lien RG-040
                    df_out_power_bi.at[index, "Date début"] = parent[
                        "Date début"
                    ] + datetime.timedelta(
                        days=round(
                            df_out_power_bi.at[index, "Délai"] + delai_lien(row)
                        )
                    )
                    # MODIF GEN-5 : délai de lien RG-040
                    df_out_power_bi.at[index, "date début TO"] = (
                        round(parent["date début TO"])
                        + round(parent["Délai"])
                        + round(delai_lien(parent))
                    )
                    df_out_power_bi.at[index, "Délais analysé (mois)"] = (
                        df_out_power_bi.at[index, "Délai"] / 20
                    )

                    # MODIF GEN-5 : délai de lien RG-040
                    df_out_power_bi.at[index, "date début To (mois)"] = (
                        parent["Délais analysé (mois)"]
                        if parent["Analysé"] == "OUI"
                        else parent["Délais non analysé (mois)"]
                    ) + parent["date début To (mois)"] + (delai_lien(parent) / 20)

    df_out_power_bi["Durée restante"] = df_out_power_bi.apply(
        lambda r: duree_restante(r) if r["Analysé"] != "OUI" else 0.0,
        axis=1,
    )

    for _, r in df_out_power_bi.iterrows():
        if r["Cyc_Cum"] == 0:
            continue
        p = df_out_power_bi[df_out_power_bi["Article"] == r["article parent"]]
        if not p.empty and p.iloc[0]["Cyc_Cum"] > 0:
            if r["Cyc_Cum"] < p.iloc[0]["Cyc_Cum"]:
                print("incohérent:", r["Article"], r["Cyc_Cum"], "<", p.iloc[0]["Cyc_Cum"])

    df_out_power_bi.drop(
        columns=["Référence Article", "Désignation Article"],
        errors="ignore",
        inplace=True,
    )

    df_ref_mongo = df_cycle_detail.drop_duplicates(
        subset=["Référence Article"],
        keep="last",
    ).set_index("Référence Article")

    for col_mongo, col_sap in dico_ecrasement_mongo.items():
        if col_mongo not in df_ref_mongo.columns or col_sap not in df_out_power_bi.columns:
            continue
        valeurs_mongo = df_out_power_bi["Article"].map(df_ref_mongo[col_mongo])
        df_out_power_bi[col_sap] = valeurs_mongo.fillna(df_out_power_bi[col_sap])

    return df_out_power_bi


def stocker_donnee_power_bi(config, df) -> bool:
    """Écrit le DataFrame df sous forme de CSV dans le bucket PowerBI."""
    try:
        with ConnexionMinIO(config) as clientMinIO:
            bucket_name = config["MINIO"]["BucketPowerBI"]

            if not clientMinIO.bucket_exists(bucket_name):
                clientMinIO.make_bucket(bucket_name)
                logger.info(f"Bucket MinIO créé: {bucket_name}")

            csv_bytes = df.to_csv(index=False).encode("utf-8")
            csv_buffer = BytesIO(csv_bytes)
            csv_buffer.seek(0)

            try:
                clientMinIO.remove_object(bucket_name, "export_power_bi.csv")
                logger.debug("Objet export_power_bi.csv supprimé avant réécriture")
            except S3Error as e:
                if e.code != "NoSuchKey":
                    logger.warning(f"Impossible de supprimer l'objet précédent: {e}")

            clientMinIO.put_object(
                bucket_name,
                "export_power_bi.csv",
                csv_buffer,
                len(csv_bytes),
                content_type="text/csv",
            )
            logger.info(
                f"Fichier export_power_bi.csv écrit avec succès dans le bucket {bucket_name}"
            )
            return True

    except S3Error as exc:
        logger.error(f"Erreur MinIO lors de l'écriture de export_power_bi.csv : {exc}")
        return False
    except Exception as exc:
        logger.exception(f"Erreur inattendue dans stocker_donnee_power_bi: {exc}")
        return False


def get_donnee_power_bi(config):
    b_power_bi_ready = True

    with ConnexionMinIO(config) as clientMinIO:
        list_buckets = [bucket.name for bucket in clientMinIO.list_buckets()]
        if config["MINIO"]["BucketPowerBI"] not in list_buckets:
            clientMinIO.make_bucket(config["MINIO"]["BucketPowerBI"])
            b_power_bi_ready = False
        else:
            objects = clientMinIO.list_objects(
                bucket_name=config["MINIO"]["BucketPowerBI"],
                prefix="",
                recursive=True,
            )
            list_name_object = [obj.object_name for obj in objects]

            if "export_power_bi.csv" in list_name_object:
                response = clientMinIO.get_object(
                    config["MINIO"]["BucketPowerBI"],
                    "export_power_bi.csv",
                )
                return pd.read_csv(response)
            else:
                b_power_bi_ready = False

    if not b_power_bi_ready:
        df_power_bi = generate_data_power_bi(config)
        if df_power_bi is not None:
            stocker_donnee_power_bi(config, df_power_bi)
        return df_power_bi
