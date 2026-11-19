"""ARVO — CRM base adapter. RAW→VALIDATION→NORMALIZATION→CANONICAL→PROVENANCE→EVENT."""

from typing import Any
from sqlalchemy import select

class CRMAdapter:
    source: str = "BASE"
    async def fetch(self, ctx: dict) -> list[dict]: raise NotImplementedError
    def normalize(self, raw: dict, org_id: str) -> dict:
        # Map raw → canonical account/contact/opportunity stub
        return {"raw": raw, "org_id": org_id, "source": self.source}

class _UnimplementedAdapter(CRMAdapter):
    async def fetch(self, ctx):
        raise NotImplementedError(f"{self.source} live sync not implemented; CSV/upload only")


class HubspotAdapter(_UnimplementedAdapter):
    source="HUBSPOT"

class SalesforceAdapter(_UnimplementedAdapter):
    source="SALESFORCE"

class PipedriveAdapter(_UnimplementedAdapter):
    source="PIPEDRIVE"

class TwentyAdapter(_UnimplementedAdapter):
    source="TWENTY"

class GmailAdapter(_UnimplementedAdapter):
    source="GMAIL"

class OutlookAdapter(_UnimplementedAdapter):
    source="OUTLOOK"

ADAPTERS = {"hubspot": HubspotAdapter(), "salesforce": SalesforceAdapter(), "pipedrive": PipedriveAdapter(), "twenty": TwentyAdapter(), "gmail": GmailAdapter(), "outlook": OutlookAdapter()}
