# OpenTable Fly viability spike

This is a one-shot, read-only probe for anonymous OpenTable availability from
Fly datacenter IPs. It is not an MCP server and cannot book reservations.

## Local

```powershell
python -m pip install -r requirements.txt
python -m unittest -v
python probe.py
```

The probe:

1. Uses `curl_cffi` to impersonate Chrome's TLS fingerprint.
2. Bootstraps CSRF and cookies from an OpenTable numeric profile page.
3. Calls the `RestaurantsAvailability` persisted GraphQL query.
4. Distinguishes blocks, rate limits, schema drift, genuine no-slot responses,
   and available slots.

## Fly acceptance gate

Build and push the image, then run disposable machines from at least two
regions. Proceed to an MCP only when at least three fixtures return valid
`SLOTS_FOUND` or `NO_SLOTS` responses repeatedly from Fly.

Always disable machine restarts because a nonzero probe exit is an expected
diagnostic result:

```powershell
fly machine run <image> --app pht-opentable-spike --region iad --restart no
```

## Result: passed

On 2026-07-12, image
`registry.fly.io/pht-opentable-spike:deployment-01KXBMXDEJW7SK17X3S5PMEFMX`
was run twice from IAD, ORD, and SJC.

| Region | Run 1 | Run 2 |
|---|---|---|
| IAD | 3/3 valid | 3/3 valid |
| ORD | 3/3 valid | 3/3 valid |
| SJC | 3/3 valid | GraphQL 403 |

IAD and ORD returned consistent slot hashes and plausible availability for all
fixtures. SJC demonstrated that GraphQL blocking remains probabilistic even
when bootstrap and CSRF acquisition succeed.

The future MCP should prefer IAD, retain bounded retries, and return an explicit
`provider_unavailable` result rather than interpreting a 403 as no availability.
