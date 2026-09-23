"""
Liste les images d'un dossier upload vers un CSV (et un XLSX si openpyxl est installé).
LECTURE SEULE sur UPLOAD_DIR : aucun ajout, aucune modification, aucune suppression.
Format attendu : [prefixe]<1 à 6 chiffres><suffixe libre>.<extension image>
Ex : 123.JPG, 123CONTENT.jpg, 123_img-2284 (2).jpg, STOK123.JPG

Optimisé pour les gros volumes (200 000+ fichiers) sur lecteur réseau :
os.scandir() au lieu de pathlib, et aucun appel système par fichier.
"""
import csv
import os
import re
import sys
import time

# ---------- Config ----------
UPLOAD_DIR = r"V:/upload"
OUTPUT_DIR = r"C:/temp/liste_images"   # surtout PAS dans UPLOAD_DIR
RECURSIF = False          # True pour parcourir aussi les sous-dossiers
SEPARATEUR = ";"          # ";" pour Excel FR, "," sinon
PAS_LOG = 5000            # une ligne de suivi tous les N fichiers
PREFIXES = ["STOK"]       # liste blanche des préfixes avant le numéro
FAIRE_XLSX = True         # False = CSV seul (bien plus rapide sur gros volumes)
# ----------------------------

_prefixes = "|".join(re.escape(p) for p in PREFIXES)
# (?!\d) : le numéro s'arrête au 6e chiffre max et n'est pas suivi d'un autre chiffre
PATTERN = re.compile(
    rf"^(?:{_prefixes})?(\d{{1,6}})(?!\d)(.*?)\.(jpe?g|png|gif|bmp|webp|tiff?)$",
    re.IGNORECASE,
)

def log(msg):
    print(msg, flush=True)

def parcourir(racine):
    """Génère (nom_fichier, chemin_complet) sans stat() superflu."""
    if RECURSIF:
        for dossier, _sous, fichiers in os.walk(racine):
            base = dossier.replace("\\", "/").rstrip("/")
            for nom in fichiers:
                yield nom, f"{base}/{nom}"
    else:
        base = racine.replace("\\", "/").rstrip("/")
        with os.scandir(racine) as it:
            for entry in it:
                # is_file() utilise le cache du scandir : pas d'aller-retour réseau
                if entry.is_file():
                    yield entry.name, f"{base}/{entry.name}"

def main():
    log(f"Dossier source : {UPLOAD_DIR}  (lecture seule)")
    if not os.path.isdir(UPLOAD_DIR):
        log("ERREUR : dossier introuvable ou inaccessible.")
        return
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log(f"Dossier de sortie : {OUTPUT_DIR}")
    log("Parcours en cours...")

    t0 = time.time()
    match = PATTERN.match
    lignes = []
    ajouter = lignes.append
    ignores = []
    vus = 0

    for nom, chemin in parcourir(UPLOAD_DIR):
        vus += 1
        m = match(nom)
        if m:
            ajouter((m.group(1), nom, chemin))
        else:
            ignores.append(chemin)
        if vus % PAS_LOG == 0:
            dt = time.time() - t0
            log(f"  {vus} fichiers lus - {len(lignes)} retenus - {vus/dt:.0f} fich./s")

    dt = time.time() - t0
    log(f"Parcours terminé : {vus} fichiers en {dt:.1f}s ({vus/max(dt, 0.001):.0f} fich./s)")
    log(f"  -> {len(lignes)} images retenues, {len(ignores)} hors format")

    log("Tri...")
    lignes.sort(key=lambda l: (int(l[0]), l[0], l[1].lower()))

    csv_path = os.path.join(OUTPUT_DIR, "liste_images.csv")
    log(f"Écriture du CSV : {csv_path}")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=SEPARATEUR)
        w.writerow(["Num", "nomfichier", "chemin"])
        w.writerows(lignes)
    log(f"  CSV terminé : {len(lignes)} lignes")

    if FAIRE_XLSX:
        try:
            from openpyxl import Workbook
            xlsx_path = os.path.join(OUTPUT_DIR, "liste_images.xlsx")
            log(f"Écriture du XLSX (peut prendre 1-2 min) : {xlsx_path}")
            wb = Workbook(write_only=True)   # mode léger, peu de RAM
            ws = wb.create_sheet("Images")
            ws.append(["Num", "nomfichier", "chemin"])
            for i, ligne in enumerate(lignes, 1):
                ws.append(list(ligne))
                if i % 50000 == 0:
                    log(f"  {i}/{len(lignes)} lignes préparées")
            wb.save(xlsx_path)
            log("  XLSX terminé")
        except ImportError:
            log("  openpyxl absent : pas de XLSX (pip install openpyxl)")

    if ignores:
        ign_path = os.path.join(OUTPUT_DIR, "fichiers_ignores.txt")
        with open(ign_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(ignores))
        log(f"{len(ignores)} fichiers hors format listés dans {ign_path}")

    log(f"Terminé en {time.time() - t0:.1f}s")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERREUR : {e}", flush=True)
    if sys.stdout.isatty():
        input("Appuyez sur Entrée pour fermer...")
