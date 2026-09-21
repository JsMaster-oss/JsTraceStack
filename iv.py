#!/usr/bin/env python3
"""
import_versions.py

Phase 1 : Analyse complète des fichiers XLSX (dry-run, aucune écriture)
Phase 2 : Import en base (transactions par version)

Usage :
    python import_versions.py
"""

import os
import re
import sys
import json
import html
import logging
import unicodedata
from datetime import datetime, date
from difflib import SequenceMatcher
from collections import defaultdict
from pathlib import Path

import openpyxl
import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Configuration (chemins d'abord : nécessaires au logging fichier)
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
# IMPORT_DIR configurable par variable d'env : indispensable en conteneur restreint
# (root FS read-only, user non-root) → pointer vers un volume INSCRIPTIBLE,
# ex. IMPORT_DIR=/data. Par défaut : dossier import/ à côté du script.
IMPORT_DIR = Path(os.environ.get("IMPORT_DIR", SCRIPT_DIR / "import"))
MAPPINGS_FILE = IMPORT_DIR / "mappings_valides.json"
REPORT_FILE = IMPORT_DIR / "rapport_import.html"

# Tableau de mapping inter-versions saisi À LA MAIN (source de vérité des liens).
# Il vit dans IMPORT_DIR à côté des XLSX de version : son nom est donc RÉSERVÉ et
# exclu du glob de load_xlsx_files(), sinon il serait importé comme une version.
MAPPING_XLSX = IMPORT_DIR / os.environ.get("MAPPING_FILE", "mapping_versions.xlsx")

# ---------------------------------------------------------------------------
# Logging — fichier si possible, sinon stdout seul.
# Ne JAMAIS planter au démarrage si le dossier n'est pas inscriptible.
# ---------------------------------------------------------------------------

_log_handlers = [logging.StreamHandler(sys.stdout)]
try:
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    _log_handlers.append(logging.FileHandler(IMPORT_DIR / 'import.log', encoding='utf-8'))
except OSError as _e:
    print(f"[WARN] Journalisation fichier désactivée ({IMPORT_DIR}/import.log "
          f"non accessible : {_e}). Logs sur la sortie standard uniquement.")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=_log_handlers,
)
log = logging.getLogger('import_versions')

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_FTCI1lZinO5g@ep-broad-hill-alx7ru5n-pooler.c-3.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
)

# Mode non-interactif (drapeau --yes) : auto-confirme tout. À n'utiliser QUE si
# tous les mappings sont déjà en cache (run prod en Job, après validation staging).
AUTO_YES = False

FUZZY_THRESHOLD_CHAINE = 0.85
FUZZY_THRESHOLD_REF    = 0.80   # seuil pour pays, scénarios, activités
ECHEANCE_MATCH_RATIO   = 0.60   # Jaccard min sur les dates d'échéance pour suspecter
                                 # une même ligne ayant changé d'identité

# Indices colonnes XLSX (0-based).
# Les fichiers ont une 1re LIGNE d'en-tête (sautée) et une 1re COLONNE d'index
# technique (ignorée) → toutes les colonnes de données sont décalées de +1.
HEADER_ROWS     = 1   # nombre de lignes d'en-tête à ignorer en tête de chaque onglet
COL_INDEX       = 0   # index technique du fichier (ignoré)
COL_ACTIVITE    = 1   # nom de l'activité (ex. "Développement")
COL_NOM_PRODUIT = 2
COL_REFERENCE   = 3
COL_LIBELLE     = 4
COL_NUM_WONO    = 5
COL_T0_DATE     = 6
COL_NOM_PAYS    = 7
COL_NOM_SCENARIO= 8
COL_QUANTITE    = 9
COL_NON_RETENUE = 10
COL_DATE_MAD    = 11
COL_QTY_TOTALE  = 12
COL_NR_TOTALE   = 13
COL_PRISE_COM   = 14
COL_COMM_COM    = 15

# ---------------------------------------------------------------------------
# Rapport HTML incrémental (dashboard)
# ---------------------------------------------------------------------------

_LEVEL_LABEL = {
    'info': 'Info', 'success': 'OK', 'warning': 'Attention',
    'blocking': 'Bloquant', 'error': 'Erreur',
}


class HtmlReporter:
    """Écrit un dashboard HTML ré-écrit sur disque à CHAQUE événement.

    Robuste à une interruption : le fichier reflète toujours le dernier état connu.
    """

    def __init__(self, path):
        self.path = path
        self.started = datetime.now()
        self.status = 'En cours'
        self.meta = {}                 # libellé → valeur (entête)
        self.kpis = {}                 # libellé → (valeur, level)
        self.entries = []              # {ts, phase, level, title, detail}

    def reset(self):
        self.__init__(self.path)
        self.flush()

    def set_meta(self, **kw):
        self.meta.update(kw)
        self.flush()

    def kpi(self, label, value, level='info'):
        self.kpis[label] = (value, level)
        self.flush()

    def add(self, phase, level, title, detail=''):
        self.entries.append({
            'ts': datetime.now().strftime('%H:%M:%S'),
            'phase': phase, 'level': level,
            'title': title, 'detail': detail,
        })
        log_fn = {'warning': log.warning, 'blocking': log.warning,
                  'error': log.error}.get(level, log.info)
        log_fn("[%s] %s %s", phase, title, f"— {detail}" if detail else '')
        self.flush()

    def set_status(self, status):
        self.status = status
        self.flush()

    # ------------------------------------------------------------------
    def _render(self):
        e = html.escape
        status_cls = {
            'En cours': 'st-run', 'Terminé': 'st-ok',
            'Bloqué': 'st-block', 'Interrompu': 'st-err', 'Annulé': 'st-warn',
        }.get(self.status, 'st-run')

        kpi_html = ''.join(
            f'<div class="kpi kpi-{lvl}"><div class="kpi-v">{e(str(val))}</div>'
            f'<div class="kpi-l">{e(label)}</div></div>'
            for label, (val, lvl) in self.kpis.items()
        )

        meta_html = ''.join(
            f'<tr><th>{e(str(k))}</th><td>{e(str(v))}</td></tr>'
            for k, v in self.meta.items()
        )

        # Regrouper les entrées par phase, dans l'ordre d'apparition
        phases = []
        for ent in self.entries:
            if ent['phase'] not in phases:
                phases.append(ent['phase'])

        sections = ''
        for ph in phases:
            rows = ''
            for ent in self.entries:
                if ent['phase'] != ph:
                    continue
                detail = f'<div class="detail">{e(ent["detail"])}</div>' if ent['detail'] else ''
                rows += (
                    f'<tr class="lv-{ent["level"]}">'
                    f'<td class="ts">{e(ent["ts"])}</td>'
                    f'<td><span class="badge b-{ent["level"]}">{_LEVEL_LABEL.get(ent["level"], ent["level"])}</span></td>'
                    f'<td><div class="title">{e(ent["title"])}</div>{detail}</td>'
                    f'</tr>'
                )
            sections += (
                f'<section><h2>{e(ph)}</h2>'
                f'<table class="log"><tbody>{rows}</tbody></table></section>'
            )

        return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rapport d'import — RepliqueB</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0;
         background: #0f172a; color: #e2e8f0; }}
  header {{ padding: 24px 32px; background: #1e293b; border-bottom: 1px solid #334155; }}
  header h1 {{ margin: 0 0 4px; font-size: 20px; }}
  header .sub {{ color: #94a3b8; font-size: 13px; }}
  .status {{ display: inline-block; padding: 4px 12px; border-radius: 999px;
            font-size: 12px; font-weight: 600; margin-left: 8px; }}
  .st-run {{ background:#3b82f6; }} .st-ok {{ background:#16a34a; }}
  .st-block {{ background:#dc2626; }} .st-err {{ background:#b91c1c; }}
  .st-warn {{ background:#d97706; }}
  main {{ padding: 24px 32px; max-width: 1100px; margin: 0 auto; }}
  .kpis {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 24px; }}
  .kpi {{ flex: 1 1 120px; background:#1e293b; border:1px solid #334155;
         border-radius: 10px; padding: 14px 16px; }}
  .kpi-v {{ font-size: 26px; font-weight: 700; }}
  .kpi-l {{ font-size: 12px; color:#94a3b8; margin-top:2px; }}
  .kpi-blocking .kpi-v {{ color:#f87171; }} .kpi-warning .kpi-v {{ color:#fbbf24; }}
  .kpi-success .kpi-v {{ color:#4ade80; }} .kpi-info .kpi-v {{ color:#60a5fa; }}
  table {{ width: 100%; border-collapse: collapse; }}
  .meta th {{ text-align:left; color:#94a3b8; font-weight:500; padding:4px 12px 4px 0; width:160px; }}
  .meta td {{ padding:4px 0; }}
  section {{ margin-bottom: 28px; }}
  section h2 {{ font-size: 15px; border-bottom:1px solid #334155; padding-bottom:6px; }}
  table.log td {{ padding: 8px 10px; border-bottom: 1px solid #1e293b; vertical-align: top; }}
  .ts {{ color:#64748b; font-variant-numeric: tabular-nums; white-space:nowrap; width:70px; }}
  .title {{ font-weight: 500; }}
  .detail {{ color:#94a3b8; font-size: 13px; margin-top: 3px; white-space: pre-wrap; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:6px; font-size:11px;
           font-weight:600; white-space:nowrap; }}
  .b-info {{ background:#1d4ed8; }} .b-success {{ background:#15803d; }}
  .b-warning {{ background:#b45309; }} .b-blocking {{ background:#b91c1c; }}
  .b-error {{ background:#7f1d1d; }}
  tr.lv-blocking td, tr.lv-error td {{ background: rgba(220,38,38,0.08); }}
  tr.lv-warning td {{ background: rgba(217,119,6,0.07); }}
</style></head>
<body>
<header>
  <h1>Rapport d'import des versions <span class="status {status_cls}">{e(self.status)}</span></h1>
  <div class="sub">Démarré le {self.started.strftime('%d/%m/%Y %H:%M:%S')} ·
       dernière mise à jour {datetime.now().strftime('%H:%M:%S')}</div>
</header>
<main>
  <div class="kpis">{kpi_html or '<div class="kpi"><div class="kpi-l">En attente…</div></div>'}</div>
  <table class="meta"><tbody>{meta_html}</tbody></table>
  {sections}
</main>
</body></html>"""

    def flush(self):
        try:
            IMPORT_DIR.mkdir(exist_ok=True)
            with open(self.path, 'w', encoding='utf-8') as f:
                f.write(self._render())
        except OSError as exc:
            log.warning("Écriture du rapport HTML impossible : %s", exc)


REPORT = HtmlReporter(REPORT_FILE)

# ---------------------------------------------------------------------------
# Helpers généraux
# ---------------------------------------------------------------------------

def parse_date(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s:
        return None
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def cell_str(row, idx):
    if idx >= len(row) or row[idx] is None:
        return ''
    return str(row[idx]).strip()


def cell_int(row, idx, default=0):
    if idx >= len(row) or row[idx] is None:
        return default
    try:
        return int(row[idx])
    except (ValueError, TypeError):
        return default


def version_sort_key(name):
    """Tri NATUREL (numérique) des noms de version, = ordre chronologique.

    Découpe le nom en segments chiffres / texte et compare les chiffres comme
    des nombres : 'V9' < 'V10', 'V61.2' < 'V61.10' < 'V62', 'V99' < 'V100'.
    """
    return [int(p) if p.isdigit() else p.lower()
            for p in re.split(r'(\d+)', str(name))]


def determine_successor(db_data, newest_version):
    """Version chronologiquement juste APRÈS newest_version : la plus ancienne
    version archivée plus récente déjà en base (ex. un gel V62), sinon 'En cours'.

    C'est elle qu'on rattache à la dernière version importée (son parent_id) —
    donc l'En cours n'est touché que s'il n'y a pas de version figée entre les deux.
    """
    key_newest = version_sort_key(newest_version)
    plus_recentes = [
        v for v in db_data['versions']
        if v != 'En cours' and version_sort_key(v) > key_newest
    ]
    if plus_recentes:
        return min(plus_recentes, key=version_sort_key)
    return 'En cours'


def fuzzy(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def best_match(value, candidates, threshold):
    """Retourne (candidate, score) ou (None, score_max)."""
    best, best_s = None, 0.0
    for c in candidates:
        s = fuzzy(value, c)
        if s > best_s:
            best, best_s = c, s
    if best_s >= threshold:
        return best, best_s
    return None, best_s


def match_lines(current, prev):
    """Apparie les lignes 'current' (version + récente) aux lignes 'prev' (version
    + ancienne), selon la RÈGLE SCÉNARIO, en appariement 1-à-1 :

      1) exact (activité, produit, scénario) ;
      2) sinon, si (activité, produit) est SEUL des deux côtés → même ligne
         (changement de scénario) → lien ;
      3) sinon (plusieurs scénarios pour ce (activité, produit)) → on discrimine
         par la RÉFÉRENCE : même reference → lien ; sinon → rien (nouvelle ligne).

    `current` / `prev` : dict { (act, prod, scen): {... , 'reference': str, ...} }.
    Retourne { ident_current: info_prev }. Une ligne prev n'est appariée qu'une fois.
    """
    result, consumed = {}, set()
    curr_ap = defaultdict(int)
    for (a, p, _s) in current:
        curr_ap[(a, p)] += 1

    # 1) matchs exacts
    for ident in current:
        if ident in prev:
            result[ident] = prev[ident]
            consumed.add(ident)

    # 2) (act, prod) seul → scénario changé ; 3) sinon référence
    for ident in current:
        if ident in result:
            continue
        a, p, _s = ident
        ap = (a, p)
        prev_ap = [(k, v) for k, v in prev.items()
                   if (k[0], k[1]) == ap and k not in consumed]
        if not prev_ap:
            continue
        if len(prev_ap) == 1 and curr_ap[ap] == 1:
            result[ident] = prev_ap[0][1]
            consumed.add(prev_ap[0][0])
        else:
            ref = current[ident].get('reference')
            for k, v in prev_ap:
                if ref and v.get('reference') == ref:
                    result[ident] = v
                    consumed.add(k)
                    break
    return result


def jaccard(a, b):
    """Indice de Jaccard entre deux ensembles (0.0 si l'un est vide)."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def build_version_line_index(version_data):
    """Index ligne d'une version XLSX.

    Retourne { (wono, libelle): { (activite, produit, scenario):
                                  {'reference', 'dates': set(date_mad)} } }.
    wono est le num_wono BRUT du fichier (cohérent avec les clés de chaîne).
    """
    idx = defaultdict(dict)
    for rows in version_data.values():
        for row in rows:
            ck = (row['num_wono'], row['libelle'])
            lk = (row['nom_activite'], row['nom_produit'], row['nom_scenario'])
            entry = idx[ck].setdefault(lk, {'reference': row['reference'], 'dates': set()})
            if row['date_mad']:
                entry['dates'].add(row['date_mad'])
    return idx


def identity_change_candidate(ref, dates, candidates):
    """Cherche parmi `candidates` (dict identité→{reference,dates}) une ligne
    d'identité DIFFÉRENTE mais dont la reference ou l'ensemble des dates
    correspond — signe probable d'un changement activité/produit/scénario.

    Retourne (identite_candidate, raison, score) ou (None, None, 0.0).
    """
    best_key, best_reason, best_score = None, None, 0.0
    for cand_key, cand in candidates.items():
        ref_match = bool(ref) and ref == cand.get('reference')
        jac = jaccard(dates, cand.get('dates', set()))
        if ref_match and jac >= best_score:
            best_key, best_reason, best_score = cand_key, 'référence + échéances', max(jac, 0.99)
        elif jac >= ECHEANCE_MATCH_RATIO and jac > best_score:
            best_key, best_reason, best_score = cand_key, 'échéances', jac
        elif ref_match and best_key is None:
            best_key, best_reason, best_score = cand_key, 'référence', 0.90
    return best_key, best_reason, best_score


def confirm(prompt):
    if AUTO_YES:
        print(f"{prompt} [auto-oui]")
        return True
    while True:
        ans = input(f"{prompt} [oui/non, q=quitter] ").strip().lower()
        if ans in ('oui', 'o', 'yes', 'y'):
            return True
        if ans in ('non', 'n', 'no'):
            return False
        if ans in ('q', 'quit'):
            raise KeyboardInterrupt   # arrêt volontaire (progression déjà sauvegardée)
        print("Répondre par 'oui', 'non' ou 'q'.")


def choose(prompt, choices):
    print(prompt)
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    while True:
        ans = input("Votre choix (numéro, q=quitter) : ").strip().lower()
        if ans in ('q', 'quit'):
            raise KeyboardInterrupt
        try:
            idx = int(ans) - 1
            if 0 <= idx < len(choices):
                return idx
        except ValueError:
            pass
        print(f"Entrer un nombre entre 1 et {len(choices)} (ou q pour quitter).")

# ---------------------------------------------------------------------------
# Mappings persistants
# ---------------------------------------------------------------------------

# Namespaces de mappings persistés (source unique de vérité)
#   produit        : correction de typo (nom XLSX → nom produit existant en base)
#   produit_create : produits à créer, confirmés par l'utilisateur
MAPPING_KEYS = ('wono', 'pays', 'scenario', 'activite', 'chaine', 'encours',
                'produit', 'produit_create')


def load_mappings():
    IMPORT_DIR.mkdir(exist_ok=True)
    m = {}
    if MAPPINGS_FILE.exists():
        with open(MAPPINGS_FILE, 'r', encoding='utf-8') as f:
            m = json.load(f)
    # Si le fichier contient autre chose qu'un objet JSON (null, liste…), repartir propre
    if not isinstance(m, dict):
        m = {}
    # Garantir que chaque namespace existe et soit bien un dict (robuste si JSON corrompu)
    for k in MAPPING_KEYS:
        if not isinstance(m.get(k), dict):
            m[k] = {}
    return m


def _json_safe(o):
    """Rend une structure sérialisable JSON (sets → listes triées, récursif)."""
    if isinstance(o, set):
        return sorted(o)
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


def save_mappings(mappings):
    IMPORT_DIR.mkdir(exist_ok=True)
    with open(MAPPINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(_json_safe(mappings), f, ensure_ascii=False, indent=2)
    log.info("Mappings sauvegardés dans %s", MAPPINGS_FILE)

# ---------------------------------------------------------------------------
# Chargement XLSX
# ---------------------------------------------------------------------------

def load_xlsx_files():
    """
    Retourne dict trié : {version_name → {famille → [row_dict, ...]}}
    """
    files = sorted(
        (f for f in IMPORT_DIR.glob("*.xlsx") if f.name != MAPPING_XLSX.name),
        key=lambda p: version_sort_key(p.stem),
    )
    if not files:
        print(f"[ERREUR] Aucun fichier .xlsx trouvé dans {IMPORT_DIR}")
        sys.exit(1)

    result = {}
    for fpath in files:
        version_name = fpath.stem
        try:
            wb = openpyxl.load_workbook(fpath, data_only=True)
        except Exception as e:
            print(f"[ERREUR] Lecture de {fpath.name} impossible : {e}")
            sys.exit(1)

        version_data = {}
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            # min_row est 1-based : sauter les lignes d'en-tête (1re ligne = headers)
            for raw in ws.iter_rows(min_row=HEADER_ROWS + 1, values_only=True):
                if all(v is None for v in raw):
                    continue
                rows.append({
                    'nom_activite':       cell_str(raw, COL_ACTIVITE),
                    'nom_produit':        cell_str(raw, COL_NOM_PRODUIT),
                    'reference':          cell_str(raw, COL_REFERENCE),
                    'libelle':            cell_str(raw, COL_LIBELLE),
                    'num_wono':           cell_str(raw, COL_NUM_WONO),
                    't0_date':            parse_date(raw[COL_T0_DATE] if len(raw) > COL_T0_DATE else None),
                    'nom_pays':           cell_str(raw, COL_NOM_PAYS),
                    'nom_scenario':       cell_str(raw, COL_NOM_SCENARIO),
                    'quantite':           cell_int(raw, COL_QUANTITE),
                    'non_retenue':        cell_int(raw, COL_NON_RETENUE),
                    'date_mad':           parse_date(raw[COL_DATE_MAD] if len(raw) > COL_DATE_MAD else None),
                    'quantite_totale':    cell_int(raw, COL_QTY_TOTALE),
                    'non_retenue_totale': cell_int(raw, COL_NR_TOTALE),
                    'prise_com':          parse_date(raw[COL_PRISE_COM] if len(raw) > COL_PRISE_COM else None),
                    'commentaire_com':    cell_str(raw, COL_COMM_COM),
                    '_famille':           sheet_name,
                    '_version':           version_name,
                })
            version_data[sheet_name] = rows
        result[version_name] = version_data

    return result

# ---------------------------------------------------------------------------
# Tableau de mapping inter-versions (saisi à la main)
# ---------------------------------------------------------------------------
#
# Format (un seul onglet, MULTI-FAMILLE) :
#   ligne 1 : le nom de la version en tête de chaque bloc de colonnes, du plus
#             RÉCENT au plus ANCIEN de gauche à droite (V61.2 | V60 | V59 | ...) ;
#   ligne 2 : sous-en-têtes du bloc — « Produit », « Contrat », « WONO »
#             (l'ordre réel est lu, pas supposé) ;
#   ligne 3+: une ligne = une LIGNÉE, c.-à-d. un contrat_ligne suivi dans le temps.
#             Chaque bloc donne son identité dans cette version-là.
#             Bloc VIDE = la lignée n'existe pas dans cette version.
#
# Le tableau ne recense que les CAS LITIGIEUX : il n'est pas exhaustif. Tout ce
# qu'il ne couvre pas reste traité par l'appariement habituel.
#
# Règle du TROU : si une lignée est absente d'une version intermédiaire, la chaîne
# est COUPÉE — aucun lien par-dessus le trou, une nouvelle lignée (nouveau
# premier_id) repart à la réapparition.

# Rôle d'une sous-colonne, déduit par MOT-CLÉ CONTENU dans l'en-tête plutôt que
# par égalité stricte : les intitulés réels sont décorés ('WONO/H', 'N° WONO',
# 'Libellé contrat', 'Produit (nom)'…). Ordre = priorité d'examen.
_MAPPING_ROLES = (
    ('wono',    'wono'),
    ('contrat', 'libelle'),
    ('libelle', 'libelle'),
    ('produit', 'produit'),
)


def _norm(s):
    """Normalisation de comparaison : casse, accents et espaces neutralisés."""
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', s).strip().lower()


def _role_sous_colonne(entete):
    """Rôle d'une sous-colonne d'après son en-tête, ou None.

    Tolérant aux décorations : 'WONO/H', 'N° WONO', 'Libellé contrat' sont
    reconnus. Seule la ponctuation et la casse sont neutralisées — on ne devine
    rien d'autre : un en-tête sans mot-clé connu reste non reconnu, et le bloc
    est signalé comme incomplet.
    """
    h = re.sub(r'[^a-z0-9]', '', _norm(entete))
    for mot, role in _MAPPING_ROLES:
        if mot in h:
            return role
    return None


def load_manual_mapping():
    """Lit MAPPING_XLSX et retourne (lineages, ordre_versions).

    lineages : [ {'_row': int, 'famille': str|None,
                  'versions': {version: {'produit','libelle','wono'}}} ]
    ordre_versions : noms de version triés du plus ANCIEN au plus RÉCENT
                     (ordre naturel du script, inverse de celui du tableau).

    Colonne d'étiquettes (optionnelle, à gauche) : repérée par l'en-tête
    « Famille » en ligne 2 (« Version » en ligne 1 au-dessus). Elle porte la
    famille de chaque lignée et lève les ambiguïtés entre familles. Une famille
    vide reprend celle de la lignée du dessus — les cellules fusionnées et les
    familles écrites une seule fois par groupe fonctionnent donc aussi.
    """
    if not MAPPING_XLSX.exists():
        log.info("Aucun tableau de mapping manuel (%s) — appariement automatique seul.",
                 MAPPING_XLSX)
        return [], []

    try:
        wb = openpyxl.load_workbook(MAPPING_XLSX, data_only=True)
    except Exception as exc:
        print(f"[ERREUR] Lecture de {MAPPING_XLSX.name} impossible : {exc}")
        sys.exit(1)

    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 3:
        log.warning("%s : moins de 3 lignes, aucun mapping exploitable.", MAPPING_XLSX.name)
        return [], []

    # Colonne d'étiquettes : en-tête « Famille » en ligne 2.
    col_famille = next((ci for ci, v in enumerate(rows[1]) if _norm(v) == 'famille'), None)

    # Ligne 1 : chaque cellule non vide ouvre un bloc de colonnes pour une version.
    # On écarte la colonne d'étiquettes et les intitulés « Version » / « Famille »,
    # qui nomment les lignes d'en-tête et non une version.
    starts = [(str(v).strip(), ci) for ci, v in enumerate(rows[0])
              if v is not None and str(v).strip()
              and ci != col_famille and _norm(v) not in ('version', 'famille')]
    if not starts:
        log.warning("%s : aucune version en ligne 1.", MAPPING_XLSX.name)
        return [], []

    # Ligne 2 : rôle réel de chaque colonne du bloc (on ne suppose pas l'ordre).
    blocks = {}
    for bi, (ver, ci) in enumerate(starts):
        end = starts[bi + 1][1] if bi + 1 < len(starts) else len(rows[1])
        cols = {}
        for col in range(ci, end):
            role = _role_sous_colonne(rows[1][col])
            if role and role not in cols:      # 1re colonne trouvée pour ce rôle
                cols[role] = col
        missing = {'produit', 'libelle', 'wono'} - set(cols)
        if missing:
            vus = [str(rows[1][col]) for col in range(ci, end)
                   if col < len(rows[1]) and rows[1][col] is not None]
            print(f"[ERREUR] {MAPPING_XLSX.name} : bloc '{ver}' — sous-en-tête(s) "
                  f"manquant(s) : {', '.join(sorted(missing))}. "
                  f"En-têtes lus : {', '.join(vus) if vus else '(aucun)'}. "
                  f"Attendu : un intitulé contenant 'produit', 'contrat' (ou "
                  f"'libellé') et 'wono'.")
            sys.exit(1)
        blocks[ver] = cols

    ordre = sorted(blocks, key=version_sort_key)   # chronologique CROISSANT

    lineages = []
    famille_courante = ''
    for ri, row in enumerate(rows[2:], start=3):
        # Lue AVANT le saut des lignes sans version : une ligne ne portant que la
        # famille sert d'en-tête de groupe pour les lignées qui suivent.
        if col_famille is not None:
            famille = cell_str(row, col_famille)
            if famille:
                famille_courante = famille

        if all(v is None or not str(v).strip() for v in row):
            continue
        versions = {}
        for ver in ordre:
            cols = blocks[ver]
            blk = {role: cell_str(row, col) for role, col in cols.items()}
            if any(blk.values()):
                versions[ver] = blk
        if versions:
            lineages.append({'_row': ri, 'famille': famille_courante or None,
                             'versions': versions})

    log.info("Tableau de mapping manuel : %d lignée(s) sur %s%s",
             len(lineages), ' → '.join(ordre),
             '' if col_famille is None else ' (familles renseignées)')
    return lineages, ordre


def build_manual_lookup(version_data, mappings):
    """Index d'une version pour retrouver les lignes visées par le tableau.

    { (wono_résolu, libelle_normalisé) : { produit_normalisé : [ligne, ...] } }
    où ligne = {'raw': (activite, produit, scenario), 'wono_raw', 'libelle', 'famille'}
    — 'raw' porte les valeurs BRUTES du XLSX, celles qui composent les clés de
    mappings['chaine'].
    """
    idx = defaultdict(lambda: defaultdict(list))
    seen = set()
    for famille, rows in version_data.items():
        for row in rows:
            ck = (resolve_wono(row['num_wono'], mappings), _norm(row['libelle']))
            ident = (row['nom_activite'], row['nom_produit'], row['nom_scenario'])
            if (ck, famille, ident) in seen:   # une ligne = N échéances → dédoublonner
                continue
            seen.add((ck, famille, ident))
            idx[ck][_norm(row['nom_produit'])].append({
                'raw': ident, 'wono_raw': row['num_wono'],
                'libelle': row['libelle'], 'famille': famille,
            })
    return idx


def chain_key(version, wono_raw, libelle, famille, activite, produit, scenario):
    """Clé de mappings['chaine'] — valeurs BRUTES du XLSX, comme partout ailleurs.

    La FAMILLE en fait partie : un même (activité, produit, scénario) peut exister
    dans deux familles du même contrat, et ce sont deux lignes distinctes.
    """
    return f"{version}|{wono_raw}|{libelle}|{famille}|{activite}|{produit}|{scenario}"


def _describe(ligne):
    a, p, s = ligne['raw']
    return f"{a} / {p} / {s} [{ligne['famille']}]"


# ---------------------------------------------------------------------------
# Chargement DB
# ---------------------------------------------------------------------------

def connect_db():
    return psycopg2.connect(DATABASE_URL)


def load_db_data(conn):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT id, nom_pays FROM pays WHERE statut = true")
    db_pays = {r['nom_pays']: r['id'] for r in cur.fetchall()}

    cur.execute("SELECT id, nom_scenario FROM scenario WHERE statut = true")
    db_scenarios = {r['nom_scenario']: r['id'] for r in cur.fetchall()}

    cur.execute("SELECT id, nom_famille FROM famille WHERE statut = true")
    db_familles = {r['nom_famille']: r['id'] for r in cur.fetchall()}

    cur.execute("SELECT id, nom_activite FROM activite WHERE statut = true")
    db_activites = {r['nom_activite']: r['id'] for r in cur.fetchall()}

    cur.execute("""
        SELECT p.id, p.nom_produit, p.id_famille, p.id_activite,
               f.nom_famille, a.nom_activite
        FROM produit p
        LEFT JOIN famille f ON p.id_famille = f.id
        LEFT JOIN activite a ON p.id_activite = a.id
        WHERE p.statut = true
    """)
    # (nom_produit, id_famille) → list of {id, id_activite, nom_activite}
    db_produits = defaultdict(list)
    for p in cur.fetchall():
        db_produits[(p['nom_produit'], p['id_famille'])].append(dict(p))

    cur.execute("SELECT id, nom_version, etat FROM version")
    db_versions = {r['nom_version']: {'id': r['id'], 'etat': r['etat']} for r in cur.fetchall()}

    cur.execute("SELECT DISTINCT num_wono FROM contrat WHERE LENGTH(num_wono) <= 10")
    db_wonos = [r['num_wono'] for r in cur.fetchall()]

    # Index « En cours » enrichi : identité complète + reference + échéances,
    # pour le rattachement final ET la détection de changement d'identité.
    cur.execute("""
        SELECT cl.id            AS cl_id,
               cl.premier_id    AS cl_premier_id,
               cl.id_contrat,
               cl.reference,
               c.num_wono, c.libelle, c.premier_id AS c_premier_id,
               a.nom_activite, p.nom_produit, s.nom_scenario,
               e.id AS ech_id, e.date_mad, e.premier_id AS ech_premier_id
        FROM contrat_ligne cl
        JOIN contrat c   ON cl.id_contrat  = c.id
        JOIN version v   ON c.id_version   = v.id
        JOIN produit p   ON cl.id_produit  = p.id
        JOIN activite a  ON cl.id_activite = a.id
        JOIN scenario s  ON cl.id_scenario = s.id
        LEFT JOIN echeance e ON e.id_contrat_ligne = cl.id
        WHERE v.nom_version = 'En cours'
          AND cl.premier_id IS NOT NULL
    """)
    encours = {'contrats': {}, 'lignes': {}}
    for r in cur.fetchall():
        ck = (r['num_wono'], r['libelle'])
        encours['contrats'].setdefault(ck, {
            'id': r['id_contrat'], 'premier_id': r['c_premier_id'],
        })
        lk = (r['num_wono'], r['libelle'], r['nom_activite'],
              r['nom_produit'], r['nom_scenario'])
        ligne = encours['lignes'].setdefault(lk, {
            'id': r['cl_id'], 'premier_id': r['cl_premier_id'],
            'id_contrat': r['id_contrat'], 'reference': r['reference'],
            'num_wono': r['num_wono'], 'libelle': r['libelle'],
            'nom_activite': r['nom_activite'], 'nom_produit': r['nom_produit'],
            'nom_scenario': r['nom_scenario'],
            'dates': set(), 'echeances': {},
        })
        if r['ech_id']:
            ligne['dates'].add(r['date_mad'])
            ligne['echeances'][r['date_mad']] = {
                'id': r['ech_id'], 'premier_id': r['ech_premier_id'],
            }

    cur.execute("SELECT id FROM utilisateur ORDER BY id LIMIT 1")
    row = cur.fetchone()
    system_user_id = row['id'] if row else None

    cur.close()
    return {
        'pays': db_pays,
        'scenarios': db_scenarios,
        'familles': db_familles,
        'activites': db_activites,
        'produits': db_produits,
        'versions': db_versions,
        'wonos': db_wonos,
        'encours': encours,
        'system_user_id': system_user_id,
    }


def load_imported_version_from_db(conn, version_name):
    """Reconstruit la structure imported{} d'une version déjà en base (recovery)."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT c.id, c.num_wono, c.libelle, c.premier_id,
               cl.id AS cl_id, cl.premier_id AS cl_premier_id, cl.reference,
               p.nom_produit, a.nom_activite, s.nom_scenario, f.nom_famille,
               e.id AS ech_id, e.date_mad, e.premier_id AS ech_premier_id
        FROM version v
        JOIN contrat c        ON c.id_version     = v.id
        JOIN contrat_ligne cl  ON cl.id_contrat    = c.id
        JOIN produit p         ON cl.id_produit    = p.id
        LEFT JOIN famille f    ON p.id_famille     = f.id
        JOIN activite a        ON cl.id_activite   = a.id
        JOIN scenario s        ON cl.id_scenario   = s.id
        LEFT JOIN echeance e   ON e.id_contrat_ligne = cl.id
        WHERE v.nom_version = %s
    """, (version_name,))
    rows = cur.fetchall()
    cur.close()

    imported = {}
    for row in rows:
        ck = (row['num_wono'], row['libelle'])
        if ck not in imported:
            imported[ck] = {'id': row['id'], 'premier_id': row['premier_id'], 'lignes': {}}
        if row['cl_id']:
            # Indexé PAR FAMILLE : un même (activité, produit, scénario) peut
            # exister dans deux familles sur le même contrat (les produits sont
            # distincts en base, un par famille) — sans ce niveau, l'un écrase
            # l'autre et son chaînage est perdu.
            fam = row['nom_famille']
            lk = (row['nom_activite'], row['nom_produit'], row['nom_scenario'])
            lignes_fam = imported[ck]['lignes'].setdefault(fam, {})
            if lk not in lignes_fam:
                lignes_fam[lk] = {
                    'id': row['cl_id'],
                    'premier_id': row['cl_premier_id'],
                    'reference': row['reference'],
                    'echeances': {},
                }
            if row['ech_id']:
                lignes_fam[lk]['echeances'][row['date_mad']] = {
                    'id': row['ech_id'],
                    'premier_id': row['ech_premier_id'],
                }
    return imported

# ---------------------------------------------------------------------------
# PHASE 1 — Vérifications
# ---------------------------------------------------------------------------

def check_a_wono_length(xlsx_data, db_data, mappings, issues, proposals):
    """a) num_wono > 10 chars."""
    db_wonos = db_data['wonos']
    seen = set()

    for ver, ver_data in xlsx_data.items():
        for famille, rows in ver_data.items():
            for row in rows:
                wono = row['num_wono']
                if len(wono) <= 10 or wono in seen:
                    continue
                seen.add(wono)

                if wono in mappings['wono']:
                    proposals.append({
                        'type': 'info',
                        'message': f"[WONO] '{wono}' → '{mappings['wono'][wono]}' (cache)",
                    })
                    continue

                # Scorer TOUS les wono de la base et garder les 3 meilleurs.
                # Score = max(similarité globale, similarité sur le préfixe) ; bonus si
                # le wono DB est contenu dans le long wono ou en est le préfixe.
                scored = []
                for w in db_wonos:
                    s = max(fuzzy(w, wono), fuzzy(w, wono[:len(w)]))
                    if w in wono or wono.startswith(w):
                        s = max(s, 0.95)
                    scored.append((s, w))
                scored.sort(reverse=True)
                top = [w for s, w in scored if s >= 0.5][:3]

                if top:
                    proposals.append({
                        'type': 'wono',
                        'original': wono,
                        'candidates': top,          # liste ordonnée, meilleur en 1er
                        'message': f"[WONO] '{wono}' ({len(wono)} chars) > 10",
                    })
                else:
                    issues.append({
                        'type': 'blocking',
                        'message': (
                            f"[WONO BLOQUANT] '{wono}' ({len(wono)} chars) > 10, "
                            f"aucune correspondance en base"
                        ),
                    })


def check_b_chaine_ruptures(xlsx_data, mappings, proposals):
    """b) Ruptures de chaîne entre versions consécutives.

    Pour chaque ligne de V(n) sans correspondance exacte dans V(n-1) :
      1. fuzzy wono+libelle (scénario égal) ;
      2. fuzzy nom_produit (activité + scénario égaux) ;
      3. changement d'identité : même contrat (wono+libelle) mais activité/produit/
         scénario différents, repéré par reference et/ou ensemble des dates d'échéance.
    Tout lien proposé alimente mappings['chaine'].
    """
    versions = sorted(xlsx_data.keys())

    for i in range(1, len(versions)):
        prev_ver = versions[i - 1]
        curr_ver = versions[i]

        prev_idx = build_version_line_index(xlsx_data[prev_ver])   # {(wono,lib): {ident: {...}}}
        # ensemble plat des identités précédentes (wono, lib, act, prod, scen)
        prev_keys = set()
        for (pw, pl), lignes in prev_idx.items():
            for (pa, pp, ps) in lignes:
                prev_keys.add((pw, pl, pa, pp, ps))

        curr_idx = build_version_line_index(xlsx_data[curr_ver])

        for (cw, cl), lignes in curr_idx.items():
            for (ca, cp, cs), info in lignes.items():
                key = (cw, cl, ca, cp, cs)
                chain_key = f"{curr_ver}|{cw}|{cl}|{ca}|{cp}|{cs}"

                if key in prev_keys or chain_key in mappings['chaine']:
                    continue

                # 1. Fuzzy sur wono + libelle (scénario identique imposé)
                best_combo, best_s = None, 0.0
                for pk in prev_keys:
                    if pk[4] != cs:
                        continue
                    s = (fuzzy(cw, pk[0]) + fuzzy(cl, pk[1])) / 2
                    if s > best_s:
                        best_s, best_combo = s, pk
                if best_s >= FUZZY_THRESHOLD_CHAINE:
                    proposals.append({
                        'type': 'chaine', 'version': curr_ver, 'prev_version': prev_ver,
                        'current_key': key, 'suggested_key': best_combo, 'score': best_s,
                        'message': f"[CHAÎNE] {curr_ver}: {key} → {prev_ver}: {best_combo} (score={best_s:.2f})",
                    })
                    continue

                # 2. Fuzzy sur nom_produit seul, à activité + scénario égaux
                prod_candidates = [pk for pk in prev_keys if pk[2] == ca and pk[4] == cs]
                prod_best, prod_s = best_match(
                    cp, [pk[3] for pk in prod_candidates], FUZZY_THRESHOLD_CHAINE
                )
                if prod_best is not None:
                    suggested = next(pk for pk in prod_candidates if pk[3] == prod_best)
                    proposals.append({
                        'type': 'chaine', 'version': curr_ver, 'prev_version': prev_ver,
                        'current_key': key, 'suggested_key': suggested, 'score': prod_s,
                        'message': f"[CHAÎNE-PROD] {curr_ver}: {key} → produit proche '{prod_best}' dans {prev_ver} (score={prod_s:.2f})",
                    })
                    continue

                # 3. Changement d'identité : même contrat, reference/échéances proches
                same_contract = prev_idx.get((cw, cl), {})
                cand_key, reason, score = identity_change_candidate(
                    info['reference'], info['dates'], same_contract
                )
                if cand_key is not None:
                    suggested = (cw, cl, cand_key[0], cand_key[1], cand_key[2])
                    proposals.append({
                        'type': 'chaine', 'version': curr_ver, 'prev_version': prev_ver,
                        'current_key': key, 'suggested_key': suggested, 'score': score,
                        'message': (
                            f"[CHAÎNE-IDENTITÉ] {curr_ver}: {key} → {prev_ver}: {suggested} "
                            f"(même contrat, correspondance {reason}, score={score:.2f}) "
                            f"— changement activité/produit/scénario ?"
                        ),
                    })
                # sinon : nouvelle ligne, aucun lien — pas d'anomalie


def check_g_mapping_manuel(xlsx_data, lineages, ordre, mappings, issues, proposals):
    """g) Tableau de mapping manuel — SOURCE DE VÉRITÉ des liens inter-versions.

    Pour chaque lignée :
      1. localiser, dans chaque version renseignée, le contrat (WONO + libellé)
         puis ses lignes portant le produit indiqué → introuvable = BLOQUANT ;
      2. créer un lien entre chaque paire de versions ADJACENTES du tableau toutes
         deux renseignées (un trou coupe la chaîne : nouvelle lignée) ;
      3. si le produit visé désigne PLUSIEURS lignes du contrat (activités ou
         scénarios différents), ne rien deviner : proposer le choix à l'utilisateur.

    Les liens sans ambiguïté sont écrits directement dans mappings['chaine'] :
    ils viennent déjà d'une décision humaine, ils n'ont pas à être re-confirmés
    (et restent donc utilisables en mode --yes).
    """
    # Le tableau est la SOURCE DE VÉRITÉ : ses liens automatiques (source 'manuel')
    # sont des données DÉRIVÉES, recalculées à chaque run. Sans ça, corriger une
    # ligne du tableau resterait sans effet — le cache du run précédent primerait
    # silencieusement. Les arbitrages rendus à la main ('manuel-choisi') sont
    # conservés : ce sont des décisions, pas du dérivé.
    # Fait AVANT le retour anticipé : retirer une lignée du tableau doit aussi
    # retirer son lien.
    anciens = [k for k, v in mappings['chaine'].items()
               if isinstance(v, dict) and v.get('source') == 'manuel']
    if MAPPING_XLSX.exists():
        for k in anciens:
            del mappings['chaine'][k]
        if anciens:
            log.info("Mapping manuel : %d lien(s) automatique(s) du run précédent "
                     "recalculé(s) depuis le tableau.", len(anciens))
    elif anciens:
        # Pas de tableau sous la main : on ne détruit pas des liens qu'on ne peut
        # pas recalculer (ex. rejeu prod sans avoir recopié le tableau).
        issues.append({
            'type': 'warning',
            'message': (f"[MAPPING] {len(anciens)} lien(s) manuel(s) en cache mais "
                        f"{MAPPING_XLSX.name} absent d'{IMPORT_DIR} : les liens du run "
                        f"précédent sont conservés tels quels (non revérifiés)."),
        })

    if not lineages:
        return 0

    indexes = {v: build_manual_lookup(d, mappings) for v, d in xlsx_data.items()}
    liens = 0

    for lin in lineages:
        row_no = lin['_row']
        famille = lin.get('famille')
        fam_norm = _norm(famille) if famille else None
        located = {}

        # --- 1. Localisation + contrôles bloquants ---
        for ver in ordre:
            blk = lin['versions'].get(ver)
            if not blk:
                continue
            if ver not in indexes:
                proposals.append({
                    'type': 'info',
                    'message': (f"[MAPPING l.{row_no}] version '{ver}' absente de "
                                f"{IMPORT_DIR} — bloc ignoré"),
                })
                continue
            ck = (resolve_wono(blk['wono'], mappings), _norm(blk['libelle']))
            contrat = indexes[ver].get(ck)
            if not contrat:
                issues.append({
                    'type': 'blocking',
                    'message': (f"[MAPPING BLOQUANT l.{row_no}] version {ver} : contrat "
                                f"WONO '{blk['wono']}' / libellé '{blk['libelle']}' "
                                f"introuvable dans {ver}.xlsx"),
                })
                continue
            cands = contrat.get(_norm(blk['produit']))
            if not cands:
                issues.append({
                    'type': 'blocking',
                    'message': (f"[MAPPING BLOQUANT l.{row_no}] version {ver} : produit "
                                f"'{blk['produit']}' introuvable sur le contrat "
                                f"'{blk['libelle']}' ({blk['wono']}) — produits présents : "
                                f"{', '.join(sorted({c['raw'][1] for c in sum(contrat.values(), [])}))}"),
                })
                continue

            # La famille de la lignée restreint la recherche : c'est elle qui
            # départage deux lignes de même produit dans des familles différentes.
            if fam_norm:
                familles_version = {_norm(f): f for f in xlsx_data[ver]}
                if fam_norm not in familles_version:
                    issues.append({
                        'type': 'blocking',
                        'message': (f"[MAPPING BLOQUANT l.{row_no}] version {ver} : famille "
                                    f"'{famille}' absente de {ver}.xlsx — onglets présents : "
                                    f"{', '.join(sorted(xlsx_data[ver]))}"),
                    })
                    continue
                dans_famille = [c for c in cands if _norm(c['famille']) == fam_norm]
                if not dans_famille:
                    issues.append({
                        'type': 'blocking',
                        'message': (f"[MAPPING BLOQUANT l.{row_no}] version {ver} : produit "
                                    f"'{blk['produit']}' présent sur le contrat "
                                    f"'{blk['libelle']}' ({blk['wono']}) mais pas dans la "
                                    f"famille '{famille}' — familles trouvées : "
                                    f"{', '.join(sorted({c['famille'] for c in cands}))}"),
                    })
                    continue
                cands = dans_famille

            located[ver] = cands

        # --- 2. Liens entre colonnes ADJACENTES (le trou coupe la chaîne) ---
        for i in range(1, len(ordre)):
            prev_ver, curr_ver = ordre[i - 1], ordre[i]
            if prev_ver not in located or curr_ver not in located:
                continue
            prev_c, curr_c = located[prev_ver], located[curr_ver]

            for cl in curr_c:
                ck = chain_key(curr_ver, cl['wono_raw'], cl['libelle'],
                               cl['famille'], *cl['raw'])

                # --- 3. Cas simple : une ligne de chaque côté → lien direct ---
                # Écrase un éventuel lien en cache : plus d'ambiguïté ici, donc
                # plus rien à arbitrer, et c'est le tableau qui fait foi.
                if len(prev_c) == 1 and len(curr_c) == 1:
                    pl = prev_c[0]
                    mappings['chaine'][ck] = {
                        'prev_version':  prev_ver,
                        'prev_wono':     pl['wono_raw'],
                        'prev_libelle':  pl['libelle'],
                        'prev_famille':  pl['famille'],
                        'prev_activite': pl['raw'][0],
                        'prev_produit':  pl['raw'][1],
                        'prev_scenario': pl['raw'][2],
                        'source':        'manuel',
                    }
                    liens += 1
                    continue

                # --- 3bis. Ambiguïté : le produit désigne plusieurs lignes ---
                if ck in mappings['chaine']:
                    continue                 # arbitrage déjà rendu, on le garde
                proposals.append({
                    'type': 'chaine_manuel',
                    'version': curr_ver, 'prev_version': prev_ver,
                    'chain_key': ck, 'row_no': row_no,
                    'current_desc': _describe(cl),
                    'candidates': prev_c,
                    'message': (
                        f"[MAPPING AMBIGU l.{row_no}] {curr_ver}: {_describe(cl)} "
                        f"→ {len(prev_c)} ligne(s) candidates en {prev_ver} pour le "
                        f"produit '{lin['versions'][prev_ver]['produit']}'"
                        + (f" (famille '{famille}')" if famille else '')
                    ),
                })

    return liens


def check_c_pays_scenarios(xlsx_data, db_data, mappings, issues, proposals):
    """c) Pays et scénarios inconnus."""
    db_pays = db_data['pays']
    db_scenarios = db_data['scenarios']
    unknown_pays, unknown_scen = set(), set()

    for ver, ver_data in xlsx_data.items():
        for famille, rows in ver_data.items():
            for row in rows:
                p = row['nom_pays']
                s = row['nom_scenario']
                if p and p not in db_pays and p not in mappings['pays']:
                    unknown_pays.add(p)
                if s and s not in db_scenarios and s not in mappings['scenario']:
                    unknown_scen.add(s)

    for pays in sorted(unknown_pays):
        best, score = best_match(pays, list(db_pays.keys()), FUZZY_THRESHOLD_REF)
        if best:
            proposals.append({
                'type': 'pays',
                'original': pays,
                'suggested': best,
                'score': score,
                'message': f"[PAYS] '{pays}' inconnu → proposition: '{best}' (score={score:.2f})",
            })
        else:
            issues.append({
                'type': 'warning',
                'message': f"[PAYS] '{pays}' inconnu, aucune correspondance (création requise ou typo)",
            })

    for scen in sorted(unknown_scen):
        best, score = best_match(scen, list(db_scenarios.keys()), FUZZY_THRESHOLD_REF)
        if best:
            proposals.append({
                'type': 'scenario',
                'original': scen,
                'suggested': best,
                'score': score,
                'message': f"[SCÉNARIO] '{scen}' inconnu → proposition: '{best}' (score={score:.2f})",
            })
        else:
            issues.append({
                'type': 'warning',
                'message': f"[SCÉNARIO] '{scen}' inconnu, aucune correspondance (création requise ou typo)",
            })


def check_d_produit_activite(xlsx_data, db_data, mappings, issues, proposals):
    """d) Vérification produit + activité (triplet nom_produit / famille / activité explicite)."""
    db_familles  = db_data['familles']
    db_produits  = db_data['produits']
    db_activites = db_data['activites']
    seen = set()

    for ver, ver_data in xlsx_data.items():
        for famille, rows in ver_data.items():
            id_famille = db_familles.get(famille)
            if id_famille is None:
                issues.append({
                    'type': 'warning',
                    'message': f"[FAMILLE] '{famille}' absente de la base — onglet ignoré",
                })
                continue

            for row in rows:
                nom_produit  = row['nom_produit']
                nom_activite = row['nom_activite']
                triple = (nom_produit, famille, nom_activite)
                map_key = f"{nom_produit}|{famille}"

                if triple in seen:
                    continue
                seen.add(triple)

                # Activité inconnue → fuzzy ou anomalie
                if nom_activite not in db_activites:
                    best, score = best_match(nom_activite, list(db_activites.keys()), FUZZY_THRESHOLD_REF)
                    if best:
                        proposals.append({
                            'type': 'activite',
                            'original': nom_activite,
                            'suggested': best,
                            'score': score,
                            'map_key': map_key,
                            'message': (
                                f"[ACTIVITÉ] '{nom_activite}' inconnue "
                                f"→ proposition: '{best}' (score={score:.2f})"
                            ),
                        })
                    else:
                        issues.append({
                            'type': 'warning',
                            'message': (
                                f"[ACTIVITÉ] '{nom_activite}' inconnue, aucune correspondance "
                                f"(produit: {nom_produit}, famille: {famille})"
                            ),
                        })
                    continue

                # Produit introuvable pour ce triplet (nom_produit, id_famille, id_activite)
                id_activite = db_activites[nom_activite]
                matches = [
                    p for p in db_produits.get((nom_produit, id_famille), [])
                    if p['id_activite'] == id_activite
                ]
                pc_key = f"{nom_produit}|{famille}|{nom_activite}"
                if matches or pc_key in mappings['produit'] or pc_key in mappings['produit_create']:
                    continue

                # Produits existants pour cette famille + activité (validation de saisie),
                # et les 3 plus proches pour le menu rapide.
                existing_names = sorted({
                    nom for (nom, fid), plist in db_produits.items()
                    if fid == id_famille and any(p['id_activite'] == id_activite for p in plist)
                })
                scored = sorted(
                    ((fuzzy(nom_produit, n), n) for n in existing_names), reverse=True
                )
                candidates = [n for _, n in scored[:3]]

                proposals.append({
                    'type': 'produit',
                    'nom_produit': nom_produit,
                    'famille': famille,
                    'nom_activite': nom_activite,
                    'id_famille': id_famille,
                    'id_activite': id_activite,
                    'pc_key': pc_key,
                    'candidates': candidates,    # jusqu'à 3 produits existants proches
                    'existing': existing_names,  # tous les produits valides (famille+activité)
                    'message': f"[PRODUIT] '{nom_produit}' / {nom_activite} / {famille} absent en base",
                })


def check_totaux_nuls(xlsx_data, issues):
    """Ligne dont quantite_totale ET non_retenue_totale = 0 → anomalie BLOQUANTE.

    Règle métier : un total de 0 n'est valide que si non_retenue_totale > 0.
    """
    seen = set()
    for ver, ver_data in xlsx_data.items():
        for rows in ver_data.values():
            for row in rows:
                if row['quantite_totale'] == 0 and row['non_retenue_totale'] == 0:
                    key = (ver, row['num_wono'], row['libelle'],
                           row['nom_activite'], row['nom_produit'], row['nom_scenario'])
                    if key in seen:
                        continue
                    seen.add(key)
                    issues.append({
                        'type': 'blocking',
                        'message': (
                            f"[TOTAUX NULS] {ver} — {row['nom_produit']} / {row['nom_activite']} / "
                            f"{row['nom_scenario']} (wono {row['num_wono']}, « {row['libelle']} ») : "
                            f"quantite_totale ET non_retenue_totale = 0 (ligne invalide)"
                        ),
                    })


def check_e_version_existe(xlsx_data, db_data, issues):
    """e) Version déjà importée."""
    for ver in xlsx_data:
        if ver in db_data['versions']:
            issues.append({
                'type': 'duplicate_version',
                'version': ver,
                'message': f"[VERSION EXISTANTE] '{ver}' existe déjà en base",
            })


def check_f_en_cours(xlsx_data, db_data, mappings, issues, proposals):
    """f) Aperçu du rattachement : à quelle version SUCCESSEUR (figée) la dernière
    version importée sera rattachée.

    Invariant : on ne touche jamais l'En cours. Il doit donc exister une version
    figée plus récente (ex. V62). Sinon, on PRÉVIENT : le rattachement sera sauté.
    """
    versions = sorted(xlsx_data.keys(), key=version_sort_key)
    if not versions:
        return []
    newest = versions[-1]
    succ = determine_successor(db_data, newest)

    if succ == 'En cours':
        issues.append({
            'type': 'warning',
            'message': (
                f"[RATTACHEMENT] Aucune version figée plus récente que '{newest}' "
                f"en base → le rattachement sera SAUTÉ (l'En cours n'est pas touché). "
                f"Fige la version cible (ex. V62) AVANT l'import pour établir les liens."
            ),
        })
    else:
        proposals.append({
            'type': 'encours',
            'message': (
                f"[RATTACHEMENT] '{newest}' sera rattachée au successeur figé "
                f"« {succ} » (parent_id de {succ} mis à jour, premier_id des imports "
                f"ré-ancré). L'En cours n'est PAS touché."
            ),
        })
    return [succ]   # truthy

# ---------------------------------------------------------------------------
# Confirmation interactive des propositions
# ---------------------------------------------------------------------------

def confirm_proposals(proposals, mappings):
    total = len(proposals)
    for n, prop in enumerate(proposals, 1):
        ptype = prop.get('type')
        print(f"\n--- [{n}/{total}] ---")

        if ptype == 'info':
            print(f"  {prop['message']}")

        elif ptype == 'wono':
            print(f"\n{prop['message']}")
            # compat : 'candidates' (nouveau) ou 'suggested' (ancien format)
            cands = prop.get('candidates') or (
                [prop['suggested']] if prop.get('suggested') else []
            )
            options = [f"Utiliser '{c}'" for c in cands]
            options.append("Saisir un autre num_wono")
            options.append("Laisser bloquant (ne rien mapper)")

            idx = choose("  Quel num_wono correspond ?", options)
            if idx < len(cands):
                mappings['wono'][prop['original']] = cands[idx]
            elif idx == len(cands):
                ans = input("  num_wono correct (≤ 10 chars) : ").strip()
                if ans and len(ans) <= 10:
                    mappings['wono'][prop['original']] = ans
                else:
                    print("  Ignoré — restera bloquant.")
            else:
                print("  Laissé bloquant.")

        elif ptype == 'chaine':
            # Chemin FUZZY hérité (check_b), débranché de phase1. Ses propositions
            # ne portent pas la famille, alors que chain_key l'exige désormais :
            # à retravailler avant tout réarmement de check_b.
            print(f"\n{prop['message']}")
            if prop['suggested_key'] and confirm("  Accepter ce lien de chaîne ? (oui/non)"):
                k = prop['current_key']   # (wono, libelle, activite, produit, scenario)
                ck = f"{prop['version']}|{k[0]}|{k[1]}|{k[2]}|{k[3]}|{k[4]}"
                sk = prop['suggested_key']
                mappings['chaine'][ck] = {
                    'prev_version':  prop['prev_version'],
                    'prev_wono':     sk[0],
                    'prev_libelle':  sk[1],
                    'prev_activite': sk[2],
                    'prev_produit':  sk[3],
                    'prev_scenario': sk[4],
                }
            else:
                print("  Lien non établi — cette ligne sera traitée comme nouvelle.")

        elif ptype == 'chaine_manuel':
            print(f"\n{prop['message']}")
            print(f"  Ligne {prop['version']} : {prop['current_desc']}")
            cands = prop['candidates']
            options = [f"Lier à {_describe(c)} ({prop['prev_version']})" for c in cands]
            options.append("Aucun lien (traiter comme une nouvelle ligne)")

            idx = choose("  Le tableau ne permet pas de trancher — quelle ligne "
                         f"de {prop['prev_version']} correspond ?", options)
            if idx < len(cands):
                pl = cands[idx]
                mappings['chaine'][prop['chain_key']] = {
                    'prev_version':  prop['prev_version'],
                    'prev_wono':     pl['wono_raw'],
                    'prev_libelle':  pl['libelle'],
                    'prev_famille':  pl['famille'],
                    'prev_activite': pl['raw'][0],
                    'prev_produit':  pl['raw'][1],
                    'prev_scenario': pl['raw'][2],
                    'source':        'manuel-choisi',
                }
                REPORT.add('Phase 1 — Mapping manuel', 'success',
                           f"Lien choisi : {prop['current_desc']} → {_describe(pl)}",
                           f"{prop['prev_version']} → {prop['version']} "
                           f"(tableau ligne {prop['row_no']})")
            else:
                print("  Aucun lien — cette ligne repartira sur une nouvelle lignée.")
                REPORT.add('Phase 1 — Mapping manuel', 'warning',
                           f"Aucun lien retenu pour {prop['current_desc']}",
                           f"Nouvelle lignée en {prop['version']}")

        elif ptype == 'pays':
            print(f"\n{prop['message']}")
            if confirm("  Accepter ce mapping pays ? (oui/non)"):
                mappings['pays'][prop['original']] = prop['suggested']
            else:
                ans = input("  Entrer le nom_pays correct (ENTER pour ignorer) : ").strip()
                if ans:
                    mappings['pays'][prop['original']] = ans

        elif ptype == 'scenario':
            print(f"\n{prop['message']}")
            if confirm("  Accepter ce mapping scénario ? (oui/non)"):
                mappings['scenario'][prop['original']] = prop['suggested']
            else:
                ans = input("  Entrer le nom_scenario correct (ENTER pour ignorer) : ").strip()
                if ans:
                    mappings['scenario'][prop['original']] = ans

        elif ptype == 'activite':
            print(f"\n{prop['message']}")
            if confirm("  Accepter ce mapping activité ? (oui/non)"):
                mappings['activite'][prop['original']] = prop['suggested']
            else:
                ans = input("  Entrer le nom_activite correct (ENTER pour ignorer) : ").strip()
                if ans:
                    mappings['activite'][prop['original']] = ans

        elif ptype == 'encours_identity':
            print(f"\n{prop['message']}")
            if confirm("  Confirmer qu'il s'agit de la même ligne (changement d'identité) ? (oui/non)"):
                mappings['encours'][str(prop['encours_line_id'])] = prop['xlsx_identity']
            else:
                print("  Non confirmé — la ligne En cours ne sera pas rattachée à cette ligne.")

        elif ptype == 'produit':
            print(f"\n{prop['message']}")
            np, fam, act = prop['nom_produit'], prop['famille'], prop['nom_activite']
            cands = prop.get('candidates') or []
            existing = set(prop.get('existing') or [])
            options = [f"Utiliser le produit existant '{c}'" for c in cands]
            options.append("Saisir un autre nom de produit existant")
            options.append(f"Créer le produit '{np}' sur {fam} / {act}")
            options.append("Ignorer (lignes de ce produit non importées)")

            idx = choose(f"  Produit '{np}' absent. Que faire ?", options)
            chosen = None              # nom de produit existant retenu (mapping)
            if idx < len(cands):
                chosen = cands[idx]
            elif idx == len(cands):
                # Saisie manuelle, vérifiée contre la base (famille + activité)
                while True:
                    ans = input("  Nom EXACT du produit existant (ENTER pour annuler) : ").strip()
                    if not ans:
                        break
                    if ans in existing:
                        chosen = ans
                        break
                    print(f"  '{ans}' n'existe pas en base pour {fam} / {act}. Réessaie.")
            elif idx == len(cands) + 1:
                mappings['produit_create'][prop['pc_key']] = {
                    'nom_produit': np, 'id_famille': prop['id_famille'],
                    'id_activite': prop['id_activite'], 'famille': fam, 'nom_activite': act,
                }
                REPORT.add('Phase 1 — Produits', 'success',
                           f"Création confirmée : '{np}'", f"Famille {fam}, activité {act}")

            if chosen:
                mappings['produit'][prop['pc_key']] = chosen
                REPORT.add('Phase 1 — Produits', 'success',
                           f"Produit mappé : '{np}' → '{chosen}'",
                           f"Famille {fam}, activité {act}")
            elif idx != len(cands) + 1:   # tout sauf la création (déjà tracée) :
                # « Ignorer » OU saisie manuelle annulée → non importé
                REPORT.add('Phase 1 — Produits', 'warning',
                           f"Produit '{np}' ignoré",
                           "Les lignes référençant ce produit ne seront pas importées")

        elif ptype == 'encours':
            print(f"  {prop['message']}")

        # Persister après CHAQUE proposition traitée : si tu coupes (q / Ctrl-C),
        # tout ce qui précède est déjà sur disque et ne sera pas reproposé.
        save_mappings(mappings)

# ---------------------------------------------------------------------------
# Résolution via mappings
# ---------------------------------------------------------------------------

def resolve_wono(wono, mappings):
    if len(wono) > 10:
        return mappings['wono'].get(wono, wono)
    return wono


def resolve_pays_id(nom_pays, db_data, mappings):
    mapped = mappings['pays'].get(nom_pays, nom_pays)
    return db_data['pays'].get(mapped)


def resolve_scenario_id(nom_scenario, db_data, mappings):
    mapped = mappings['scenario'].get(nom_scenario, nom_scenario)
    return db_data['scenarios'].get(mapped)


def resolve_activite_id(nom_activite, mappings, db_data):
    """Retourne id_activite après application du mapping éventuel."""
    mapped = mappings['activite'].get(nom_activite, nom_activite)
    return db_data['activites'].get(mapped)


def resolve_identity(activite, produit, scenario, famille, mappings):
    """Identité de ligne BRUTE (XLSX) → identité RÉSOLUE (noms en base).

    C'est la clé utilisée par prev_imported[...]['lignes'].
    """
    return (
        mappings['activite'].get(activite, activite),
        mappings['produit'].get(f"{produit}|{famille}|{activite}", produit),
        mappings['scenario'].get(scenario, scenario),
    )


def resolve_produit(nom_produit, nom_famille, nom_activite, db_data, mappings):
    """Retourne (id_produit, id_activite) ou (None, None).

    Utilise nom_activite (colonne 0 du XLSX) pour identifier le produit exact,
    et applique une éventuelle correction de typo (mappings['produit']).
    """
    id_famille  = db_data['familles'].get(nom_famille)
    if id_famille is None:
        return None, None

    id_activite = resolve_activite_id(nom_activite, mappings, db_data)
    if id_activite is None:
        return None, None

    # Correction de typo confirmée en Phase 1 (nom XLSX → nom existant)
    pc_key = f"{nom_produit}|{nom_famille}|{nom_activite}"
    nom_resolu = mappings['produit'].get(pc_key, nom_produit)

    candidates = db_data['produits'].get((nom_resolu, id_famille), [])
    for p in candidates:
        if p['id_activite'] == id_activite:
            return p['id'], id_activite

    return None, None

# ---------------------------------------------------------------------------
# PHASE 2 — Import d'une version
# ---------------------------------------------------------------------------

def import_single_version(conn, version_name, version_data, db_data, mappings,
                           prev_imported, system_user_id):
    """
    Insère version + contrats + lignes + échéances dans une transaction.
    Retourne imported{} pour la chaîne suivante.
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Créer la version
    cur.execute(
        "INSERT INTO version (nom_version, etat) VALUES (%s, 'Archivé') RETURNING id",
        (version_name,),
    )
    version_id = cur.fetchone()['id']

    imported = {}
    stats = {'contrats': 0, 'lignes': 0, 'echeances': 0, 'liens': 0,
             'liens_manuels': 0, 'echeances_fusionnees': 0}

    # Regrouper les lignes XLSX par (wono, libelle) → famille → rows.
    # (num_wono, libelle) = identité du CONTRAT entre versions : un écart sur l'un
    # des deux ⇒ contrat non relié ⇒ toutes ses lignes deviennent nouvelles.
    # (Le T0/t0_date n'entre PAS dans l'identité — il est juste stocké.)
    contrats_raw = defaultdict(lambda: defaultdict(list))
    for famille, rows in version_data.items():
        for row in rows:
            wono = resolve_wono(row['num_wono'], mappings)
            contrats_raw[(wono, row['libelle'])][famille].append(row)

    for (wono, libelle), familles_rows in contrats_raw.items():

        # Chercher le contrat parent dans la version précédente
        prev_contrat = prev_imported.get((wono, libelle))

        # Essayer via mapping de chaîne (wono/libelle renommés) : il suffit qu'une
        # des lignes du contrat ait un mapping de chaîne pour retrouver le contrat parent.
        if prev_contrat is None:
            for famille, rows in familles_rows.items():
                for r in rows:
                    # la clé utilise le num_wono BRUT (cohérent avec check_g/confirm)
                    cm = mappings['chaine'].get(chain_key(
                        version_name, r['num_wono'], r['libelle'], famille,
                        r['nom_activite'], r['nom_produit'], r['nom_scenario']))
                    if cm:
                        prev_contrat = prev_imported.get((cm['prev_wono'], cm['prev_libelle']))
                        break
                if prev_contrat:
                    break

        parent_id  = prev_contrat['id']        if prev_contrat else None
        premier_id = prev_contrat['premier_id'] if prev_contrat else None

        cur.execute("""
            INSERT INTO contrat
                (id_version, num_wono, libelle, id_auteur, id_editeur,
                 parent_id, premier_id, date_creation, date_modification)
            VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
            RETURNING id
        """, (version_id, wono, libelle, system_user_id, system_user_id,
              parent_id, premier_id))
        contrat_id = cur.fetchone()['id']
        stats['contrats'] += 1

        if premier_id is None:
            cur.execute("UPDATE contrat SET premier_id=%s WHERE id=%s", (contrat_id, contrat_id))
            premier_id = contrat_id
        else:
            stats['liens'] += 1

        imported[(wono, libelle)] = {'id': contrat_id, 'premier_id': premier_id, 'lignes': {}}

        for famille, rows in familles_rows.items():
            id_famille = db_data['familles'].get(famille)
            if id_famille is None:
                log.warning("Famille '%s' inconnue, contrat_famille ignoré", famille)
                continue

            first = rows[0]
            id_pays = resolve_pays_id(first['nom_pays'], db_data, mappings)

            cur.execute("""
                INSERT INTO contrat_famille
                    (id_contrat, id_famille, t0_date, prise_com, commentaire_com, id_pays)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id_contrat, id_famille) DO NOTHING
            """, (contrat_id, id_famille,
                  first['t0_date'], first['prise_com'], first['commentaire_com'], id_pays))

            # Regrouper les échéances par (activité, produit, scénario) BRUTS du XLSX
            # (le regroupement final se fait sur l'identité RÉSOLUE, voir Temps 1).
            lignes_raw = defaultdict(list)
            for row in rows:
                lignes_raw[(
                    row['nom_activite'], row['nom_produit'], row['nom_scenario'],
                )].append(row)

            # =====================================================================
            # APPARIEMENT DES LIGNES À LA VERSION PRÉCÉDENTE — en 3 temps.
            #
            # Identité : contrat = (num_wono, libelle) [plus haut] ;
            #            ligne   = (nom_activite, nom_produit, nom_scenario) RÉSOLUS
            #                      (= noms EN BASE après mapping — jamais les bruts XLSX).
            #
            # Règle scénario (métier) :
            #   1) (activité, produit, scénario) identiques → lien (exact).
            #   2) sinon, si (activité, produit) est SEUL des deux côtés → même ligne,
            #      scénario changé → lien.
            #   3) sinon (plusieurs scénarios pour ce (activité, produit)) → on
            #      discrimine par la RÉFÉRENCE : même reference → lien, sinon nouvelle.
            # Appariement 1-à-1 : une ligne précédente n'est liée qu'une seule fois.
            # =====================================================================

            # --- Temps 1 : résolution (identité DB + infos) ---
            resolved = []
            for (r_act, r_prod, r_scen), ech_rows in lignes_raw.items():
                id_produit, id_activite = resolve_produit(r_prod, famille, r_act, db_data, mappings)
                if id_produit is None:
                    log.warning("Produit '%s' (famille: %s) non résolu — ligne ignorée", r_prod, famille)
                    continue
                id_scenario = resolve_scenario_id(r_scen, db_data, mappings)
                if id_scenario is None:
                    log.warning("Scénario '%s' non résolu — ligne ignorée", r_scen)
                    continue
                fr = ech_rows[0]
                if fr['quantite_totale'] == 0 and fr['non_retenue_totale'] == 0:
                    log.warning("Ligne %s/%s/%s : les deux totaux = 0 (aurait dû être bloqué en Phase 1)",
                                r_act, r_prod, r_scen)
                a_db = mappings['activite'].get(r_act, r_act)
                p_db = mappings['produit'].get(f"{r_prod}|{famille}|{r_act}", r_prod)
                s_db = mappings['scenario'].get(r_scen, r_scen)
                resolved.append({
                    'lk': (a_db, p_db, s_db), 'ap': (a_db, p_db),
                    'raw': (r_act, r_prod, r_scen),
                    'raw_wono': fr['num_wono'], 'raw_libelle': fr['libelle'],
                    'id_produit': id_produit, 'id_activite': id_activite, 'id_scenario': id_scenario,
                    'reference': fr['reference'],
                    'qty': fr['quantite_totale'], 'nr': fr['non_retenue_totale'],
                    'ech_rows': ech_rows, 'prev': None,
                })

            # --- Temps 2 : appariement à la version précédente (règle scénario) ---
            prev_lignes = prev_contrat['lignes'].get(famille, {}) if prev_contrat else {}
            current_map = {r['lk']: r for r in resolved}     # r porte 'reference'
            matches = match_lines(current_map, prev_lignes)
            for r in resolved:
                r['prev'] = matches.get(r['lk'])

            # --- Temps 2bis : surcharge par le TABLEAU MANUEL (source de vérité) ---
            # Un lien saisi à la main prime toujours sur match_lines. L'appariement
            # reste 1-à-1 : si la ligne précédente visée avait été prise par un
            # appariement automatique, celui-ci est libéré au profit du lien manuel.
            for r in resolved:
                cm = mappings['chaine'].get(chain_key(
                    version_name, r['raw_wono'], r['raw_libelle'], famille, *r['raw']))
                if not cm:
                    continue
                # La ligne précédente peut vivre dans une autre famille si la
                # lignée en a changé : on cherche là où le mapping l'a trouvée.
                fam_prec = cm.get('prev_famille') or famille
                prev_lk = resolve_identity(
                    cm['prev_activite'], cm['prev_produit'], cm['prev_scenario'],
                    fam_prec, mappings,
                )
                pl = (prev_lignes if fam_prec == famille
                      else (prev_contrat['lignes'].get(fam_prec, {}) if prev_contrat else {})
                      ).get(prev_lk)
                if pl is None:
                    log.warning(
                        "Mapping manuel : ligne %s introuvable dans '%s' pour %s "
                        "— lien ignoré (nouvelle lignée)",
                        prev_lk, cm.get('prev_version'), r['lk'],
                    )
                    continue
                for other in resolved:
                    if other is not r and other['prev'] is pl:
                        other['prev'] = None
                r['prev'] = pl
                stats['liens_manuels'] += 1

            # --- Temps 3 : insertion (lignes + échéances) avec les liens décidés ---
            for r in resolved:
                prev_ligne = r['prev']
                lk = r['lk']
                l_parent  = prev_ligne['id']         if prev_ligne else None
                l_premier = prev_ligne['premier_id'] if prev_ligne else None

                cur.execute("""
                    INSERT INTO contrat_ligne
                        (id_contrat, id_produit, id_activite, id_scenario,
                         reference, quantite_totale, non_retenue_totale,
                         parent_id, premier_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (contrat_id, r['id_produit'], r['id_activite'], r['id_scenario'],
                      r['reference'], r['qty'], r['nr'], l_parent, l_premier))
                ligne_id = cur.fetchone()['id']
                stats['lignes'] += 1

                if l_premier is None:
                    cur.execute("UPDATE contrat_ligne SET premier_id=%s WHERE id=%s",
                                (ligne_id, ligne_id))
                    l_premier = ligne_id
                else:
                    stats['liens'] += 1

                imported[(wono, libelle)]['lignes'].setdefault(famille, {})[lk] = {
                    'id': ligne_id, 'premier_id': l_premier,
                    'reference': r['reference'], 'echeances': {},
                }

                prev_ech = prev_ligne.get('echeances', {}) if prev_ligne else {}

                # Fusionner les échéances de MÊME date_mad sur ce couple
                # (activité, produit, scénario) : on somme quantité et non_retenue.
                # (évite aussi la violation de UNIQUE(id_contrat_ligne, date_mad))
                ech_par_date = {}
                for row in r['ech_rows']:
                    d = row['date_mad']
                    if not d:
                        continue
                    agg = ech_par_date.get(d)
                    if agg is None:
                        ech_par_date[d] = {
                            'quantite':    row['quantite'] or 0,
                            'non_retenue': row['non_retenue'] or 0,
                        }
                    else:
                        agg['quantite']    += row['quantite'] or 0
                        agg['non_retenue'] += row['non_retenue'] or 0
                        stats['echeances_fusionnees'] += 1
                        log.info(
                            "Échéances fusionnées %s/%s/%s date %s (+%s qté, +%s NR)",
                            r['id_activite'], r['id_produit'], r['id_scenario'],
                            d, row['quantite'] or 0, row['non_retenue'] or 0,
                        )

                for date_mad, agg in ech_par_date.items():
                    pe = prev_ech.get(date_mad)
                    e_parent  = pe['id']         if pe else None
                    e_premier = pe['premier_id'] if pe else None
                    cur.execute("""
                        INSERT INTO echeance
                            (id_contrat_ligne, date_mad, quantite, non_retenue,
                             parent_id, premier_id)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        RETURNING id
                    """, (ligne_id, date_mad, agg['quantite'], agg['non_retenue'],
                          e_parent, e_premier))
                    ech_id = cur.fetchone()['id']
                    stats['echeances'] += 1
                    if e_premier is None:
                        cur.execute("UPDATE echeance SET premier_id=%s WHERE id=%s",
                                    (ech_id, ech_id))
                        e_premier = ech_id
                    imported[(wono, libelle)]['lignes'][famille][lk]['echeances'][date_mad] = {
                        'id': ech_id, 'premier_id': e_premier,
                    }

    cur.close()
    log.info(
        "Version '%s': %d contrats, %d lignes, %d échéances (%d fusionnées), "
        "%d liens établis (dont %d via le tableau manuel)",
        version_name, stats['contrats'], stats['lignes'], stats['echeances'],
        stats['echeances_fusionnees'], stats['liens'], stats['liens_manuels'],
    )
    return imported, stats

# ---------------------------------------------------------------------------
# Création des produits confirmés
# ---------------------------------------------------------------------------

def ensure_schema(conn):
    """Assouplit `CHECK (quantite_totale > 0)` → `>= 0` sur contrat_ligne.

    Les anciennes versions comportent des lignes à `quantite_totale = 0` avec
    `non_retenue_totale > 0` : légitime, mais refusé par la contrainte d'origine
    (`base.sql`). Les deux totaux à 0 restent bloqués en Phase 1 ([d2]).

    Idempotent : ne touche rien si la contrainte est déjà au bon format (cas d'une
    base créée par `newbase.sql`). Prend un lock bref sur `contrat_ligne` → lancer
    en heure creuse (cf. README_procedure_import.md §7).
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT conname, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'contrat_ligne'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%%quantite_totale%%'
          AND pg_get_constraintdef(oid) NOT ILIKE '%%non_retenue_totale%%'
    """)
    contraintes = cur.fetchall()

    deja_ok = False
    for conname, condef in contraintes:
        if '>= 0' in condef.replace('> = 0', '>= 0'):
            deja_ok = True
            continue
        log.info("ensure_schema : suppression de la contrainte '%s' (%s)", conname, condef)
        cur.execute(f'ALTER TABLE contrat_ligne DROP CONSTRAINT "{conname}"')

    if deja_ok:
        log.info("ensure_schema : contrainte quantite_totale déjà en '>= 0', rien à faire.")
    else:
        cur.execute("""
            ALTER TABLE contrat_ligne
            ADD CONSTRAINT chk_contrat_ligne_quantite_totale
            CHECK (quantite_totale >= 0)
        """)
        log.info("ensure_schema : contrainte 'chk_contrat_ligne_quantite_totale' "
                 "(quantite_totale >= 0) posée.")
        REPORT.add('Phase 2 — Préparation', 'success',
                   "Contrainte quantite_totale assouplie (>= 0)",
                   "0 autorisé si non_retenue_totale > 0 ; les deux à 0 restent bloqués en [d2].")

    conn.commit()
    cur.close()


def create_pending_products(conn, db_data, mappings):
    """Crée les produits confirmés en Phase 1 (mappings['produit_create']) et met
    à jour l'index en mémoire pour que resolve_produit les trouve. Transaction propre."""
    pending = mappings.get('produit_create', {})
    if not pending:
        return
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    created = 0
    for info in pending.values():
        np, fid, aid = info['nom_produit'], info['id_famille'], info['id_activite']
        cur.execute(
            "SELECT id FROM produit WHERE nom_produit=%s AND id_famille=%s AND id_activite=%s",
            (np, fid, aid),
        )
        row = cur.fetchone()
        if row:
            pid = row['id']
        else:
            cur.execute(
                "INSERT INTO produit (nom_produit, statut, id_famille, id_activite) "
                "VALUES (%s, true, %s, %s) RETURNING id", (np, fid, aid),
            )
            pid = cur.fetchone()['id']
            created += 1
            REPORT.add('Phase 2 — Produits créés', 'success',
                       f"Produit '{np}' créé (id={pid})",
                       f"Famille {info.get('famille', '?')}, activité {info.get('nom_activite', '?')}")
        # Index mémoire (éviter le doublon si déjà présent)
        existing = db_data['produits'].get((np, fid), [])
        if not any(p['id_activite'] == aid for p in existing):
            db_data['produits'][(np, fid)].append({
                'id': pid, 'nom_produit': np, 'id_famille': fid, 'id_activite': aid,
                'nom_famille': info.get('famille'), 'nom_activite': info.get('nom_activite'),
            })
    conn.commit()
    cur.close()
    if created:
        print(f"  {created} produit(s) créé(s).")

# ---------------------------------------------------------------------------
# Rattachement "En cours"
# ---------------------------------------------------------------------------

def rattachement_successeur(conn, db_data, all_imported):
    """Rattache la version SUCCESSEUR à la dernière version importée.

    Successeur = la version chronologiquement juste après la dernière version
    importée : une version figée plus récente (ex. V62) si elle existe, sinon
    l'En cours. On ne touche donc l'En cours QUE s'il est le successeur.

    - parent_id : le successeur (plus récent) devient enfant de la dernière
      version importée.
    - premier_id : on ré-ancre le groupe importé sur le premier_id du successeur
      (= la racine partagée), indispensable pour l'appariement des comparaisons.
      Le premier_id du successeur lui-même n'est jamais modifié.
    Transaction séparée, après confirmation.
    """
    if not all_imported:
        return
    newest = max(all_imported, key=version_sort_key)
    succ   = determine_successor(db_data, newest)

    # Invariant : on ne touche JAMAIS l'En cours. Une version figée plus récente
    # (ex. V62) doit toujours exister comme successeur. Si ce n'est pas le cas,
    # on refuse de rattacher plutôt que de modifier l'En cours.
    if succ == 'En cours':
        msg = (f"Aucune version figée plus récente que '{newest}' : le successeur "
               f"serait l'En cours. Rattachement ANNULÉ (l'En cours n'est pas touché). "
               f"Fige la version cible (ex. V62) puis relance.")
        print(f"\n[ATTENTION] {msg}")
        log.warning(msg)
        REPORT.add('Phase 2 — Rattachement', 'warning',
                   "Rattachement ignoré : pas de version figée successeur",
                   "L'En cours n'a pas été modifié. Figer la version cible puis relancer.")
        return

    succ_data = load_imported_version_from_db(conn, succ)
    if not succ_data:
        log.info("Successeur '%s' sans lignes — rien à rattacher.", succ)
        return

    print(f"\nRattachement de '{newest}' → successeur « {succ} » (version figée)")
    if not confirm("Procéder au rattachement ? (oui/non)"):
        print("Rattachement annulé.")
        return

    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    versions_sorted = sorted(all_imported.keys(), key=version_sort_key)
    rattached = 0

    # On itère les contrats du SUCCESSEUR : chacun (plus récent) devient enfant du
    # contrat importé le plus récent de même (wono, libelle). Les lignes sont
    # appariées par la MÊME règle scénario que le chaînage entre versions (match_lines).
    for (wono, libelle), sc in succ_data.items():
        # contrat importé le + récent pour ce (wono, libelle)
        imp_contrat = None
        for ver in reversed(versions_sorted):
            if (wono, libelle) in all_imported[ver]:
                imp_contrat = all_imported[ver][(wono, libelle)]
                break
        if imp_contrat is None:
            continue

        # --- contrat : parent + ré-ancrage premier (une fois) ---
        if sc.get('premier_id') is not None:
            cur.execute("UPDATE contrat SET parent_id=%s WHERE id=%s",
                        (imp_contrat['id'], sc['id']))
            if imp_contrat['premier_id'] != sc['premier_id']:
                cur.execute("UPDATE contrat SET premier_id=%s WHERE premier_id=%s",
                            (sc['premier_id'], imp_contrat['premier_id']))

        # --- lignes : successeur (current) → import (prev) via la règle scénario ---
        # Appariement famille par famille (même raison que dans l'index ci-dessus).
        paires = []
        for fam, sc_lignes in sc['lignes'].items():
            matches = match_lines(sc_lignes, imp_contrat['lignes'].get(fam, {}))
            paires.extend((ident, sline, matches.get(ident))
                          for ident, sline in sc_lignes.items())

        for ident, sline, imp_line in paires:
            if not imp_line:
                continue

            # contrat_ligne : successeur (enfant) → import (parent) + ré-ancrage
            cur.execute("UPDATE contrat_ligne SET parent_id=%s WHERE id=%s",
                        (imp_line['id'], sline['id']))
            if sline['premier_id'] is not None and imp_line['premier_id'] != sline['premier_id']:
                cur.execute("UPDATE contrat_ligne SET premier_id=%s WHERE premier_id=%s",
                            (sline['premier_id'], imp_line['premier_id']))

            # echeance : matcher sur date_mad
            for date_mad, s_ech in sline['echeances'].items():
                imp_ech = imp_line['echeances'].get(date_mad)
                if not imp_ech:
                    continue
                cur.execute("UPDATE echeance SET parent_id=%s WHERE id=%s",
                            (imp_ech['id'], s_ech['id']))
                if s_ech['premier_id'] is not None and imp_ech['premier_id'] != s_ech['premier_id']:
                    cur.execute("UPDATE echeance SET premier_id=%s WHERE premier_id=%s",
                                (s_ech['premier_id'], imp_ech['premier_id']))
            rattached += 1

    conn.commit()
    print(f"{rattached} ligne(s) rattachée(s) à « {succ} ».")
    REPORT.kpi('Lignes rattachées', rattached, 'success')
    REPORT.add('Phase 2 — Rattachement', 'success',
               f"{rattached} ligne(s) rattachée(s) : « {succ} » → '{newest}'",
               "parent_id du successeur (version figée) mis à jour ; "
               "premier_id des imports ré-ancré. En cours intact.")
    cur.close()

# ---------------------------------------------------------------------------
# PHASE 1 orchestration
# ---------------------------------------------------------------------------

def phase1(xlsx_data, db_data, mappings):
    print("\n" + "=" * 60)
    print("PHASE 1 — ANALYSE (dry-run)")
    print("=" * 60)

    issues, proposals = [], []

    print("\n[a] num_wono longueur...")
    check_a_wono_length(xlsx_data, db_data, mappings, issues, proposals)

    # [b] Ruptures de chaîne (matching flou) : DÉBRANCHÉ — le code de
    # check_b_chaine_ruptures() est conservé comme repli éventuel, mais les liens
    # viennent du tableau manuel [g] puis de la correspondance exacte d'identité.

    print("[g] Tableau de mapping manuel inter-versions...")
    lineages, ordre = load_manual_mapping()
    liens_manuels = check_g_mapping_manuel(
        xlsx_data, lineages, ordre, mappings, issues, proposals)
    if MAPPING_XLSX.exists():
        # Sauver même sans lignée : la purge des liens dérivés doit être persistée.
        save_mappings(mappings)   # les liens sans ambiguïté sont déjà acquis
        print(f"  {len(lineages)} lignée(s) lue(s), {liens_manuels} lien(s) direct(s) établi(s).")

    print("[c] Pays et scénarios inconnus...")
    check_c_pays_scenarios(xlsx_data, db_data, mappings, issues, proposals)

    print("[d] Ambiguïtés produit/activité...")
    check_d_produit_activite(xlsx_data, db_data, mappings, issues, proposals)

    print("[d2] Totaux nuls (quantite_totale ET non_retenue_totale = 0)...")
    check_totaux_nuls(xlsx_data, issues)

    print("[e] Versions déjà importées...")
    check_e_version_existe(xlsx_data, db_data, issues)

    print("[f] Rattachement (successeur figé)...")
    encours_targets = check_f_en_cours(xlsx_data, db_data, mappings, issues, proposals)

    # Décomptes
    blocking    = [i for i in issues if i['type'] == 'blocking']
    warnings    = [i for i in issues if i['type'] == 'warning']
    duplicates  = [i for i in issues if i['type'] == 'duplicate_version']
    prop_maps   = [p for p in proposals if p['type'] not in ('encours', 'info')]
    encours_p   = [p for p in proposals if p['type'] == 'encours']

    # Rapport
    print("\n" + "=" * 60)
    print("=== RAPPORT D'ANALYSE ===")
    print(f"Fichiers détectés            : {', '.join(sorted(xlsx_data.keys(), key=version_sort_key))}")
    print(f"Anomalies bloquantes         : {len(blocking)}")
    print(f"Avertissements               : {len(warnings)}")
    print(f"Versions déjà en base        : {len(duplicates)}")
    print(f"Mappings proposés (à valider): {len(prop_maps)}")
    print(f"Lignes En cours concernées   : {len(encours_targets)}")
    print("=" * 60)

    # --- Alimentation du rapport HTML (toujours à jour sur disque) ---
    REPORT.kpi('Fichiers', len(xlsx_data))
    if lineages:
        REPORT.kpi('Lignées mappées', len(lineages), 'info')
        REPORT.kpi('Liens manuels', liens_manuels, 'success')
        REPORT.add('Phase 1 — Mapping manuel', 'info',
                   f"{len(lineages)} lignée(s) lue(s) dans {MAPPING_XLSX.name}",
                   f"Versions couvertes : {' → '.join(ordre)} · "
                   f"{liens_manuels} lien(s) sans ambiguïté établi(s) directement")
    REPORT.kpi('Bloquantes', len(blocking), 'blocking' if blocking else 'success')
    REPORT.kpi('Avertissements', len(warnings), 'warning' if warnings else 'success')
    REPORT.kpi('Déjà en base', len(duplicates))
    REPORT.kpi('Mappings à valider', len(prop_maps))
    REPORT.kpi('Lignes En cours', len(encours_targets))
    for issue in issues:
        lvl = 'blocking' if issue['type'] == 'blocking' else 'warning'
        REPORT.add('Phase 1 — Anomalies', lvl, issue['message'])
    for p in proposals:
        if p['type'] == 'encours':
            REPORT.add('Phase 1 — Lignes En cours', 'info', p['message'])
        elif p['type'] == 'info':
            REPORT.add('Phase 1 — Analyse', 'info', p['message'])
        else:
            REPORT.add('Phase 1 — Propositions détectées', 'info', p['message'])

    if issues:
        print("\nDétail anomalies :")
        for issue in issues:
            tag = "[BLOQUANT]" if issue['type'] == 'blocking' else "[WARN]    "
            print(f"  {tag} {issue['message']}")

    if blocking:
        print(f"\n{len(blocking)} anomalie(s) bloquante(s) détectée(s).")
        print("Corriger les données sources avant de relancer.")
        REPORT.set_status('Bloqué')
        REPORT.add('Phase 1 — Anomalies', 'blocking',
                   f"{len(blocking)} anomalie(s) bloquante(s) — import interrompu",
                   "Corriger les données sources puis relancer.")
        return False, mappings, encours_targets

    # Gérer les versions dupliquées
    skip_versions = set()
    for issue in duplicates:
        print(f"\n{issue['message']}")
        if AUTO_YES:
            action = 's'   # non-interactif : on skippe les versions déjà en base
            print("  (s)kip auto")
        else:
            action = input("  (s)kipper / (a)bandonner ? [s/a] : ").strip().lower()
        if action == 'a':
            print("Import abandonné.")
            sys.exit(0)
        skip_versions.add(issue['version'])

    for v in skip_versions:
        del xlsx_data[v]

    if not xlsx_data:
        print("Aucune version à importer.")
        return False, mappings, encours_targets

    # En mode non-interactif (--yes), tous les mappings doivent déjà être en cache.
    # S'il reste des propositions à confirmer, on ne peut pas demander → on abandonne.
    if AUTO_YES and prop_maps:
        print(f"[ERREUR] {len(prop_maps)} mapping(s) non résolus en mode non-interactif.")
        print("Relance en interactif (sans --yes) pour les valider, puis réessaie.")
        REPORT.set_status('Interrompu')
        REPORT.add('Phase 1 — Confirmations', 'blocking',
                   f"{len(prop_maps)} mapping(s) manquants en mode --yes",
                   "Valider d'abord en interactif (staging) ; le cache sera réutilisé.")
        return False, mappings, encours_targets

    # Confirmation interactive des mappings (sauvegarde au fil de l'eau dans
    # confirm_proposals ; on peut quitter par 'q'/Ctrl-C et reprendre plus tard).
    if prop_maps:
        print(f"\n{len(prop_maps)} mapping(s) à confirmer (q pour quitter en sauvegardant) :")
        try:
            confirm_proposals(prop_maps, mappings)
        except KeyboardInterrupt:
            save_mappings(mappings)
            print("\n>>> Arrêt demandé — progression sauvegardée. "
                  "Relance le script pour reprendre là où tu t'es arrêté.")
            REPORT.set_status('Annulé')
            REPORT.add('Phase 1 — Confirmations', 'warning',
                       "Confirmations interrompues par l'utilisateur (q/Ctrl-C)",
                       "Les mappings déjà validés sont sauvegardés ; relancer pour continuer.")
            return False, mappings, encours_targets
        except Exception:
            import traceback
            save_mappings(mappings)
            print(">>> Erreur pendant les confirmations (progression sauvegardée) :")
            traceback.print_exc()
            REPORT.set_status('Interrompu')
            return False, mappings, encours_targets
        save_mappings(mappings)
        REPORT.add('Phase 1 — Confirmations', 'success',
                   f"{len(prop_maps)} proposition(s) traitée(s) par l'utilisateur",
                   "Mappings sauvegardés dans mappings_valides.json")
    elif proposals:
        # Il peut y avoir des infos/encours seulement
        for p in [x for x in proposals if x['type'] in ('info', 'encours')]:
            print(f"  {p['message']}")

    if encours_p:
        print(f"\n{len(encours_p)} ligne(s) 'En cours' seront rattachées après import :")
        for ep in encours_p:
            print(f"  {ep['message']}")

    return True, mappings, encours_targets

# ---------------------------------------------------------------------------
# PHASE 2 orchestration
# ---------------------------------------------------------------------------

def phase2(xlsx_data, db_data, mappings, encours_targets):
    print("\n" + "=" * 60)
    print("PHASE 2 — IMPORT")
    print("=" * 60)

    system_user_id = db_data['system_user_id']
    if not system_user_id:
        print("[ERREUR] Aucun utilisateur trouvé en base.")
        sys.exit(1)

    versions = sorted(xlsx_data.keys(), key=version_sort_key)
    print(f"Versions à importer : {', '.join(versions)}")
    REPORT.set_status('En cours')
    REPORT.add('Phase 2 — Import', 'info', f"Démarrage de l'import de {len(versions)} version(s)",
               ', '.join(versions))

    conn = connect_db()
    all_imported = {}

    # Pré-requis schéma + création des produits confirmés (transactions propres)
    try:
        ensure_schema(conn)
        create_pending_products(conn, db_data, mappings)
    except Exception as exc:
        conn.rollback()
        print(f"[ERREUR] Préparation (schéma / produits) : {exc}")
        REPORT.add('Phase 2 — Préparation', 'error',
                   "Échec de la préparation (schéma / produits)", str(exc))
        REPORT.set_status('Interrompu')
        conn.close()
        sys.exit(1)

    # Chronologie globale = toutes les versions archivées (lot + déjà en base), triées.
    # L'ordre alphabético-numérique = ordre chronologique (cf. cahier des charges).
    archived_in_db = [v for v in db_data['versions'] if v != 'En cours']
    timeline = sorted(set(versions) | set(archived_in_db), key=version_sort_key)

    def predecessor_imported(version_name):
        """Structure imported{} de la version archivée juste AVANT version_name,
        qu'elle soit dans le lot déjà importé ou déjà présente en base."""
        idx = timeline.index(version_name)
        for j in range(idx - 1, -1, -1):
            pv = timeline[j]
            if pv in all_imported:
                return all_imported[pv]
            if pv in archived_in_db:
                log.info("Prédécesseur '%s' chargé depuis la base pour chaîner '%s'",
                         pv, version_name)
                all_imported[pv] = load_imported_version_from_db(conn, pv)
                return all_imported[pv]
        return {}

    try:
        for version_name in versions:
            print(f"\nImport de '{version_name}'...")
            try:
                conn.autocommit = False
                prev_imported = predecessor_imported(version_name)
                imported, stats = import_single_version(
                    conn, version_name, xlsx_data[version_name],
                    db_data, mappings, prev_imported, system_user_id,
                )
                conn.commit()
                all_imported[version_name] = imported
                print(f"  [OK] '{version_name}' importé.")
                REPORT.add(
                    'Phase 2 — Import', 'success', f"Version '{version_name}' importée",
                    f"{stats['contrats']} contrat(s), {stats['lignes']} ligne(s), "
                    f"{stats['echeances']} échéance(s), {stats['liens']} lien(s) de version "
                    f"établi(s) dont {stats['liens_manuels']} via le tableau manuel",
                )
            except Exception as exc:
                conn.rollback()
                print(f"  [ERREUR] '{version_name}' : {exc}")
                log.exception("Erreur import version '%s'", version_name)
                REPORT.add('Phase 2 — Import', 'error',
                           f"Version '{version_name}' en échec — rollback", str(exc))
                if not confirm("  Continuer avec la prochaine version ? (oui/non)"):
                    print("Import interrompu.")
                    REPORT.set_status('Interrompu')
                    conn.close()
                    sys.exit(1)

        # Rattachement du successeur (version figée plus récente, sinon En cours)
        if all_imported:
            rattachement_successeur(conn, db_data, all_imported)

    finally:
        conn.close()

    imported_names = [v for v in versions if v in all_imported]
    print(f"\n=== IMPORT TERMINÉ : {len(imported_names)} version(s) importée(s) ===")
    REPORT.kpi('Versions importées', len(imported_names), 'success')
    REPORT.set_status('Terminé')
    REPORT.add('Phase 2 — Import', 'success',
               f"Import terminé : {len(imported_names)} version(s)",
               f"Rapport disponible : {REPORT_FILE}")

# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def main():
    global AUTO_YES
    AUTO_YES = ('--yes' in sys.argv[1:]) or ('-y' in sys.argv[1:])

    print("=" * 60)
    print("IMPORT VERSIONS — RepliqueB" + (" [NON-INTERACTIF --yes]" if AUTO_YES else ""))
    print("=" * 60)

    IMPORT_DIR.mkdir(exist_ok=True)
    REPORT.reset()
    REPORT.add('Initialisation', 'info',
               "Démarrage du script d'import" + (" (mode --yes)" if AUTO_YES else ""))

    mappings = load_mappings()

    print("\nConnexion à la base de données...")
    try:
        conn = connect_db()
        db_data = load_db_data(conn)
        conn.close()
        print(
            f"  OK — {len(db_data['familles'])} famille(s), "
            f"{len(db_data['pays'])} pays, "
            f"{len(db_data['scenarios'])} scénarios, "
            f"{len(db_data['versions'])} version(s) en base."
        )
        REPORT.set_meta(**{
            'Base de données': 'connectée',
            'Référentiels': f"{len(db_data['familles'])} familles · "
                            f"{len(db_data['pays'])} pays · "
                            f"{len(db_data['scenarios'])} scénarios · "
                            f"{len(db_data['activites'])} activités",
            'Lignes En cours': len(db_data['encours']['lignes']),
        })
    except Exception as exc:
        print(f"[ERREUR] Connexion DB : {exc}")
        REPORT.add('Initialisation', 'error', "Connexion à la base impossible", str(exc))
        REPORT.set_status('Interrompu')
        sys.exit(1)

    print(f"\nChargement des fichiers depuis {IMPORT_DIR}...")
    xlsx_data = load_xlsx_files()
    print(f"  {len(xlsx_data)} fichier(s) : {', '.join(sorted(xlsx_data.keys(), key=version_sort_key))}")
    REPORT.set_meta(**{'Fichiers détectés': ', '.join(sorted(xlsx_data.keys(), key=version_sort_key))})
    REPORT.add('Initialisation', 'info',
               f"{len(xlsx_data)} fichier(s) XLSX détecté(s)", ', '.join(sorted(xlsx_data.keys(), key=version_sort_key)))

    ok, mappings, encours_targets = phase1(xlsx_data, db_data, mappings)
    if not ok:
        sys.exit(1)

    # Les versions skippées (déjà en base) seront rechargées au besoin par phase2
    # comme prédécesseurs chronologiques (via db_data['versions']).

    print()
    if not confirm("Continuer vers la Phase 2 (import effectif) ? (oui/non)"):
        print("Import annulé. Mappings sauvegardés.")
        REPORT.set_status('Annulé')
        REPORT.add('Phase 1 — Analyse', 'warning', "Import annulé par l'utilisateur",
                   "La Phase 2 n'a pas été lancée. Les mappings validés sont conservés.")
        sys.exit(0)

    phase2(xlsx_data, db_data, mappings, encours_targets)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        # Filet de sécurité : un 'q'/Ctrl-C hors des confirmations (Phase 2,
        # rattachement…) sort proprement sans traceback.
        print("\n>>> Interrompu par l'utilisateur. À bientôt.")
        sys.exit(130)
