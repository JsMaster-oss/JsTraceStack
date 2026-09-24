#!/usr/bin/env python3
"""
Migration d'UN couple (entite, programme) d'une base source vers une base cible.

Les deux bases ne se voyant pas, la migration se fait en deux passes :

    # sur un poste qui voit la SOURCE
    python migrate_entity.py export --dsn "postgresql://..." --relation-id 42 --out ./dump_42

    # sur un poste qui voit la CIBLE
    python migrate_entity.py import --dsn "postgresql://..." --in ./dump_42 --dry-run
    python migrate_entity.py import --dsn "postgresql://..." --in ./dump_42 --commit

--relation-id est l'ID de la ligne Entites_Programmes, PAS l'ID de l'entite.
C'est cette ligne qui materialise le couple (entite1, programme1) : si entite1
est rattachee a plusieurs programmes, seule la relation demandee est migree.

Chaque table globale a une STRATEGIE :
  CREATE           : n'existe pas en cible, on l'insere (Programme, Entites,
                     Entites_Programmes)
  RESOLVE          : existe en cible, on retrouve son ID par cle naturelle
  RESOLVE_OR_CREATE: on resout, et on insere si absent
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# A VERIFIER AVANT UTILISATION
# ---------------------------------------------------------------------------
# 1. "SelectionErdt" reste sans DDL connu : MspCrds."selectionEntityId" n'est
#    PAS remappe, et les 12 tables SelectionErdt_* ne sont pas migrees.
# 2. Operations."ustID" et MspCrds."ustID" n'ont PAS de contrainte FK declaree
#    dans le schema. Je les traite comme des FK vers UST.id. Si ce sont des
#    identifiants metier opaques, retire-les des `fks`.
# 3. "User".password n'est jamais exporte (voir EXCLUDED_COLUMNS).
# ---------------------------------------------------------------------------

# Nom confirme par Greg. A noter : le DDL transmis ecrivait les contraintes
# "REFERENCES public.\"Entities_Programmes\"(id)" (orthographe anglaise) ;
# c'est le dump qui etait approximatif, pas la base.
ENTITY_TABLE = "Entites_Programmes"
ENTITE_TABLE = "Entites"
PROGRAMME_TABLE = "Programme"

# Colonnes logiques de Entites_Programmes. Les noms reels sont resolus a
# l'execution sans tenir compte de la casse (voir resolve_entity_fks), donc
# "EntiteId" comme "entiteId" fonctionnent.
ENTITY_FKS = {
    "entiteId": ENTITE_TABLE,
    "programmeId": PROGRAMME_TABLE,
}

# Colonnes denormalisees de Entites_Programmes, recalculees apres import.
ENTITY_DATE_COLS = {"newestTestDate": "MAX", "oldestTestDate": "MIN"}

# Colonnes jamais exportees (secrets, donnees inutiles a la migration)
EXCLUDED_COLUMNS: dict[str, tuple[str, ...]] = {
    "User": ("password",),
}

CREATE = "create"
RESOLVE = "resolve"
RESOLVE_OR_CREATE = "resolve_or_create"


@dataclass
class Global:
    """Table hors scope entite, traitee a part."""
    name: str
    strategy: str
    keycols: tuple[str, ...] = ()      # cle naturelle (resolution)
    fks: dict[str, str] = field(default_factory=dict)


# Ordre significatif : les dependances d'abord.
GLOBALS: list[Global] = [
    # Annonces absents de la cible, mais on resout d'abord par labelFR : si un
    # homonyme existe deja, on le reutilise au lieu de creer un doublon.
    Global(PROGRAMME_TABLE, RESOLVE_OR_CREATE, keycols=("labelFR",)),
    Global(ENTITE_TABLE, RESOLVE_OR_CREATE, keycols=("labelFR",)),
    Global(ENTITY_TABLE, CREATE, fks=ENTITY_FKS),
    # Referentiels partages : la base cible contient deja des donnees, donc on
    # se contente de resoudre. Passe en RESOLVE_OR_CREATE si besoin.
    Global("User", RESOLVE, keycols=("ctinfo",)),
    Global("Operateurs", RESOLVE, keycols=("info",)),
]
GLOBALS_BY_NAME = {g.name: g for g in GLOBALS}


@dataclass
class Table:
    """Table scopee sur la relation entite/programme."""
    name: str
    entity_col: str | None = None          # filtre direct
    parent: tuple[str, str] | None = None  # (table_parente, colonne_locale)
    fks: dict[str, str] = field(default_factory=dict)
    generated: tuple[str, ...] = ()        # GENERATED ALWAYS -> exclues


TABLES: list[Table] = [
    Table("Environnements", entity_col="entityProgramRelationId",
          fks={"entityProgramRelationId": ENTITY_TABLE}),

    Table("GroupeLogiciel", entity_col="entityProgramRelationId",
          fks={"entityProgramRelationId": ENTITY_TABLE}),

    Table("Logiciels", entity_col="entityProgramRelationId",
          fks={"entityProgramRelationId": ENTITY_TABLE,
               "groupeLogicielId": "GroupeLogiciel"}),

    Table("Bancs", entity_col="entityProgramRelationId",
          fks={"entityProgramRelationId": ENTITY_TABLE}),

    Table("TypeTest", entity_col="entityProgramRelationId",
          fks={"entityProgramRelationId": ENTITY_TABLE}),

    Table("groupe_ust", entity_col="entity_program_relation_id",
          fks={"entity_program_relation_id": ENTITY_TABLE}),

    Table("UST", entity_col="entityProgramRelationId",
          fks={"entityProgramRelationId": ENTITY_TABLE,
               "groupe_ust_id": "groupe_ust"}),

    Table("Sanctions", entity_col="entityProgramRelationId",
          fks={"entityProgramRelationId": ENTITY_TABLE}),

    Table("Operations", parent=("Logiciels", "logicielId"),
          fks={"logicielId": "Logiciels", "ustID": "UST"}),

    Table("Parametre", parent=("Operations", "operationID"),
          fks={"operationID": "Operations"}),

    Table("ParametreAlpha", parent=("Operations", "operationId"),
          fks={"operationId": "Operations"}, generated=("spec",)),

    Table("MspCrds", parent=("Logiciels", "logicielId"),
          fks={"logicielId": "Logiciels", "ustID": "UST"}),

    Table("MspCrds_Parametre", parent=("MspCrds", "mspCrdsId"),
          fks={"mspCrdsId": "MspCrds"}),

    Table("Tests", parent=("Operations", "operationId"),
          fks={"operationId": "Operations", "bancId": "Bancs",
               "typeTestId": "TypeTest", "mspCrdsId": "MspCrds",
               "operateurId": "Operateurs", "sanctionId": "Sanctions"},
          generated=("roundedDate",)),

    Table("Mesures", parent=("Parametre", "parametreId"),
          fks={"parametreId": "Parametre", "testId": "Tests"}),

    Table("ValeurAlpha", parent=("ParametreAlpha", "parametreAlphaId"),
          fks={"parametreAlphaId": "ParametreAlpha", "testId": "Tests"}),

    Table("User_EntityProgram", entity_col="entityProgramRelationId",
          fks={"entityProgramRelationId": ENTITY_TABLE, "userId": "User"}),
]

BATCH = 5000


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# Nom logique (utilise comme cle d'idmap) -> nom reel en base, resolu au
# demarrage. Evite d'avoir a trancher entre "Entities_Programmes" (orthographe
# du DDL fourni) et "Entites_Programmes" (orthographe francaise).
REAL_TABLE: dict[str, str] = {}


def qt(logical: str) -> str:
    """Quote le nom REEL de la table correspondant a un nom logique."""
    return q(REAL_TABLE.get(logical, logical))


def resolve_table_names(cur) -> None:
    """Retrouve le nom reel de chaque table, sans tenir compte de la casse.

    Tolere aussi la variante Entities_/Entites_ sur la table de relation.
    """
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public'"
    )
    actual = {r[0] for r in cur.fetchall()}
    by_lower = {name.lower(): name for name in actual}

    logical_names = (
        [t.name for t in TABLES]
        + [g.name for g in GLOBALS]
        + ["Tests", "Operations", "Logiciels"]
    )

    for name in logical_names:
        if name in actual:
            REAL_TABLE[name] = name
            continue
        hit = by_lower.get(name.lower())
        if hit is None and name == ENTITY_TABLE:
            # Filet de securite : variante anglaise Entities_Programmes
            hit = by_lower.get("entities_programmes")
        if hit is None:
            print(f"  !! table {name!r} introuvable en base", file=sys.stderr)
            continue
        REAL_TABLE[name] = hit
        if hit != name:
            print(f"  ~~ table {name!r} resolue en {hit!r}")

    if ENTITY_TABLE not in REAL_TABLE:
        raise SystemExit(
            f"Table de relation introuvable. Tables candidates : "
            f"{sorted(n for n in actual if 'rogramme' in n.lower())}"
        )


def columns_of(cur, table: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (REAL_TABLE.get(table, table),),
    )
    return [r[0] for r in cur.fetchall()]


def usable_columns(cur, table: str, generated: tuple[str, ...] = ()) -> list[str]:
    excluded = set(generated) | set(EXCLUDED_COLUMNS.get(table, ()))
    return [c for c in columns_of(cur, table) if c not in excluded]


def real_col(cur, table: str, logical: str) -> str | None:
    """Retrouve le nom reel d'une colonne sans tenir compte de la casse."""
    for c in columns_of(cur, table):
        if c.lower() == logical.lower():
            return c
    return None


def resolve_entity_fks(cur) -> dict[str, str]:
    """Remplace les noms logiques d'ENTITY_FKS par les noms reels en base.

    Evite d'avoir a deviner entre "EntiteId" et "entiteId".
    """
    resolved: dict[str, str] = {}
    for logical, target in ENTITY_FKS.items():
        actual = real_col(cur, ENTITY_TABLE, logical)
        if actual is None:
            raise SystemExit(
                f'Colonne "{logical}" introuvable dans {ENTITY_TABLE}. '
                f"Colonnes presentes : {columns_of(cur, ENTITY_TABLE)}"
            )
        resolved[actual] = target
    GLOBALS_BY_NAME[ENTITY_TABLE].fks = resolved
    return resolved


# ---------------------------------------------------------------------------
# EXPORT
# ---------------------------------------------------------------------------

def do_export(dsn: str, relation_id: int, outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)
    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True)
    manifest: dict = {"relation_id": relation_id, "tables": {}, "globals": {}}

    with conn.cursor() as cur:
        # 1. La chaine Programme / Entite / relation.
        resolve_table_names(cur)
        entity_fks = resolve_entity_fks(cur)
        export_header(cur, relation_id, outdir, manifest, entity_fks)

        # 2. Les tables scopees.
        kept: dict[str, set] = {}
        referenced: dict[str, set] = {g.name: set() for g in GLOBALS}

        for t in TABLES:
            cols = usable_columns(cur, t.name, t.generated)
            if not cols:
                print(f"  !! {t.name}: introuvable, ignoree", file=sys.stderr)
                continue

            sel = ", ".join(q(c) for c in cols)
            if t.entity_col:
                sql = f"SELECT {sel} FROM public.{qt(t.name)} WHERE {q(t.entity_col)} = %s"
                params: tuple = (relation_id,)
            else:
                ptable, pcol = t.parent
                pids = kept.get(ptable, set())
                if not pids:
                    kept[t.name] = set()
                    manifest["tables"][t.name] = {"columns": cols, "count": 0}
                    open(os.path.join(outdir, f"{t.name}.jsonl"), "w").close()
                    print(f"  -- {t.name}: parent {ptable} vide, 0 ligne")
                    continue
                sql = f"SELECT {sel} FROM public.{qt(t.name)} WHERE {q(pcol)} = ANY(%s)"
                params = (list(pids),)

            ids: set = set()
            n = 0
            with open(os.path.join(outdir, f"{t.name}.jsonl"), "w",
                      encoding="utf-8") as fh:
                cur2 = conn.cursor(name=f"cur_{t.name}")
                cur2.itersize = BATCH
                cur2.execute(sql, params)
                for row in cur2:
                    rec = dict(zip(cols, row))
                    if "id" in rec:
                        ids.add(rec["id"])
                    # On note les ID globaux reellement references.
                    for col, target in t.fks.items():
                        if target in referenced and rec.get(col) is not None:
                            referenced[target].add(rec[col])
                    fh.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
                    n += 1
                cur2.close()

            kept[t.name] = ids
            manifest["tables"][t.name] = {"columns": cols, "count": n}
            print(f"  -> {t.name}: {n} lignes")

        # 3. Referentiels partages : seulement les lignes referencees.
        for g in GLOBALS:
            if g.strategy == CREATE:
                continue
            ids = referenced.get(g.name, set())
            if not ids:
                manifest["globals"][g.name] = {}
                continue
            cols = usable_columns(cur, g.name)
            sel = ", ".join(q(c) for c in cols)
            cur.execute(
                f"SELECT {sel} FROM public.{qt(g.name)} WHERE \"id\" = ANY(%s)",
                (list(ids),),
            )
            rows = {str(r[cols.index("id")]): dict(zip(cols, r))
                    for r in cur.fetchall()}
            manifest["globals"][g.name] = {"columns": cols, "rows": rows}
            print(f"  ~~ {g.name}: {len(rows)} lignes referencees")

    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False, default=str)
    conn.close()
    print(f"\nExport termine dans {outdir}")


def export_header(cur, relation_id: int, outdir: str, manifest: dict,
                  entity_fks: dict[str, str]) -> None:
    """Exporte Entites_Programmes + l'entite + le programme concernes."""
    cols = usable_columns(cur, ENTITY_TABLE)
    if not cols:
        raise SystemExit(f"{ENTITY_TABLE} introuvable : corrige ENTITY_TABLE.")
    sel = ", ".join(q(c) for c in cols)
    cur.execute(f'SELECT {sel} FROM public.{qt(ENTITY_TABLE)} WHERE "id" = %s',
                (relation_id,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"Aucune ligne {ENTITY_TABLE} id={relation_id}.")
    rel = dict(zip(cols, row))
    manifest["globals"][ENTITY_TABLE] = {
        "columns": cols, "rows": {str(relation_id): rel}
    }
    manifest["entity_fks"] = entity_fks
    print(f"  ~~ {ENTITY_TABLE}: relation {relation_id} -> "
          + ", ".join(f"{k}={rel.get(k)}" for k in entity_fks))

    # Les parents de la relation (entite, programme).
    for col, target in entity_fks.items():
        val = rel.get(col)
        if val is None:
            manifest["globals"][target] = {}
            continue
        tcols = usable_columns(cur, target)
        if not tcols:
            print(f"  !! {target}: introuvable, a corriger", file=sys.stderr)
            manifest["globals"][target] = {}
            continue
        tsel = ", ".join(q(c) for c in tcols)
        cur.execute(f'SELECT {tsel} FROM public.{qt(target)} WHERE "id" = %s', (val,))
        trow = cur.fetchone()
        manifest["globals"][target] = {
            "columns": tcols,
            "rows": {str(val): dict(zip(tcols, trow))} if trow else {},
        }
        print(f"  ~~ {target}: id={val} exporte")


# ---------------------------------------------------------------------------
# IMPORT
# ---------------------------------------------------------------------------

def read_jsonl(path: str):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def next_id(cur, table: str) -> int:
    cur.execute(f'SELECT COALESCE(MAX("id"), 0) FROM public.{qt(table)}')
    return cur.fetchone()[0]


def insert_row(cur, table: str, cols: list[str], rec: dict) -> None:
    collist = ", ".join(q(c) for c in cols)
    ph = ", ".join(["%s"] * len(cols))
    cur.execute(f"INSERT INTO public.{qt(table)} ({collist}) VALUES ({ph})",
                tuple(rec.get(c) for c in cols))


def process_globals(cur, manifest: dict, idmap: dict) -> None:
    """Cree ou resout chaque table globale, dans l'ordre de GLOBALS."""
    for g in GLOBALS:
        blob = manifest["globals"].get(g.name) or {}
        rows = blob.get("rows", {})
        cols = blob.get("columns", [])
        m: dict[int, int] = {}

        if not rows:
            idmap[g.name] = m
            continue

        if g.strategy in (RESOLVE, RESOLVE_OR_CREATE):
            sel = ", ".join(q(c) for c in ("id",) + g.keycols)
            cur.execute(f"SELECT {sel} FROM public.{qt(g.name)}")
            prod = {tuple(str(v) for v in r[1:]): r[0] for r in cur.fetchall()}
        else:
            prod = {}

        created = resolved = 0
        missing: list = []
        offset = next_id(cur, g.name) if g.strategy != RESOLVE else 0

        for old_id, rec in rows.items():
            old_id = int(old_id)
            key = tuple(str(rec[c]) for c in g.keycols) if g.keycols else None

            if key is not None and key in prod:
                m[old_id] = prod[key]
                resolved += 1
                continue

            if g.strategy == RESOLVE:
                missing.append((old_id, key))
                continue

            # Creation.
            offset += 1
            new = dict(rec)
            new["id"] = offset
            for col, target in g.fks.items():
                val = rec.get(col)
                if val is not None:
                    mapped = idmap.get(target, {}).get(val)
                    if mapped is None:
                        raise SystemExit(
                            f"{g.name}.{col}={val} non resolu vers {target}. "
                            f"Verifie ENTITY_FKS et l'ordre de GLOBALS."
                        )
                    new[col] = mapped
            insert_row(cur, g.name, cols, new)
            m[old_id] = offset
            created += 1

        idmap[g.name] = m
        print(f"  ~~ {g.name}: {resolved} resolus, {created} crees"
              + (f", {len(missing)} ABSENTS" if missing else ""))
        for old_id, key in missing[:5]:
            print(f"       absent : id={old_id} cle={key}")
        if missing:
            raise SystemExit(
                f"{g.name}: {len(missing)} lignes absentes en cible. "
                f"Passe cette table en RESOLVE_OR_CREATE ou cree-les a la main."
            )


def guard_relation_absent(cur, manifest: dict, entity_fks: dict[str, str]) -> None:
    """Refuse d'importer si le couple (entite, programme) existe deja en cible.

    La base cible contenant deja des donnees, un second passage creerait une
    relation en double et dupliquerait tout l'arbre sous-jacent.
    """
    blob = manifest["globals"].get(ENTITY_TABLE) or {}
    rows = blob.get("rows", {})
    if not rows:
        return
    rel = next(iter(rows.values()))

    # La ligne exportee porte les noms de colonnes de la SOURCE ; la requete
    # ci-dessous ceux de la CIBLE. On lit chacune avec les siens.
    src_fks = manifest.get("entity_fks") or entity_fks
    labels = {}
    for col, target in src_fks.items():
        src = (manifest["globals"].get(target) or {}).get("rows", {})
        old = rel.get(col)
        if old is not None and str(old) in src:
            labels[target] = src[str(old)].get("labelFR")

    if len(labels) < len(entity_fks):
        print("  !! garde-fou incomplet : labels source manquants, verifie a la main")
        return

    ecol = real_col(cur, ENTITY_TABLE, next(
        c for c, t in entity_fks.items() if t == ENTITE_TABLE))
    pcol = real_col(cur, ENTITY_TABLE, next(
        c for c, t in entity_fks.items() if t == PROGRAMME_TABLE))

    cur.execute(
        f'''
        SELECT ep."id"
        FROM public.{qt(ENTITY_TABLE)} ep
        JOIN public.{qt(ENTITE_TABLE)} e ON e."id" = ep.{q(ecol)}
        JOIN public.{qt(PROGRAMME_TABLE)} p ON p."id" = ep.{q(pcol)}
        WHERE e."labelFR" = %s AND p."labelFR" = %s
        ''',
        (labels[ENTITE_TABLE], labels[PROGRAMME_TABLE]),
    )
    existing = cur.fetchone()
    if existing:
        raise SystemExit(
            f"La relation ({labels[ENTITE_TABLE]} / {labels[PROGRAMME_TABLE]}) "
            f"existe DEJA en cible sous l'id {existing[0]}. "
            f"Import annule : ce serait un doublon, pas une migration."
        )
    print(f"  == garde-fou OK : ({labels[ENTITE_TABLE]} / "
          f"{labels[PROGRAMME_TABLE]}) absent de la cible")


def recompute_entity_dates(cur, new_rel: int) -> None:
    """Recalcule newestTestDate / oldestTestDate depuis les Tests migres.

    Ces colonnes sont denormalisees : les recopier telles quelles serait faux
    si des lignes ont ete ecartees a l'import.
    """
    sets = []
    for logical, agg in ENTITY_DATE_COLS.items():
        col = real_col(cur, ENTITY_TABLE, logical)
        if col is None:
            print(f"  !! colonne {logical} absente de {ENTITY_TABLE}, ignoree")
            continue
        sets.append((col, agg))
    if not sets:
        return

    assigns = ", ".join(
        f'{q(col)} = (SELECT {agg}(t."date") FROM public.{qt("Tests")} t '
        f'JOIN public.{qt("Operations")} o ON o."id" = t."operationId" '
        f'JOIN public.{qt("Logiciels")} l ON l."id" = o."logicielId" '
        f'WHERE l."entityProgramRelationId" = %s)'
        for col, agg in sets
    )
    params = tuple([new_rel] * len(sets)) + (new_rel,)
    cur.execute(
        f'UPDATE public.{qt(ENTITY_TABLE)} SET {assigns} WHERE "id" = %s', params)
    print(f"  ~~ dates de {ENTITY_TABLE} recalculees depuis les Tests")


def do_import(dsn: str, indir: str, commit: bool) -> None:
    with open(os.path.join(indir, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # Necessite superuser / rds_superuser. Retire si tu ne l'as pas :
        # l'ordre de TABLES est deja topologique.
        cur.execute("SET session_replication_role = replica")

        resolve_table_names(cur)
        entity_fks = resolve_entity_fks(cur)
        guard_relation_absent(cur, manifest, entity_fks)

        idmap: dict[str, dict] = {}
        process_globals(cur, manifest, idmap)

        new_rel = idmap.get(ENTITY_TABLE, {}).get(manifest["relation_id"])
        print(f"  == relation {manifest['relation_id']} -> {new_rel} en cible")

        touched = [g.name for g in GLOBALS if idmap.get(g.name)]

        for t in TABLES:
            meta = manifest["tables"].get(t.name)
            if not meta or meta["count"] == 0:
                idmap.setdefault(t.name, {})
                continue

            cols = meta["columns"]
            offset = next_id(cur, t.name)
            local: dict[int, int] = {}
            rows: list[tuple] = []
            unresolved = 0

            for rec in read_jsonl(os.path.join(indir, f"{t.name}.jsonl")):
                if "id" in rec:
                    local[rec["id"]] = rec["id"] + offset
                    rec["id"] = rec["id"] + offset

                skip = False
                for col, target in t.fks.items():
                    val = rec.get(col)
                    if val is None:
                        continue
                    mapped = idmap.get(target, {}).get(val)
                    if mapped is None:
                        unresolved += 1
                        skip = True
                        break
                    rec[col] = mapped
                if skip:
                    continue
                rows.append(tuple(rec.get(c) for c in cols))

            idmap[t.name] = local
            if unresolved:
                print(f"  !! {t.name}: {unresolved} lignes ecartees (FK non resolue)")

            collist = ", ".join(q(c) for c in cols)
            sql = f"INSERT INTO public.{qt(t.name)} ({collist}) VALUES %s"
            for i in range(0, len(rows), BATCH):
                psycopg2.extras.execute_values(cur, sql, rows[i:i + BATCH],
                                               page_size=BATCH)
            touched.append(t.name)
            print(f"  -> {t.name}: {len(rows)} lignes inserees (offset={offset})")

        cur.execute("SET session_replication_role = DEFAULT")

        if new_rel is not None:
            recompute_entity_dates(cur, new_rel)

        for name in touched:
            cur.execute("SELECT pg_get_serial_sequence(%s, 'id')",
                        (f'public.{qt(name)}',))
            seq = cur.fetchone()[0]
            if seq:
                cur.execute(
                    f'SELECT setval(%s, COALESCE((SELECT MAX("id") '
                    f'FROM public.{qt(name)}), 1))', (seq,))
        print("  ~~ sequences recalees")

        if commit:
            conn.commit()
            print("\nCOMMIT effectue.")
        else:
            conn.rollback()
            print("\nDRY-RUN : ROLLBACK, rien n'a ete ecrit.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export")
    e.add_argument("--dsn", required=True)
    e.add_argument("--relation-id", type=int, required=True,
                   help="ID de la ligne Entites_Programmes (couple entite/programme)")
    e.add_argument("--out", required=True)

    i = sub.add_parser("import")
    i.add_argument("--dsn", required=True)
    i.add_argument("--in", dest="indir", required=True)
    g = i.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True)
    g.add_argument("--commit", action="store_true")

    a = p.parse_args()
    if a.cmd == "export":
        do_export(a.dsn, a.relation_id, a.out)
    else:
        do_import(a.dsn, a.indir, a.commit)


if __name__ == "__main__":
    main()
