-- LinkedIn pipeline tables (run once in Supabase: SQL Editor → New query → paste → Run)
-- From claude-plan.md section 2.

create table if not exists linkedin_running_order (
  id              bigint generated always as identity primary key,
  scheduled_date  date not null unique,
  process_id      bigint,
  process_name    text not null,
  style           text not null,
  sequence_number int  not null,
  created_at      timestamptz not null default now()
);

create table if not exists linkedin_posts (
  id                bigint generated always as identity primary key,
  running_order_id  bigint not null references linkedin_running_order(id),
  scheduled_date    date not null unique,
  status            text not null default 'generating'
    check (status in ('generating','qa_passed','needs_manual_review',
                      'awaiting_approval','approved','publishing',
                      'published','publish_failed','skipped')),
  coordinator_brief jsonb,
  post_text         text,
  qa_score          int,
  qa_feedback       jsonb,
  revision_count    int not null default 0,
  slack_channel_id  text,
  slack_message_ts  text,
  approved_by       text,
  approved_at       timestamptz,
  publish_ref       text,          -- e.g. google_sheet:test (later: Blotato post id)
  published_at      timestamptz,
  error_detail      text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create index if not exists linkedin_posts_date_status
  on linkedin_posts (scheduled_date, status);

-- Lock the tables down: with RLS on and no policies, the public anon key sees
-- nothing. n8n and the seed script use the service_role key, which bypasses
-- RLS, so the pipeline is unaffected.
alter table linkedin_running_order enable row level security;
alter table linkedin_posts enable row level security;
