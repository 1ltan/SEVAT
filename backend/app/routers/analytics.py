import io
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, and_, text, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Detection, Camera
from app.schemas import APIResponse, ReportRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary", response_model=APIResponse)
async def get_summary(db: AsyncSession = Depends(get_db)):
    r_confirmed = await db.execute(
        select(func.count()).where(Detection.status.in_(["CONFIRMED", "ARCHIVED"]))
    )
    total_confirmed = r_confirmed.scalar()

    r_archived = await db.execute(
        select(func.count()).where(Detection.status == "ARCHIVED")
    )
    auto_confirmed = r_archived.scalar()

    r_op_confirmed = await db.execute(
        select(func.count()).where(Detection.status == "CONFIRMED")
    )
    op_confirmed = r_op_confirmed.scalar()

    r_rejected = await db.execute(
        select(func.count()).where(Detection.status == "TRASH")
    )
    rejected = r_rejected.scalar()

    r_pending = await db.execute(
        select(func.count()).where(Detection.status == "PENDING")
    )
    pending = r_pending.scalar()

    # Use operator correction if available, otherwise fall back to AI class_name
    effective_class = func.coalesce(Detection.operator_correction, Detection.class_name)
    r_classes = await db.execute(
        select(effective_class.label("effective_class"), func.count().label("cnt"))
        .where(Detection.status.in_(["CONFIRMED", "ARCHIVED"]))
        .group_by(effective_class)
        .order_by(func.count().desc())
    )
    class_breakdown = [{"class_name": r[0], "count": r[1]} for r in r_classes.all()]

    return APIResponse(data={
        "total_confirmed": total_confirmed,
        "auto_confirmed": auto_confirmed,
        "operator_confirmed": op_confirmed,
        "rejected": rejected,
        "pending": pending,
        "class_breakdown": class_breakdown,
    })


@router.post("/report", response_model=APIResponse)
async def generate_report(body: ReportRequest, db: AsyncSession = Depends(get_db)):
    filters = [
        Detection.detected_at >= body.date_from,
        Detection.detected_at <= body.date_to,
        Detection.status.in_(["CONFIRMED", "ARCHIVED"]),
    ]
    if body.camera_ids:
        filters.append(Detection.camera_id.in_(body.camera_ids))
    if body.class_names:
        filters.append(Detection.class_name.in_(body.class_names))

    if body.group_by == "day":
        day_expr = func.date_trunc("day", Detection.detected_at)
        stmt = (
            select(
                day_expr.label("period"),
                func.count().label("count"),
            )
            .where(and_(*filters))
            .group_by(day_expr)
            .order_by(day_expr)
        )
    elif body.group_by == "camera":
        stmt = (
            select(
                Camera.name.label("period"),
                func.count().label("count"),
            )
            .join(Camera, Camera.id == Detection.camera_id)
            .where(and_(*filters))
            .group_by(Camera.name)
            .order_by(func.count().desc())
        )
    elif body.group_by == "class":
        # Use operator correction if available, otherwise fall back to AI class_name
        effective_class = func.coalesce(Detection.operator_correction, Detection.class_name)
        stmt = (
            select(
                effective_class.label("period"),
                func.count().label("count"),
            )
            .where(and_(*filters))
            .group_by(effective_class)
            .order_by(func.count().desc())
        )
    else:
        stmt = (
            select(func.count().label("count"))
            .where(and_(*filters))
        )

    result = await db.execute(stmt)
    rows = result.all()
    data = [{"label": str(r[0]), "count": r[1]} for r in rows]
    return APIResponse(data=data)


# ─── PDF Report ───────────────────────────────────────────────────────────────

# ── Strings (English only) ────────────────────────────────────────────────────
T = {
    # Header
    "sys_title":    "SEVAT — SYSTEM FOR ENEMY VEHICLE ACQUISITION AND TRACKING",
    "report_title": "Detection Incident Report",
    "period":       "Period",
    "generated":    "Generated",
    # Summary
    "total":        "Total detections in report:",
    "confirmed":    "Confirmed:",
    "pending":      "Pending review:",
    "high":         "HIGH level:",
    "critical":     "CRITICAL level:",
    # Section headings
    "detections":   "DETECTIONS LIST",
    # Column headers
    "col_no":       "No",
    "col_id":       "ID",
    "col_type":     "Threat type",
    "col_camera":   "Camera",
    "col_location": "Location",
    "col_coords":   "Coordinates",
    "col_datetime": "Date & Time",
    "col_conf":     "Conf.",
    "col_status":   "Status",
    "col_threat":   "Threat level",
    "col_desc":     "Threat description",
    "col_corr":     "Operator correction",
    # Footer
    "footer":       "Document generated automatically by SEVAT system. Generated at: {dt}. This document is confidential and must not be disclosed.",
    # Status labels
    "status": {
        "CONFIRMED": "Confirmed by operator",
        "ARCHIVED":  "Confirmed by system (auto)",
        "TRASH":     "Rejected / deleted",
        "PENDING":   "Pending review",
    },
    # Operator correction labels
    "correction": {
        "confirm": "Confirmed by operator",
        "reject":  "Rejected by operator",
        "correct": "Corrected by operator",
    },
    # Class names
    "class": {
        "TANK":    "Tank",
        "APC":     "APC (APC)",
        "IFV":     "IFV (BMP)",
        "APC-IFV": "IFV (BMP)",
        "CAR":     "Car",
        "TRUCK":   "Truck",
        "ART":     "Artillery",
        "MLRS":    "MLRS",
    },
    # Threat levels
    "threat": {
        "HIGH":     "High",
        "MEDIUM":   "Medium",
        "LOW":      "Low",
        "CRITICAL": "Critical",
    },
}


def _build_pdf(
    incidents: list,
    camera_stats: list,
    date_from: str,
    date_to: str,
    generated_at: str,
) -> bytes:
    """Build a PDF report using reportlab and return as bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable,
    )
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import os

    # Try to register a Unicode font; fall back to built-in if unavailable
    FONT_NAME = "Helvetica"
    FONT_BOLD = "Helvetica-Bold"

    for font_path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/app/DejaVuSans.ttf",
    ]:
        if os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(TTFont("DejaVu", font_path))
                bold_path = font_path.replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
                if os.path.exists(bold_path):
                    pdfmetrics.registerFont(TTFont("DejaVu-Bold", bold_path))
                    FONT_BOLD = "DejaVu-Bold"
                FONT_NAME = "DejaVu"
                break
            except Exception:
                pass

    RED    = colors.HexColor("#CC0000")
    BLACK  = colors.HexColor("#111111")
    GREY   = colors.HexColor("#666666")
    WHITE  = colors.white
    ROW_A  = colors.HexColor("#FFFFFF")   # odd rows  — white
    ROW_B  = colors.HexColor("#F5F5F5")   # even rows — very light grey
    HEADER_BG = RED

    # ─── Styles ───────────────────────────────────────────────────────────────
    title_style = ParagraphStyle(
        "ReportTitle", fontName=FONT_BOLD, fontSize=14,
        textColor=RED, spaceAfter=2, leading=18,
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle", fontName=FONT_NAME, fontSize=8,
        textColor=GREY, spaceAfter=10,
    )
    section_style = ParagraphStyle(
        "Section", fontName=FONT_BOLD, fontSize=10,
        textColor=RED, spaceBefore=12, spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "Meta", fontName=FONT_NAME, fontSize=8,
        textColor=BLACK, leading=12,
    )
    hdr_style = ParagraphStyle(
        "Hdr", fontName=FONT_BOLD, fontSize=7,
        textColor=WHITE, leading=9,
    )
    cell_style = ParagraphStyle(
        "Cell", fontName=FONT_NAME, fontSize=7,
        textColor=BLACK, leading=9,
    )
    cell_bold_style = ParagraphStyle(
        "CellBold", fontName=FONT_BOLD, fontSize=7,
        textColor=BLACK, leading=9,
    )
    footer_style = ParagraphStyle(
        "Footer", fontName=FONT_NAME, fontSize=6,
        textColor=GREY, leading=8,
    )

    # ─── Document (Landscape A4 to fit all columns) ───────────────────────────
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
    )
    W = landscape(A4)[0] - 24 * mm   # usable page width

    elements = []

    # ─── Header ───────────────────────────────────────────────────────────────
    elements.append(Paragraph(T["sys_title"], title_style))
    elements.append(Paragraph(
        f"{T['report_title']}   |   {T['period']}: {date_from} — {date_to}"
        f"   |   {T['generated']}: {generated_at}",
        subtitle_style,
    ))
    elements.append(HRFlowable(
        width="100%", thickness=1.2, color=RED, spaceAfter=8,
    ))

    # ─── Summary block ────────────────────────────────────────────────────────
    total      = len(incidents)
    confirmed  = sum(1 for i in incidents if i["status"] in ("CONFIRMED", "ARCHIVED"))
    pending    = sum(1 for i in incidents if i["status"] == "PENDING")
    high_th    = sum(1 for i in incidents if (i.get("threat_level") or "") == "HIGH")
    critical   = sum(1 for i in incidents if (i.get("threat_level") or "") == "CRITICAL")

    summary_rows = [
        [
            Paragraph(T["total"],    cell_bold_style),
            Paragraph(str(total),    cell_bold_style),
            Paragraph(T["confirmed"], cell_bold_style),
            Paragraph(str(confirmed), cell_bold_style),
            Paragraph(T["pending"],  cell_bold_style),
            Paragraph(str(pending),  cell_bold_style),
            Paragraph(T["high"],     cell_bold_style),
            Paragraph(str(high_th),  cell_bold_style),
            Paragraph(T["critical"], cell_bold_style),
            Paragraph(str(critical), cell_bold_style),
        ]
    ]
    summary_table = Table(summary_rows, colWidths=[None] * 10)
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0F0F0")),
        ("BOX",        (0, 0), (-1, -1), 0.8, colors.HexColor("#CCCCCC")),
        ("PADDING",    (0, 0), (-1, -1), 5),
        ("TEXTCOLOR",  (0, 0), (-1, -1), BLACK),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 10))

    # ─── Detections table ─────────────────────────────────────────────────────
    elements.append(Paragraph(T["detections"], section_style))

    # Column widths (total = W)
    col_widths = [
        8  * mm,   # No
        10 * mm,   # ID
        22 * mm,   # Type
        28 * mm,   # Camera
        28 * mm,   # Location
        22 * mm,   # Coords
        24 * mm,   # DateTime
        16 * mm,   # Confidence
        26 * mm,   # Status
        22 * mm,   # Threat level
        None,      # Threat description (fills rest)
        24 * mm,   # Operator correction
    ]

    header_row = [
        Paragraph(T["col_no"],       hdr_style),
        Paragraph(T["col_id"],       hdr_style),
        Paragraph(T["col_type"],     hdr_style),
        Paragraph(T["col_camera"],   hdr_style),
        Paragraph(T["col_location"], hdr_style),
        Paragraph(T["col_coords"],   hdr_style),
        Paragraph(T["col_datetime"], hdr_style),
        Paragraph(T["col_conf"],     hdr_style),
        Paragraph(T["col_status"],   hdr_style),
        Paragraph(T["col_threat"],   hdr_style),
        Paragraph(T["col_desc"],     hdr_style),
        Paragraph(T["col_corr"],     hdr_style),
    ]
    table_rows = [header_row]

    for idx, inc in enumerate(incidents, 1):
        # Prefer operator_correction over AI class_name if available
        cls_raw    = inc.get("operator_correction") or inc.get("class_name", "")
        class_lbl  = T["class"].get(cls_raw, cls_raw or "—")
        cam_name   = inc.get("camera_name") or "—"
        location   = inc.get("camera_location") or "—"

        lat = inc.get("latitude")
        lon = inc.get("longitude")
        coords = f"{lat:.5f}\n{lon:.5f}" if lat is not None and lon is not None else "—"

        dt = inc.get("detected_at")
        if isinstance(dt, datetime):
            dt_str = dt.strftime("%d.%m.%Y\n%H:%M:%S UTC")
        else:
            dt_str = str(dt)[:19].replace("T", "\n") if dt else "—"

        conf_pct   = f"{int((inc.get('confidence') or 0) * 100)}%"
        status_lbl = T["status"].get(inc.get("status") or "", inc.get("status") or "—")
        threat_lbl = T["threat"].get(inc.get("threat_level") or "", inc.get("threat_level") or "—")
        reasoning  = inc.get("threat_reasoning") or "—"
        corr_raw   = inc.get("operator_correction") or ""
        corr_lbl   = T["correction"].get(corr_raw, corr_raw) if corr_raw else "—"

        table_rows.append([
            Paragraph(str(idx),    cell_style),
            Paragraph(str(inc.get("id", "—")), cell_style),
            Paragraph(class_lbl,   cell_bold_style),
            Paragraph(cam_name,    cell_style),
            Paragraph(location,    cell_style),
            Paragraph(coords,      cell_style),
            Paragraph(dt_str,      cell_style),
            Paragraph(conf_pct,    cell_style),
            Paragraph(status_lbl,  cell_style),
            Paragraph(threat_lbl,  cell_style),
            Paragraph(reasoning,   cell_style),
            Paragraph(corr_lbl,    cell_style),
        ])

    tbl = Table(table_rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR",  (0, 0), (-1, 0), WHITE),
        ("FONTNAME",   (0, 0), (-1, 0), FONT_BOLD),
        # Alternating row backgrounds — white/light grey
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ROW_A, ROW_B]),
        ("TEXTCOLOR",  (0, 1), (-1, -1), BLACK),
        # Grid
        ("GRID",       (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("BOX",        (0, 0), (-1, -1), 0.8, colors.HexColor("#AAAAAA")),
        # Alignment
        ("VALIGN",     (0, 0), (-1, -1), "TOP"),
        ("PADDING",    (0, 0), (-1, -1), 4),
        ("ALIGN",      (7, 1), (7, -1), "CENTER"),
        ("ALIGN",      (0, 0), (1, -1), "CENTER"),
    ]))
    elements.append(tbl)

    # ─── Footer ───────────────────────────────────────────────────────────────
    elements.append(Spacer(1, 16))
    elements.append(HRFlowable(
        width="100%", thickness=0.5,
        color=colors.HexColor("#CCCCCC"), spaceAfter=4,
    ))
    elements.append(Paragraph(
        T["footer"].format(dt=generated_at),
        footer_style,
    ))

    doc.build(elements)
    return buf.getvalue()


@router.post("/report/pdf")
async def generate_pdf_report(body: ReportRequest, db: AsyncSession = Depends(get_db)):
    """Generate a full PDF incident report."""
    filters = [
        Detection.detected_at >= body.date_from,
        Detection.detected_at <= body.date_to,
        Detection.status.in_(["CONFIRMED", "ARCHIVED"]),
    ]
    if body.camera_ids:
        filters.append(Detection.camera_id.in_(body.camera_ids))
    if body.class_names:
        filters.append(Detection.class_name.in_(body.class_names))

    # Fetch all matching incidents with camera data
    stmt = (
        select(
            Detection.id,
            Detection.detected_at,
            Detection.class_name,
            Detection.confidence,
            Detection.status,
            Detection.operator_correction,
            Detection.threat_level,
            Detection.threat_reasoning,
            Camera.name.label("camera_name"),
            Camera.location_name.label("camera_location"),
            Camera.latitude,
            Camera.longitude,
        )
        .join(Camera, Camera.id == Detection.camera_id)
        .where(and_(*filters))
        .order_by(Detection.detected_at.desc())
        .limit(2000)
    )
    result = await db.execute(stmt)
    rows = result.all()

    incidents = [
        {
            "id": r[0],
            "detected_at": r[1],
            "class_name": r[2],
            "confidence": r[3],
            "status": r[4],
            "operator_correction": r[5],
            "threat_level": r[6],
            "threat_reasoning": r[7],
            "camera_name": r[8],
            "camera_location": r[9],
            "latitude": r[10],
            "longitude": r[11],
        }
        for r in rows
    ]

    # Camera-level statistics
    cam_filter = [
        Detection.detected_at >= body.date_from,
        Detection.detected_at <= body.date_to,
    ]
    if body.camera_ids:
        cam_filter.append(Detection.camera_id.in_(body.camera_ids))

    cam_stmt = (
        select(
            Camera.id,
            Camera.name,
            Camera.location_name,
            Camera.latitude,
            Camera.longitude,
            func.count(Detection.id).label("total"),
            func.sum(
                case((Detection.status.in_(["CONFIRMED", "ARCHIVED"]), 1), else_=0)
            ).label("confirmed"),
            func.sum(
                case((Detection.status == "TRASH", 1), else_=0)
            ).label("rejected"),
            func.avg(Detection.confidence).label("avg_confidence"),
        )
        .join(Detection, Detection.camera_id == Camera.id, isouter=True)
        .where(and_(*cam_filter))
        .group_by(Camera.id, Camera.name, Camera.location_name, Camera.latitude, Camera.longitude)
        .order_by(func.count(Detection.id).desc())
    )
    cam_result = await db.execute(cam_stmt)
    cam_rows = cam_result.all()

    camera_stats = [
        {
            "id": r[0],
            "name": r[1],
            "location_name": r[2],
            "latitude": r[3],
            "longitude": r[4],
            "total": r[5] or 0,
            "confirmed": int(r[6] or 0),
            "rejected": int(r[7] or 0),
            "avg_confidence": float(r[8]) if r[8] is not None else None,
        }
        for r in cam_rows
    ]

    date_from_str = body.date_from.strftime("%d.%m.%Y")
    date_to_str = body.date_to.strftime("%d.%m.%Y")
    generated_at = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")

    # Build PDF in thread so we don't block the event loop
    import asyncio
    loop = asyncio.get_event_loop()
    pdf_bytes = await loop.run_in_executor(
        None,
        lambda: _build_pdf(incidents, camera_stats, date_from_str, date_to_str, generated_at),
    )

    filename = f"SEVAT_report_{date_from_str.replace('.', '')}_{date_to_str.replace('.', '')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
