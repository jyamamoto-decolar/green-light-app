import sqlite3

def diagnose(answers: dict, db_path: str) -> dict:
    required_teams = []
    reasons = {}
    authority_flags = []

    required_teams.append("Product")
    reasons["Product"] = "Toda iniciativa requiere validación del equipo de Producto."

    if answers.get("has_partners"):
        required_teams.append("Legal")
        reasons["Legal"] = "La iniciativa involucra partners o terceros externos. Se requiere revisión de contratos y cláusulas."
        required_teams.append("Tax")
        reasons["Tax"] = "La participación de partners tiene implicancias impositivas."
        authority_flags.append({
            "flag": "GENERAL_COUNSEL_REQUIRED",
            "description": "Iniciativas con partners requieren revisión del General Counsel (Prosus Authority Matrix).",
            "authority_level": "General Counsel"
        })

    fi = answers.get("financial_impact", "minimal")
    if answers.get("affects_checkout") or fi in ("medium", "high", "critical"):
        if "Billing" not in required_teams:
            required_teams.append("Billing")
            reasons["Billing"] = "Impacto en checkout o flujo financiero detectado."
        if "Accounting" not in required_teams:
            required_teams.append("Accounting")
            reasons["Accounting"] = "Se requiere definir tratamiento contable del impacto."
        if "Tax" not in required_teams:
            required_teams.append("Tax")
            reasons["Tax"] = "Impacto financiero requiere revisión impositiva."

    if answers.get("has_security_risk"):
        required_teams.append("Security")
        reasons["Security"] = "Riesgos de seguridad informática identificados."

    if fi in ("high", "critical") or answers.get("new_business_line"):
        if "Internal Control" not in required_teams:
            required_teams.append("Internal Control")
            reasons["Internal Control"] = "Impacto financiero alto o nueva línea de negocio: requiere validación de Control Interno."
        if answers.get("new_business_line"):
            authority_flags.append({
                "flag": "GROUP_BOARD_REQUIRED",
                "description": "Nueva línea de negocio requiere aprobación del Group Board (Prosus Authority Matrix).",
                "authority_level": "Group Board"
            })
        capex = answers.get("capex_or_opex", "unknown")
        if capex in ("capex", "both"):
            authority_flags.append({
                "flag": "CAPEX_AUTHORITY_CHECK",
                "description": "CAPEX >10% del presupuesto aprobado requiere Group Board. <10% requiere Group CEO.",
                "authority_level": "Group Board / Group CEO"
            })

    if fi == "critical":
        authority_flags.append({
            "flag": "GROUP_CFO_NOTIFICATION",
            "description": "Impacto financiero crítico (>$1M). Notificar al Group CFO.",
            "authority_level": "Group CFO"
        })

    conflicts = []
    synergies = []
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        product_area = answers.get("product_area", "")
        title_words = set(answers.get("title", "").lower().split()) - {"de","el","la","los","las","un","una","y","en","con","para","por"}
        cur.execute("SELECT id, title, product_area, status FROM initiatives WHERE status NOT IN ('archived')")
        for eid, etitle, epa, estatus in cur.fetchall():
            if epa and epa.lower() == product_area.lower():
                etitle_words = set(etitle.lower().split()) - {"de","el","la","los","las","un","una","y","en","con","para","por"}
                overlap = title_words & etitle_words
                if len(overlap) >= 2:
                    conflicts.append({"id": eid, "title": etitle, "status": estatus})
                else:
                    synergies.append({"id": eid, "title": etitle, "status": estatus, "shared_area": epa})
        conn.close()
    except Exception:
        pass

    risk_factors = sum([
        bool(answers.get("has_partners")),
        bool(answers.get("affects_checkout")),
        bool(answers.get("has_security_risk")),
        fi in ("high", "critical")
    ])
    risk_level = ["low","medium","high","critical"][min(risk_factors, 3)]

    recommendations = []
    if answers.get("has_partners"):
        recommendations.append("Asegurate de tener contratos firmados y cláusulas de confidencialidad antes de avanzar.")
    if answers.get("affects_checkout"):
        recommendations.append("Coordinar con el equipo de QA para testing regresivo del flujo de checkout.")
    if answers.get("new_business_line"):
        recommendations.append("Preparar business case para presentar al Group Board antes de invertir recursos.")
    if not recommendations:
        recommendations.append("Seguir el proceso estándar de validación y mantener actualizado el estado en Green Light.")

    return {
        "required_teams": required_teams,
        "reasons": reasons,
        "authority_flags": authority_flags,
        "risk_level": risk_level,
        "conflicts": conflicts,
        "synergies": synergies,
        "recommendations": recommendations
    }
