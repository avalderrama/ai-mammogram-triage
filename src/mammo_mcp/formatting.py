"""Human-readable (Spanish) summaries and JSON payloads for tool results."""
from __future__ import annotations

from dataclasses import asdict

from mammo_application.analyses import AnalysisResultDto, MammogramCheckDto

DISCLAIMER = ("Resultado orientativo de una herramienta de investigación. No sustituye la lectura del "
              "radiólogo ni es un dispositivo médico aprobado.")

LEVEL_TEXT = {"high_priority": "ALTA PRIORIDAD", "review": "REVISAR",
              "no_suspicious_findings": "SIN HALLAZGOS SOSPECHOSOS"}
SIDE_TEXT = {"R": "Mama derecha", "L": "Mama izquierda"}
QUALITY_TEXT = {
    "low_resolution": "Resolución baja: acércate o usa la imagen original.",
    "blurry": "Foto borrosa: enfoca tocando la mama en la pantalla del celular.",
    "glare": "Reflejos o zonas quemadas: evita luces sobre la pantalla y no uses flash.",
    "small_breast": "La mama ocupa poco de la foto: recórtala o acércate.",
}


def masked_regions_text(count: int) -> str:
    return "Se tapó 1 zona de texto" if count == 1 else f"Se taparon {count} zonas de texto"


def analysis_payload(result: AnalysisResultDto) -> dict:
    """JSON-safe structured content (heatmaps travel as separate image content blocks)."""
    data = asdict(result)
    for breast in data["breasts"]:
        for view in breast["views"]:
            view["has_heatmap"] = view.pop("heatmap") is not None
    data["disclaimer"] = DISCLAIMER
    return data


def analysis_markdown(result: AnalysisResultDto) -> str:
    lines = [f"## Resultado: {LEVEL_TEXT[result.overall_level]}", ""]
    for b in result.breasts:
        name = SIDE_TEXT.get(b.side, f"Imagen ({b.label})")
        p = b.probabilities
        lines.append(f"### {name}: {LEVEL_TEXT[b.level]}")
        lines.append(f"- Probabilidades: BI-RADS 1-2 {p['birads_1_2']:.0%} · BI-RADS 3 {p['birads_3']:.0%} · "
                     f"BI-RADS 4-5 {p['birads_4_5']:.0%}")
        lines.append("- Modelo: " + ("global + parches de alta resolución (con mapa de calor)"
                                     if b.scoring_mode == "fused" else "global (resolución insuficiente para parches)"))
        for q in b.quality_issues:
            lines.append(f"- Aviso de calidad: {QUALITY_TEXT.get(q, q)}")
        lines.append("")
    if result.rejected_panels:
        lines.append("Paneles descartados por no parecer mamografía: "
                     + ", ".join(str(r.panel) for r in result.rejected_panels) + ".")
    if result.masked_text_regions:
        lines.append(masked_regions_text(result.masked_text_regions) + " (posibles datos personales).")
    lines += ["", f"_{DISCLAIMER}_", f"Versión del modelo: {result.model_version}"]
    return "\n".join(lines)


def check_markdown(result: MammogramCheckDto) -> str:
    lines = ["## Verificación de la imagen"]
    for p in result.panels:
        state = "es mamografía" if p.is_mammogram else "NO es mamografía"
        lines.append(f"- Panel {p.panel}: {state} (confianza {p.mammogram_confidence:.0%})")
        for q in p.quality_issues:
            lines.append(f"  - {QUALITY_TEXT.get(q, q)}")
    return "\n".join(lines)
