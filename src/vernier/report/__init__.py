"""Human and JSON report output."""

from vernier.report.escape import escape_untrusted
from vernier.report.gate_report import (
    GATE_REPORT_SCHEMA,
    build_gate_report,
    gate_report_to_jcs,
    render_gate_comment,
)
from vernier.report.json_report import (
    REPORT_SCHEMA_VERSION,
    build_report,
    diverging_region,
    report_to_jcs,
)
from vernier.report.render import render_human, use_color

__all__ = [
    "GATE_REPORT_SCHEMA",
    "REPORT_SCHEMA_VERSION",
    "build_gate_report",
    "build_report",
    "diverging_region",
    "escape_untrusted",
    "gate_report_to_jcs",
    "render_gate_comment",
    "render_human",
    "report_to_jcs",
    "use_color",
]
