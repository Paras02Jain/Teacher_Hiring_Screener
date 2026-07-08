"""
visualizations.py — EduHire Visual Analytics Dashboard
========================================================
Provides all Plotly-based charts for the recruiter dashboard.
Each function takes the `results` list from ResumeScreener.screen()
and returns a Plotly figure ready for st.plotly_chart().
"""

from __future__ import annotations
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd

# ── Shared theme ───────────────────────────────────────────────────────────────
THEME = dict(
    bg         = "rgba(13,27,42,0)",      # transparent (inherits Streamlit bg)
    paper_bg   = "rgba(13,33,64,0.85)",
    grid_color = "rgba(30,58,95,0.6)",
    font_color = "#A8C5E8",
    title_font = dict(color="#E8EFF8", size=14, family="DM Sans"),
    axis_font  = dict(color="#7A9DC0", size=11),
    sky        = "#0EA5E9",
    teal       = "#0F766E",
    sage       = "#10B981",
    gold       = "#F59E0B",
    rose       = "#F43F5E",
    silver     = "#94A3B8",
    violet     = "#8B5CF6",
    amber      = "#F97316",
)

QUAL_GRADIENT = ["#0EA5E9", "#0F766E", "#10B981", "#F59E0B", "#8B5CF6", "#F97316"]
STATUS_COLORS = {True: "#F43F5E", False: "#10B981"}  # filtered=True → rose, False → sage


def _apply_dark_layout(fig, title="", height=380):
    fig.update_layout(
        title=dict(text=title, font=THEME["title_font"], x=0.02, xanchor="left"),
        height=height,
        paper_bgcolor=THEME["paper_bg"],
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=THEME["font_color"], family="DM Sans"),
        margin=dict(l=20, r=20, t=48, b=20),
        legend=dict(
            bgcolor="rgba(13,27,42,0.6)",
            bordercolor="rgba(30,58,95,0.5)",
            borderwidth=1,
            font=dict(color="#A8C5E8", size=11),
        ),
    )
    fig.update_xaxes(
        gridcolor=THEME["grid_color"], zerolinecolor=THEME["grid_color"],
        tickfont=THEME["axis_font"], showgrid=True,
    )
    fig.update_yaxes(
        gridcolor=THEME["grid_color"], zerolinecolor=THEME["grid_color"],
        tickfont=THEME["axis_font"], showgrid=True,
    )
    return fig


def _to_df(results: list[dict]) -> pd.DataFrame:
    rows = []
    for r in results:
        rows.append({
            "name":       r.get("name", "Unknown"),
            "score":      r.get("score", 0),
            "exp":        r.get("exp", 0),
            "has_deg":    r.get("has_deg", False),
            "has_cert":   r.get("has_cert", False),
            "is_premier": r.get("is_premier", False),
            "filtered":   r.get("filtered", False),
            "rank":       r.get("rank"),
            "tags":       r.get("tags", []),
            "matched_kw": r.get("matched_kw", []),
            "missing_kw": r.get("missing_kw", []),
            "fail_reasons": r.get("fail_reasons", []),
            "status":     "Filtered Out" if r.get("filtered") else "Qualified",
        })
    return pd.DataFrame(rows)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. CANDIDATE SCORE BAR CHART (horizontal, colour-coded by pass/fail)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_score_bar(results: list[dict]) -> go.Figure:
    df = _to_df(results).sort_values("score", ascending=True)
    colors = [STATUS_COLORS[f] for f in df["filtered"]]

    fig = go.Figure(go.Bar(
        x=df["score"],
        y=df["name"],
        orientation="h",
        marker=dict(
            color=colors,
            line=dict(width=0),
            opacity=0.88,
        ),
        text=[f"{s:.1f}%" for s in df["score"]],
        textposition="outside",
        textfont=dict(color="#E8EFF8", size=11),
        customdata=df[["status", "exp"]].values,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Score: %{x:.1f}%<br>"
            "Status: %{customdata[0]}<br>"
            "Experience: %{customdata[1]} yr(s)<extra></extra>"
        ),
    ))

    # Threshold reference line at min_score
    min_score = df[~df["filtered"]]["score"].min() if not df[~df["filtered"]].empty else 0
    fig.add_vline(
        x=min_score, line_dash="dot",
        line_color="rgba(245,158,11,0.6)", line_width=1.5,
        annotation_text=f"Min threshold {min_score:.0f}%",
        annotation_font=dict(color="#F59E0B", size=10),
        annotation_position="top",
    )

    _apply_dark_layout(fig, "📊 Candidate Match Scores", height=max(320, len(df) * 36 + 60))
    fig.update_xaxes(title_text="Match Score (%)", range=[0, max(df["score"].max() + 12, 100)])
    fig.update_yaxes(title_text="")

    # Legend annotation
    fig.add_annotation(
        text="🟢 Qualified  🔴 Filtered Out",
        xref="paper", yref="paper", x=0.99, y=-0.04,
        showarrow=False, font=dict(color="#7A9DC0", size=10), xanchor="right",
    )
    return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. SCORE vs EXPERIENCE BUBBLE CHART
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_score_vs_experience(results: list[dict]) -> go.Figure:
    df = _to_df(results)

    fig = go.Figure()
    for status, group in df.groupby("status"):
        color = THEME["sage"] if status == "Qualified" else THEME["rose"]
        fig.add_trace(go.Scatter(
            x=group["exp"],
            y=group["score"],
            mode="markers+text",
            name=status,
            marker=dict(
                size=18,
                color=color,
                opacity=0.85,
                line=dict(width=1.5, color="rgba(255,255,255,0.3)"),
            ),
            text=group["name"].apply(lambda n: n.split()[0]),
            textposition="top center",
            textfont=dict(color="#E8EFF8", size=9),
            customdata=group[["score", "exp", "status"]].values,
            hovertemplate=(
                "<b>%{text}</b><br>"
                "Score: %{customdata[0]:.1f}%<br>"
                "Experience: %{customdata[1]} yr(s)<br>"
                "Status: %{customdata[2]}<extra></extra>"
            ),
        ))

    _apply_dark_layout(fig, "🎯 Score vs. Teaching Experience", height=380)
    fig.update_xaxes(title_text="Teaching Experience (years)", dtick=1)
    fig.update_yaxes(title_text="Match Score (%)")
    return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. SKILLS FREQUENCY BAR (which skills appear most among ALL candidates)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_skills_frequency(results: list[dict]) -> go.Figure:
    skill_counts: dict[str, int] = {}
    for r in results:
        for tag in r.get("tags", []):
            skill_counts[tag] = skill_counts.get(tag, 0) + 1

    if not skill_counts:
        fig = go.Figure()
        fig.add_annotation(text="No skill data available", x=0.5, y=0.5,
                           xref="paper", yref="paper", showarrow=False,
                           font=dict(color="#7A9DC0", size=14))
        return _apply_dark_layout(fig, "🛠 Skill Distribution")

    df = pd.DataFrame(list(skill_counts.items()), columns=["skill", "count"]).sort_values("count")
    colors = [QUAL_GRADIENT[i % len(QUAL_GRADIENT)] for i in range(len(df))]

    fig = go.Figure(go.Bar(
        x=df["count"],
        y=df["skill"],
        orientation="h",
        marker=dict(color=colors, opacity=0.85, line=dict(width=0)),
        text=df["count"],
        textposition="outside",
        textfont=dict(color="#E8EFF8", size=11),
        hovertemplate="<b>%{y}</b>: %{x} candidate(s)<extra></extra>",
    ))
    _apply_dark_layout(fig, "🛠 Skill Distribution Across All Candidates", height=360)
    fig.update_xaxes(title_text="Number of Candidates", dtick=1)
    return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. JD KEYWORD GAP ANALYSIS (matched vs missing for qualified candidates)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_keyword_gap(results: list[dict]) -> go.Figure:
    matched_counts: dict[str, int] = {}
    missing_counts: dict[str, int] = {}

    qualified = [r for r in results if not r.get("filtered")]
    for r in qualified:
        for kw in r.get("matched_kw", []):
            matched_counts[kw] = matched_counts.get(kw, 0) + 1
        for kw in r.get("missing_kw", []):
            missing_counts[kw] = missing_counts.get(kw, 0) + 1

    all_kw = sorted(set(list(matched_counts.keys()) + list(missing_counts.keys())))
    if not all_kw:
        fig = go.Figure()
        fig.add_annotation(text="No keyword data available", x=0.5, y=0.5,
                           xref="paper", yref="paper", showarrow=False,
                           font=dict(color="#7A9DC0", size=14))
        return _apply_dark_layout(fig, "🔍 JD Keyword Gap Analysis")

    matched_vals = [matched_counts.get(kw, 0) for kw in all_kw]
    missing_vals = [missing_counts.get(kw, 0) for kw in all_kw]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="✅ Matched", x=all_kw, y=matched_vals,
        marker_color=THEME["sage"], opacity=0.85,
        hovertemplate="<b>%{x}</b><br>Matched by %{y} candidate(s)<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="❌ Missing", x=all_kw, y=missing_vals,
        marker_color=THEME["rose"], opacity=0.75,
        hovertemplate="<b>%{x}</b><br>Missing in %{y} candidate(s)<extra></extra>",
    ))
    fig.update_layout(barmode="group")
    _apply_dark_layout(fig, "🔍 JD Keyword Gap Analysis (Qualified Candidates)", height=400)
    fig.update_xaxes(title_text="JD Keyword", tickangle=-35)
    fig.update_yaxes(title_text="Number of Candidates", dtick=1)
    return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. CREDENTIAL BREAKDOWN PIE / DONUT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_credentials_donut(results: list[dict]) -> go.Figure:
    total    = len(results)
    with_deg = sum(1 for r in results if r.get("has_deg"))
    with_cert= sum(1 for r in results if r.get("has_cert"))
    premier  = sum(1 for r in results if r.get("is_premier"))

    labels = ["Has Degree", "No Degree", "Certified", "Not Certified", "Premier", "Non-Premier"]
    values = [with_deg, total - with_deg, with_cert, total - with_cert, premier, total - premier]
    colors = [THEME["sage"], THEME["rose"], THEME["sky"], THEME["silver"],
              THEME["violet"], "rgba(100,116,139,0.4)"]

    fig = make_subplots(rows=1, cols=3, specs=[[{"type": "pie"}] * 3],
                        subplot_titles=["Education Degree", "Certification", "Premier Institution"])

    for col, (lbl_pair, val_pair, col_pair) in enumerate([
        (labels[0:2], values[0:2], colors[0:2]),
        (labels[2:4], values[2:4], colors[2:4]),
        (labels[4:6], values[4:6], colors[4:6]),
    ], start=1):
        fig.add_trace(go.Pie(
            labels=lbl_pair,
            values=val_pair,
            marker_colors=col_pair,
            hole=0.55,
            textinfo="percent+label",
            textfont=dict(size=10, color="#E8EFF8"),
            hovertemplate="<b>%{label}</b>: %{value} candidates (%{percent})<extra></extra>",
            showlegend=False,
        ), row=1, col=col)

    _apply_dark_layout(fig, "🎓 Credential Breakdown", height=300)
    fig.update_layout(
        annotations=[dict(
            text=t, x=x, y=1.12, xref="paper", yref="paper",
            font=dict(color="#A8C5E8", size=12), showarrow=False, xanchor="center",
        ) for t, x in zip(["Education Degree", "Certification", "Premier Institution"],
                           [0.12, 0.5, 0.88])]
    )
    return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. RADAR CHART — Top 3 Candidates Compared
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_radar_top3(results: list[dict]) -> go.Figure:
    qualified = sorted([r for r in results if not r.get("filtered")], key=lambda x: -x["score"])[:3]

    if len(qualified) < 2:
        fig = go.Figure()
        fig.add_annotation(
            text="Need at least 2 qualified candidates for comparison",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(color="#7A9DC0", size=13)
        )
        return _apply_dark_layout(fig, "🕸 Top Candidates Radar Comparison")

    all_skills = ["curriculum design", "classroom management", "assessment & grading",
                  "ed-technology", "mentoring", "stem", "language arts", "research",
                  "early childhood", "special education"]
    categories = all_skills + [all_skills[0]]  # close the polygon

    colors_list = [THEME["sky"], THEME["gold"], THEME["violet"]]
    fig = go.Figure()

    for idx, r in enumerate(qualified):
        candidate_tags = [t.lower() for t in r.get("tags", [])]
        # Normalise axes: score, experience (max 10), degree, cert, premier, + skills
        score_norm = r["score"]
        exp_norm   = min(r.get("exp", 0) * 10, 100)
        deg_norm   = 100 if r.get("has_deg") else 0
        cert_norm  = 100 if r.get("has_cert") else 0
        prem_norm  = 100 if r.get("is_premier") else 0
        kw_norm    = min(len(r.get("matched_kw", [])) * 14, 100)

        skill_vals = [100 if skill in candidate_tags else 0 for skill in all_skills]
        # Aggregate axes: Score, Experience, Degree, Cert, Premier Institution, JD Keywords, + skills
        radar_axes  = ["Match Score", "Experience", "Degree", "Certification",
                       "Premier Inst.", "JD Keywords"] + all_skills
        radar_vals  = [score_norm, exp_norm, deg_norm, cert_norm, prem_norm, kw_norm] + skill_vals
        radar_axes  += [radar_axes[0]]
        radar_vals  += [radar_vals[0]]

        name = r.get("name", f"Candidate {idx+1}").split()[0]
        color = colors_list[idx % len(colors_list)]

        if color.startswith("#"):
            h = color.lstrip("#")
            fillcolor = f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},0.12)"
        elif "rgb" in color:
            fillcolor = color.replace(")", ",0.12)").replace("rgb", "rgba")
        else:
            fillcolor = color

        fig.add_trace(go.Scatterpolar(
            r=radar_vals,
            theta=radar_axes,
            fill="toself",
            name=f"#{r.get('rank')} {name} ({r['score']:.0f}%)",
            line=dict(color=color, width=2),
            fillcolor=fillcolor,
            opacity=0.9,
            hovertemplate="%{theta}: %{r:.0f}<extra></extra>",
        ))

    _apply_dark_layout(fig, "🕸 Top Candidates Multi-Dimension Comparison", height=480)
    fig.update_layout(
        polar=dict(
            bgcolor="rgba(13,33,64,0.5)",
            radialaxis=dict(
                visible=True, range=[0, 100],
                gridcolor="rgba(30,58,95,0.7)",
                tickfont=dict(color="#5A7A9A", size=8),
                tickvals=[0, 25, 50, 75, 100],
            ),
            angularaxis=dict(
                gridcolor="rgba(30,58,95,0.7)",
                tickfont=dict(color="#A8C5E8", size=9),
            ),
        ),
    )
    return fig

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. EXPERIENCE DISTRIBUTION HISTOGRAM
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_experience_distribution(results: list[dict]) -> go.Figure:
    qualified = [r for r in results if not r.get("filtered")]
    filtered  = [r for r in results if r.get("filtered")]

    fig = go.Figure()
    for grp, grp_name, color in [
        (qualified, "Qualified",    THEME["sage"]),
        (filtered,  "Filtered Out", THEME["rose"]),
    ]:
        exps = [r.get("exp", 0) for r in grp]
        if exps:
            fig.add_trace(go.Histogram(
                x=exps, name=grp_name,
                marker_color=color, opacity=0.75,
                xbins=dict(start=0, end=max(exps) + 2, size=1),
                hovertemplate=f"{grp_name} — %{{x}} yr(s): %{{y}} candidate(s)<extra></extra>",
            ))

    fig.update_layout(barmode="overlay")
    _apply_dark_layout(fig, "📅 Teaching Experience Distribution", height=320)
    fig.update_xaxes(title_text="Years of Teaching Experience", dtick=1)
    fig.update_yaxes(title_text="Number of Candidates", dtick=1)
    return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. SCORE DISTRIBUTION HISTOGRAM (pass/fail colour)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_score_distribution(results: list[dict]) -> go.Figure:
    qualified = [r["score"] for r in results if not r.get("filtered")]
    filtered  = [r["score"] for r in results if r.get("filtered")]

    fig = go.Figure()
    if qualified:
        fig.add_trace(go.Histogram(
            x=qualified, name="Qualified",
            marker_color=THEME["sage"], opacity=0.8,
            xbins=dict(start=0, end=100, size=5),
            hovertemplate="Score %{x}–%{x:.0f}%: %{y} candidate(s)<extra></extra>",
        ))
    if filtered:
        fig.add_trace(go.Histogram(
            x=filtered, name="Filtered Out",
            marker_color=THEME["rose"], opacity=0.7,
            xbins=dict(start=0, end=100, size=5),
            hovertemplate="Score %{x}–%{x:.0f}%: %{y} candidate(s)<extra></extra>",
        ))

    fig.update_layout(barmode="overlay")
    _apply_dark_layout(fig, "📈 Score Distribution", height=300)
    fig.update_xaxes(title_text="Match Score (%)", dtick=10)
    fig.update_yaxes(title_text="Number of Candidates", dtick=1)
    return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 10. CANDIDATE HEATMAP — All dimensions as a grid
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_candidate_heatmap(results: list[dict]) -> go.Figure:
    df = _to_df(results).sort_values("score", ascending=False)
    names = df["name"].tolist()

    dimensions = {
        "Score (%)":    df["score"].tolist(),
        "Experience":   [min(e * 10, 100) for e in df["exp"]],
        "Degree":       [100 if d else 0 for d in df["has_deg"]],
        "Certification":[100 if c else 0 for c in df["has_cert"]],
        "Premier Inst.":[100 if p else 0 for p in df["is_premier"]],
        "JD Match KW":  [min(len(r.get("matched_kw", [])) * 14, 100) for r in results
                         if True or not r.get("filtered")],  # all rows, same order as df
    }

    # Re-align JD Match KW to df order
    name_to_result = {r.get("name"): r for r in results}
    dimensions["JD Match KW"] = [
        min(len(name_to_result.get(n, {}).get("matched_kw", [])) * 14, 100)
        for n in names
    ]

    z  = list(dimensions.values())
    ys = list(dimensions.keys())

    fig = go.Figure(go.Heatmap(
        z=z,
        x=names,
        y=ys,
        colorscale=[
            [0.0,  "rgba(244,63,94,0.85)"],
            [0.35, "rgba(245,158,11,0.85)"],
            [0.65, "rgba(14,165,233,0.85)"],
            [1.0,  "rgba(16,185,129,0.90)"],
        ],
        zmin=0, zmax=100,
        text=[[f"{v:.0f}" for v in row] for row in z],
        texttemplate="%{text}",
        textfont=dict(size=10, color="white"),
        hovertemplate="<b>%{x}</b><br>%{y}: %{z:.0f}<extra></extra>",
        colorbar=dict(
            title=dict(text="Score (0–100)", font=dict(color="#A8C5E8", size=11)),
            tickfont=dict(color="#A8C5E8", size=10),
            bgcolor="rgba(13,33,64,0.7)",
            bordercolor="rgba(30,58,95,0.5)",
        ),
    ))
    _apply_dark_layout(fig, "🗺 Candidate Comparison Heatmap", height=max(320, len(ys) * 46 + 80))
    fig.update_xaxes(side="top", tickangle=-25, tickfont=dict(size=10))
    fig.update_yaxes(autorange="reversed")
    return fig
