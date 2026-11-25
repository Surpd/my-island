ALTER TABLE public.identities ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1;
ALTER TABLE public.groups ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS public.directory_edit_history (
    entity_type text NOT NULL, entity_id text NOT NULL, revision bigint NOT NULL,
    changed_fields jsonb NOT NULL, actor_id text, changed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_type, entity_id, revision)
);
ALTER TABLE public.directory_edit_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.directory_edit_history FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.directory_bump_revision(root_name text, ids text[], fields jsonb)
RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF root_name NOT IN ('identities', 'groups') THEN RAISE EXCEPTION 'Invalid directory root'; END IF;
    -- IDs are UUIDs in PostgreSQL. The comparison keeps the primary-key index usable.
    EXECUTE format('WITH changed AS (UPDATE public.%I SET revision=revision+1 WHERE id=ANY($1::uuid[])
                    RETURNING id,revision)
                    INSERT INTO public.directory_edit_history(entity_type,entity_id,revision,changed_fields,actor_id)
                    SELECT $2,id::text,revision,$3,NULLIF(current_setting(''my_island.directory_actor'',true),'''') FROM changed', root_name)
    USING ids,root_name,fields;
END $$;
REVOKE ALL ON FUNCTION public.directory_bump_revision(text,text[],jsonb) FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.directory_track_revision()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE
    prev jsonb := to_jsonb(OLD);
    curr jsonb := to_jsonb(NEW);
    fields jsonb;
BEGIN
    IF TG_TABLE_NAME IN ('identities','groups') THEN
        SELECT jsonb_agg(field ORDER BY field) INTO fields FROM unnest(TG_ARGV) AS field
        WHERE prev->field IS DISTINCT FROM curr->field;
        IF fields IS NOT NULL THEN
            PERFORM public.directory_bump_revision(TG_TABLE_NAME, ARRAY[curr->>'id'], fields);
        END IF;
    ELSE
        IF TG_OP = 'UPDATE' AND (prev - ARRAY['created_at','created_by','reason'])
            IS NOT DISTINCT FROM (curr - ARRAY['created_at','created_by','reason']) THEN RETURN NULL; END IF;
        fields := jsonb_build_array(TG_ARGV[1]);
        PERFORM public.directory_bump_revision('identities', ARRAY[prev->>TG_ARGV[0],curr->>TG_ARGV[0]], fields);
        IF TG_TABLE_NAME <> 'teacher_subjects' THEN
            PERFORM public.directory_bump_revision('groups', ARRAY[prev->>'group_id',curr->>'group_id'], fields);
        END IF;
    END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.directory_track_revision() FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE TRIGGER directory_revision AFTER UPDATE OF display_name,class_name,status,admin_note,kind
ON public.identities FOR EACH ROW EXECUTE FUNCTION public.directory_track_revision('display_name','class_name','status','admin_note','kind');
CREATE OR REPLACE TRIGGER directory_revision AFTER UPDATE OF name,display_name,group_type,subject,base_class_name,subject_subgroup,exam_track,archived_at,canonical,admin_note
ON public.groups FOR EACH ROW EXECUTE FUNCTION public.directory_track_revision('name','display_name','group_type','subject','base_class_name','subject_subgroup','exam_track','archived_at','canonical','admin_note');
CREATE OR REPLACE TRIGGER directory_revision AFTER INSERT OR UPDATE OR DELETE ON public.memberships
FOR EACH ROW EXECUTE FUNCTION public.directory_track_revision('identity_id','memberships');
CREATE OR REPLACE TRIGGER directory_revision AFTER INSERT OR UPDATE OR DELETE ON public.membership_overrides
FOR EACH ROW EXECUTE FUNCTION public.directory_track_revision('identity_id','memberships');
CREATE OR REPLACE TRIGGER directory_revision AFTER INSERT OR UPDATE OR DELETE ON public.teacher_assignments
FOR EACH ROW EXECUTE FUNCTION public.directory_track_revision('teacher_identity_id','assignments');
CREATE OR REPLACE TRIGGER directory_revision AFTER INSERT OR UPDATE OR DELETE ON public.teacher_subjects
FOR EACH ROW EXECUTE FUNCTION public.directory_track_revision('teacher_identity_id','subjects');

GRANT EXECUTE ON FUNCTION public.directory_bump_revision(text,text[],jsonb) TO service_role;
GRANT INSERT, SELECT ON public.directory_edit_history TO service_role;
