@echo off
REM ============================================================
REM ETAPE 1 - A executer sur le poste Windows qui voit V:\upload
REM Aucune librairie, aucun Python : uniquement des commandes natives.
REM LECTURE SEULE : dir ne fait que lister, rien n'est modifie.
REM ============================================================

set "SOURCE=V:\upload"
set "SORTIE=C:\temp\liste_upload.txt"

REM Code page UTF-8 pour ne pas casser les accents dans les noms de fichiers
chcp 65001 >nul

if not exist "C:\temp" mkdir "C:\temp"

echo Listing de %SOURCE% en cours...
echo (quelques minutes pour 200 000 fichiers sur un lecteur reseau)

REM /b = noms bruts, /s = recursif, /a-d = fichiers seulement
dir /b /s /a-d "%SOURCE%" > "%SORTIE%"

if errorlevel 1 (
  echo ERREUR : dossier inaccessible ou chemin incorrect.
  pause
  exit /b 1
)

for /f %%N in ('find /c /v "" ^< "%SORTIE%"') do set LIGNES=%%N
echo.
echo Termine : %LIGNES% fichiers listes dans %SORTIE%
echo Copiez ce fichier sur la machine Linux, puis lancez 2_mapping.py
pause
