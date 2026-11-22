"""ARVO — CRM adapters. Live sync not implemented; CSV/upload only."""

class UnimplementedAdapter:
    """Stub: caller maps fetch's NotImplementedError to HTTP 501."""
    def __init__(self, source: str):
        self.source = source

    async def fetch(self, ctx: dict) -> list[dict]:
        raise NotImplementedError(f"{self.source} live sync not implemented; CSV/upload only")


ADAPTERS = {
    name: UnimplementedAdapter(name.upper())
    for name in ("hubspot", "salesforce", "pipedrive", "twenty", "gmail", "outlook")
}
