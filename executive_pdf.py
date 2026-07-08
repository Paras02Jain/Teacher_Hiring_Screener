"""
executive_pdf.py — EduHire Executive Summary PDF Generator
============================================================
Generates a clean, print-ready PDF decision-support report that a school
principal (non-technical) can read and sign off on.

Contents
--------
  Page 1 : Cover — School name, role, date, top-line stats
  Page 2+ : Ranked candidate cards (one card per shortlisted candidate)
            Each card: name, score badge, experience, degree, top strengths,
            red flags, and JD keyword match summary.
  Final   : Filtered candidates table + fairness audit snapshot

Dependencies: reportlab (pure Python, no libreoffice needed)
Install:  pip install reportlab
"""

from __future__ import annotations
import io
import datetime
from typing import Any

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.colors import (
        HexColor, white, black, Color
    )
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, PageBreak, KeepTogether
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    _REPORTLAB = True
except ImportError:
    _REPORTLAB = False


# ── Colour palette (matches app.py theme) ────────────────────────────────────
_INK      = HexColor("#0D1B2A") if _REPORTLAB else None
_CREAM    = HexColor("#E8EFF8") if _REPORTLAB else None
_SKY      = HexColor("#0EA5E9") if _REPORTLAB else None
_TEAL     = HexColor("#0F766E") if _REPORTLAB else None
_SAGE     = HexColor("#10B981") if _REPORTLAB else None
_ROSE     = HexColor("#F43F5E") if _REPORTLAB else None
_GOLD     = HexColor("#F59E0B") if _REPORTLAB else None
_SILVER   = HexColor("#94A3B8") if _REPORTLAB else None
_NAVY     = HexColor("#0D2137") if _REPORTLAB else None
_BORDER   = HexColor("#1E3A5F") if _REPORTLAB else None
_WHITE    = white if _REPORTLAB else None
_LIGHT_BG = HexColor("#EFF6FF") if _REPORTLAB else None

W, H = A4 if _REPORTLAB else (595, 842)

MARGIN = 1.8 * cm if _REPORTLAB else 0


def reportlab_available() -> bool:
    return _REPORTLAB


# ══════════════════════════════════════════════════════════════════════════════
# STYLE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _styles():
    base = getSampleStyleSheet()

    def _add(name, **kw):
        base.add(ParagraphStyle(name=name, **kw))

    _add("CoverTitle",
         fontName="Helvetica-Bold", fontSize=26, leading=32,
         textColor=_INK, alignment=TA_CENTER, spaceAfter=6)

    _add("CoverSubtitle",
         fontName="Helvetica", fontSize=13, leading=18,
         textColor=_SILVER, alignment=TA_CENTER, spaceAfter=4)

    _add("CoverMeta",
         fontName="Helvetica", fontSize=10, leading=14,
         textColor=_SILVER, alignment=TA_CENTER, spaceAfter=2)

    _add("SectionHeading",
         fontName="Helvetica-Bold", fontSize=13, leading=17,
         textColor=_INK, spaceBefore=14, spaceAfter=6)

    _add("CandName",
         fontName="Helvetica-Bold", fontSize=14, leading=18,
         textColor=_INK, spaceAfter=2)

    _add("BodySmall",
         fontName="Helvetica", fontSize=9, leading=13,
         textColor=_INK, spaceAfter=2)

    _add("BodySmallBold",
         fontName="Helvetica-Bold", fontSize=9, leading=13,
         textColor=_INK, spaceAfter=2)

    _add("TagText",
         fontName="Helvetica", fontSize=8, leading=12,
         textColor=_INK, spaceAfter=1)

    _add("FooterText",
         fontName="Helvetica", fontSize=8, leading=11,
         textColor=_SILVER, alignment=TA_CENTER)

    return base


def _score_color(score: float):
    if score >= 75:   return _SAGE
    if score >= 55:   return _SKY
    if score >= 35:   return _GOLD
    return _ROSE


# ══════════════════════════════════════════════════════════════════════════════
# COVER PAGE
# ══════════════════════════════════════════════════════════════════════════════

def _cover_page(story, S, school_name, role_title, results, jd_snippet, audit):
    qualified = [r for r in results if not r.get("filtered")]
    filtered  = [r for r in results if r.get("filtered")]
    total     = len(results)
    today     = datetime.date.today().strftime("%d %B %Y")

    # Header band — use a 1-row coloured table as a "banner"
    banner_data = [[Paragraph(
        f'<font color="#FFFFFF"><b>CANDIDATE SCREENING REPORT</b></font>',
        ParagraphStyle("BannerText", fontName="Helvetica-Bold", fontSize=18,
                       leading=22, textColor=white, alignment=TA_CENTER)
    )]]
    banner_tbl = Table(banner_data, colWidths=[W - 2 * MARGIN])
    banner_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), _NAVY),
        ("TOPPADDING",   (0, 0), (-1, -1), 18),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 18),
        ("LEFTPADDING",  (0, 0), (-1, -1), 24),
        ("RIGHTPADDING", (0, 0), (-1, -1), 24),
        ("ROUNDEDCORNERS", [8]),
    ]))
    story.append(banner_tbl)
    story.append(Spacer(1, 0.5 * cm))

    # School & role
    story.append(Paragraph(school_name or "School Hiring Report", S["CoverTitle"]))
    story.append(Paragraph(role_title or "Teaching Position", S["CoverSubtitle"]))
    story.append(Paragraph(f"Generated: {today}", S["CoverMeta"]))
    story.append(Spacer(1, 0.6 * cm))
    story.append(HRFlowable(width="100%", thickness=1.5, color=_BORDER))
    story.append(Spacer(1, 0.5 * cm))

    # KPI row
    kpi_vals = [
        (str(total),        "Total Screened",  _INK),
        (str(len(qualified)), "Shortlisted",   _SAGE),
        (str(len(filtered)),  "Filtered Out",  _ROSE),
        (f"{max((r['score'] for r in qualified), default=0):.0f}%", "Top Score", _GOLD),
        (f"{(sum(r['score'] for r in qualified)/len(qualified)) if qualified else 0:.0f}%", "Avg Score", _SILVER),
    ]

    kpi_cells = []
    for val, lbl, color in kpi_vals:
        cell_content = [
            Paragraph(f'<font color="#{color.hexval()[2:]}"><b>{val}</b></font>',
                      ParagraphStyle("KPIVal", fontName="Helvetica-Bold",
                                     fontSize=22, leading=26, alignment=TA_CENTER)),
            Paragraph(lbl, ParagraphStyle("KPILabel", fontName="Helvetica",
                                          fontSize=8, leading=11, textColor=_SILVER,
                                          alignment=TA_CENTER)),
        ]
        kpi_cells.append(cell_content)

    kpi_tbl = Table([kpi_cells], colWidths=[(W - 2 * MARGIN) / 5] * 5)
    kpi_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), _LIGHT_BG),
        ("TOPPADDING",   (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 12),
        ("INNERGRID",    (0, 0), (-1, -1), 0.5, _BORDER),
        ("BOX",          (0, 0), (-1, -1), 1,   _BORDER),
        ("ROUNDEDCORNERS", [6]),
    ]))
    story.append(kpi_tbl)
    story.append(Spacer(1, 0.6 * cm))

    # Fairness snapshot (if available)
    if audit and audit.get("total_candidates", 0) > 0:
        disp_count = len(audit.get("disparities", []))
        disp_str   = f"⚠️ {disp_count} disparity flag(s)" if disp_count else "✅ No disparities"
        story.append(Paragraph(
            f'<b>Fairness Audit:</b> {disp_str}. {audit.get("summary", "")}',
            S["BodySmall"]
        ))
        story.append(Spacer(1, 0.3 * cm))

    # JD preview
    if jd_snippet:
        story.append(Paragraph("<b>Job Description (excerpt):</b>", S["BodySmallBold"]))
        safe_jd = jd_snippet[:500].replace("<", "&lt;").replace(">", "&gt;")
        story.append(Paragraph(safe_jd + ("..." if len(jd_snippet) > 500 else ""),
                                S["BodySmall"]))

    story.append(PageBreak())


# ══════════════════════════════════════════════════════════════════════════════
# CANDIDATE CARD
# ══════════════════════════════════════════════════════════════════════════════

def _candidate_card(story, S, r: dict, is_qualified: bool):
    score      = r.get("score", 0)
    name       = r.get("name", "Unknown")
    exp        = r.get("exp", 0)
    has_deg    = r.get("has_deg", False)
    has_cert   = r.get("has_cert", False)
    is_premier = r.get("is_premier", False)
    strengths  = r.get("groq_strengths", [])
    red_flags  = r.get("groq_red_flags", [])
    reasoning  = r.get("groq_reasoning") or r.get("justification", "")
    matched_kw = r.get("matched_kw", [])
    missing_kw = r.get("missing_kw", [])
    fail_reasons = r.get("fail_reasons", [])
    rank       = r.get("rank")
    score_col  = _score_color(score)

    # Card header row: name + score badge
    rank_str = f"Rank #{rank}" if rank else "Filtered Out"
    rank_col = _SAGE if is_qualified else _ROSE

    header_left = [
        Paragraph(f'<b>{name}</b>', S["CandName"]),
        Paragraph(f'{rank_str}  ·  {exp} yr(s) exp  ·  '
                  f'{"✓ Degree" if has_deg else "✗ Degree"}  ·  '
                  f'{"✓ Cert" if has_cert else "✗ Cert"}  ·  '
                  f'{"★ Premier" if is_premier else "Non-premier"}',
                  S["BodySmall"]),
    ]
    header_right = [
        Paragraph(
            f'<font color="#{score_col.hexval()[2:]}"><b>{score:.1f}%</b></font>',
            ParagraphStyle("ScoreLarge", fontName="Helvetica-Bold", fontSize=22,
                           leading=26, alignment=TA_RIGHT, textColor=score_col)
        ),
        Paragraph("match score",
                  ParagraphStyle("ScoreLabel", fontName="Helvetica", fontSize=8,
                                 leading=10, alignment=TA_RIGHT, textColor=_SILVER)),
    ]

    hdr_tbl = Table([[header_left, header_right]],
                    colWidths=[W - 2 * MARGIN - 3 * cm, 3 * cm])
    hdr_color = HexColor("#EFF6FF") if is_qualified else HexColor("#FFF1F2")
    hdr_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), hdr_color),
        ("TOPPADDING",   (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 10),
        ("LEFTPADDING",  (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("BOX",          (0, 0), (-1, -1), 1.5, rank_col),
        ("ROUNDEDCORNERS", [6]),
    ]))
    story.append(KeepTogether([hdr_tbl]))
    story.append(Spacer(1, 0.2 * cm))

    # Body: reasoning / justification
    if reasoning:
        safe_r = reasoning[:400].replace("<", "&lt;").replace(">", "&gt;")
        story.append(Paragraph(safe_r, S["BodySmall"]))
        story.append(Spacer(1, 0.15 * cm))

    # Strengths + Red flags in two columns
    if strengths or red_flags or fail_reasons:
        s_text = ("<b>Strengths:</b> " + ", ".join(strengths[:3])) if strengths else ""
        f_list = (red_flags[:2] if red_flags else []) + (fail_reasons[:2] if fail_reasons else [])
        f_text = ("<b>Concerns:</b> " + "; ".join(f_list)) if f_list else ""

        sf_data = [[
            Paragraph(s_text or "—", S["BodySmall"]),
            Paragraph(f_text or "—", S["BodySmall"]),
        ]]
        sf_tbl = Table(sf_data, colWidths=[(W - 2 * MARGIN) / 2] * 2)
        sf_tbl.setStyle(TableStyle([
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(sf_tbl)

    # Keywords
    if matched_kw or missing_kw:
        kw_parts = []
        if matched_kw:
            kw_parts.append(f'<b>JD keywords matched:</b> {", ".join(matched_kw[:6])}')
        if missing_kw:
            kw_parts.append(f'<b>Missing:</b> {", ".join(missing_kw[:4])}')
        story.append(Paragraph("  ·  ".join(kw_parts), S["BodySmall"]))

    story.append(HRFlowable(width="100%", thickness=0.5, color=_BORDER, spaceAfter=8))
    story.append(Spacer(1, 0.2 * cm))


# ══════════════════════════════════════════════════════════════════════════════
# FILTERED CANDIDATES TABLE
# ══════════════════════════════════════════════════════════════════════════════

def _filtered_table(story, S, filtered: list[dict]):
    if not filtered:
        return
    story.append(Paragraph("Filtered-Out Candidates", S["SectionHeading"]))

    headers = ["Name", "Score", "Exp (yrs)", "Degree", "Reason(s)"]
    rows    = [headers]
    for r in filtered:
        rows.append([
            r.get("name", "?"),
            f'{r.get("score", 0):.0f}%',
            str(r.get("exp", 0)),
            "✓" if r.get("has_deg") else "✗",
            "; ".join(r.get("fail_reasons", ["—"]))[:80],
        ])

    col_w = [3.5 * cm, 1.8 * cm, 2 * cm, 2 * cm, W - 2 * MARGIN - 9.3 * cm]
    tbl = Table(rows, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),   _NAVY),
        ("TEXTCOLOR",    (0, 0), (-1, 0),   white),
        ("FONTNAME",     (0, 0), (-1, 0),   "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1),  8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#F8FAFC"), white]),
        ("INNERGRID",    (0, 0), (-1, -1),  0.5, _BORDER),
        ("BOX",          (0, 0), (-1, -1),  1,   _BORDER),
        ("TOPPADDING",   (0, 0), (-1, -1),  5),
        ("BOTTOMPADDING",(0, 0), (-1, -1),  5),
        ("LEFTPADDING",  (0, 0), (-1, -1),  6),
        ("RIGHTPADDING", (0, 0), (-1, -1),  6),
        ("VALIGN",       (0, 0), (-1, -1),  "MIDDLE"),
        ("WORDWRAP",     (4, 1), (4, -1),   True),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 0.4 * cm))


# ══════════════════════════════════════════════════════════════════════════════
# FAIRNESS AUDIT PAGE
# ══════════════════════════════════════════════════════════════════════════════

def _fairness_page(story, S, audit: dict):
    if not audit or not audit.get("total_candidates"):
        return

    story.append(PageBreak())
    story.append(Paragraph("Fairness & Equity Audit Snapshot", S["SectionHeading"]))
    story.append(Paragraph(audit.get("summary", ""), S["BodySmall"]))
    story.append(Spacer(1, 0.3 * cm))

    # Institution tier table
    tier = audit.get("institution_tier", {})
    if tier:
        story.append(Paragraph("<b>Institution Tier Breakdown</b>", S["BodySmallBold"]))
        t_data = [["Group", "Count", "Pass Rate", "Avg Score"]]
        for grp, d in tier.items():
            t_data.append([grp, str(d["count"]), f'{d["pass_rate"]}%', f'{d["avg_score"]}%'])
        t_tbl = Table(t_data, colWidths=[5 * cm, 2.5 * cm, 3 * cm, 3 * cm])
        t_tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0),  _NAVY),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  white),
            ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, -1), 9),
            ("INNERGRID",    (0, 0), (-1, -1), 0.5, _BORDER),
            ("BOX",          (0, 0), (-1, -1), 1,   _BORDER),
            ("TOPPADDING",   (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#F8FAFC"), white]),
        ]))
        story.append(t_tbl)
        story.append(Spacer(1, 0.3 * cm))

    # Disparity flags
    disparities = audit.get("disparities", [])
    if disparities:
        story.append(Paragraph("<b>Flagged Disparities</b>", S["BodySmallBold"]))
        for d in disparities:
            sev_col = "#EF4444" if d["severity"] == "High" else "#F59E0B"
            story.append(Paragraph(
                f'<font color="{sev_col}">⚠ {d["severity"]}</font>  —  '
                f'{d["metric"]}: {d["gap"]}pp gap',
                S["BodySmall"]
            ))

    # Recommendations
    recs = audit.get("recommendations", [])
    if recs:
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph("<b>Recommendations</b>", S["BodySmallBold"]))
        for rec in recs:
            story.append(Paragraph(f"• {rec}", S["BodySmall"]))


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: generate_executive_pdf()
# ══════════════════════════════════════════════════════════════════════════════

def generate_executive_pdf(
    results: list[dict],
    job_description: str = "",
    school_name: str = "School",
    role_title: str = "Teaching Position",
    hr_name: str = "",
    audit: dict | None = None,
) -> bytes | None:
    """
    Build a PDF executive summary and return it as bytes.
    Returns None if reportlab is not installed.

    Parameters
    ----------
    results         : full screener output (qualified + filtered)
    job_description : full JD text (used for excerpt)
    school_name     : shown on cover
    role_title      : shown on cover
    hr_name         : optional HR contact on cover
    audit           : output of fairness_audit.run_fairness_audit()
    """
    if not _REPORTLAB:
        return None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        title=f"{school_name} — {role_title} Screening Report",
        author=hr_name or "EduHire Screener",
    )

    S     = _styles()
    story = []

    qualified = sorted([r for r in results if not r.get("filtered")],
                       key=lambda x: x.get("rank", 999))
    filtered  = [r for r in results if r.get("filtered")]

    # ── Cover page ────────────────────────────────────────────────────────
    _cover_page(story, S, school_name, role_title, results, job_description, audit)

    # ── Shortlisted candidates ────────────────────────────────────────────
    if qualified:
        story.append(Paragraph(
            f"Shortlisted Candidates ({len(qualified)})",
            S["SectionHeading"]
        ))
        story.append(Spacer(1, 0.2 * cm))
        for r in qualified:
            _candidate_card(story, S, r, is_qualified=True)
    else:
        story.append(Paragraph("No candidates passed the screening filters.", S["BodySmall"]))

    # ── Filtered candidates table ─────────────────────────────────────────
    if filtered:
        story.append(PageBreak())
        _filtered_table(story, S, filtered)

    # ── Fairness audit page ───────────────────────────────────────────────
    if audit:
        _fairness_page(story, S, audit)

    # ── Footer helper via doc template ───────────────────────────────────
    def _add_footer(canvas, doc):
        canvas.saveState()
        today = datetime.date.today().strftime("%d %B %Y")
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(_SILVER)
        canvas.drawCentredString(
            W / 2, 1 * cm,
            f"EduHire Screener  ·  {school_name}  ·  Generated {today}  ·  Page {doc.page}"
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_add_footer, onLaterPages=_add_footer)
    buf.seek(0)
    return buf.read()
