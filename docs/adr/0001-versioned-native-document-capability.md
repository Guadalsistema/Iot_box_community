# Version native document transport separately from PDF transport

Agents advertise `document_print_v1` to receive MIME-aware native PDF, JPEG,
and WebP jobs. The existing `pdf_print_v1` and `pdf_print_v2` capabilities stay
restricted to PDF so an older agent can never be offered bytes it may interpret
as PDF; `document_print_v1` uses the established grouped lease, result identity,
uncertain-outcome, and retention semantics rather than weakening those guarantees.
