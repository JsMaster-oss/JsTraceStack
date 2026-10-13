-- =====================================================================
-- Migration 001 — alignement de la base d'intégration sur la base de test
--
-- Contenu :
--   1. nouvelle table public.sector (+ séquence, PK)
--   2. public."user".id_sector      (nullable, FK -> sector)
--   3. public."user".state          boolean -> character varying(20)
--   4. public."user".role (varchar) -> public."user".id_role (FK -> role)
--
-- La suppression de l'ancienne colonne "user".role est volontairement
-- reportée dans la migration 002, à jouer après déploiement du code.
--
-- Fichier encodé en UTF-8 (libellés accentués).
-- Exécution :  psql -d <base> -v ON_ERROR_STOP=1 -f migrations/001_sector_role_state.sql
--
-- Le script est entièrement transactionnel : au moindre contrôle en
-- échec, tout est annulé et la base reste dans son état initial.
-- =====================================================================

\set ON_ERROR_STOP on
\encoding UTF8

BEGIN;

SET lock_timeout = '5s';


-- ---------------------------------------------------------------------
-- 0. Garde-fous : refuser d'avancer si la base n'est pas dans l'état
--    attendu (migration déjà passée, ou schéma différent).
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class c
               JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relname = 'sector') THEN
        RAISE EXCEPTION 'La table public.sector existe déjà — migration 001 probablement déjà appliquée.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'user'
                     AND column_name = 'role') THEN
        RAISE EXCEPTION 'La colonne public."user".role est absente — schéma de départ inattendu.';
    END IF;

    IF (SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'user'
          AND column_name = 'state') <> 'boolean' THEN
        RAISE EXCEPTION 'public."user".state n''est pas un boolean — schéma de départ inattendu.';
    END IF;
END
$$;


-- ---------------------------------------------------------------------
-- 1. Table sector
-- ---------------------------------------------------------------------
CREATE TABLE public.sector (
    id integer NOT NULL,
    sector character varying(100) NOT NULL
);

CREATE SEQUENCE public.sector_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.sector_id_seq OWNED BY public.sector.id;

ALTER TABLE ONLY public.sector
    ALTER COLUMN id SET DEFAULT nextval('public.sector_id_seq'::regclass);

ALTER TABLE ONLY public.sector
    ADD CONSTRAINT sector_pkey PRIMARY KEY (id);

-- Les secteurs sont créés par l'application. Pour pré-remplir
-- l'intégration, ajouter ici les INSERT puis, obligatoirement :
--   SELECT setval('public.sector_id_seq', (SELECT max(id) FROM public.sector));


-- ---------------------------------------------------------------------
-- 2. "user".id_sector
--
--    NOTE : la base de test déclare cette FK en ON DELETE CASCADE, ce qui
--    supprimerait les utilisateurs d'un secteur supprimé. Choix écarté
--    ici au profit de ON DELETE SET NULL. La base de test doit être
--    alignée sur cette règle, sinon les deux schémas redivergent.
-- ---------------------------------------------------------------------
ALTER TABLE public."user"
    ADD COLUMN id_sector integer;

ALTER TABLE ONLY public."user"
    ADD CONSTRAINT fk_sector_user FOREIGN KEY (id_sector)
        REFERENCES public.sector(id)
        ON UPDATE CASCADE
        ON DELETE SET NULL;


-- ---------------------------------------------------------------------
-- 3. "user".state : boolean -> character varying(20)
--
--    Un boolean ne porte que deux valeurs : la conversion ne peut donc
--    produire que 'Activé' et 'Désactivé'. Le troisième état, 'Supprimé',
--    n'est atteignable que par l'application après cette migration.
--
--    L'ordre des trois ordres ci-dessous est contraint : le DEFAULT
--    booléen doit disparaître avant le changement de type, sinon
--    PostgreSQL refuse de convertir true en varchar.
-- ---------------------------------------------------------------------
ALTER TABLE public."user"
    ALTER COLUMN state DROP DEFAULT;

ALTER TABLE public."user"
    ALTER COLUMN state TYPE character varying(20)
    USING CASE WHEN state THEN 'Activé' ELSE 'Désactivé' END;

ALTER TABLE public."user"
    ALTER COLUMN state SET DEFAULT 'Activé'::character varying;

-- Le NOT NULL de la colonne d'origine est conservé par ALTER ... TYPE.


-- ---------------------------------------------------------------------
-- 4. "user".role (varchar 200) -> "user".id_role (FK -> role.id)
-- ---------------------------------------------------------------------

-- 4a. Aucun libellé orphelin : chaque "user".role doit exister dans role.
--     On refuse de créer des rôles à la volée, pour ne pas fabriquer de
--     doublons silencieux.
DO $$
DECLARE
    orphelins text;
BEGIN
    SELECT string_agg(DISTINCT quote_literal(u.role), ', ')
      INTO orphelins
      FROM public."user" u
     WHERE u.role IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM public.role r WHERE r.role = u.role);

    IF orphelins IS NOT NULL THEN
        RAISE EXCEPTION 'Rôles présents dans "user".role mais absents de la table role : %', orphelins;
    END IF;
END
$$;

-- 4b. Colonne nullable, puis backfill par jointure sur le libellé.
ALTER TABLE public."user"
    ADD COLUMN id_role integer;

UPDATE public."user" u
   SET id_role = r.id
  FROM public.role r
 WHERE r.role = u.role;

-- 4c. Backfill exhaustif ? Sinon on annule tout.
DO $$
DECLARE
    restants bigint;
BEGIN
    SELECT count(*) INTO restants FROM public."user" WHERE id_role IS NULL;
    IF restants > 0 THEN
        RAISE EXCEPTION '% utilisateur(s) sans id_role après backfill — migration annulée.', restants;
    END IF;
END
$$;

-- 4d. Verrouillage et clé étrangère.
ALTER TABLE public."user"
    ALTER COLUMN id_role SET NOT NULL;

ALTER TABLE ONLY public."user"
    ADD CONSTRAINT fk_user_role FOREIGN KEY (id_role)
        REFERENCES public.role(id);

-- La séquence de role n'est pas touchée : aucune ligne n'a été insérée.


-- ---------------------------------------------------------------------
-- 5. Contrôle final du schéma obtenu
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'user'
                     AND column_name = 'id_role' AND is_nullable = 'NO') THEN
        RAISE EXCEPTION 'Contrôle final : "user".id_role absente ou nullable.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'user'
                     AND column_name = 'state'
                     AND data_type = 'character varying'
                     AND character_maximum_length = 20) THEN
        RAISE EXCEPTION 'Contrôle final : "user".state n''est pas un varchar(20).';
    END IF;

    IF EXISTS (SELECT 1 FROM public."user"
                WHERE state NOT IN ('Activé', 'Désactivé', 'Supprimé')) THEN
        RAISE EXCEPTION 'Contrôle final : valeurs de "user".state hors des trois libellés attendus.';
    END IF;
END
$$;


COMMIT;
