"""Human-readable summaries (English by default, Spanish on request) and JSON payloads for tool results."""
from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from mammo_application.analyses import AnalysisResultDto, MammogramCheckDto

Language = Literal["en", "es"]

DISCLAIMER = ("Indicative result from a research tool. It does not replace the radiologist's reading and is not an "
              "approved medical device.")

_TEXT = {
    "en": {
        "disclaimer": DISCLAIMER,
        "levels": {"high_priority": "HIGH PRIORITY", "review": "REVIEW",
                   "no_suspicious_findings": "NO SUSPICIOUS FINDINGS"},
        "sides": {"R": "Right breast", "L": "Left breast"},
        "image": "Image ({label})",
        "result": "Result",
        "probabilities": "Probabilities",
        "model": "Model",
        "fused": "global + high-resolution patches (with heatmap)",
        "global_only": "global only (resolution too low for patches)",
        "quality_warning": "Quality warning",
        "quality": {
            "low_resolution": "Low resolution: move closer or use the original image.",
            "blurry": "Blurry photo: tap the breast on the phone screen to focus.",
            "glare": "Glare or burnt-out areas: avoid lights on the screen and don't use the flash.",
            "small_breast": "The breast fills little of the photo: crop it or move closer.",
        },
        "rejected": "Panels discarded because they don't look like mammograms: {panels}.",
        "masked_one": "1 text area was covered",
        "masked_many": "{n} text areas were covered",
        "masked_suffix": " (possible personal data).",
        "version": "Model version: {v}",
        "check_title": "Image check",
        "panel": "Panel {n}: {state} (confidence {c})",
        "is": "is a mammogram",
        "is_not": "is NOT a mammogram",
    },
    "es": {
        "disclaimer": ("Resultado orientativo de una herramienta de investigación. No sustituye la lectura del "
                       "radiólogo ni es un dispositivo médico aprobado."),
        "levels": {"high_priority": "ALTA PRIORIDAD", "review": "REVISAR",
                   "no_suspicious_findings": "SIN HALLAZGOS SOSPECHOSOS"},
        "sides": {"R": "Mama derecha", "L": "Mama izquierda"},
        "image": "Imagen ({label})",
        "result": "Resultado",
        "probabilities": "Probabilidades",
        "model": "Modelo",
        "fused": "global + parches de alta resolución (con mapa de calor)",
        "global_only": "global (resolución insuficiente para parches)",
        "quality_warning": "Aviso de calidad",
        "quality": {
            "low_resolution": "Resolución baja: acércate o usa la imagen original.",
            "blurry": "Foto borrosa: enfoca tocando la mama en la pantalla del celular.",
            "glare": "Reflejos o zonas quemadas: evita luces sobre la pantalla y no uses flash.",
            "small_breast": "La mama ocupa poco de la foto: recórtala o acércate.",
        },
        "rejected": "Paneles descartados por no parecer mamografía: {panels}.",
        "masked_one": "Se tapó 1 zona de texto",
        "masked_many": "Se taparon {n} zonas de texto",
        "masked_suffix": " (posibles datos personales).",
        "version": "Versión del modelo: {v}",
        "check_title": "Verificación de la imagen",
        "panel": "Panel {n}: {state} (confianza {c})",
        "is": "es mamografía",
        "is_not": "NO es mamografía",
    },
}


def masked_regions_text(count: int, language: Language = "en") -> str:
    t = _TEXT[language]
    return t["masked_one"] if count == 1 else t["masked_many"].format(n=count)


def analysis_payload(result: AnalysisResultDto) -> dict:
    """JSON-safe structured content (heatmaps travel as separate image content blocks). Always in English."""
    data = asdict(result)
    for breast in data["breasts"]:
        for view in breast["views"]:
            view["has_heatmap"] = view.pop("heatmap") is not None
    data["disclaimer"] = DISCLAIMER
    return data


def analysis_markdown(result: AnalysisResultDto, language: Language = "en") -> str:
    t = _TEXT[language]
    lines = [f"## {t['result']}: {t['levels'][result.overall_level]}", ""]
    for b in result.breasts:
        name = t["sides"].get(b.side, t["image"].format(label=b.label))
        p = b.probabilities
        lines.append(f"### {name}: {t['levels'][b.level]}")
        lines.append(f"- {t['probabilities']}: BI-RADS 1-2 {p['birads_1_2']:.0%} · BI-RADS 3 {p['birads_3']:.0%} · "
                     f"BI-RADS 4-5 {p['birads_4_5']:.0%}")
        lines.append(f"- {t['model']}: " + (t["fused"] if b.scoring_mode == "fused" else t["global_only"]))
        for q in b.quality_issues:
            lines.append(f"- {t['quality_warning']}: {t['quality'].get(q, q)}")
        lines.append("")
    if result.rejected_panels:
        lines.append(t["rejected"].format(panels=", ".join(str(r.panel) for r in result.rejected_panels)))
    if result.masked_text_regions:
        lines.append(masked_regions_text(result.masked_text_regions, language) + t["masked_suffix"])
    lines += ["", f"_{t['disclaimer']}_", t["version"].format(v=result.model_version)]
    return "\n".join(lines)


def check_markdown(result: MammogramCheckDto, language: Language = "en") -> str:
    t = _TEXT[language]
    lines = [f"## {t['check_title']}"]
    for p in result.panels:
        state = t["is"] if p.is_mammogram else t["is_not"]
        lines.append("- " + t["panel"].format(n=p.panel, state=state, c=f"{p.mammogram_confidence:.0%}"))
        for q in p.quality_issues:
            lines.append(f"  - {t['quality'].get(q, q)}")
    return "\n".join(lines)
