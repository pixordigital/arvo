-- ARVO RLS — enable org_id isolation for Supabase Postgres prod
-- App enforces org_id filter; these policies are defense-in-depth.

-- Helper: set app.current_org_id via SET LOCAL (called by backend per request)
-- For Supabase direct access, create role arvo_app with bypass.

-- Enable RLS
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE opportunities ENABLE ROW LEVEL SECURITY;
ALTER TABLE proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE proposal_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE financial_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE financial_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE finding_evidences ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE recommendations ENABLE ROW LEVEL SECURITY;
ALTER TABLE actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_extractions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_decisions ENABLE ROW LEVEL SECURITY;

-- Policies: org_id = current_setting('app.current_org_id') OR service_role bypass
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='arvo_accounts_org_isolation') THEN
    CREATE POLICY arvo_accounts_org_isolation ON accounts
      USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role')
      WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role');
  END IF;
END $$;

DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='arvo_contacts_org_isolation') THEN CREATE POLICY arvo_contacts_org_isolation ON contacts USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role') WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role'); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='arvo_opps_org_isolation') THEN CREATE POLICY arvo_opps_org_isolation ON opportunities USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role') WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role'); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='arvo_findings_org_isolation') THEN CREATE POLICY arvo_findings_org_isolation ON findings USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role') WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role'); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='arvo_events_org_isolation') THEN CREATE POLICY arvo_events_org_isolation ON financial_events USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role') WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role'); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='arvo_ledger_org_isolation') THEN CREATE POLICY arvo_ledger_org_isolation ON financial_ledger USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role') WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role'); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='arvo_raw_org_isolation') THEN CREATE POLICY arvo_raw_org_isolation ON raw_records USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role') WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role'); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='arvo_recs_org_isolation') THEN CREATE POLICY arvo_recs_org_isolation ON recommendations USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role') WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role'); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='arvo_actions_org_isolation') THEN CREATE POLICY arvo_actions_org_isolation ON actions USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role') WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role'); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='arvo_proposals_org_isolation') THEN CREATE POLICY arvo_proposals_org_isolation ON proposals USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role') WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role'); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='arvo_invoices_org_isolation') THEN CREATE POLICY arvo_invoices_org_isolation ON invoices USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role') WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role'); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='arvo_extractions_org_isolation') THEN CREATE POLICY arvo_extractions_org_isolation ON ai_extractions USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role') WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role'); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='arvo_decisions_org_isolation') THEN CREATE POLICY arvo_decisions_org_isolation ON ai_decisions USING (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role') WITH CHECK (org_id = current_setting('app.current_org_id', true) OR current_setting('request.jwt.claim.role', true) = 'service_role'); END IF; END $$;
