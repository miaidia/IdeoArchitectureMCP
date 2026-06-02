"""Polish MVP source-profile registry (Phase 6 §6.1.B.3; §6 URLs; §32 must-have).

Each entry configures a generic adapter (ULDK text-api / WMS / WFS / WCS / GML) as a
concrete source. Endpoints come from base_assumptions §6; they do NOT need to be
reachable to build the registry (profiles are config). Layer/dataset names are
*representative placeholders* — real layer names are discovered at runtime via
GetCapabilities (F-0082) — and are clearly marked as such.

``legal_status`` follows the §5 ranking:
* ``binding``     — local-law acts / cadastre / authoritative registers used as truth
                    (ULDK cadastre, APP/GML planning acts, NID heritage register).
* ``informative`` — official services whose layers inform analysis (Geoportal rasters,
                    BDOT10k, ISOK flood hazard maps, GDOŚ protected areas, PIG SOPO).
* ``auxiliary``   — preview-only / supporting layers.

WMS layers are PREVIEW (§21) so their profiles are tagged ``approximate`` precision; the
WMS adapter additionally forces low precision/confidence on the emitted evidence.
"""

from __future__ import annotations

from plot_domain import GeometryPrecision, LegalStatus, SourceType

from plot_connectors.base.connector import SourceProfile
from plot_connectors.uldk import uldk_profile

_PLACEHOLDER = " (placeholder — real names discovered via GetCapabilities, F-0082)"


def _profiles() -> dict[str, SourceProfile]:
    profiles: dict[str, SourceProfile] = {}

    def add(p: SourceProfile) -> None:
        profiles[p.source_id] = p

    # --- ULDK (fully implemented connector) — §6.1, F-0041 --------------------- #
    add(uldk_profile())

    # --- Geoportal WMS (ortofoto) — PREVIEW pixels (§21), F-0045/0049 ---------- #
    add(
        SourceProfile(
            source_id="pl.geoportal.wms.ortofoto",
            publisher="GUGiK / Geoportal",
            base_url="https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution",
            service="WMS",
            license="Dane GUGiK — see geoportal.gov.pl regulamin",
            legal_status=LegalStatus.AUXILIARY,
            source_type=SourceType.OFFICIAL_REGISTER,
            geometry_precision=GeometryPrecision.APPROXIMATE,
            confidence=0.3,
            layers=("Raster",),
            version="1.3.0",
            fallback_note="Preview only — use WCS NMT/NMPT or WFS vectors for analysis.",
        )
    )

    # --- Geoportal WFS (EGiB / BDOT vectors) — analytical, F-0047/0054 --------- #
    add(
        SourceProfile(
            source_id="pl.geoportal.wfs.bdot10k",
            publisher="GUGiK / Geoportal",
            base_url="https://mapy.geoportal.gov.pl/wss/service/PZGIK/BDOT/WFS/GetExtent",
            service="WFS",
            license="Dane GUGiK / BDOT10k — see geoportal.gov.pl regulamin",
            legal_status=LegalStatus.INFORMATIVE,
            source_type=SourceType.OFFICIAL_REGISTER,
            geometry_precision=GeometryPrecision.TOPOGRAPHIC,
            confidence=0.8,
            layers=("bdot10k_layer" + _PLACEHOLDER,),
            version="2.0.0",
            fallback_note="If WFS is down, fall back to BDOT10k GML download or local SIP.",
        )
    )

    # --- Geoportal WCS (NMT / NMPT terrain rasters) — F-0048/0051/0052 --------- #
    add(
        SourceProfile(
            source_id="pl.geoportal.wcs.nmt",
            publisher="GUGiK / Geoportal",
            base_url="https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/WCS/DigitalTerrainModel",
            service="WCS",
            license="Dane GUGiK / NMT — see geoportal.gov.pl regulamin",
            legal_status=LegalStatus.INFORMATIVE,
            source_type=SourceType.OFFICIAL_REGISTER,
            geometry_precision=GeometryPrecision.RASTER_DERIVED,
            confidence=0.8,
            layers=("DTM_PL-KRON86-NH" + _PLACEHOLDER,),
            version="2.0.1",
            fallback_note="If WCS is down, fall back to LiDAR/LAZ tiles or archival NMT.",
        )
    )

    # --- GESUT / KIUT utilities (WFS) — F-0056 --------------------------------- #
    add(
        SourceProfile(
            source_id="pl.geoportal.wfs.gesut",
            publisher="GUGiK / PZGiK",
            base_url="https://mapy.geoportal.gov.pl/wss/service/PZGIK/GESUT/WFS/GetExtent",
            service="WFS",
            license="Dane GESUT/KIUT — county geodesy office regulamin",
            legal_status=LegalStatus.INFORMATIVE,
            source_type=SourceType.OFFICIAL_REGISTER,
            geometry_precision=GeometryPrecision.SURVEY,
            confidence=0.75,
            layers=("gesut_uzbrojenie" + _PLACEHOLDER,),
            version="2.0.0",
            fallback_note="GESUT coverage is patchy; fall back to gestor data + on-site survey.",
        )
    )

    # --- ISOK flood hazard / risk (MZP / MRP / WORP) WFS — F-0064 -------------- #
    add(
        SourceProfile(
            source_id="pl.isok.wfs.flood",
            publisher="Wody Polskie / ISOK",
            base_url="https://wody.isok.gov.pl/wfs",
            service="WFS",
            license="Dane ISOK / Wody Polskie — INSPIRE",
            legal_status=LegalStatus.INFORMATIVE,
            source_type=SourceType.OFFICIAL_REGISTER,
            geometry_precision=GeometryPrecision.TOPOGRAPHIC,
            confidence=0.8,
            layers=("MZP_Q1%", "MRP", "WORP" + _PLACEHOLDER),
            version="2.0.0",
            fallback_note="If ISOK is down, mark flood risk unknown; never assume no flood.",
        )
    )

    # --- GDOŚ / CRFOP protected areas (WFS) — F-0066 --------------------------- #
    add(
        SourceProfile(
            source_id="pl.gdos.wfs.crfop",
            publisher="GDOŚ",
            base_url="https://sdi.gdos.gov.pl/wfs",
            service="WFS",
            license="Dane GDOŚ / CRFOP",
            legal_status=LegalStatus.INFORMATIVE,
            source_type=SourceType.OFFICIAL_REGISTER,
            geometry_precision=GeometryPrecision.TOPOGRAPHIC,
            confidence=0.8,
            layers=("ProtectedSite" + _PLACEHOLDER,),
            version="2.0.0",
            fallback_note="If GDOŚ is down, mark protected-area status unknown.",
        )
    )

    # --- PIG SOPO landslides (WFS) — F-0068 ------------------------------------ #
    add(
        SourceProfile(
            source_id="pl.pig.wfs.sopo",
            publisher="PIG-PIB",
            base_url="https://geozagrozenia.pgi.gov.pl/sopo/wfs",
            service="WFS",
            license="Dane PIG-PIB / SOPO",
            legal_status=LegalStatus.INFORMATIVE,
            source_type=SourceType.OFFICIAL_REGISTER,
            geometry_precision=GeometryPrecision.TOPOGRAPHIC,
            confidence=0.75,
            layers=("osuwiska", "tereny_zagrozone" + _PLACEHOLDER),
            version="2.0.0",
            fallback_note="If SOPO is down, mark landslide risk unknown.",
        )
    )

    # --- NID heritage register (WFS) — F-0071 ---------------------------------- #
    add(
        SourceProfile(
            source_id="pl.nid.wfs.heritage",
            publisher="NID",
            base_url="https://mapy.zabytek.gov.pl/wfs",
            service="WFS",
            # Heritage register entries are legally binding constraints (§5 rank 1/3).
            license="Dane NID — rejestr zabytków",
            legal_status=LegalStatus.BINDING,
            source_type=SourceType.OFFICIAL_REGISTER,
            geometry_precision=GeometryPrecision.TOPOGRAPHIC,
            confidence=0.85,
            layers=("zabytki_nieruchome" + _PLACEHOLDER,),
            version="2.0.0",
            fallback_note="If NID is down, mark heritage status unknown (do not clear it).",
        )
    )

    # --- PRG / TERYT / GUS administrative units (WFS) — F-0043/0044 ------------ #
    add(
        SourceProfile(
            source_id="pl.gugik.wfs.prg",
            publisher="GUGiK / GUS",
            base_url="https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries",
            service="WFS",
            license="Dane PRG / TERYT — GUGiK / GUS",
            legal_status=LegalStatus.INFORMATIVE,
            source_type=SourceType.OFFICIAL_REGISTER,
            geometry_precision=GeometryPrecision.TOPOGRAPHIC,
            confidence=0.85,
            layers=("A03_Granice_gmin" + _PLACEHOLDER,),
            version="2.0.0",
            fallback_note="If PRG is down, derive TERYT from ULDK teryt fields.",
        )
    )

    # --- APP/GML planning acts (fetch+record only; deep parse Phase 8) — F-0057 - #
    add(
        SourceProfile(
            source_id="pl.app.gml.planning",
            publisher="Gmina / Rejestr Urbanistyczny",
            base_url="https://www.gov.pl/web/zagospodarowanieprzestrzenne",
            service="GML",
            # A planning act (MPZP/POG) is local law — binding (§5 rank 1).
            license="Akt planowania przestrzennego (APP) — local-law GML",
            legal_status=LegalStatus.BINDING,
            source_type=SourceType.OFFICIAL_REGISTER,
            geometry_precision=GeometryPrecision.TOPOGRAPHIC,
            confidence=0.6,
            layers=(),
            version="app-1",
            fallback_note="If APP/GML unavailable, fall back to local BIP/SIP or user wypis.",
        )
    )

    return profiles


#: The MVP must-have source profiles (§32), keyed by source_id.
PL_PROFILES: dict[str, SourceProfile] = _profiles()


def get_profile(source_id: str) -> SourceProfile:
    """Return the profile for ``source_id`` (raises ``KeyError`` if unknown)."""
    return PL_PROFILES[source_id]


def list_profiles() -> tuple[SourceProfile, ...]:
    """Return all registered MVP profiles."""
    return tuple(PL_PROFILES.values())
