import httpx
import pytest
import asyncio

from app.providers.public_ats import PublicATSProvider, PublicBoard
from app.domain.jobs import display_city, target_job_scope


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


def test_greenhouse_provider_keeps_posting_timestamp_and_canada_remote_evidence():
    provider=PublicATSProvider(PublicBoard("greenhouse","boards-api.greenhouse.io","acme"),
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"jobs":[{
            "id":43,"title":"Technical Support Engineer, Canada","location":{"name":"Americas Remote"},
            "absolute_url":"https://boards.greenhouse.io/acme/jobs/43",
            "first_published":"2026-10-20T09:00:00-04:00",
            "content":"<p>This position requires applicants to be located in Canada.</p>"}]})))
    row=asyncio.run(provider.discover())[0]
    assert row["location"]=="Remote Canada"
    assert row["source_location"]=="Americas Remote"
    assert row["posted_at"]=="2026-10-20T09:00:00-04:00"


def test_lever_provider_converts_creation_timestamp_to_utc():
    provider=PublicATSProvider(PublicBoard("lever","api.lever.co","acme"),
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json=[{
            "id":"p2","text":"Junior Technical Support Engineer","hostedUrl":"https://jobs.lever.co/acme/p2",
            "categories":{"location":"Canada"},"createdAt":1792098347201,
            "descriptionPlain":"Remote position based in Canada."}])))
    row=asyncio.run(provider.discover())[0]
    assert row["posted_at"]=="2026-10-15T21:05:47.201000+00:00"


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


def test_smartrecruiters_reads_scoped_public_board_and_fetches_only_relevant_details():
    def handler(request):
        assert request.url.scheme=="https" and request.url.host=="api.smartrecruiters.com"
        if request.url.path.endswith("/postings"):
            return httpx.Response(200,json={"totalFound":1,"content":[{"id":"123","name":"Junior IT Helpdesk Technician",
              "location":{"fullLocation":"Toronto, ON, Canada"}}]})
        assert request.url.path.endswith("/postings/123")
        return httpx.Response(200,json={"id":"123","name":"Junior IT Helpdesk Technician",
          "releasedDate":"2026-10-01T14:55:19.441Z","applyUrl":"https://jobs.smartrecruiters.com/Example/123-job",
          "location":{"fullLocation":"Toronto, ON, Canada","remote":False,"hybrid":False},
          "typeOfEmployment":{"label":"Full-time"},"compensation":{"min":48000,"max":50000,"currency":"CAD"},
          "jobAd":{"sections":{"jobDescription":{"text":"<p>Supports Windows endpoints and help desk requests.</p>"},
            "qualifications":{"text":"<p>At least one year in technical support.</p>"}}}})
    provider=PublicATSProvider(PublicBoard("smartrecruiters","api.smartrecruiters.com","example","Example"),
      transport=httpx.MockTransport(handler))
    rows=asyncio.run(provider.discover())
    assert len(rows)==1
    assert rows[0]["company"]=="Example" and rows[0]["source"]=="smartrecruiters"
    assert rows[0]["posted_at"]=="2026-10-01T14:55:19.441Z"
    assert rows[0]["employment_type"]=="Full-time"
    assert rows[0]["salary"]["min"]==48000
    assert "technical support" in rows[0]["description"]


def test_smartrecruiters_discards_non_target_roles_before_detail_fetch():
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200,json={"totalFound":1,"content":[{"id":"999","name":"Nurse","location":{"fullLocation":"Toronto, ON, Canada"}}]})
    provider=PublicATSProvider(PublicBoard("smartrecruiters","api.smartrecruiters.com","example"),transport=httpx.MockTransport(handler))
    assert asyncio.run(provider.discover())==[]
    assert len(calls)==1


def test_smartrecruiters_rejects_non_vendor_apply_origin():
    def handler(request):
        if request.url.path.endswith("/postings"):
            return httpx.Response(200,json={"totalFound":1,"content":[{"id":"123","name":"Junior IT Helpdesk Technician",
              "location":{"fullLocation":"Toronto, ON, Canada"}}]})
        return httpx.Response(200,json={"id":"123","name":"Junior IT Helpdesk Technician",
          "applyUrl":"https://attacker.example/collect","location":{"fullLocation":"Toronto, ON, Canada"},
          "jobAd":{"sections":{"jobDescription":{"text":"Help desk support"}}}})
    provider=PublicATSProvider(PublicBoard("smartrecruiters","api.smartrecruiters.com","example"),transport=httpx.MockTransport(handler))
    assert asyncio.run(provider.discover())==[]


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


def test_target_scope_accepts_technical_solutions_support_role_variant():
    assert target_job_scope("Technical Solutions Engineer","Toronto, Ontario") is not None


@pytest.mark.parametrize("title,location",[
    ("Senior IT Support Specialist","Toronto, Ontario"),
    ("IT Support Manager","Toronto, Ontario"),
    ("Software Engineer","Toronto, Ontario"),
    ("Service Desk Analyst","Austin, Texas"),
    ("Technical Support Specialist","Remote United States"),
])
def test_target_job_scope_excludes_senior_unrelated_and_out_of_region(title,location):
    assert target_job_scope(title,location) is None


def test_display_city_reduces_multi_location_ats_label_without_losing_cities():
    assert display_city("Kitchener Ontario, Toronto, Ontario, Ontario (Remote)") == "Kitchener / Toronto"
    assert display_city("Remote - Canada") == "Remote Canada"


def test_provider_rejects_arbitrary_hosts_and_malformed_board_identifiers():
    with pytest.raises(ValueError): PublicATSProvider(PublicBoard("greenhouse","evil.example","acme"))
    with pytest.raises(ValueError): PublicATSProvider(PublicBoard("greenhouse","boards-api.greenhouse.io","https://evil"))


def test_provider_does_not_follow_redirects():
    provider=PublicATSProvider(PublicBoard("lever","api.lever.co","acme"),
        transport=httpx.MockTransport(lambda request:httpx.Response(302,headers={"Location":"http://127.0.0.1/"})))
    with pytest.raises(httpx.HTTPStatusError): asyncio.run(provider.discover())
