# Quality report - run `6599ce52-8d1b-4d98-b81f-395bfd289934`

- **Status:** failed
- **Source mode:** live
- **Endpoint:** `https://geodaten-wasser.rlp.de/wfs/kompensation`
- **Layer:** `rp_oekokonto`
- **Started / finished (UTC):** 2026-10-29T04:01:57.931980+00:00 / 2026-10-29T04:02:11.566576+00:00

## Failure

```
ConnectionError: HTTPSConnectionPool(host='geodaten-wasser.rlp.de', port=443): Max retries exceeded with url: /wfs/kompensation?SERVICE=WFS&REQUEST=GetCapabilities&ACCEPTVERSIONS=2.0.0%2C1.1.0 (Caused by NameResolutionError("HTTPSConnection(host='geodaten-wasser.rlp.de', port=443): Failed to resolve 'geodaten-wasser.rlp.de' ([Errno -2] Name or service not known)"))
```
