#!/bin/sh
# Re-download the public-domain documents in corpus/public/ (US federal government works).
# Files are kept byte-for-byte as served. Fetched 2026-10-30; sources may change over time,
# so a re-fetch can produce different bytes than the committed copies.
set -e
cd "$(dirname "$0")/../public"
UA="Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/128.0"
get() { curl -sSfL -A "$UA" --max-time 120 -o "$1" "$2"; echo "$1"; }

get scotus-coinbase-v-suski-2024.pdf https://www.supremecourt.gov/opinions/23pdf/602us1r26_k537.pdf
get scotus-smith-v-spizzirri-2024.pdf https://www.supremecourt.gov/opinions/23pdf/601us2r22_o7jq.pdf
get usc-title9-arbitration-2023.pdf https://www.govinfo.gov/content/pkg/USCODE-2023-title9/pdf/USCODE-2023-title9.pdf
get usc-title9-arbitration-2023.htm https://www.govinfo.gov/content/pkg/USCODE-2023-title9/html/USCODE-2023-title9.htm
get fr-2026-03266-water-carriers.pdf https://www.govinfo.gov/content/pkg/FR-2026-02-19/pdf/2026-03266.pdf
get fr-2024-27552-transportation-payment-audit.pdf https://www.govinfo.gov/content/pkg/FR-2024-11-26/pdf/2024-27552.pdf
get fr-2024-27115-property-broker-transparency.html https://www.federalregister.gov/documents/full_text/html/2024/11/20/2024-27115.html
get txsd-lamex-foods-v-blakeman-transportation-2007.pdf https://www.govinfo.gov/content/pkg/USCOURTS-txsd-4_06-cv-03733/pdf/USCOURTS-txsd-4_06-cv-03733-0.pdf
get txsd-latshaw-drilling-v-saia-motor-freight-2013.pdf https://www.govinfo.gov/content/pkg/USCOURTS-txsd-4_12-cv-01623/pdf/USCOURTS-txsd-4_12-cv-01623-0.pdf
get cbp-counterfeit-watches-louisville.html https://www.cbp.gov/newsroom/national-media-release/counterfeit-watches-worth-66-million-intercepted-louisville-cbp
get uscourts-federal-rules-of-civil-procedure.pdf https://www.uscourts.gov/sites/default/files/document/federal-rules-of-civil-procedure.pdf
