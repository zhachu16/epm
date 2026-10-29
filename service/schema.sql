-- Tables and functions for the epm service. Run once in the Supabase SQL editor.
-- The service connects with the service-role key, which bypasses row-level security;
-- RLS is on with no policies so the public (anon) key can read nothing.

create table if not exists projects (
  id text primary key,
  name text not null,
  description text not null default '',
  is_example boolean not null default false,
  current_version integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);
create index if not exists projects_updated_at on projects (updated_at desc);

create table if not exists versions (
  project_id text not null references projects (id) on delete cascade,
  number integer not null,
  blob_key text not null,
  label text not null default '',
  source text not null default '',
  ops integer not null default 0,
  bytes bigint not null default 0,
  sha256 text not null default '',
  restored_from integer,
  created_at timestamptz not null default now(),
  primary key (project_id, number)
);

create table if not exists outputs (
  id text primary key,
  project_id text not null references projects (id) on delete cascade,
  version integer not null,
  kind text not null,
  name text not null,
  blob_key text not null,
  content_type text not null,
  bytes bigint not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists outputs_project on outputs (project_id, created_at desc);

alter table projects enable row level security;
alter table versions enable row level security;
alter table outputs enable row level security;

-- A project and its first version, together.
create or replace function create_project(
  p_id text, p_name text, p_description text, p_is_example boolean, p_number integer,
  p_blob_key text, p_label text, p_source text, p_ops integer, p_bytes bigint, p_sha256 text
) returns setof projects language plpgsql as $$
begin
  insert into projects (id, name, description, is_example, current_version)
    values (p_id, p_name, p_description, p_is_example, p_number);
  insert into versions (project_id, number, blob_key, label, source, ops, bytes, sha256)
    values (p_id, p_number, p_blob_key, p_label, p_source, p_ops, p_bytes, p_sha256);
  return query select * from projects where id = p_id;
end $$;

-- Add a version and make it current, only if nobody saved since p_expected.
create or replace function commit_version(
  p_project_id text, p_expected integer, p_number integer, p_blob_key text, p_label text,
  p_source text, p_ops integer, p_bytes bigint, p_sha256 text, p_restored_from integer
) returns boolean language plpgsql as $$
begin
  update projects set current_version = p_number, updated_at = now()
    where id = p_project_id and current_version = p_expected;
  if not found then
    return false;
  end if;
  insert into versions (
    project_id, number, blob_key, label, source, ops, bytes, sha256, restored_from
  ) values (
    p_project_id, p_number, p_blob_key, p_label, p_source, p_ops, p_bytes, p_sha256,
    p_restored_from
  );
  return true;
end $$;

-- Case-insensitive substring search on name and description; no pattern escaping needed.
create or replace function search_projects(p_q text, p_deleted boolean, p_limit integer)
returns setof projects language sql stable as $$
  select * from projects
  where (deleted_at is not null) = p_deleted
    and (p_q = '' or position(lower(p_q) in lower(name)) > 0
                  or position(lower(p_q) in lower(description)) > 0)
  order by updated_at desc
  limit p_limit;
$$;

revoke all on function create_project from public, anon, authenticated;
revoke all on function commit_version from public, anon, authenticated;
revoke all on function search_projects from public, anon, authenticated;
