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
