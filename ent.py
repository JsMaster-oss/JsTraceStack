"""
ETAPE 2 - A executer sur la machine Linux (Python + librairies OK).

Croise :
  - la liste des fichiers produite par 1_lister_windows.bat (liste_upload.txt)
  - la table MariaDB (colonnes num, FichierJoint, FichierJoint2,
    photoContenu, PhotoContenant)

Sortie : un CSV/XLSX num | colonne | nom en base | nom reel | chemin complet | statut
Aucun acces au dossier upload n'est necessaire ici : on travaille sur le listing.
"""
import csv
import os
import sys
import time
from collections import defaultdict

# ---------- Config ----------
LISTING = "liste_upload.txt"        # fichier produit par le .bat
OUTPUT_DIR = "."

# Racine telle qu'elle apparait dans le listing, et racine voulue en sortie.
# Mettre les deux identiques pour ne rien reecrire du tout.
# Attention : une chaine r"..." ne peut pas se terminer par un backslash.
# Ecrire r"V:\upload" (sans backslash final), pas r"V:\upload\".
RACINE_LISTING = r"V:\upload"
RACINE_SORTIE = r"V:\upload"

# Style des chemins ecrits dans la colonne "chemin" :
#   "windows" -> V:\upload\123.JPG   (antislash, comme dans le listing)
#   "posix"   -> V:/upload/123.JPG   (slash)
#   "brut"    -> exactement la ligne du listing, sans aucune reecriture
STYLE_CHEMIN = "windows"

SOURCE_BDD = "db"                   # "db" = MariaDB direct, "csv" = export CSV
TABLE = "ma_table"
COLONNES = ["FichierJoint", "FichierJoint2", "photoContenu", "PhotoContenant"]
COL_NUM = "num"

DB = dict(host="192.168.1.10", port=3306, user="lecteur",
          password="motdepasse", database="mabase")

EXPORT_CSV = "export_table.csv"     # si SOURCE_BDD = "csv"
SEPARATEUR = ";"
FAIRE_XLSX = True
# ----------------------------

def log(msg):
    print(msg, flush=True)

def charger_listing(chemin):
    """Index : nom de fichier en minuscules -> liste des chemins complets."""
    index = defaultdict(list)
    total = 0
    with open(chemin, encoding="utf-8", errors="replace") as fh:
        for ligne in fh:
            ligne = ligne.strip().lstrip("\ufeff")
            if not ligne:
                continue
            total += 1
            nom = ligne.replace("\\", "/").rsplit("/", 1)[-1]
            index[nom.lower()].append(ligne)
    log(f"Listing : {total} fichiers, {len(index)} noms distincts")
    return index

def charger_bdd():
    """Retourne une liste de dicts {num, FichierJoint, ...}."""
    if SOURCE_BDD == "csv":
        with open(EXPORT_CSV, encoding="utf-8-sig", newline="") as fh:
            # sniff du separateur (; ou ,)
            echantillon = fh.read(4096)
            fh.seek(0)
            try:
                dialecte = csv.Sniffer().sniff(echantillon, delimiters=";,\t")
            except csv.Error:
                dialecte = csv.excel
            return list(csv.DictReader(fh, dialect=dialecte))

    import pymysql          # pip install pymysql
    cols = ", ".join(f"`{c}`" for c in [COL_NUM] + COLONNES)
    conn = pymysql.connect(charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, **DB)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {cols} FROM `{TABLE}`")
            return cur.fetchall()
    finally:
        conn.close()

def chemin_sortie(chemin):
    """Reecrit la racine si demande, puis applique le style de separateur choisi."""
    if STYLE_CHEMIN == "brut":
        return chemin

    # Comparaison sur une forme neutre pour que la racine soit reconnue
    # quel que soit le separateur utilise dans le listing.
    neutre = chemin.replace("\\", "/")
    racine_src = RACINE_LISTING.replace("\\", "/").rstrip("/")
    racine_dst = RACINE_SORTIE.replace("\\", "/").rstrip("/")
    if racine_src and neutre.lower().startswith(racine_src.lower()):
        neutre = racine_dst + neutre[len(racine_src):]

    if STYLE_CHEMIN == "windows":
        return neutre.replace("/", "\\")
    return neutre

def normaliser(valeur):
    """Nettoie une valeur de la base : NULL, espaces, chemin eventuel."""
    if valeur is None:
        return ""
    v = str(valeur).strip().strip('"').strip()
    if not v or v.lower() in ("null", "none", "0"):
        return ""
    # si la base stocke un chemin et pas juste un nom de fichier
    return v.replace("\\", "/").rsplit("/", 1)[-1]

def main():
    t0 = time.time()
    if not os.path.isfile(LISTING):
        log(f"ERREUR : listing introuvable ({LISTING})")
        return
    index = charger_listing(LISTING)

    log("Lecture de la base...")
    lignes_bdd = charger_bdd()
    log(f"  {len(lignes_bdd)} enregistrements")

    resultats = []
    stats = defaultdict(int)

    for row in lignes_bdd:
        num = str(row.get(COL_NUM, "")).strip()
        for col in COLONNES:
            nom_bdd = normaliser(row.get(col))
            if not nom_bdd:
                stats["vide"] += 1
                continue
            trouves = index.get(nom_bdd.lower(), [])
            if not trouves:
                resultats.append((num, col, nom_bdd, "", "", "INTROUVABLE"))
                stats["introuvable"] += 1
            else:
                statut = "OK" if len(trouves) == 1 else f"DOUBLON({len(trouves)})"
                stats["ok" if len(trouves) == 1 else "doublon"] += 1
                for chemin in trouves:
                    reel = chemin.replace("\\", "/").rsplit("/", 1)[-1]
                    resultats.append(
                        (num, col, nom_bdd, reel, chemin_sortie(chemin), statut))

    log(f"Resultats : {stats['ok']} OK, {stats['doublon']} doublons, "
        f"{stats['introuvable']} introuvables, {stats['vide']} colonnes vides")

    # Fichiers presents sur le disque mais references nulle part
    references = {n.lower() for _, _, n, _, _, _ in resultats if n}
    orphelins = [c[0] for nom, c in index.items() if nom not in references]
    log(f"  {len(orphelins)} fichiers du disque non references en base")

    entetes = ["num", "colonne", "nom_en_base", "nom_reel", "chemin", "statut"]
    csv_path = os.path.join(OUTPUT_DIR, "mapping.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=SEPARATEUR)
        w.writerow(entetes)
        w.writerows(resultats)
    log(f"CSV ecrit : {csv_path} ({len(resultats)} lignes)")

    with open(os.path.join(OUTPUT_DIR, "orphelins.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(orphelins))

    if FAIRE_XLSX:
        try:
            from openpyxl import Workbook
            wb = Workbook(write_only=True)
            ws = wb.create_sheet("Mapping")
            ws.append(entetes)
            for r in resultats:
                ws.append(list(r))
            wb.save(os.path.join(OUTPUT_DIR, "mapping.xlsx"))
            log("XLSX ecrit : mapping.xlsx")
        except ImportError:
            log("openpyxl absent : pas de XLSX")

    log(f"Termine en {time.time() - t0:.1f}s")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERREUR : {e}", flush=True)
        sys.exit(1)
