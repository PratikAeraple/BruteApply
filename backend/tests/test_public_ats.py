import httpx
import pytest
import asyncio

from app.providers.public_ats import PublicATSProvider, PublicBoard
from app.domain.jobs import target_job_scope


def test_greenhouse_provider_uses_fixed_https_host_and_normalizes_untrusted_fields():
    def handler(request):
        assert request.url.scheme == "https"
        assert request.url.host == "boards-api.greenhouse.io"
        return httpx.Response(200,json={"jobs":[{"id":42,"title":"Support <script>bad()</script> Analyst",
            "location":{"name":"Toronto"},"absolute_url":"https://boards.greenhouse.io/acme/jobs/42",
            "content":"<p>Linux &amp; support</p>"}]})
    provider=PublicATSProvider(PublicBoard("greenhouse","boards-api.greenhouse.io","acme"),transport=httpx.MockTransport(handler))
    rows=asyncio.run(provider.discover())
    assert rows[0]["title"] == "Support bad() Analyst"
    assert rows[0]["description"] == "Linux & support"
    assert rows[0]["untrusted"] is True


def test_lever_provider_reads_public_json_not_html():
    def handler(request):
        assert "mode=json" in str(request.url)
        return httpx.Response(200,json=[{"id":"p1","text":"Desktop Technician",
            "categories":{"location":"Calgary"},"hostedUrl":"https://jobs.lever.co/acme/p1",
            "descriptionPlain":"Troubleshoot endpoints"}])
    provider=PublicATSProvider(PublicBoard("lever","api.lever.co","acme"),transport=httpx.MockTransport(handler))
    assert asyncio.run(provider.discover())[0]["company"] == "acme"


def test_ats_provider_preserves_owner_friendly_company_and_allowed_metadata():
    provider=PublicATSProvider(PublicBoard("lever","api.lever.co","getmaple","Maple"),
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json=[{
            "id":"1","text":"IT Support Specialist","hostedUrl":"https://jobs.lever.co/getmaple/1",
            "categories":{"location":"Toronto, Ontario","commitment":"Full-time"},
            "workplaceType":"hybrid","salaryRange":{"min":60000,"max":68000},
            "descriptionPlain":"Windows troubleshooting"}])))
    row=asyncio.run(provider.discover())[0]
    assert row["company"]=="Maple"
    assert row["work_mode"]=="Hybrid"
    assert row["employment_type"]=="Full-time"
    assert row["salary"]["min"]==60000


@pytest.mark.parametrize("title,location,expected",[
    ("IT Support Specialist","Toronto, Ontario Hybrid",("Ontario","Hybrid")),
    ("Product Support Analyst","Kitchener, Ontario; Remote",("Ontario","Remote")),
    ("IT System Administrator","Toronto, ON",("Ontario","Unspecified")),
    ("Service Desk Analyst","Remote Canada",("Remote Canada","Remote")),
])
def test_target_job_scope_includes_requested_roles_and_regions(title,location,expected):
    scope=target_job_scope(title,location)
    assert scope is not None
    assert (scope["province"],scope["work_mode"])==expected


@pytest.mark.parametrize("title,location",[
    ("Senior IT Support Specialist","Toronto, Ontario"),
    ("IT Support Manager","Toronto, Ontario"),
    ("Software Engineer","Toronto, Ontario"),
    ("Service Desk Analyst","Austin, Texas"),
    ("Technical Support Specialist","Remote United States"),
])
def test_target_job_scope_excludes_senior_unrelated_and_out_of_region(title,location):
    assert target_job_scope(title,location) is None


def test_provider_rejects_arbitrary_hosts_and_malformed_board_identifiers():
    with pytest.raises(ValueError): PublicATSProvider(PublicBoard("greenhouse","evil.example","acme"))
    with pytest.raises(ValueError): PublicATSProvider(PublicBoard("greenhouse","boards-api.greenhouse.io","https://evil"))


def test_provider_does_not_follow_redirects():
    provider=PublicATSProvider(PublicBoard("lever","api.lever.co","acme"),
        transport=httpx.MockTransport(lambda request:httpx.Response(302,headers={"Location":"http://127.0.0.1/"})))
    with pytest.raises(httpx.HTTPStatusError): asyncio.run(provider.discover())
