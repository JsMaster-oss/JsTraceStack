"""
Liste les images d'un dossier upload vers un CSV (et un XLSX si openpyxl est installé).
Format attendu des fichiers : <1 à 6 chiffres>[CONTENT|BOX].<extension image>
Ex : 123.JPG, 123CONTENT.jpg, 123BOX.png
"""
import csv
import re
from pathlib import Path

# ---------- Config ----------
UPLOAD_DIR = Path(r"V:/upload")
OUTPUT_CSV = Path("liste_images.csv")
OUTPUT_XLSX = Path("liste_images.xlsx")
RECURSIF = False          # True pour parcourir aussi les sous-dossiers
SEPARATEUR = ";"          # ";" pour Excel FR, "," sinon
# ----------------------------

PATTERN = re.compile(
    r"^(\d{1,6})(CONTENT|BOX)?\.(jpe?g|png|gif|bmp|webp|tiff?)$",
    re.IGNORECASE,
)

def main():
    fichiers = UPLOAD_DIR.rglob("*") if RECURSIF else UPLOAD_DIR.iterdir()
    lignes, ignores = [], []

    for f in fichiers:
        if not f.is_file():
            continue
        m = PATTERN.match(f.name)
        if m:
            lignes.append((m.group(1), f.name, f.resolve().as_posix()))
        else:
            ignores.append(f.as_posix())

    # Tri par numéro puis par nom (123.JPG, 123BOX.jpg, 123CONTENT.jpg)
    lignes.sort(key=lambda l: (int(l[0]), l[0], l[1].lower()))

    # CSV (utf-8-sig pour qu'Excel lise bien les accents)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=SEPARATEUR)
        w.writerow(["Num", "nomfichier", "chemin"])
        w.writerows(lignes)
    print(f"{len(lignes)} images -> {OUTPUT_CSV}")

    # XLSX optionnel
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Images"
        ws.append(["Num", "nomfichier", "chemin"])
        for l in lignes:
            ws.append(l)
        # Colonne Num forcée en texte pour conserver les zéros de tête
        for cell in ws["A"][1:]:
            cell.number_format = "@"
        ws.column_dimensions["B"].width = 25
        ws.column_dimensions["C"].width = 60
        wb.save(OUTPUT_XLSX)
        print(f"XLSX -> {OUTPUT_XLSX}")
    except ImportError:
        print("openpyxl absent : pas de XLSX (pip install openpyxl)")

    # Fichiers qui ne collent pas au format, pour vérif
    if ignores:
        Path("fichiers_ignores.txt").write_text("\n".join(ignores), encoding="utf-8")
        print(f"{len(ignores)} fichiers hors format -> fichiers_ignores.txt")

if __name__ == "__main__":
    main()
