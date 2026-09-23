"""
ETAPE 1 (version Python) - a executer sur le poste Windows qui voit V:\\upload.
Uniquement la bibliotheque standard : os, csv, sys, time. Aucun pip, aucun import externe.
LECTURE SEULE : le script ne fait que lire des noms de fichiers.

Produit liste_upload.txt (un chemin complet par ligne), a transferer sur la machine
Linux pour 2_mapping.py.
"""
import csv
import os
import sys
import time

# ---------- Config ----------
SOURCE = r"V:\upload"
# Par defaut dans le profil utilisateur : toujours accessible en ecriture,
# contrairement a C:\temp sur un poste d'entreprise.
SORTIE = os.path.join(os.path.expanduser("~"), "liste_upload.txt")
SORTIE_CSV = os.path.join(os.path.expanduser("~"), "liste_upload.csv")
FAIRE_CSV = False         # True = ecrit aussi un CSV nom;chemin
PAS_LOG = 5000
# ----------------------------

def log(msg):
    print(msg, flush=True)

def parcourir(racine):
    """Parcours recursif via os.scandir : rapide, et sans stat() par fichier."""
    piles = [racine]
    while piles:
        dossier = piles.pop()
        try:
            with os.scandir(dossier) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            piles.append(entry.path)
                        else:
                            yield entry.name, entry.path
                    except OSError as e:
                        log(f"  [ignore] {entry.path} : {e}")
        except PermissionError:
            log(f"  [acces refuse] {dossier}")
        except OSError as e:
            log(f"  [erreur] {dossier} : {e}")

def main():
    log(f"Source : {SOURCE}")
    if not os.path.isdir(SOURCE):
        log("ERREUR : dossier introuvable.")
        log("  - le lecteur V: est-il monte dans CETTE session ?")
        log("  - sinon utilisez le chemin UNC : \\\\serveur\\partage\\upload")
        return

    log(f"Sortie : {SORTIE}")
    log("Parcours en cours...")
    t0 = time.time()
    n = 0

    # encoding explicite : les accents des noms de fichiers passent proprement sous Linux
    with open(SORTIE, "w", encoding="utf-8", newline="\n") as fh:
        wcsv = None
        fcsv = None
        if FAIRE_CSV:
            fcsv = open(SORTIE_CSV, "w", encoding="utf-8-sig", newline="")
            wcsv = csv.writer(fcsv, delimiter=";")
            wcsv.writerow(["nomfichier", "chemin"])
        try:
            for nom, chemin in parcourir(SOURCE):
                fh.write(chemin + "\n")
                if wcsv:
                    wcsv.writerow([nom, chemin.replace("\\", "/")])
                n += 1
                if n % PAS_LOG == 0:
                    dt = time.time() - t0
                    log(f"  {n} fichiers - {n / max(dt, 0.001):.0f} fich./s")
        finally:
            if fcsv:
                fcsv.close()

    dt = time.time() - t0
    log(f"Termine : {n} fichiers en {dt:.1f}s ({n / max(dt, 0.001):.0f} fich./s)")
    log(f"Fichier a transferer : {SORTIE}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERREUR : {e}", flush=True)
    if sys.stdout.isatty():
        input("Appuyez sur Entree pour fermer...")
