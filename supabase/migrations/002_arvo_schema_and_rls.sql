-- ARVO schema isolation — guard public tables, restore RLS.
-- Idempotent. Never touches the aios schema or its objects.

CREATE SCHEMA IF NOT EXISTS arvo;

-- Guard: never move tables by name. AIOS and ARVO share table names.
DO $$
DECLARE t text; offending text[] := ARRAY[]::text[];
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'organizations','users','memberships','accounts','contacts',
    'opportunities','raw_records','products','proposals','proposal_items',
    'invoices','financial_events','financial_ledger','findings','finding_evidences',
    'ai_extractions','ai_decisions','recommendations','actions',
    'integration_nonces','integration_events','deal_audit_jobs','integration_outbox'
  ] LOOP
    IF to_regclass('public.' || t) IS NOT NULL THEN
      offending := array_append(offending, t);
    END IF;
  END LOOP;
  IF cardinality(offending) > 0 THEN
    RAISE EXCEPTION 'Tables in public: %. Restore each app dump directly into its own schema (aios / arvo); do not move by name', array_to_string(offending, ', ');
  END IF;
END $$;

-- Grant app role access only if role already exists; never create roles here.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'arvo_app') THEN
    GRANT USAGE ON SCHEMA arvo TO arvo_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA arvo TO arvo_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA arvo GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO arvo_app;
  END IF;
END $$;

-- Re-enable RLS from migration 001 on the arvo-qualified tables.
DO $$ BEGIN
  IF to_regclass('arvo.organizations') IS NOT NULL THEN ALTER TABLE arvo.organizations ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.users') IS NOT NULL THEN ALTER TABLE arvo.users ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.memberships') IS NOT NULL THEN ALTER TABLE arvo.memberships ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.accounts') IS NOT NULL THEN ALTER TABLE arvo.accounts ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.contacts') IS NOT NULL THEN ALTER TABLE arvo.contacts ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.opportunities') IS NOT NULL THEN ALTER TABLE arvo.opportunities ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.products') IS NOT NULL THEN ALTER TABLE arvo.products ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.proposals') IS NOT NULL THEN ALTER TABLE arvo.proposals ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.proposal_items') IS NOT NULL THEN ALTER TABLE arvo.proposal_items ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.invoices') IS NOT NULL THEN ALTER TABLE arvo.invoices ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.financial_events') IS NOT NULL THEN ALTER TABLE arvo.financial_events ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.financial_ledger') IS NOT NULL THEN ALTER TABLE arvo.financial_ledger ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.findings') IS NOT NULL THEN ALTER TABLE arvo.findings ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.finding_evidences') IS NOT NULL THEN ALTER TABLE arvo.finding_evidences ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.raw_records') IS NOT NULL THEN ALTER TABLE arvo.raw_records ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.recommendations') IS NOT NULL THEN ALTER TABLE arvo.recommendations ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.actions') IS NOT NULL THEN ALTER TABLE arvo.actions ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.ai_extractions') IS NOT NULL THEN ALTER TABLE arvo.ai_extractions ENABLE ROW LEVEL SECURITY; END IF;
  IF to_regclass('arvo.ai_decisions') IS NOT NULL THEN ALTER TABLE arvo.ai_decisions ENABLE ROW LEVEL SECURITY; END IF;
END $$;

-- Recreate migration 001 org-isolation policies on arvo tables.
-- Drop first: 001 policies travelled with the table and are keyed by name,
-- so the old public-era policy must go before the arvo-qualified one exists.
DO $$ BEGIN
  IF to_regclass('arvo.accounts') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_accounts_org_isolation ON arvo.accounts;
    CREATE POLICY arvo_accounts_org_isolation ON arvo.accounts
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('arvo.products') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_products_org_isolation ON arvo.products;
    CREATE POLICY arvo_products_org_isolation ON arvo.products
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('arvo.contacts') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_contacts_org_isolation ON arvo.contacts;
    CREATE POLICY arvo_contacts_org_isolation ON arvo.contacts
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('arvo.opportunities') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_opps_org_isolation ON arvo.opportunities;
    CREATE POLICY arvo_opps_org_isolation ON arvo.opportunities
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('arvo.findings') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_findings_org_isolation ON arvo.findings;
    CREATE POLICY arvo_findings_org_isolation ON arvo.findings
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('arvo.financial_events') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_events_org_isolation ON arvo.financial_events;
    CREATE POLICY arvo_events_org_isolation ON arvo.financial_events
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('arvo.financial_ledger') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_ledger_org_isolation ON arvo.financial_ledger;
    CREATE POLICY arvo_ledger_org_isolation ON arvo.financial_ledger
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('arvo.raw_records') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_raw_org_isolation ON arvo.raw_records;
    CREATE POLICY arvo_raw_org_isolation ON arvo.raw_records
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('arvo.recommendations') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_recs_org_isolation ON arvo.recommendations;
    CREATE POLICY arvo_recs_org_isolation ON arvo.recommendations
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('arvo.actions') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_actions_org_isolation ON arvo.actions;
    CREATE POLICY arvo_actions_org_isolation ON arvo.actions
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('arvo.proposals') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_proposals_org_isolation ON arvo.proposals;
    CREATE POLICY arvo_proposals_org_isolation ON arvo.proposals
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('arvo.invoices') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_invoices_org_isolation ON arvo.invoices;
    CREATE POLICY arvo_invoices_org_isolation ON arvo.invoices
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('arvo.ai_extractions') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_extractions_org_isolation ON arvo.ai_extractions;
    CREATE POLICY arvo_extractions_org_isolation ON arvo.ai_extractions
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('arvo.ai_decisions') IS NOT NULL THEN
    DROP POLICY IF EXISTS arvo_decisions_org_isolation ON arvo.ai_decisions;
    CREATE POLICY arvo_decisions_org_isolation ON arvo.ai_decisions
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;
