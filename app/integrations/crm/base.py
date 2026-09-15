"""ARVO — CRM base adapter. RAW→VALIDATION→NORMALIZATION→CANONICAL→PROVENANCE→EVENT."""

from typing import Any
from sqlalchemy import select

class CRMAdapter:
    source: str = "BASE"
    async def fetch(self, ctx: dict) -> list[dict]: raise NotImplementedError
    def normalize(self, raw: dict, org_id: str) -> dict:
        # Map raw → canonical account/contact/opportunity stub
        return {"raw": raw, "org_id": org_id, "source": self.source}

class HubspotAdapter(CRMAdapter):
    source="HUBSPOT"
    async def fetch(self, ctx): return [{"id":"hs_1","company":"Hubspot Demo","domain":"hubspot.demo","contact":"Ana","email":"ana@hubspot.demo"}]

class SalesforceAdapter(CRMAdapter):
    source="SALESFORCE"
    async def fetch(self, ctx): return [{"Id":"sf_1","Name":"Salesforce Demo","Website":"salesforce.demo"}]

class PipedriveAdapter(CRMAdapter):
    source="PIPEDRIVE"
    async def fetch(self, ctx): return [{"id":1,"name":"Pipedrive Demo","org_name":"Pipe Co"}]

class TwentyAdapter(CRMAdapter):
    source="TWENTY"
    async def fetch(self, ctx): return [{"id":"tw_1","name":"Twenty Demo"}]

class GmailAdapter(CRMAdapter):
    source="GMAIL"
    async def fetch(self, ctx): return [{"id":"gmail_1","subject":"Proposta Acme","from":"deal@acme.com","body":"Desconto 12% solicitado"}]

class OutlookAdapter(CRMAdapter):
    source="OUTLOOK"
    async def fetch(self, ctx): return [{"id":"out_1","subject":"Follow-up Globex","body":"Aguardando aprovação"}]

ADAPTERS = {"hubspot": HubspotAdapter(), "salesforce": SalesforceAdapter(), "pipedrive": PipedriveAdapter(), "twenty": TwentyAdapter(), "gmail": GmailAdapter(), "outlook": OutlookAdapter()}
