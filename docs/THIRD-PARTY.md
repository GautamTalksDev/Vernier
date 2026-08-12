# Third-party licenses

Vernier (`vernier-cli`) is Apache-2.0. Runtime and build dependencies
below are compatible with distribution under Apache-2.0 (permissive OSI /
PSF licenses; MPL-2.0 is file-level weak copyleft and acceptable as a
transitive dependency).

Versions are those resolved in the development environment when this file
was written; see `uv.lock` for the authoritative pin set.

## Direct runtime dependencies

| Package | License |
| --- | --- |
| [httpx](https://pypi.org/project/httpx/) | BSD-3-Clause |
| [requests](https://pypi.org/project/requests/) | Apache-2.0 |
| [typer](https://pypi.org/project/typer/) | MIT |

## Notable transitive runtime dependencies

| Package | License |
| --- | --- |
| anyio | MIT |
| certifi | MPL-2.0 |
| charset-normalizer | MIT |
| click | BSD-3-Clause |
| h11 | MIT |
| httpcore | BSD-3-Clause |
| idna | BSD-3-Clause |
| rich | MIT |
| shellingham | ISC |
| urllib3 | MIT |
| annotated-doc | MIT |
| markdown-it-py | MIT |
| mdurl | MIT |
| Pygments | BSD-2-Clause |
| typing_extensions | PSF-2.0 |

## Build

| Package | License |
| --- | --- |
| hatchling | MIT |

## Development-only (not required to run `vern`)

| Package | License |
| --- | --- |
| mypy | MIT |
| pip-audit | Apache-2.0 |
| pytest | MIT |
| pytest-cov | MIT |
| ruff | MIT |
| types-requests | Apache-2.0 |
| coverage | Apache-2.0 |

## Embedded / adapted code

JSON Canonicalization Scheme (JCS) number serialization adapted from
Anders Rundgren’s reference implementation
(https://github.com/cyberphone/json-canonicalization), Apache-2.0 — see
`NOTICE`.
