-- =====================================================================
-- Migration 002 — suppression de l'ancienne colonne "user".role
--
-- À NE JOUER QU'APRÈS :
--   - la migration 001,
--   - le déploiement du code qui lit "user".id_role,
--   - une vérification applicative en intégration.
--
-- Cette migration est destructive et sans retour arrière possible :
-- tant qu'elle n'est pas passée, revenir à la version précédente du
-- code reste gratuit.
--
-- Exécution :  psql -d <base> -v ON_ERROR_STOP=1 -f migrations/002_drop_user_role.sql
-- =====================================================================

\set ON_ERROR_STOP on
\encoding UTF8

BEGIN;

SET lock_timeout = '5s';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'user'
                     AND column_name = 'id_role') THEN
        RAISE EXCEPTION 'La colonne "user".id_role est absente — migration 001 non appliquée.';
    END IF;

    IF EXISTS (SELECT 1 FROM public."user" WHERE id_role IS NULL) THEN
        RAISE EXCEPTION 'Des utilisateurs ont id_role NULL — ne pas supprimer "user".role.';
    END IF;
END
$$;

ALTER TABLE public."user" DROP COLUMN role;

COMMIT;
