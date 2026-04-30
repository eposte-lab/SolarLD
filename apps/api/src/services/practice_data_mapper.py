"""Aggregator: practice row → unified template context.

The practice templates (DM 37/08, Comunicazione Comune, future TICA /
Modello Unico / Schema unifilare) all need overlapping slices of the
same six entities:

    tenants  → installer + responsabile tecnico (signatory)
    subjects → cliente + decisore + sede operativa
    roofs    → ubicazione impianto (address/comune/provincia/lat/lng)
    leads    → status + outreach metadata
    practices       → impianto + catastali + componenti + data_snapshot
    lead_quotes     → economici / tech_* (passed through componenti)

Rather than duplicate the SELECT-and-massage logic in every template
worker, we centralise it here. Each PDF gets the same shape, so the
templates stay simple (``{{ tenant.codice_fiscale }}`` instead of
``{{ practice.snapshot.tenant.codice_fiscale }}``).

Two public methods:

    get_full_context() -> dict
        Returns the merged context. The keys are stable; new docs add
        new sub-keys but never rename existing ones.

    validate_for_template(template_code) -> list[str]
        Returns the list of human-readable missing-field labels (not
        column names). Empty list = OK to render. The route layer turns
        a non-empty list into a 422 with a friendly error message and
        a deep-link to /settings/legal so the user can fix it in one
        click.

Read-only — no writes happen here. Snapshotting into
``practices.data_snapshot`` is the practice_service's responsibility.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from .roi_service import compute_roi

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Static "norme" block — same on every DM 37/08, copied verbatim from the
# CNA / Confindustria reference template. Living here (not in the Jinja
# template) so a regulatory update is a one-line code change with a
# matching test, not a template-tag hunt across N files.
# ---------------------------------------------------------------------------

_NORME_REFERENCE: dict[str, str] = {
    "dm_37_08": "DM 22 gennaio 2008 n. 37 — Riordino delle disposizioni "
    "in materia di attività di installazione degli impianti all'interno "
    "degli edifici",
    "cei_0_21": "CEI 0-21 — Regola tecnica di riferimento per la "
    "connessione di Utenti attivi e passivi alle reti BT",
    "cei_0_16": "CEI 0-16 — Regola tecnica di riferimento per la "
    "connessione di Utenti attivi e passivi alle reti AT/MT",
    "cei_82_25": "CEI 82-25 — Guida alla realizzazione di sistemi di "
    "generazione fotovoltaica connessi alle reti elettriche",
    "uni_8290": "UNI 8290 — Edilizia residenziale. Sistema tecnologico",
    "dlgs_28_2011": "D.Lgs. 3 marzo 2011 n. 28 — Attuazione direttiva "
    "2009/28/CE sulla promozione dell'uso dell'energia da fonti rinnovabili",
}


# ---------------------------------------------------------------------------
# Required fields per template_code, mapped to (column_path, friendly_label).
# ``column_path`` is dotted: "tenant.codice_fiscale" → context["tenant"]["codice_fiscale"].
# ``friendly_label`` is what the dashboard surfaces in the validation banner.
# ---------------------------------------------------------------------------

_TEMPLATE_REQUIREMENTS: dict[str, list[tuple[str, str]]] = {
    "dm_37_08": [
        # The dichiarazione di conformità is signed by the iscritto-all'albo
        # responsabile tecnico — without these five it cannot be legally
        # printed. CCIAA goes in the header; CF on the legal entity is
        # routinely double-checked by inspecting Camere di Commercio.
        ("tenant.codice_fiscale", "Codice fiscale azienda installatrice"),
        ("tenant.numero_cciaa", "Numero iscrizione CCIAA"),
        ("tenant.responsabile_tecnico_nome", "Nome responsabile tecnico"),
        ("tenant.responsabile_tecnico_cognome", "Cognome responsabile tecnico"),
        ("tenant.responsabile_tecnico_qualifica", "Qualifica responsabile tecnico"),
        ("tenant.responsabile_tecnico_iscrizione_albo", "Iscrizione albo responsabile tecnico"),
        # Impianto data — without these the document refers to "an
        # installation" without identifying which one.
        ("impianto.potenza_kw", "Potenza impianto (kW)"),
        ("ubicazione.indirizzo", "Indirizzo impianto"),
        ("ubicazione.comune", "Comune"),
    ],
    "comunicazione_comune": [
        # The communication is addressed "Al Sig. Sindaco del Comune di
        # ..." — comune is the mandatory field; everything else degrades
        # gracefully (e.g. catastali render as "—" when missing).
        ("ubicazione.comune", "Comune"),
        ("ubicazione.indirizzo", "Indirizzo impianto"),
        ("impianto.potenza_kw", "Potenza impianto (kW)"),
        ("impianto.data_inizio_lavori", "Data inizio lavori"),
        ("impianto.data_fine_lavori", "Data fine lavori"),
    ],
    # ---- Sprint 2 templates ----
    "modello_unico_p1": [
        # The richiedente is the customer (decisore) — they sign as
        # "sottoscritto". MU pt.I is sent BEFORE works begin, so as-built
        # data is not yet known.
        ("decisore.nome_completo", "Nome richiedente (cliente)"),
        ("cliente.codice_fiscale", "Codice fiscale richiedente"),
        ("ubicazione.indirizzo", "Indirizzo impianto"),
        ("ubicazione.comune", "Comune"),
        ("ubicazione.cap", "CAP"),
        ("impianto.potenza_kw", "Potenza picco (kW)"),
        ("componenti.inverter.potenza_kw", "Potenza nominale inverter (kW)"),
        ("impianto.data_inizio_lavori", "Data inizio lavori"),
        ("extras.qualita_richiedente", "Titolo del richiedente (proprietario, etc.)"),
        ("extras.tipologia_struttura", "Tipologia (edificio / fuori terra)"),
        ("extras.regime_ritiro", "Regime ritiro energia (GSE / mercato)"),
    ],
    "modello_unico_p2": [
        # MU pt.II is sent at fine-lavori — we now know as-built numbers
        # plus the codice identificativo assigned by the distributor when
        # MU pt.I was accepted.
        ("decisore.nome_completo", "Nome richiedente (cliente)"),
        ("impianto.potenza_kw", "Potenza picco as-built (kW)"),
        ("componenti.inverter.potenza_kw", "Potenza inverter as-built (kW)"),
        ("componenti.inverter.marca", "Marca inverter"),
        ("componenti.inverter.modello", "Modello inverter"),
        ("componenti.pannelli.marca", "Marca moduli"),
        ("componenti.pannelli.modello", "Modello moduli"),
        ("impianto.data_fine_lavori", "Data fine lavori"),
        ("extras.codice_identificativo_connessione", "Codice identificativo connessione (assegnato dal distributore)"),
        ("extras.regime_ritiro", "Regime ritiro energia"),
    ],
    "schema_unifilare": [
        # The single-line diagram needs the component counts to draw.
        # All other fields degrade to placeholders.
        ("impianto.potenza_kw", "Potenza impianto (kW)"),
        ("componenti.pannelli.quantita", "Numero moduli FV"),
        ("componenti.inverter.quantita", "Numero inverter"),
    ],
    "attestazione_titolo": [
        # Modulo ATR — declares the legal title under which the customer
        # holds the building. Comune/POD are needed for the header.
        ("decisore.nome_completo", "Nome richiedente"),
        ("cliente.codice_fiscale", "Codice fiscale richiedente"),
        ("impianto.pod", "POD"),
        ("ubicazione.comune", "Comune"),
        ("extras.qualita_richiedente", "Titolo (proprietà, locazione, comodato, ecc.)"),
    ],
    "tica_areti": [
        # Areti TICA istanza — needs at least one POD principale in the
        # tabelle. We accept the simple single-POD case in Sprint 2.
        ("decisore.nome_completo", "Nome richiedente"),
        ("cliente.codice_fiscale", "Codice fiscale richiedente"),
        ("impianto.pod", "POD principale"),
        ("ubicazione.comune", "Comune"),
        ("impianto.potenza_kw", "Potenza impianto (kW)"),
    ],
    "transizione_50_ex_ante": [
        # Industrial-credit certification — the certifying technician's
        # albo data is shared with DM 37/08 (responsabile tecnico).
        # The ATECO + tep risparmio are extras.
        ("tenant.responsabile_tecnico_nome", "Nome certificatore"),
        ("tenant.responsabile_tecnico_cognome", "Cognome certificatore"),
        ("tenant.responsabile_tecnico_iscrizione_albo", "Iscrizione albo certificatore"),
        ("cliente.ragione_sociale", "Ragione sociale impresa beneficiaria"),
        ("cliente.piva", "P.IVA impresa beneficiaria"),
        ("cliente.ateco_code", "Codice ATECO struttura produttiva"),
        ("ubicazione.comune", "Comune struttura produttiva"),
        ("ubicazione.provincia", "Provincia struttura produttiva"),
        ("extras.transizione50.tep_anno", "Risparmio energetico in tep/anno"),
        ("extras.transizione50.percentuale_riduzione", "% riduzione consumi energetici"),
        ("impianto.potenza_kw", "Potenza autoproduzione (kW)"),
    ],
    "transizione_50_ex_post": [
        ("tenant.responsabile_tecnico_nome", "Nome certificatore"),
        ("tenant.responsabile_tecnico_cognome", "Cognome certificatore"),
        ("tenant.responsabile_tecnico_iscrizione_albo", "Iscrizione albo certificatore"),
        ("cliente.ragione_sociale", "Ragione sociale impresa beneficiaria"),
        ("cliente.piva", "P.IVA impresa beneficiaria"),
        ("cliente.ateco_code", "Codice ATECO struttura produttiva"),
        ("extras.transizione50.tep_anno", "Risparmio energetico in tep/anno"),
        ("extras.transizione50.percentuale_riduzione", "% riduzione consumi energetici"),
        ("impianto.data_fine_lavori", "Data fine lavori"),
    ],
    "transizione_50_attestazione": [
        # Allegato V — the legal rappresentante attests to holding the
        # perizia + cert contabile. Lighter requirements than ex-ante/post.
        ("decisore.nome_completo", "Legale rappresentante"),
        ("cliente.ragione_sociale", "Ragione sociale impresa"),
        ("cliente.piva", "P.IVA impresa"),
    ],
}


class PracticeDataMapper:
    """Build a unified template context from a practice + its joined entities.

    Construction loads the practice row and its parent lead/subject/roof/
    tenant in two Supabase round-trips (one wide JOIN, one tenant lookup —
    tenants aren't reachable through the leads embed because Supabase
    doesn't follow the FK chain back through tenant_id).

    Single-use: instantiate once per render, call ``get_full_context()``.
    """

    def __init__(self, practice_id: str | UUID, tenant_id: str | UUID) -> None:
        self._practice_id = str(practice_id)
        self._tenant_id = str(tenant_id)
        self._sb = get_service_client()

        # Eager-load — the validate() and get_full_context() methods both
        # need the same data, and the round-trip is cheap enough that
        # we don't bother lazy-loading.
        self._load()

    # ----- loading ---------------------------------------------------------

    def _load(self) -> None:
        # Practice + lead + subject + roof + lead_quote in one shot via
        # PostgREST embedded resources. quote_id is nullable, so the
        # embed may be None — defensive .get() throughout.
        practice_res = (
            self._sb.table("practices")
            .select(
                "*, "
                "leads:lead_id(*, subjects(*), roofs(*)), "
                "lead_quotes:quote_id(*)"
            )
            .eq("id", self._practice_id)
            .eq("tenant_id", self._tenant_id)
            .limit(1)
            .execute()
        )
        if not practice_res.data:
            raise ValueError(
                f"practice {self._practice_id} not found for tenant {self._tenant_id}"
            )
        self._practice = practice_res.data[0]
        self._lead = self._practice.get("leads") or {}
        self._subject = self._lead.get("subjects") or {}
        self._roof = self._lead.get("roofs") or {}
        self._quote = self._practice.get("lead_quotes") or {}

        # Tenant: separate lookup. We pull every column the practice
        # documents could need rather than doing per-doc projections —
        # the tenant row is small (KB scale).
        tenant_res = (
            self._sb.table("tenants")
            .select(
                "id, business_name, legal_name, vat_number, contact_email, "
                "contact_phone, brand_logo_url, brand_primary_color, settings, "
                "legal_address, codice_fiscale, numero_cciaa, "
                "responsabile_tecnico_nome, responsabile_tecnico_cognome, "
                "responsabile_tecnico_codice_fiscale, "
                "responsabile_tecnico_qualifica, "
                "responsabile_tecnico_iscrizione_albo"
            )
            .eq("id", self._tenant_id)
            .limit(1)
            .execute()
        )
        self._tenant = tenant_res.data[0] if tenant_res.data else {}

    # ----- public API ------------------------------------------------------

    def get_full_context(self) -> dict[str, Any]:
        """Assemble the merged context. See module docstring for keys."""
        return {
            "tenant": self._tenant_context(),
            # Convenience alias — templates read more naturally as
            # ``installatore.ragione_sociale`` than ``tenant.business_name``
            # in the dichiarazione header.
            "installatore": self._installatore_context(),
            "cliente": self._cliente_context(),
            "decisore": self._decisore_context(),
            "impianto": self._impianto_context(),
            "componenti": self._componenti_context(),
            "ubicazione": self._ubicazione_context(),
            "energetico": self._energetico_context(),
            "pratica": self._pratica_context(),
            "norme": _NORME_REFERENCE,
            # Sprint 2: template-specific fields (IBAN, codice
            # identificativo connessione, regime ritiro, ATECO, tep/anno
            # risparmio, ecc.). See EXTRAS_SHAPE.
            "extras": self._extras_context(),
        }

    def validate_for_template(self, template_code: str) -> list[str]:
        """Return human-readable labels for any missing required fields.

        Empty list = OK to render. Non-empty = route layer should 422 the
        request (or, for the optimistic-create path, render anyway and
        flag the document with ``generation_error``).
        """
        reqs = _TEMPLATE_REQUIREMENTS.get(template_code)
        if reqs is None:
            # Unknown template code: don't validate — the renderer will
            # raise its own ValueError and the route surfaces that.
            return []
        ctx = self.get_full_context()
        missing: list[str] = []
        for path, label in reqs:
            if not _resolve_path(ctx, path):
                missing.append(label)
        return missing

    def get_missing_fields_report(self) -> dict[str, Any]:
        """Structured gap report for every registered template_code.

        Returns a dict consumed by ``GET /v1/practices/{id}/missing-fields``:

        {
          "all_ready": bool,
          "templates": [
            {
              "template_code": str,
              "ready": bool,
              "missing": [
                {
                  "path": str,           # dotted context path e.g. "tenant.codice_fiscale"
                  "label": str,          # human-readable Italian label
                  "source": str,         # "tenant" | "practice" | "extras" | "subject"
                  "api_field": str | None  # the API/DB field to PATCH (None = not patchable here)
                }
              ]
            }
          ],
          "by_source": {
            "tenant": [...],     # distinct missing fields that must go to PATCH /v1/tenants/me
            "practice": [...],   # fields on the practices row or extras JSONB
            "subject": [...],    # on subjects — user must edit lead; not patchable inline
          }
        }
        """
        from .practice_pdf_renderer import SUPPORTED_TEMPLATE_CODES

        ctx = self.get_full_context()
        templates_out: list[dict[str, Any]] = []
        # Deduplicate per source so the form only shows each field once.
        seen_tenant: set[str] = set()
        seen_practice: set[str] = set()
        seen_subject: set[str] = set()
        by_source: dict[str, list[dict[str, Any]]] = {
            "tenant": [],
            "practice": [],
            "subject": [],
        }

        for tc in sorted(SUPPORTED_TEMPLATE_CODES):
            reqs = _TEMPLATE_REQUIREMENTS.get(tc) or []
            missing_items: list[dict[str, Any]] = []
            for path, label in reqs:
                if _resolve_path(ctx, path):
                    continue  # field is present
                source, api_field = _classify_field(path)
                item = {
                    "path": path,
                    "label": label,
                    "source": source,
                    "api_field": api_field,
                }
                missing_items.append(item)
                # Accumulate into by_source (dedup by path).
                if source == "tenant" and path not in seen_tenant:
                    seen_tenant.add(path)
                    by_source["tenant"].append(item)
                elif source in ("practice", "extras") and path not in seen_practice:
                    seen_practice.add(path)
                    by_source["practice"].append(item)
                elif source == "subject" and path not in seen_subject:
                    seen_subject.add(path)
                    by_source["subject"].append(item)

            templates_out.append(
                {
                    "template_code": tc,
                    "ready": len(missing_items) == 0,
                    "missing": missing_items,
                }
            )

        all_ready = all(t["ready"] for t in templates_out)
        return {
            "all_ready": all_ready,
            "templates": templates_out,
            "by_source": by_source,
        }

    # ----- block builders --------------------------------------------------

    def _tenant_context(self) -> dict[str, Any]:
        t = self._tenant
        settings = t.get("settings") or {}
        return {
            # Identità legale.
            "ragione_sociale": t.get("legal_name") or t.get("business_name") or "",
            "business_name": t.get("business_name") or "",
            "piva": t.get("vat_number") or "",
            "codice_fiscale": t.get("codice_fiscale") or "",
            "numero_cciaa": t.get("numero_cciaa") or "",
            "sede_legale": t.get("legal_address") or settings.get("sede_legale") or "",
            "sede_operativa": settings.get("sede_operativa")
            or t.get("legal_address")
            or "",
            # Contatti.
            "email": t.get("contact_email") or "",
            "telefono": t.get("contact_phone") or "",
            "pec": settings.get("pec") or "",
            # Branding (used by the template header bar).
            "logo_url": t.get("brand_logo_url") or "",
            "brand_color": t.get("brand_primary_color") or "#0F766E",
            "brand_color_accent": settings.get("brand_color_accent")
            or t.get("brand_primary_color")
            or "#F4A300",
            # Responsabile tecnico (signatory of DM 37/08).
            "responsabile_tecnico_nome": t.get("responsabile_tecnico_nome") or "",
            "responsabile_tecnico_cognome": t.get("responsabile_tecnico_cognome") or "",
            "responsabile_tecnico_nome_completo": _join_name(
                t.get("responsabile_tecnico_nome"),
                t.get("responsabile_tecnico_cognome"),
            ),
            "responsabile_tecnico_codice_fiscale": t.get(
                "responsabile_tecnico_codice_fiscale"
            )
            or "",
            "responsabile_tecnico_qualifica": t.get(
                "responsabile_tecnico_qualifica"
            )
            or "",
            "responsabile_tecnico_iscrizione_albo": t.get(
                "responsabile_tecnico_iscrizione_albo"
            )
            or "",
        }

    def _installatore_context(self) -> dict[str, Any]:
        # Templates use ``installatore`` for legibility in the
        # dichiarazione header. Keep the keys short and Italian-friendly.
        t = self._tenant_context()
        return {
            "ragione_sociale": t["ragione_sociale"],
            "piva": t["piva"],
            "codice_fiscale": t["codice_fiscale"],
            "cciaa": t["numero_cciaa"],
            "sede": t["sede_legale"],
            "telefono": t["telefono"],
            "email": t["email"],
            "pec": t["pec"],
            "responsabile_tecnico": {
                "nome_completo": t["responsabile_tecnico_nome_completo"],
                "codice_fiscale": t["responsabile_tecnico_codice_fiscale"],
                "qualifica": t["responsabile_tecnico_qualifica"],
                "iscrizione_albo": t["responsabile_tecnico_iscrizione_albo"],
            },
        }

    def _cliente_context(self) -> dict[str, Any]:
        s = self._subject
        # B2B vs B2C: pick the most descriptive name available.
        ragione_sociale = (
            s.get("business_name")
            or _join_name(s.get("owner_first_name"), s.get("owner_last_name"))
            or ""
        )
        return {
            "tipo": s.get("type") or "",  # "b2b" / "b2c" / "unknown"
            "ragione_sociale": ragione_sociale,
            "piva": s.get("vat_number") or "",
            "codice_fiscale": s.get("vat_number") or "",  # B2B: PIVA == CF often
            "ateco_code": s.get("ateco_code") or "",
            "ateco_description": s.get("ateco_description") or "",
            "sede_operativa": _format_address(
                s.get("sede_operativa_address"),
                s.get("sede_operativa_cap"),
                s.get("sede_operativa_city"),
                s.get("sede_operativa_province"),
            ),
            "indirizzo_postale": _format_address(
                s.get("postal_address_line1"),
                s.get("postal_cap"),
                s.get("postal_city"),
                s.get("postal_province"),
            ),
            "email": s.get("decision_maker_email") or "",
        }

    def _decisore_context(self) -> dict[str, Any]:
        s = self._subject
        # B2B: decision_maker_*; B2C: owner_first/last; fall back to either.
        nome_completo = (
            s.get("decision_maker_name")
            or _join_name(s.get("owner_first_name"), s.get("owner_last_name"))
            or ""
        )
        return {
            "nome_completo": nome_completo,
            "ruolo": s.get("decision_maker_role") or "",
            "email": s.get("decision_maker_email") or "",
        }

    def _impianto_context(self) -> dict[str, Any]:
        p = self._practice
        return {
            "potenza_kw": _to_float(p.get("impianto_potenza_kw")),
            "pannelli_count": p.get("impianto_pannelli_count"),
            "pod": p.get("impianto_pod") or "",
            "distributore": p.get("impianto_distributore") or "",
            "distributore_label": _DISTRIBUTORE_LABELS.get(
                p.get("impianto_distributore") or "", ""
            ),
            "data_inizio_lavori": p.get("impianto_data_inizio_lavori"),
            "data_fine_lavori": p.get("impianto_data_fine_lavori"),
            # Catastali — frequently missing in Sprint 1; renderers
            # show "—" via Jinja default.
            "catastale_foglio": p.get("catastale_foglio") or "",
            "catastale_particella": p.get("catastale_particella") or "",
            "catastale_subalterno": p.get("catastale_subalterno") or "",
        }

    def _componenti_context(self) -> dict[str, Any]:
        """Return a normalised view of components.

        Sources, in priority order:
          1. ``practices.componenti_data`` JSONB — set at practice creation.
          2. ``lead_quotes.manual_fields.tech_*`` — fallback when the
             practice form didn't override (the form prefills from here).

        Schema:
            {
              "pannelli": {"marca", "modello", "potenza_w", "quantita"},
              "inverter": {"marca", "modello", "potenza_kw", "quantita"},
              "accumulo": {"presente": bool, "marca", "modello", "capacita_kwh"},
            }

        Templates iterate over this — keep keys stable.
        """
        c = self._practice.get("componenti_data") or {}
        manual = (self._quote.get("manual_fields") or {}) if self._quote else {}

        def first(*candidates: Any) -> Any:
            for v in candidates:
                if v not in (None, ""):
                    return v
            return ""

        return {
            "pannelli": {
                "marca": first(
                    (c.get("pannelli") or {}).get("marca"),
                    manual.get("tech_marca_pannelli"),
                ),
                "modello": first(
                    (c.get("pannelli") or {}).get("modello"),
                    manual.get("tech_modello_pannelli"),
                ),
                "potenza_w": first(
                    (c.get("pannelli") or {}).get("potenza_w"),
                    manual.get("tech_potenza_singolo_pannello"),
                ),
                "quantita": first(
                    (c.get("pannelli") or {}).get("quantita"),
                    self._practice.get("impianto_pannelli_count"),
                ),
                "garanzia_anni": first(
                    (c.get("pannelli") or {}).get("garanzia_anni"),
                    manual.get("tech_garanzia_pannelli_anni"),
                ),
            },
            "inverter": {
                "marca": first(
                    (c.get("inverter") or {}).get("marca"),
                    manual.get("tech_marca_inverter"),
                ),
                "modello": first(
                    (c.get("inverter") or {}).get("modello"),
                    manual.get("tech_modello_inverter"),
                ),
                "potenza_kw": first(
                    (c.get("inverter") or {}).get("potenza_kw"),
                    self._practice.get("impianto_potenza_kw"),
                ),
                "quantita": first(
                    (c.get("inverter") or {}).get("quantita"),
                    1,
                ),
                "garanzia_anni": first(
                    (c.get("inverter") or {}).get("garanzia_anni"),
                    manual.get("tech_garanzia_inverter_anni"),
                ),
            },
            "accumulo": {
                "presente": bool(
                    (c.get("accumulo") or {}).get("presente")
                    or manual.get("tech_accumulo_incluso")
                ),
                "marca": (c.get("accumulo") or {}).get("marca") or "",
                "modello": (c.get("accumulo") or {}).get("modello") or "",
                "capacita_kwh": (c.get("accumulo") or {}).get("capacita_kwh") or "",
            },
        }

    def _ubicazione_context(self) -> dict[str, Any]:
        r = self._roof
        s = self._subject
        # The impianto is installed where the roof is. Fall back to the
        # subject's sede operativa when the roof row was upgraded but
        # we still want a printable address (rare edge case).
        indirizzo = r.get("address") or s.get("sede_operativa_address") or ""
        comune = r.get("comune") or s.get("sede_operativa_city") or ""
        provincia = r.get("provincia") or s.get("sede_operativa_province") or ""
        cap = r.get("cap") or s.get("sede_operativa_cap") or ""
        return {
            "indirizzo": indirizzo,
            "cap": cap,
            "comune": comune,
            "provincia": provincia,
            "lat": r.get("lat"),
            "lng": r.get("lng"),
            # Pre-formatted "Via ..., 16100 Genova (GE)" for templates
            # that just want a single-line address.
            "indirizzo_completo": _format_address(indirizzo, cap, comune, provincia),
        }

    def _energetico_context(self) -> dict[str, Any]:
        # Reuse the lead's ROI computation. We deliberately don't trust
        # ``leads.roi_data`` (potentially stale); recompute from the
        # roof sizing — same approach as quote_service.build_auto_fields.
        r = self._roof
        s = self._subject
        roi = compute_roi(
            estimated_kwp=r.get("estimated_kwp"),
            estimated_yearly_kwh=r.get("estimated_yearly_kwh"),
            subject_type=(s.get("type") or "b2b").lower(),
        )
        roi_jsonb = roi.to_jsonb() if roi else {}
        return {
            "kwp": _to_float(r.get("estimated_kwp")),
            "kwh_annui": _to_float(r.get("estimated_yearly_kwh")),
            "co2_kg_anno": roi_jsonb.get("co2_kg_per_year") or 0,
            "co2_ton_anno": _to_round(
                (roi_jsonb.get("co2_kg_per_year") or 0) / 1000.0, 2
            ),
            "risparmio_anno_eur": roi_jsonb.get("net_self_savings_eur") or 0,
            "risparmio_25_anni_eur": roi_jsonb.get("savings_25y_eur") or 0,
            "payback_anni": roi_jsonb.get("payback_years") or 0,
        }

    def _extras_context(self) -> dict[str, Any]:
        """Return ``practices.extras`` JSONB with safe defaults.

        Schema (EXTRAS_SHAPE):
            {
              "iban":                              str,
              "regime_ritiro":                     "gse_po"|"gse_pmg"|"mercato",
              "qualita_richiedente":               "proprietario"|"proprietario_altro_diritto"|"amministratore"|"locatario"|"comodatario"|"altro",
              "qualita_richiedente_altro":         str,           # quando qualita == "altro"
              "denominazione_impianto":            str,
              "tipologia_struttura":               "edificio"|"fuori_terra",
              "codice_identificativo_connessione": str,           # Modello Unico Pt. II
              "codice_rintracciabilita":           str,           # TICA
              "potenza_immissione_kw":             float,
              "configurazione_accumulo":           "lato_produzione_mono"|"lato_produzione_bi"|"post_produzione_bi",
              "potenza_convertitore_accumulo_kw":  float,
              "marca_protezione_interfaccia":      str,           # Modello Unico Pt. II
              "modello_protezione_interfaccia":    str,
              "utente_dispacciamento": {                          # Quando regime_ritiro == "mercato"
                "ragione_sociale": str, "cf": str, "piva": str,
                "pec": str, "email": str, "codice_contratto": str,
              },
              "transizione50": {
                "tep_anno":                float,                 # Risparmio in tonnellate equivalenti petrolio
                "percentuale_riduzione":   float,                 # % riduzione consumi
                "scenario":                "struttura_produttiva"|"processo_interessato",
                "soglia_riduzione":        "3"|"6"|"10"|"5"|"15", # % soglia di accesso al credito
                "data_certificazione":     "YYYY-MM-DD",
                "polizza_assicurativa":    str,                   # Estremi polizza ex art. 15 c. 8
              }
            }

        All keys are optional. Templates use ``{{ extras.iban or "—" }}``
        to degrade gracefully when an installer hasn't filled them in.
        """
        e = self._practice.get("extras") or {}
        # Normalise nested objects so templates can chain without None checks.
        e.setdefault("utente_dispacciamento", {})
        e.setdefault("transizione50", {})
        return e

    def _pratica_context(self) -> dict[str, Any]:
        p = self._practice
        return {
            "id": p.get("id"),
            "numero": p.get("practice_number") or "",
            "seq": p.get("practice_seq"),
            "status": p.get("status") or "",
            "data_apertura": p.get("created_at"),
            # The "data del documento" is normally today, but we use the
            # practice's created_at for reproducibility — re-rendering a
            # document a week later still bears the original date.
            "data_documento": p.get("created_at"),
        }


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------

# Italian distributor codes → display labels. Used in templates that show
# "Distributore: E-Distribuzione S.p.A.". The CHECK constraint on
# practices.impianto_distributore guarantees one of these keys.
_DISTRIBUTORE_LABELS: dict[str, str] = {
    "e_distribuzione": "E-Distribuzione S.p.A.",
    "areti": "Areti S.p.A. (Roma)",
    "unareti": "Unareti S.p.A. (Milano)",
    "altro": "Altro distributore",
}


def _resolve_path(ctx: dict[str, Any], path: str) -> Any:
    """Walk a dotted path through nested dicts. Empty/missing → None.

    Used by validate_for_template to check arbitrary required fields
    without hardcoding the structure.
    """
    cur: Any = ctx
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    # Treat empty strings and 0 as missing for required-field validation;
    # 0 kW is an obviously broken impianto. Falsy bool == False is fine
    # because no required field is a bool.
    if cur in (None, "", 0, 0.0):
        return None
    return cur


def _join_name(first: str | None, last: str | None) -> str:
    return " ".join(p for p in [first, last] if p).strip()


def _format_address(
    street: str | None,
    cap: str | None,
    city: str | None,
    province: str | None,
) -> str:
    parts: list[str] = []
    if street:
        parts.append(street)
    locality = " ".join(p for p in [cap, city] if p)
    if locality:
        parts.append(locality)
    if province:
        parts.append(f"({province})")
    return ", ".join(parts).strip(", ")


def _to_float(val: Any) -> float:
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _to_round(val: Any, ndigits: int) -> float:
    try:
        return round(float(val), ndigits)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# _classify_field — map a context path → (source, api_field) for the
# missing-fields report.
#
# "source" tells the dashboard which form to show and which API to call:
#   "tenant"   → PATCH /v1/tenants/me with the tenant column name
#   "practice" → PATCH /v1/practices/{id} with the practice column name
#   "extras"   → PATCH /v1/practices/{id} with extras.{sub_key} path
#   "subject"  → read-only; user must edit lead subject
#
# "api_field" is the specific field name for the PATCH body.  None means
# the field can't be patched from the practice detail page.
# ---------------------------------------------------------------------------

_PATH_SOURCE_MAP: dict[str, tuple[str, str | None]] = {
    # Tenant legal fields
    "tenant.codice_fiscale": ("tenant", "codice_fiscale"),
    "tenant.numero_cciaa": ("tenant", "numero_cciaa"),
    "tenant.responsabile_tecnico_nome": ("tenant", "responsabile_tecnico_nome"),
    "tenant.responsabile_tecnico_cognome": ("tenant", "responsabile_tecnico_cognome"),
    "tenant.responsabile_tecnico_qualifica": ("tenant", "responsabile_tecnico_qualifica"),
    "tenant.responsabile_tecnico_iscrizione_albo": (
        "tenant",
        "responsabile_tecnico_iscrizione_albo",
    ),
    # Practice impianto fields
    "impianto.potenza_kw": ("practice", "impianto_potenza_kw"),
    "impianto.pod": ("practice", "impianto_pod"),
    "impianto.data_inizio_lavori": ("practice", "impianto_data_inizio_lavori"),
    "impianto.data_fine_lavori": ("practice", "impianto_data_fine_lavori"),
    "impianto.distributore": ("practice", "impianto_distributore"),
    # Componenti (nested in componenti_data JSONB — PATCH sends the whole dict)
    "componenti.inverter.potenza_kw": ("practice", "componenti_data"),
    "componenti.inverter.marca": ("practice", "componenti_data"),
    "componenti.inverter.modello": ("practice", "componenti_data"),
    "componenti.pannelli.marca": ("practice", "componenti_data"),
    "componenti.pannelli.modello": ("practice", "componenti_data"),
    "componenti.pannelli.quantita": ("practice", "componenti_data"),
    "componenti.inverter.quantita": ("practice", "componenti_data"),
    # Extras JSONB
    "extras.iban": ("extras", "iban"),
    "extras.regime_ritiro": ("extras", "regime_ritiro"),
    "extras.qualita_richiedente": ("extras", "qualita_richiedente"),
    "extras.tipologia_struttura": ("extras", "tipologia_struttura"),
    "extras.codice_identificativo_connessione": ("extras", "codice_identificativo_connessione"),
    "extras.potenza_immissione_kw": ("extras", "potenza_immissione_kw"),
    "extras.transizione50.tep_anno": ("extras", "transizione50.tep_anno"),
    "extras.transizione50.percentuale_riduzione": (
        "extras",
        "transizione50.percentuale_riduzione",
    ),
    # Subject / decisore — not patchable inline (must edit subject in lead)
    "decisore.nome_completo": ("subject", None),
    "cliente.codice_fiscale": ("subject", None),
    "cliente.ragione_sociale": ("subject", None),
    "cliente.piva": ("subject", None),
    "cliente.ateco_code": ("subject", None),
    # Ubicazione — comes from the roof; surface as practice (can accept
    # override in extras.ubicazione_override in a future sprint; for now
    # mark as subject so the user knows to fix the lead/roof record).
    "ubicazione.indirizzo": ("subject", None),
    "ubicazione.comune": ("subject", None),
    "ubicazione.provincia": ("subject", None),
    "ubicazione.cap": ("subject", None),
}


def _classify_field(path: str) -> tuple[str, str | None]:
    """Return (source, api_field) for a context path.

    Falls back to ("subject", None) for any unknown path so the UI
    always has an actionable guidance even for paths added in future
    template requirements.
    """
    return _PATH_SOURCE_MAP.get(path, ("subject", None))
