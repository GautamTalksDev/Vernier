"""Human and JSON report output."""

from vernier.report.escape import escape_untrusted
from vernier.report.json_report import (
    REPORT_SCHEMA_VERSION,
    build_report,
    diverging_region,
    report_to_jcs,
)
from vernier.report.render import render_human, use_color

__all__ = [
    "REPORT_SCHEMA_VERSION",
    "build_report",
    "diverging_region",
    "escape_untrusted",
    "render_human",
    "report_to_jcs",
    "use_color",
]
