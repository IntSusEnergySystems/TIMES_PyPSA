"""Guards against soft-link energy that silently never reaches pypsa-wal.

Regression suite for the 2026 Walloon heat leak: 16 building heating processes
had no row in ``mapping_processes.csv`` and a 17th carried a label no heat rule
listed, so 2.2–7.7 % of TIMES appliance useful heat was never exported. A full QA
pass reported it clean and a visual Sankey review missed it.

The reason the existing checks all passed is worth stating, because it is what
these tests are shaped around: every one of them is **rule-relative**.

===========================  ==========================================
check                        why it was blind
===========================  ==========================================
``export_reconciliation``    compares *tagged* to *coloured* — a flow no
                             rule matched is in neither column
``parent_child_sum_checks``  compares rule totals to rule totals — a
                             process in neither parent nor child sums to
                             zero on both sides
``qa_coverage_gap``          scans ``VAR_FIn`` only, and every heat rule
                             is ``measure_at = service_output``
                             (``VAR_FOut``); also filters on the
                             process's sector, which an unmapped process
                             does not have
===========================  ==========================================

So the tests below deliberately compare the rules against the ``.vd`` and against
``mapping_processes.csv``, never against other rules.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from times_pypsa.balances import (
    SERVICE_OUTPUT_CARRIERS,
    service_output_coverage,
    service_output_gap,
    unmapped_process_report,
)
from times_pypsa.pipeline import load_extraction_rules, load_metadata
from times_pypsa.qa import filter_energy_carrier_flows, tag_flows_with_rules
from times_pypsa.sankey_html import flag_leak_suspects

#: The processes whose absence from ``mapping_processes.csv`` caused the leak.
#: Pinned by code, not by count, so a future mapping rewrite that drops one of
#: them fails loudly instead of silently re-opening the hole.
LEAKED_HEAT_PROCESSES = {
    # residential — pellet / log / gas boilers, "HeatHotwater" and water-heat only
    "RH2FPELN2": "Residential urban decentral biomass heater",
    "RW2FPELN3": "Residential urban decentral biomass heater",
    "RW4FPELN3": "Residential rural biomass heater",
    "RWAPPELN3": "Residential urban decentral biomass heater",
    "RWN2FGMXN3": "Residential urban decentral gas heater",
    "RHN2FLOGN2": "Residential urban decentral biomass heater",
    "RHN4FLOGN2": "Residential rural biomass heater",
    "RHNAPLOGN2": "Residential urban decentral biomass heater",
    "RHN3FGMXN2": "Residential rural gas heater",
    # tertiary geothermal space + water heat
    "CHBAGEO101": "commercial Geothermal",
    "CHBPGEO101": "commercial Geothermal",
    "CHCOGEO101": "commercial Geothermal",
    "CHENGEO101": "commercial Geothermal",
    "CWSAGEO101": "commercial Geothermal",
    "CWNBAGEO101": "commercial Geothermal",
    "CWNENGEO101": "commercial Geothermal",
    # Non-zero only in 2045 — invisible to a 2025/2030/2040/2050 spot check, and
    # found only because the closure check runs over every year in the .vd.
    "RHN3FLOGN2": "Residential rural biomass heater",
    "CHENELC201": "Commercial electrical stove",
}

#: Second wave, found by ``test_every_local_vd_closes`` when the September-2026
#: `.vd` files (``scen_central_demande_haute_*``) introduced new vintages the
#: mapping had never seen: 19 processes with no row at all. Nine of them are
#: heating devices feeding ``service_output`` rules, so they leaked exactly like
#: the first wave — up to 1.68 PJ of residential useful heat in 2025. The rest
#: are industrial steam / cogeneration, invisible rather than leaking: their
#: labels are read by no exporting rule.
#:
#: Pinned the same way as ``LEAKED_HEAT_PROCESSES``: by code, so a mapping
#: rewrite that drops one fails here instead of silently reopening the hole.
UNMAPPED_2026_09_PROCESSES = {
    # --- leaking: service_output (VAR_FOut, carrier Heat) rules read these ---
    "RW2FOILN3": "Residential urban decentral oil heater",
    "RW4FOILN3": "Residential rural oil heater",
    "RWAPOILN3": "Residential urban decentral oil heater",
    "RWN4FPELN3": "Residential rural biomass heater",
    "RWNAPPELN3": "Residential urban decentral biomass heater",
    "RHN4FHETN1": "District heating",
    "RHNAPHETN1": "District heating",
    "CHCSELCHP201": "Commercial Heat pump",
    "CHNCSELCHP301": "Commercial Heat pump",
    "CHNBPLTH101": "Commercial Heat Exchanger",
    # --- invisible but not leaking: `Industry` is read only by the ammonia /
    # --- methanol rules, both `expect: zero`; `CHP` by no rule at all.
    "ECHPP_AED_BGS_N": "CHP",
    "CHPINDGMXIMLN00_N": "Industry",
    "CHPINDGMXIPON00_N": "Industry",
    "ICHSTMBIO01": "Industry",
    "ICHSTMHFO01": "Industry",
    "ICHSTMLFO01": "Industry",
    "INMSTMCOK01": "Industry",
    "INMSTMHET01": "Industry",
    "IOIPRCBIO01": "Industry",
}

#: Heat-technology labels that no ``service_output`` rule lists **on purpose**,
#: because the energy is measured somewhere else in the chain. Exporting them too
#: would double-count. Listed explicitly so a *new* orphan label still fails.
NON_SERVICE_OUTPUT_HEAT_LABELS = {
    # Measured as a fuel input by the `low-temperature heat` rule (VAR_FIn on
    # `Geothermal`), i.e. the geothermal input rather than the "Heat for
    # industry" output.
    "Geothermal (IND)",
    # Fuel-delivery gateway: hands COMGEO to the commercial geothermal
    # appliances, whose *output* is what `services geothermal` exports. Counting
    # the gateway as well would export the same energy twice.
    "Fuel Tech - Geothermal",
}

#: Mapped, but under a label no heat rule listed (`other demand`), which leaks
#: just as effectively as having no row at all.
MISLABELLED_HEAT_PROCESSES = {
    "CHSADUM-DEM": "Commercial CHP heat",
    # GEO = geothermal; this one read `Commercial gas boiler` while its four
    # CH*GEO100 siblings were all correct.
    "CHBAGEO100": "commercial Geothermal",
}


# --------------------------------------------------------------------------- #
# Data tests — mapping_processes.csv / extraction_rules.csv only, no .vd needed
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def process_labels(mappings_dir: Path) -> dict[str, str]:
    meta = load_metadata(mappings_dir)
    df = meta.processes_df
    return {
        str(p).strip(): str(lbl or "").strip()
        for p, lbl in zip(df["Process"], df["Aggregation Level 2"])
    }


def test_leaked_heat_processes_are_mapped(process_labels):
    """Each process behind the heat leak carries its intended label."""
    missing = [p for p in LEAKED_HEAT_PROCESSES if p not in process_labels]
    assert not missing, (
        "These heating processes are absent from mapping_processes.csv again — "
        "their useful-heat output cannot be matched by any extraction rule and "
        f"will not reach pypsa-wal: {sorted(missing)}"
    )
    wrong = {
        p: (process_labels[p], want)
        for p, want in LEAKED_HEAT_PROCESSES.items()
        if process_labels[p] != want
    }
    assert not wrong, f"Aggregation Level 2 changed unexpectedly: {wrong}"


def test_2026_09_unmapped_processes_are_mapped(process_labels):
    """The September-2026 `.vd` vintages carry their intended labels.

    Same guard as :func:`test_leaked_heat_processes_are_mapped`, one wave later.
    The residential and tertiary entries are the ones that actually leaked; the
    industrial ones are pinned too so the *reason* they are harmless — a label no
    exporting rule reads — stays a deliberate choice rather than an accident.
    """
    missing = [p for p in UNMAPPED_2026_09_PROCESSES if p not in process_labels]
    assert not missing, (
        "These processes are absent from mapping_processes.csv again — no "
        "extraction rule can match them, in any sector, on either side: "
        f"{sorted(missing)}"
    )
    wrong = {
        p: (process_labels[p], want)
        for p, want in UNMAPPED_2026_09_PROCESSES.items()
        if process_labels[p] != want
    }
    assert not wrong, f"Aggregation Level 2 changed unexpectedly: {wrong}"


def test_mislabelled_heat_processes_corrected(process_labels):
    wrong = {
        p: (process_labels.get(p), want)
        for p, want in MISLABELLED_HEAT_PROCESSES.items()
        if process_labels.get(p) != want
    }
    assert not wrong, (
        "A heat producer carries a label no heat rule lists, so its output leaks: "
        f"{wrong}"
    )


def test_every_service_output_label_is_reachable_by_a_rule(mappings_dir: Path):
    """No process may carry a heat label that no extraction rule actually lists.

    ``CHSADUM-DEM`` leaked precisely this way: it *had* a label (``other demand``)
    and therefore looked mapped, but no ``service_output`` rule named it.
    """
    meta = load_metadata(mappings_dir)
    rules = pd.read_csv(meta.extraction_rules_file)
    svc = rules[rules["measure_at"].astype(str).str.strip() == "service_output"]
    listed: set[str] = set()
    for cell in svc["process_agg"].astype(str):
        listed.update(p.strip() for p in cell.split(";") if p.strip())

    labels = {
        str(lbl or "").strip()
        for lbl in meta.processes_df["Aggregation Level 2"]
        if str(lbl or "").strip()
    }
    # Only labels that at least one service_output rule *family* would want.
    heatish = {
        lbl
        for lbl in labels
        if any(
            k in lbl.lower()
            for k in ("heater", "boiler", "heat pump", "geothermal", "solar thermal",
                      "heat exchanger", "chp heat", "district heating")
        )
    }
    orphans = sorted(heatish - listed - NON_SERVICE_OUTPUT_HEAT_LABELS)
    assert not orphans, (
        "These heat-technology labels exist in mapping_processes.csv but no "
        "service_output rule lists them, so any process carrying one exports "
        f"nothing: {orphans}. If that is deliberate (the energy is measured "
        "elsewhere in the chain), add it to NON_SERVICE_OUTPUT_HEAT_LABELS with "
        "the rule that does cover it."
    )


def test_service_output_carriers_discovered_from_rules():
    """The closure check must self-configure from extraction_rules.csv."""
    assert "Heat" in SERVICE_OUTPUT_CARRIERS, (
        "No service_output rule declares a carrier — service_output_coverage "
        "would silently check nothing."
    )


# --------------------------------------------------------------------------- #
# Unit tests — synthetic frames, shaped like the real defect
# --------------------------------------------------------------------------- #


def _tagged(rows: list[dict]) -> pd.DataFrame:
    base = {
        "year": 2040,
        "region": "RW",
        "variable": "VAR_FOUT",
        "commodity_sector": "RSD",
        "pypsa_carrier": "Heat",
        "process_agg": "Residential rural gas heater",
        "process": "",
        "exported": True,
        "commodity_code": "RH4F",
    }
    return pd.DataFrame([{**base, **r} for r in rows])


def test_unmapped_process_report_splits_missing_row_from_blank_label():
    flows = _tagged(
        [
            {"process_code": "GOOD", "value": 5.0},
            {"process_code": "NOROW", "process_agg": "", "value": 2.0},
            {"process_code": "BLANK", "process_agg": "  ", "value": 1.0},
            {
                "process_code": "NOROW",
                "process_agg": "",
                "variable": "VAR_FIN",
                "value": 3.0,
            },
        ]
    )
    out = unmapped_process_report(flows, mapped_processes=["GOOD", "BLANK"])
    assert list(out["process_code"]) == ["NOROW", "BLANK"], "must sort by gross PJ"
    noro = out.set_index("process_code").loc["NOROW"]
    assert noro["reason"] == "missing_row"
    assert noro["fin_pj"] == pytest.approx(3.0)
    assert noro["fout_pj"] == pytest.approx(2.0)
    assert noro["gross_pj"] == pytest.approx(5.0)
    assert out.set_index("process_code").loc["BLANK", "reason"] == "blank_label"
    # A labelled process is never reported, however large.
    assert "GOOD" not in set(out["process_code"])


def test_unmapped_process_report_empty_when_all_labelled():
    flows = _tagged([{"process_code": "A", "value": 1.0}])
    assert unmapped_process_report(flows, mapped_processes=["A"]).empty


def test_service_output_coverage_finds_the_gap():
    tagged = _tagged(
        [
            {"process_code": "OK", "value": 90.0, "exported": True},
            # the RH2FPELN2 shape: no label, so no rule, so not exported
            {"process_code": "LEAK", "process_agg": "", "value": 10.0, "exported": False},
        ]
    )
    cov = service_output_coverage(tagged)
    assert len(cov) == 1
    row = cov.iloc[0]
    assert row["produced_pj"] == pytest.approx(100.0)
    assert row["exported_pj"] == pytest.approx(90.0)
    assert row["gap_pj"] == pytest.approx(10.0)
    assert row["exported_share"] == pytest.approx(0.9)
    assert row["n_unexported_rows"] == 1


def test_service_output_coverage_ignores_var_fin():
    """Symmetric to the old blind spot: this check owns the FOut side only."""
    tagged = _tagged(
        [
            {"process_code": "OK", "value": 10.0, "exported": True},
            {"process_code": "FUEL", "variable": "VAR_FIN", "value": 99.0,
             "exported": False},
        ]
    )
    cov = service_output_coverage(tagged)
    assert cov.iloc[0]["gap_pj"] == pytest.approx(0.0)


def test_service_output_coverage_scoped_to_declared_carriers():
    """Fuel/lighting/cooling output is not a service-output claim — not a gap.

    Without this scope the check reported ~280 PJ of by-design exclusions on the
    reference scenario and buried the 7.7 PJ that mattered.
    """
    tagged = _tagged(
        [
            {"process_code": "OK", "value": 10.0, "exported": True},
            {
                "process_code": "LIGHT",
                "pypsa_carrier": "Commercial lighting",
                "commodity_sector": "COM",
                "value": 500.0,
                "exported": False,
            },
        ]
    )
    cov = service_output_coverage(tagged)
    assert set(cov["pypsa_carrier"]) == {"Heat"}
    assert cov["gap_pj"].sum() == pytest.approx(0.0)


def test_service_output_gap_survives_a_process_with_no_sector():
    """The killer case: an unmapped process has no *process* sector.

    ``qa_coverage_gap`` filters on ``tagged['sector']``, so it dropped exactly the
    rows that leaked. Keying on the *commodity's* sector is what fixes it.
    """
    tagged = _tagged(
        [
            {"process_code": "OK", "value": 10.0, "exported": True},
            {
                "process_code": "RW4FPELN3",
                "process_agg": "",
                "value": 1.3,
                "exported": False,
            },
        ]
    )
    tagged["sector"] = ["RSD", ""]  # unmapped process: no sector at all
    gap = service_output_gap(tagged)
    assert list(gap["process_code"]) == ["RW4FPELN3"]
    assert gap["value"].sum() == pytest.approx(1.3)


def test_flag_leak_suspects_marks_grey_sibling_of_coloured_ribbons():
    links = pd.DataFrame(
        {
            "source": ["Residential rural gas heater", "RW4FPELN3", "Coal mine"],
            "target": ["Buildings: built area", "Buildings: built area", "Power plants"],
            "value": [17.3, 1.0, 50.0],
            "commodity": ["Heat", "Heat", "Coal"],
            "export_status": ["exported", "context", "context"],
        }
    )
    out = flag_leak_suspects(links)
    assert list(out["leak_suspect"]) == [False, True, False], (
        "the grey Heat ribbon landing where coloured Heat ribbons land is the "
        "suspect; unrelated grey supply-chain links must stay grey"
    )


def test_flag_leak_suspects_ignores_carriers_outside_service_scope():
    """Electricity into Industry is partly exported *by design* (anchoring)."""
    links = pd.DataFrame(
        {
            "source": ["Fuel Tech - Electricity", "Power plants"],
            "target": ["Industry", "Industry"],
            "value": [6.4, 5.5],
            "commodity": ["Electricity", "Electricity"],
            "export_status": ["exported", "context"],
        }
    )
    assert not flag_leak_suspects(links)["leak_suspect"].any()


def test_flag_leak_suspects_all_exported_is_clean():
    links = pd.DataFrame(
        {
            "source": ["a", "b"],
            "target": ["Buildings: built area"] * 2,
            "value": [1.0, 2.0],
            "commodity": ["Heat", "Heat"],
            "export_status": ["exported", "exported"],
        }
    )
    assert not flag_leak_suspects(links)["leak_suspect"].any()


# --------------------------------------------------------------------------- #
# Integration — the real .vd must close
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def tagged_by_year(times_model, mappings_dir: Path):
    meta = load_metadata(mappings_dir)
    rules = load_extraction_rules(meta.extraction_rules_file)
    horizons = [y for y in (2025, 2030, 2040, 2050) if y in times_model.years]
    return {
        y: tag_flows_with_rules(times_model.energy_flows(y), rules, apply_netting=False)
        for y in horizons
    }, meta


def test_building_heat_closes_in_every_horizon(tagged_by_year):
    """Every PJ of useful heat TIMES produces must reach a rule.

    This is the assertion the report never made. It is a *closure* statement —
    produced vs exported — so unlike the parent–child and reconciliation checks it
    cannot be satisfied by a process being absent from both sides.
    """
    tagged, _meta = tagged_by_year
    failures = []
    for year, tg in tagged.items():
        cov = service_output_coverage(tg)
        if cov.empty:
            continue
        gap = float(cov["gap_pj"].sum())
        if gap > 1e-6:
            detail = service_output_gap(tg).head(10).to_string(index=False)
            failures.append(f"{year}: {gap:.4f} PJ unexported\n{detail}")
    assert not failures, (
        "Demand-sector service output that no extraction rule matches — this "
        "energy never reaches pypsa-wal:\n" + "\n\n".join(failures)
    )


def test_no_heat_producer_lacks_an_aggregation_label(tagged_by_year):
    """Root cause, stated directly against the .vd."""
    tagged, meta = tagged_by_year
    offenders: dict[str, float] = {}
    for tg in tagged.values():
        rep = unmapped_process_report(
            filter_energy_carrier_flows(tg), meta.processes_df["Process"]
        )
        if rep.empty:
            continue
        heat = rep[rep["sector"].astype(str).str.contains("RSD|COM", na=False)]
        for row in heat.itertuples():
            offenders[row.process_code] = max(
                offenders.get(row.process_code, 0.0), float(row.gross_pj)
            )
    assert not offenders, (
        "Building-sector processes carrying energy with no Aggregation Level 2 "
        "label — no extraction rule can match them:\n"
        + "\n".join(f"  {k}: {v:.3f} PJ" for k, v in sorted(offenders.items()))
    )


def test_no_energy_carrying_process_is_unmapped(tagged_by_year):
    """Nothing carrying energy may lack an `Aggregation Level 2` label — any sector.

    Broader than the heat-only check above. An unmapped process is not always a
    soft-link leak (an unmapped power plant distorts only the Sankey, since
    pypsa-wal imports TIMES *demands*, not generation), but it always draws an
    anonymous node and always sits outside every rule, so it can become one
    silently. Cheaper to keep the set empty than to re-audit it each time.
    """
    tagged, meta = tagged_by_year
    offenders: dict[str, tuple[str, float]] = {}
    for tg in tagged.values():
        rep = unmapped_process_report(
            filter_energy_carrier_flows(tg), meta.processes_df["Process"]
        )
        for row in rep.itertuples():
            prev = offenders.get(row.process_code, ("", 0.0))
            offenders[row.process_code] = (
                row.reason,
                max(prev[1], float(row.gross_pj)),
            )
    assert not offenders, (
        "Processes carrying energy with no Aggregation Level 2 label. Give each a "
        "label reusing an EXISTING one where possible (a new label = a new Sankey "
        "node), and check first that the label is not read by an extraction rule "
        "that would newly export it:\n"
        + "\n".join(
            f"  {k}: {v[0]}, up to {v[1]:.3f} PJ" for k, v in sorted(offenders.items())
        )
    )


def test_h2_producers_are_not_in_the_road_delivery_group(process_labels):
    """`Fuel Tech - H2` is the *road* H2 delivery chain, read by `total road`.

    That rule's commodity scope is carrier-OR-code and includes `Electricity`, so
    a grid-fed electrolyser sitting in this group has its electricity exported as
    road transport fuel. `SELCH2EC01` did exactly that — harmlessly, because the
    large alkaline unit is not built in the reference scenario, but live for any
    scenario that builds it. Producers belong on `SUP_PRE_PJ_GW` / `H2 production`.
    """
    producers = [
        p
        for p in process_labels
        if p.startswith(("SELCH2", "SGASH2")) and not p.endswith("_LowAF")
    ]
    assert producers, "no H2-production processes found — has the mapping changed?"
    in_delivery = [p for p in producers if process_labels[p] == "Fuel Tech - H2"]
    assert not in_delivery, (
        "H2 *production* processes in the road H2 *delivery* group; their "
        f"electricity input is exported as `total road` fuel: {in_delivery}"
    )


def test_every_local_vd_closes(mappings_dir: Path):
    """Sweep **every** `.vd` present locally, not just the fixture.

    The mapping is shared by all scenarios, but a leak is only visible in the
    scenarios that actually build the offending technology: `scen_demande_haute`
    was already clean while `scen_alternatif` still lost 2.78 PJ (a pellet boiler
    and tertiary geothermal), `scen_base` 0.51 PJ and `scen_base_coherence`
    0.31 PJ. Checking one scenario proves nothing about the others.

    `data/*.vd` is gitignored, so this skips in CI and guards local work.
    """
    from times_pypsa.model import load_times_annual_flows
    from times_pypsa.pipeline import PipelineConfig

    vds = sorted(Path(mappings_dir).glob("*.vd"))
    if not vds:
        pytest.skip("no local .vd files (they are gitignored)")
    meta = load_metadata(mappings_dir)
    rules = load_extraction_rules(meta.extraction_rules_file)
    failures = []
    for vd in vds:
        vdt = vd.with_suffix(".vdt")
        model = load_times_annual_flows(
            vd, mappings_dir, vdt_file=vdt if vdt.exists() else None,
            config=PipelineConfig(start_year=2025),
        )
        for year in model.years:
            tg = tag_flows_with_rules(model.energy_flows(year), rules, apply_netting=False)
            gap = service_output_coverage(tg)
            gap_pj = float(gap["gap_pj"].sum()) if not gap.empty else 0.0
            unmapped = unmapped_process_report(
                filter_energy_carrier_flows(tg), meta.processes_df["Process"]
            )
            if gap_pj > 1e-6 or not unmapped.empty:
                failures.append(
                    f"{vd.name} {year}: service gap {gap_pj:.4f} PJ, "
                    f"{len(unmapped)} unmapped process(es) "
                    f"{list(unmapped['process_code'][:5])}"
                )
    assert not failures, "\n".join(failures)


def test_sankey_shows_no_leak_suspect_ribbons(tagged_by_year):
    """The visual counterpart: no orange ribbon should survive the fix."""
    from times_pypsa.qa import (
        _process_activity_units,
        aggregate_system_flows,
        prepare_system_sankey_links,
    )

    tagged, meta = tagged_by_year
    units = _process_activity_units(meta.processes_df)
    offending = []
    for year, tg in tagged.items():
        agg = aggregate_system_flows(tg, apply_netting=True)
        links = prepare_system_sankey_links(
            tg, apply_netting=True, process_activity_units=units, system_agg=agg
        )
        if "leak_suspect" not in links.columns:
            continue
        sus = links[links["leak_suspect"]]
        if not sus.empty:
            offending.append(
                f"{year}: {len(sus)} ribbons, {sus['value'].sum():.3f} PJ\n"
                + sus[["source", "target", "value"]].head(8).to_string(index=False)
            )
    assert not offending, (
        "Grey Sankey ribbons landing where their soft-linked siblings land:\n"
        + "\n\n".join(offending)
    )
