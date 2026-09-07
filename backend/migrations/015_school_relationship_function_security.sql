-- The trigger uses qualified objects; pin an empty search path to avoid object shadowing.
alter function public.validate_school_relationship() set search_path = '';
