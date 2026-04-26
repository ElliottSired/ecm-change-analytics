# Engineering Change Intelligence V1

Engineering Change Management A commissioning programme generates thousands of engineering changes that need to be tracked, prioritised, and closed before key milestones. The pipeline normalises messy multi-system change data into a dimensional model that shows which systems are stagnating, which milestones are at risk, and where intervention is needed before the window closes.

## Executive Summary

### Problem Statement: Hidden Risk in EPC Projects

Within large EPC projects, 'headline health' is a constant risk. High-level KPIs often look fine on a slide deck, while the actual system-level bottlenecks are quietly building up under the surface. A primary driver of this "false health" is the reliance on assumed milestones provided at the source, which often mask the true urgency of engineering changes. Furthermore, headline reporting frequently overlooks disparities in system performance; while a project may appear healthy on average, specific critical systems may be suffering from disproportionate stagnation and extended cycle times. Without granular visibility into these "stuck" changes, risks remain hidden until they impact the critical path, preventing timely intervention.

### The Solution: Multi-Grain Dimensional Modelling

I built this notebook to show how a production-grade ETL pipeline and a solid Dimensional Model can actually surface those risks before they hit the critical path. By analysing programme change data across multiple grains; `ChangeID`, System, Contract, and Building/Zone — stakeholders gain the visibility required to identify exactly where stagnation is occurring. This allows for the generation of accurate burndown curves and ageing reports based on real technical constraints rather than optimistic placeholders.

### The Core Insights: What the Pipeline Reveals

The pipeline derives the Driving Milestone for each change — the earliest milestone deadline across all impacted systems — using a SQL window function ranked by target date. This contrasts with the Assumed Milestone recorded at raise, which reflects the change manager's best estimate rather than the true programme constraint. At scale, the gap between these two views is substantial: changes assumed against M3 or M4 frequently drive M1 or M2 systems, concentrating unresolved work against earlier deadlines than the programme recognises. The burndown dashboard surfaces this gap directly — the assumed view shows a healthy, spread distribution across milestones while the driving view reveals a concentration of unresolved work against earlier deadlines the programme cannot meet. This distinction is invisible in standard change register reporting and is a strong analytical contribution of this pipeline.

Beyond milestone misclassification, the pipeline identifies where the programme is structurally struggling to resolve changes across every reporting level. Stagnation scoring weights open changes by age and priority level, surfacing systems that are genuinely stuck rather than simply busy. Historical lead time analysis reveals whether poor performance is consistent or driven by outliers. The milestone countdown reveals whether current closure velocity is sufficient to clear each system's backlog before its deadline. The same analytical pattern — backlog, lead time, stagnation, and readiness — is then applied at contract grain to identify underperforming contractors, at building grain to surface where on site changes are concentrating, and at zone type grain to flag risk by access classification. The result moves reporting beyond headline change counts toward a complete picture of where the programme is struggling and at what level intervention is needed.

## Data Architecture & Pipeline Design

The architecture utilises a Medallion Pipeline to transform raw data into actionable intelligence through three distinct stages:
* Bronze Layer — Raw data is ingested via a replicated API, preserving the original state of the Change Register for full auditability and traceability.
* * Silver Layer — Data is cleansed and structured into a Kimball-style Star Schema using an accumulating snapshot fact table design, one row per engineering change, updated in place as it progresses through its lifecycle. The current implementation overwrites each row on monthly refresh, preserving current state only with no historic attribute changes retained. The fact table is enriched with meaningful business logic and calculations including driving milestone derivation, physical vs documentary classification, and need date buffer logic. 
* Gold Layer — The pipeline culminates in high-performance reporting views organised into four distinct reporting grains:
    * `ChangeID` — Individual change analysis covering stagnation, lead times, raised late detection, and overdue banding.
    * `System` — Technical readiness and commissioning alignment.
    * `Contract` — Commercial accountability and contractor-specific backlogs.
    * `Building & Zone` — Spatial coordination and access classification analysis to identify restricted zone risk and physical congestion.

### Silver Table Design Principle

A core data engineering discipline applied across the notebook is the separation of transformation from presentation. This principle is fully implemented for location grain analysis through `fact_location_exploded` — built once in Cell 4 and consumed by all building and zone analytical cells downstream. Known inconsistencies remain in the system and contract grain cells where the join chain is built in the analytical layer rather than the transformation layer — these are documented in the Known Architectural Improvements section.

## Technical Implementation

To handle the real-world complexity of engineering data, the model incorporates Bridge Tables within the Star Schema. This allows the pipeline to resolve many-to-many relationships — such as a single `ChangeID` impacting multiple systems or spanning several physical buildings — without compromising data integrity or inflating metric totals. Without the bridge table a change impacting three systems would be counted three times in any system-level aggregation, producing inflated backlog counts and incorrect readiness percentages. The bridge table ensures each change counts once per system it genuinely impacts, and deduplication at the appropriate grain ensures counts remain correct at system, contract, building, and zone type level.

### Cell 1 Replicated API Call

Simulates a Bronze layer API ingestion from an Aconex-style change management system. In a production deployment this cell would be replaced with a live REST API call or a scheduled CSV export. The synthetic generation replicates the structure and statistical behaviour of a real change register — 15,000 records at `ChangeID` grain with the same fields a real system would return. `SIM_TODAY` = date(2026, 2, 2) is defined in this cell as the single source of truth for all date-dependent calculations. To run the analysis at a different reporting date change `SIM_TODAY` in Cell 1 only as all downstream calculations will update automatically.

The raw fields returned are: `ChangeID`, `DateOpened`, `DateClosed`, `DateValidated`, TargetClosureDate, Status, Level, `SystemsImpacted`, Reason, `PotentialConsequences`, `AssumedMilestone`. These are stored in `fact_table` without any transformation, exactly as they would arrive from the source system.

### Cell 2 Dimensions

This cell contains all static project reference data — the dimension tables that give raw change records their analytical context. In a production deployment these would be loaded from project configuration files, schedule exports, or a system breakdown structure document.

Four dimension tables are built and written to SQLite:

- `contract_dim` maps each system code to its parent contract.
- `systems_master` maps each system code to its commissioning milestone.
- `commissioning_milestones` contains the four programme milestones — System Installation, Initial Testing, Verification, and Handover — with their target dates.
- `date_dim` is a full date spine from 2021 to 2027 covering the project lifecycle.

A coverage check at the end validates that every system exists in both `systems_master` and `contract_dim`. Any gap would produce UNKNOWN or UNASSIGNED values in the enriched fact table downstream.

### Cell 2b Dimensions

This cell builds the location hierarchy — the dimension tables that enable building and zone grain analysis.
Three tables are built and written to SQLite:

* `building_dim` — ten buildings covering the project site with building type classification
* `zone_dim` — named zones within each building with access classification — Restricted, Controlled, Operational, Critical
* `system_location_bridge` — maps each system code to the buildings and zones it occupies

A system can span multiple buildings and multiple zones within a building. A system with no specific zone assignment is given a whole-building scope — these appear as 'Whole Building' in the zone type analysis rather than being dropped. A coverage check validates that every system in `systems_master` has at least one location assignment.

### Cell 3 System Normalisation

This cell normalises the raw `SystemsImpacted` string from the change register into a bridge table, resolving the many-to-many relationship between changes and systems. A single change can impact multiple systems — `SystemsImpacted` arrives as a comma-separated string such as "ABB, DEF, XYZ/A" which is parsed, slash-expanded, and written to `systems_bridge` as one row per `ChangeID` × `SystemCode` combination. Built once here and reused by all downstream system, contract, building, and zone grain analysis.

### Cell 4 Enrichment of Fact Table

Cell 4 builds the silver layer — a multi-CTE SQL enrichment query that joins `fact_table` to the dimension tables and derives all analytical fields used by downstream cells.
The key business logic applied:

* `Physical_Impacts` — classifies each change as physical or documentary based on `PotentialConsequences`
* `Driving_Milestone` — derived using a `ROW_NUMBER()` window function partitioned by `ChangeID` and ordered by earliest Project_Target_Date, picking the earliest milestone deadline across all impacted systems
* `Actual_Need_Date` — the internal engineering buffer: Milestone deadline minus one year for physical changes, minus three months for documentary
* `Days_to_Close` — lead time for closed changes
* `Current_Age_Days` — age of open changes as of `SIM_TODAY`
* `Days_Until_Need` — days remaining from `SIM_TODAY` to `Actual_Need_Date` for open changes
* `Days_Overdue` — how late a closed change was relative to its need date

The result is `fact_enriched` — the central silver table at `ChangeID` grain that every analytical cell reads from. Also in this cell `fact_location_exploded` is pre-built at `ChangeID` × System × Building × Zone grain, running the full location join chain once and storing the result for all building and zone cells downstream.

### Cell 5 — Inflow, Outflow and Average Lead Time

This cell produces two charts per impact type — physical and non-physical — giving four charts in total.

Chart 1 — Monthly Inflow vs Outflow
Plots the number of changes opened and closed each month on the same axis — opened as positive values, closed as negative. The gap between the two lines at any point shows whether the programme is accumulating or resolving its backlog. A widening gap indicates more changes are being raised than closed. A narrowing gap indicates the programme is recovering. This is the first analytical output in the notebook and sets the programme trajectory context before the more detailed analysis in later cells.

Chart 2 — Average Lead Time by Month
Grouped bar chart showing average `Days_to_Close` per Level per month for closed changes. The x axis is the month of closure, the y axis is average days. This flags whether the programme is getting faster or slower at resolving changes over time and whether specific months saw unusually long or short closure times. Faceted by Level so the reader can see whether the trend is driven by complex Level changes or simpler `Level 3` work.
Both charts are produced twice — once for physical impacts and once for non-physical — so the reader can see whether the two populations are behaving differently.

### Cells 6 & 7 — Burndown Dashboard

Cell 6 prepares the burndown datasets. A reusable `get_burndown_data()` function builds cumulative open change counts across the full project timeline for any milestone grouping column. Two datasets are produced — `burndown_assumed` using `AssumedMilestone` and `burndown_need` using `Driving_Milestone`. These feed directly into the Cell 7 dashboard.
Cell 7 produces the headline analytical output of the notebook — ten burndown charts arranged as two groups of five.

Charts 1-5 — Assumed Milestone View
The overall project burndown followed by one chart per milestone — M1 through M4 — based on the milestone recorded at raise. This is the view a standard change management system would produce. The distribution looks healthy — changes spread across milestones, no single deadline appears overloaded, the programme appears on track.

Charts 6-10 — Driving Milestone View
The same population re-analysed using the derived Driving Milestone — the earliest milestone deadline across all systems a change actually impacts. This is where the core insight of the pipeline becomes visible. Changes assumed against M3 and M4 shift to M1 and M2 when viewed by driving milestone, concentrating unresolved work against earlier deadlines. The overall driving burndown reveals a programme that is significantly more at risk than the assumed view suggests.

Charts 6 through 9 — the individual driving milestone charts — include a 90-day forecast extension. Closure velocity from the last 90 days is projected forward as a dashed line showing the predicted date at which each level's backlog reaches zero. A diamond marker indicates the predicted clearance date. Where this date falls after the milestone target line the programme cannot clear its backlog in time at current velocity.

The contrast between the assumed and driving views is the central analytical contribution of this notebook — the gap between the two represents programme risk that is invisible in standard reporting.

### Cell 7b - Cumulative Raised vs Closed

A programme-level time series chart showing cumulative changes raised and cumulative changes closed from project start to `PROJECT_END`. The gap between the two lines represents the current open backlog — the wider the gap the more unresolved work the programme is carrying.
The red line shows cumulative changes raised over time. The green line shows cumulative changes closed. The shaded area between them is the open backlog at any point in the timeline. The `SIM_TODAY` marker shows the current reporting date — everything to the right is the forecast period where no further data exists.
This chart reads directly from `fact_enriched` at `ChangeID` grain with no joins required — the simplest analytical cell in the notebook alongside Cell 8.

#### Note on shape
On a real EPC project this chart would show a classic S-curve — slow start during early design, rapid acceleration during detailed design and procurement, then flattening as the project approaches completion and changes are closed out. In the synthetic data changes are distributed relatively evenly across the project timeline so the lines are more linear than S-shaped. The analytical value — showing the open backlog gap and whether the programme is closing changes faster than it raises them — is the same regardless of shape.


### Cell 8 — Historical `ChangeID` Grain Analysis

This cell analyses closed changes only, staying at `ChangeID` grain throughout using `fact_enriched`. No joins are needed — `fact_enriched` is already at `ChangeID` grain with all the fields required.

Three charts are produced:

Chart 1 — Lead Time Distribution Shows how long changes took to close, split by level and physical vs non-physical impact. Mean, median, and P90 lines are overlaid on each histogram. Reveals whether average lead times in the system analysis are representative or whether they are being pulled by a small number of extreme outliers.

Chart 2 — Overdue Age Banding For changes that closed after their need date, shows how late they were — banded into 0-30 days, 30-90 days, 90-180 days, and 180+ days. The raised late flag identifies changes where `DateOpened` was already after `Actual_Need_Date` — these were overdue before anyone started working on them, which is a process failure rather than a performance failure.

Chart 3 — Closure Lag by Raise Cohort Groups closed changes by the month they were raised and shows average lead time per cohort. A declining trend over time indicates the programme is getting faster at resolving changes as it matures. A rising trend indicates deteriorating performance. A 3-month rolling average is overlaid as a dashed line — this smooths out monthly volatility and makes the underlying trend clearer, particularly in months with low closure volume where a single outlier can skew the average significantly. The stacked bar underneath shows closure volume per cohort.

Four styled tables accompany the charts:

Raised Late Summary — shown before the charts as a data quality signal. One row per `Level` × Impact Type combination showing total changes, count raised late, and percentage raised late. Highlights which categories of change are most frequently raised after their need date — a process governance metric rather than a performance metric.

Overdue Banding Detail — accompanies Chart 2. One row per Level × Impact Type × Overdue Band showing the count of changes in each band. Gives the precise numbers behind the stacked bars.

Master Late Closure List — the most operationally useful table in Cell 8. Every change that closed late, sorted worst first by `Days_Overdue`. Includes `ChangeID`, Level, Impact Type, Date Opened, Date Closed, Actual Need Date, Days to Close, Days Overdue, Overdue Band, Raised Late flag, Driving Milestone, and Driving System. On a real project this table is the starting point for a post-close review — understanding why specific changes closed late and whether the same systems or contractors appear repeatedly.

Cohort Summary — accompanies Chart 3. One row per raise cohort per level showing Avg Lead Time, Median Lead Time, and % Closed Late. Background gradient from red to green on Avg Lead Time and red on % Closed Late makes performance patterns immediately visible across the timeline.


### Cell 9 — System Lead Time by Milestone Section

This cell builds a silver table by joining `fact_enriched` to `systems_bridge`, `systems_master`, and `df_milestones` via `LEFT JOIN`, filtered to `closed` changes only.

Two charts are produced — one for physical impacts and one for non-physical.

Each chart surfaces historical lead time per system per level as a range bar — the bar spans minimum to maximum `Days_to_Close` with a black vertical line marking the average. Systems are grouped by milestone and sorted worst average lead time first within each group — matching the stagnation chart sort order in Cell 11 so the reader can compare historical performance against current stagnation consistently.
Bar colour is min-max normalised within each level — the slowest system at each level always appears darkest red and the fastest darkest green, making relative performance immediately visible regardless of absolute lead time values. A system that is slow relative to its peers at the same level will always stand out regardless of whether the absolute lead times are long or short.
A gold table accompanies each chart showing average, minimum, maximum, and P80 lead time per system — the P80 being the lead time that 80% of changes at that system were closed within, which is a more robust performance benchmark than the average when outliers are present.

### Cell 9b — System Velocity vs Backlog

Cell 9b produces a scatter plot comparing current open backlog against recent closure velocity for each system. Two aggregations from `fact_enriched` — open changes and recent closures — are joined through `systems_bridge` and merged at `SystemCode` grain to produce one row per system.

The X axis shows weekly closure rate based on closures in the last 90 days. The Y axis shows current open backlog. Each dot is a system, coloured by its milestone allocation — red for M1, amber for M2, green for M3, blue for M4.

Four quadrants divided by median backlog and median velocity lines:
* High Risk — top left — high backlog, low velocity. These systems cannot close their backlog at current rate and need intervention.
* Watch — top right — high backlog but closing quickly. Manageable if velocity is maintained.
* Stagnant — bottom left — low backlog but barely closing. Not urgent but worth monitoring.
* Healthy — bottom right — low backlog, high velocity. No action needed.
This chart complements Cell 9 — Cell 9 demonstrates historical lead time performance, Cell 9b shows current velocity. A system can have good historical lead times but have stopped closing recently — Cell 9b surfaces that immediately.

### Cell 10 System Backlog

Cell 10 produces two stacked bar charts — physical and non-physical — split by level showing a count of `ChangeID` per system grouped by milestone. `fact_enriched` is filtered by status prior to a `LEFT JOIN` of `systems_bridge` and `system_anchor` to create a silver table `df_system_open` which feeds both charts. Systems are sorted M1 through M4 by milestone deadline then by total backlog within each group — so the chart reads left to right in order of programme urgency.

### Cell 11 — System Stagnation, Milestone Countdown & Gold Tables

This cell is the most analytically rich cell in the notebook. Two silver tables are built before any plotting function runs. Both follow the same pattern — `fact_enriched` is filtered by status first to define the population, then `LEFT JOIN` through `systems_bridge` and `systems_master` to produce `ChangeID` × `SystemCode` grain. `df_precision` contains open changes and feeds the stagnation scoring, countdown chart, and gold tables. `df_closed_recent` contains changes closed in the last 90 days and feeds the current closure rate calculation used in the `On_Track` flag.

System Days to Milestone — horizontal bar chart showing days remaining to each system's milestone deadline. Bar colour reflects both urgency and backlog size — overdue systems with high change counts appear darkest red, healthy systems with low counts appear green. The Level, 2, and 3 breakdown is annotated on each bar. Systems are grouped by milestone in deadline order so the chart reads top to bottom from most urgent to least urgent.

System Stagnation — two range bar charts, physical and non-physical, showing the age distribution of open changes per system per level. Bar width spans minimum to maximum age with a black line marking the average. Colour combines age and count into a stagnation score — a system with many old high-priority changes scores worst. Systems are sorted milestone first then worst stagnation score within each group, matching Cell 9 sort order so historical lead time and current stagnation can be compared directly. A styled data table precedes each stagnation chart showing the raw Avg_Age, Min_Age, Max_Age, ID_Count, and Score per system per level — the numbers behind the visual.

Stagnation Scoring
The stagnation score is calculated at `SystemCode` × Level grain and then summed to `SystemCode` grain:

Score = Avg_Age × (ID_Count × Level_Weight)
Level weights: L1 = 3, L2 = 2, L3 = 1
This means a system with many old high-priority Level changes scores significantly worse than one with the same number of old `Level 3` changes. Age alone is not sufficient — a system with 500 days average age on two `Level 3` changes is less concerning than one with 300 days average age on ten Level changes. The weighting ensures the score reflects genuine programme risk rather than raw age or raw count in isolation.

Three gold tables accompany the charts:
`gold_physical` and `gold_non_physical` — system grain tables showing open changes, days to milestone, required weekly closure rate, current closure rate from `df_closed_recent`, `On_Track` flag, stagnation score, and `Combined_Risk` flag per system. The `On_Track` flag compares current closure velocity against the rate required to clear the backlog before the milestone deadline. The `Combined_Risk` flag combines days to milestone and stagnation score into a single HIGH, MEDIUM, or LOW signal. A system with both a high stagnation score and a deadline within 180 days is flagged HIGH — stagnation combined with proximity to deadline is the most actionable risk signal in the notebook.
`gold_grain` — the master action list at `ChangeID` grain. Every open change with its driving milestone, need date, days until need, and risk classification sorted by urgency. This is the operational output a change manager would work from in a weekly review meeting.

### Cell 12 — System Readiness

This cell shows how far each system is from having zero open changes, expressed as a percentage of total changes closed and as a raw closed versus total count. 

View 1 — All Systems
Horizontal RAG bar chart — red below 80%, amber 80% to 95%, green at 95% and above. Each bar highlights the percentage and the closed versus total count. Systems grouped by milestone with pill badges on the left margin.

View 2 — Single System Drilldown
Two charts side by side — readiness by level and readiness by physical vs documentary. Shows whether the remaining open gap is concentrated in critical Level changes or spread across lower priority work.

Shared table
Total, Closed, Open Balance, and Ready % per level/physical impact with a red to green gradient on the Ready % column.

### Cell 13 — System Risk Profile

Cell 13 produces a single horizontal stacked bar chart showing the distribution of change reasons per system, grouped into four impact categories:
* Field & Design Conflict — site conditions, constructability issues, clash detection, interface misalignment, quality non-conformances
* Supply & Equipment — material availability, equipment replacement, vendor deviations, procurement changes
* Design Evolution — design optimisation, client requirement changes, regulatory updates, safety enhancements
* Admin & Updates — drawing updates, calculation revisions, scope clarifications, late engineering input

This chart visualises why changes are being raised against each system.

### Cell 14 — Contract Grain Analysis

Cell 14 applies the same analytical pattern as the system grain cells — backlog, lead time, stagnation, readiness, and velocity — at contract grain. All contract grain analysis is consolidated in a single cell rather than split across multiple cells as with system grain.
A silver table `df_contract_base` is built inline by joining `fact_enriched` through `systems_bridge` to `contract_dim`, deduplicated on `ChangeID` × `Contract`. This is then split into open and closed populations for the respective analyses.
Four outputs are produced:

Contract Backlog — stacked bar chart showing `open`/`validated` changes per `contract` by `level`. `Physical` and `non-physical` produced separately.

Contract Lead Time — range bar chart showing historical lead time per `contract` per `level` for closed changes. Sorted worst average lead time first. Colour min-max normalised within level — slowest contract darkest red, fastest darkest green.

Contract Stagnation — range bar chart showing age distribution of open changes per contract per level. Sorted worst stagnation score first.

Contract Readiness Widget — dropdown with two views. All Contracts showing RAG readiness bars per contract. Single contract drilldown showing readiness by level and physical vs documentary split.

Contract Velocity vs Backlog — scatter plot comparing current open backlog against recent closure velocity per contract. Four quadrants identify which contractors need commercial intervention.
In a production implementation Cell 14 would be split into separate cells following the system grain pattern, each reading from a pre-built `df_contract_enriched` silver table in Cell 4.

### Cell 15 — Building Backlog 

Two stacked bar charts — `physical` and `non-physical` — showing `open` or `validated` changes per building by level.

### Cell 16 — Building Analysis 

Two silver tables built by filtering and deduplicating `fact_location_exploded`:
* `df_closed_buildings` — closed changes deduplicated on `ChangeID` × `Building_Name` — feeds lead time range bar charts
* `df_active_buildings` — open changes deduplicated on `ChangeID` × `Building_Name` — feeds stagnation range bar charts
Four charts produced — lead time physical, lead time non-physical, stagnation physical, stagnation non-physical. Each accompanied by a gold table.

### Cell 16b — Building Velocity vs Backlog 

Scatter plot comparing current open backlog against recent closure velocity per building, coloured by building type. Four quadrants identify which buildings need site management intervention.

### Cell 17 — Building Readiness 

Dropdown widget with All Buildings RAG bar chart and single building drilldown. Same pattern as Cell 12 at building grain.

### Cells 18-20 — Zone Type Grain Analysis

Zone type grain also reads from `fact_location_exploded`, deduplicated on `ChangeID` × `Zone_Type`. Whole-building assignments where `Zone_Type` is `NULL` are filled as `Whole Building` rather than dropped.

### Cell 18 — Zone Type Backlog 

Two stacked bar charts — `physical` and `non-physical` — showing open changes per zone type by level. Zone types are access classifications — `Restricted`, `Controlled`, `Operational`, `Critical`, and `Whole Building`.

### Cell 19 — Zone Type Analysis 

Two silver tables built by filtering and deduplicating `fact_location_exploded`:
* `df_closed_zonetypes` — closed changes deduplicated on `ChangeID` × `Zone_Type` — feeds lead time range bar charts
* `df_active_zonetypes` — open changes deduplicated on `ChangeID` × `Zone_Type` — feeds stagnation range bar charts
Four charts produced — lead time physical, lead time non-physical, stagnation physical, stagnation non-physical. Each accompanied by a gold table.

### Cell 20 — Zone Type Readiness 

Dropdown widget with All Zone Types RAG bar chart and single zone type drilldown. Same pattern as Cells 12 and 17 at zone type grain.
Zone type grain answers a different question to building grain — not where on site changes are concentrating but which access classifications carry the most risk. A Restricted zone with high stagnation and low readiness is a safety and access planning signal that building grain alone would not surface.

## Analytical Findings

The pipeline was run against a synthetic change register of 15,000 engineering changes across 26 systems, 6 contracts, and 4 commissioning milestones on a nuclear EPC programme. `SIM_TODAY` is set to 2 February 2026. The findings below are derived directly from the pipeline outputs.

### The assumed versus driving milestone gap is substantial

74.5% of all 15,000 changes — 11,170 records — have a different driving milestone to their assumed milestone when changes are reassigned from the milestone recorded at raise to the earliest deadline across all systems the change actually impacts. This is the central analytical finding of the pipeline. Changes assumed against M3 and M4 shift forward to M1 and M2 when viewed through the driving milestone lens, concentrating unresolved work against earlier deadlines the programme does not recognise in standard reporting. The gap between the assumed and driving burndown curves is invisible in a conventional change register and is only surfaced by the dimensional model.

### Open backlog is concentrated against near-term and overdue milestones

Of the 708 open and validated changes at the reporting date — 189 drive M1, a milestone with a deadline of 30 June 2025 which passed 217 days before the reporting date. A further 85 drive M2 with the 31 March 2026 deadline 57 days away. The 386 changes driving M3 and 48 driving M4 represent the healthier tail of the backlog with 363 and 697 days remaining respectively. The concentration of unresolved work against M1 and M2 is the most commercially urgent signal in the dataset.

### A significant proportion of closed changes closed late

671 of 14,292 closed changes — 4.7% — closed after their need date at an average of 128 days overdue. Level changes averaged 422 days to close with a maximum of 659 days, against a one-year physical need date buffer. `Level 2` changes averaged 162 days and `Level 3` averaged 57 days. The pipeline surfaces whether late closure is driven by slow governance in the validation phase or slow delivery in the implementation phase — a distinction that determines whether the intervention required is a process improvement or a commercial and resource action.

### Stagnation is concentrated in a small number of systems

The five systems with the highest open backlogs — `ABB`, `DEG`, `XYZ`,`CDE` and `LMN`. These collectively carry the oldest and most concentrated unresolved work. The stagnation scoring in the pipeline weights open changes by age and level priority, surfacing these systems as the highest intervention priority before their milestone deadlines close the window for recovery.

### The pipeline moves reporting beyond headline counts

Standard change register reporting answers how many changes are open. This pipeline answers which systems are genuinely stagnating, which milestones are at risk from changes the programme does not know are driving them, and whether current closure velocity is sufficient to clear each system's backlog before its deadline. The assumed versus driving comparison alone — showing that nearly three quarters of all changes drive an earlier deadline than recorded — represents a material shift in how programme risk is understood and communicated.

## Observations & Opportunities for Improvement

### Pipeline Architecture 

There are inconsistencies in how silver-layer transformation logic is currently structured within the pipeline.

For example, fact_enriched is correctly built once in Cell 4 as the central silver fact table at ChangeID grain. It serves as the single source of truth for all downstream analytical logic. Every analytical cell reads from this table, and all further joins — `system` `contract`, `building`, and `zone` — originate from it.

Similarly, `fact_location_exploded` is correctly implemented as a pre-built silver dataset at `ChangeID` × `SystemCode` × `Building` × `Zone grain`. It is also materialised once in Cell 4 and reused across all downstream spatial analysis. This enables multi-grain analysis without requiring repeated join logic in analytical cells.

Status filtering (e.g. `open`, `closed`) is correctly applied within analytical cells as a downstream slicing operation on top of the silver fact tables, rather than being encoded as separate transformation tables.

However, there is inconsistency in how system-level and contract-level grains are handled. At present, system-level analysis is implemented through repeated inline transformations within analytical cells (e.g. open changes, closed changes, backlog views, recent changes). These are not separate silver tables but dynamically derived subsets of `fact_enriched`. As a result, the same join logic and filtering patterns are repeated across multiple cells (Cells 9–13).

Similarly, contract-level analysis, `df_contract_base` is constructed inline in Cell 14 rather than being pre-materialised in the silver layer.

In a production-grade implementation, these would be centralised in the silver layer as reusable, full-grain datasets:

`fact_system_enriched` — `ChangeID` × `SystemCode` grain, full population, all attributes, consumed by system-level analytical cells (`open`, `closed`)
`fact_contract_enriched` — `ChangeID` × `Contract grain`, full population, deduplicated, consumed by contract-level analytical cells

Each analytical cell would then apply filters (e.g. `status`) and perform aggregation on these pre-built silver datasets, rather than reconstructing join chains repeatedly.

V2 Architecture Improvement

Currently, this notebook represents an emergent architecture, where core silver transformations are partially centralised but some grain-level derivations are still embedded within analytical cells.

In a V2 production design, the structure would be fully standardised:

Cell 1 — Bronze layer: synthetic data generation (unchanged)
Cell 2, 2b — Dimensions: unchanged
Cell 3 — Bridge construction: unchanged
Cell 4 — Silver layer (fully centralised transformations):
`fact_enriched`
`fact_location_exploded`
`fact_system_enriched`
`fact_contract_enriched`
`fact_zonetype_enriched`
Cell 5 — Gold layer: all business-facing aggregations materialised centrally
Cell 6 onwards — Analytical layer: pure consumption layer (filtering + aggregation only)

Lead time calculations throughout the notebook use calendar day arithmetic via `julianday()` function. A production implementation would calculate lead times in working days — excluding weekends, bank holidays, and project-specific non-working periods — using the `date_dim` table which is already present in the pipeline. This would require a working day flag column added to `date_dim` and a working day count function replacing the current `julianday()` subtraction.

### Status Workflow Analysis

The bronze layer contains three date fields per change — `DateOpened`, `DateValidated`, and `DateClosed` — which together represent three implicit status states: `Raised`, `Validated`, and `Closed`. The current implementation uses only the final Status field — `Open` or `Closed` — and treats `DateValidated` as unused after Cell 1.
In a production implementation two additional derived fields would be calculated in Cell 4:
* `Validation_Lead_Time` — `DateValidated` - `DateOpened` — the review and approval cycle time
* `Implementation_Lead_Time` — `DateClosed` - `DateValidated` — the delivery cycle time
Splitting `Days_to_Close` into these two components would identify whether poor lead time performance is driven by slow governance — a review process problem — or slow delivery — a contractor or resource problem. The distinction matters for intervention — the first requires process improvement, the second requires resource or commercial action.

The result
Cell 4 becomes the complete transformation layer and all silver tables built once, (`fact_enriched`, `system_enriched`, `contract_enriched`, `fact_location_exploded`) and the result is all analytical cells become pure consumers as well as the same pattern applied consistently across every grain. The result is a fully optimized transformation layer where every Silver table is built once and consumed many times, ensuring the analytical cells remain lightweight and the pipeline stays scalable.

### Source Data & API Call Structure 

A production implementation would receive updated data from the source system on a scheduled basis — monthly in line with the programme reporting cadence. Each refresh would bring new changes raised, status updates on existing changes, and potentially revised system impact lists as configuration assessments are completed. Rather than overwriting the full dataset on each refresh, a production pipeline would adopt a versioned accumulating snapshot pattern — comparing each monthly API response against the stored current state and inserting a new row only when attributes change. Each version would carry `Valid_From`, `Valid_To`, and `Is_Current` columns, preserving the full history of Status transitions, Level reclassifications, and SystemsImpacted revisions that are invisible in the current overwrite implementation. Current state queries would filter to `Is_Current = True`, preserving all existing analytical outputs without modification. This would enable full regulatory auditability — the complete record of every change as it stood at each point in the programme lifecycle.

