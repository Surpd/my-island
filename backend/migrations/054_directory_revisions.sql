ALTER TABLE public.identities ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1;
ALTER TABLE public.groups ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS public.directory_edit_history (
    entity_type text NOT NULL, entity_id text NOT NULL, revision bigint NOT NULL,
    changed_fields jsonb NOT NULL, actor_id text, changed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_type, entity_id, revision)
);
ALTER TABLE public.directory_edit_history ADD COLUMN IF NOT EXISTS field_values jsonb NOT NULL DEFAULT '{}';
ALTER TABLE public.directory_edit_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.directory_edit_history FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.directory_bump_revision(root_name text, ids text[], fields jsonb, deltas jsonb)
RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF root_name NOT IN ('identities', 'groups') THEN RAISE EXCEPTION 'Invalid directory root'; END IF;
    -- IDs are UUIDs in PostgreSQL. The comparison keeps the primary-key index usable.
    EXECUTE format('WITH changed AS (UPDATE public.%I SET revision=revision+1 WHERE id=ANY($1::uuid[])
                    RETURNING id,revision)
                    INSERT INTO public.directory_edit_history(entity_type,entity_id,revision,changed_fields,field_values,actor_id)
                    SELECT $2,id::text,revision,$3,$4,NULLIF(current_setting(''my_island.directory_actor'',true),'''') FROM changed', root_name)
    USING ids,root_name,fields,deltas;
END $$;
REVOKE ALL ON FUNCTION public.directory_bump_revision(text,text[],jsonb,jsonb) FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.directory_track_revision()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE
    prev jsonb := to_jsonb(OLD);
    curr jsonb := to_jsonb(NEW);
    fields jsonb;
    deltas jsonb;
    group_key text;
BEGIN
    IF TG_TABLE_NAME IN ('identities','groups') THEN
        SELECT jsonb_agg(field ORDER BY field) INTO fields FROM unnest(TG_ARGV) AS field
        WHERE prev->field IS DISTINCT FROM curr->field;
        IF fields IS NOT NULL THEN
            SELECT coalesce(jsonb_object_agg(f,jsonb_build_object('before',prev->f,'after',curr->f)), '{}') INTO deltas
            FROM jsonb_array_elements_text(fields) AS f WHERE f IN ('kind','status','group_type','canonical');
            PERFORM public.directory_bump_revision(TG_TABLE_NAME, ARRAY[curr->>'id'], fields, deltas);
        END IF;
    ELSE
        IF TG_OP = 'UPDATE' AND (prev - ARRAY['created_at','created_by','reason'])
            IS NOT DISTINCT FROM (curr - ARRAY['created_at','created_by','reason']) THEN RETURN NULL; END IF;
        fields := jsonb_build_array(TG_ARGV[1]);
        deltas := CASE WHEN coalesce(curr, prev) ? 'active'
            THEN jsonb_build_object('active',jsonb_build_object('before',prev->'active','after',curr->'active'))
            ELSE '{}'::jsonb END;
        group_key := CASE WHEN TG_TABLE_NAME = 'homeroom_assignments' THEN 'class_group_id' ELSE 'group_id' END;
        PERFORM public.directory_bump_revision('identities', ARRAY[prev->>TG_ARGV[0],curr->>TG_ARGV[0]], fields, deltas);
        IF TG_TABLE_NAME <> 'teacher_subjects' THEN
            PERFORM public.directory_bump_revision('groups', ARRAY[prev->>group_key,curr->>group_key], fields, deltas);
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

CREATE OR REPLACE TRIGGER directory_revision AFTER INSERT OR UPDATE OR DELETE ON public.homeroom_assignments
FOR EACH ROW EXECUTE FUNCTION public.directory_track_revision('teacher_identity_id','homeroom_assignments');

GRANT EXECUTE ON FUNCTION public.directory_bump_revision(text,text[],jsonb,jsonb) TO service_role;
GRANT INSERT, SELECT ON public.directory_edit_history TO service_role;

-- Use the group key belonging to each trigger table, including class_group_id.
create or replace function public.validate_school_relationship()
returns trigger language plpgsql set search_path = '' as $$
declare
  identity_kind text;
  identity_status text;
  canonical_group boolean;
  requires_group boolean := true;
  canonical_group_type text;
begin
  if tg_table_name = 'memberships' then
    select kind, status into identity_kind, identity_status from public.identities where id = new.identity_id;
    select canonical, group_type into canonical_group, canonical_group_type from public.groups where id = new.group_id;
    if new.member_role <> 'student' or identity_kind <> 'student' then
      raise exception 'membership requires a student identity';
    end if;
  elsif tg_table_name = 'teacher_assignments' then
    select kind, status into identity_kind, identity_status from public.identities where id = new.teacher_identity_id;
    requires_group := new.group_id is not null;
    if requires_group then
      select canonical, group_type into canonical_group, canonical_group_type from public.groups where id = new.group_id;
    end if;
    if identity_kind <> 'teacher' then
      raise exception 'teacher assignment requires a teacher identity';
    end if;
  else
    select kind, status into identity_kind, identity_status from public.identities where id = new.teacher_identity_id;
    select canonical, group_type into canonical_group, canonical_group_type from public.groups where id = new.class_group_id;
    if identity_kind <> 'teacher' then
      raise exception 'homeroom assignment requires a teacher identity';
    end if;
  end if;
  if new.active and (identity_status <> 'active' or (requires_group and canonical_group is not true)) then
    raise exception 'school relationship requires active identity and canonical group';
  end if;
  if new.active and tg_table_name = 'homeroom_assignments' and canonical_group_type <> 'class' then
    raise exception 'homeroom assignment requires a canonical class group';
  end if;
  return new;
end $$;
