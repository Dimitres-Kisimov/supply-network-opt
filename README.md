# Supply-Network Optimization

I built this to answer a question every physical-goods distributor eventually
faces: **where should we put our distribution centers, how should product flow
through the network, and how much safety stock does each tier need?** It is a
compact, honest take on three classic operations-research problems wired
together on one synthetic dataset.

All data here is **synthetic and seeded** (`numpy` default RNG, seed 42), so
anyone who runs it gets the exact numbers below. I generated it myself; it does
not describe any real company. The results are **model-based estimates** under
assumptions I state plainly, not guarantees about the real world.

## The business situation

Picture a regional distributor serving 30 customer zones from a handful of
plants. It can open distribution centers (DCs) at 8 candidate sites. Each
candidate has a fixed annual cost to operate and a throughput capacity. Opening
too many DCs wastes fixed cost; opening too few (or the wrong ones) inflates
transport. On top of that, the more places you hold inventory, the more safety
stock you tie up to hit the same service level. The distributor wants the
cheapest network that still serves everyone.

## What my run produced

Running `python -m supplynet` on seed 42 (3 plants, 8 candidate DCs, 30 customer
zones, total demand 17,880 units):

| Question | Result |
| --- | --- |
| DCs opened (MILP) | **3 of 8** — DC1, DC4, DC6 |
| Optimized total cost | **$310,666** (fixed $288,882 + transport $21,784) |
| Greedy baseline cost | $394,216 (opens 4 DCs) |
| Savings vs baseline | **$83,550, i.e. 21.2% lower** |
| Min-cost flow (plant to DC to customer) | **$105,245.59** |
| Flow cross-check (graph vs LP) | agree to **$0.00** |
| Safety stock, decentralized (1 point/zone) | 23,168 units |
| Safety stock, pooled into the 3 opened DCs | 7,946 units (**-65.7%**) |
| Safety stock, fully centralized (1 DC) | 4,608 units (**-80.1%**) |

The optimized network actually spends a little *more* on transport than the
greedy plan ($21,784 vs $17,750) but far *less* on fixed cost, because it opens
three well-placed DCs instead of four cheap-to-open ones. That trade-off is the
whole point of solving it as an optimization rather than a rule of thumb.

## Cost is not the only objective: a CO2-aware sensitivity view

Real network planners weigh more than cost. The cheapest network here (3 DCs)
also ships the *most* tonne-km, and tonne-km is what becomes freight CO2. So the
`co2_sensitivity` module attaches a second, labelled number — modeled outbound
CO2 — to every design, then sweeps the network density (number of open DCs),
**re-solving the same facility-location MILP** forced to open exactly *k* DCs at
each step. That traces the cost-optimal design at every density and how cost,
CO2 and a last-mile service proxy trade off. Seed 42:

| # DCs | Cost | CO2 (t, modeled) | Avg delivery km | Demand within 40 km | Pareto |
| ---: | ---: | ---: | ---: | ---: | :---: |
| **3** (cost-opt) | **$310,666** | **5.31** | 29.7 | 77.7% | ● |
| 4 | $394,207 | 4.33 | 24.2 | 91.6% | ● |
| 5 | $501,624 | 3.92 | 21.9 | 91.6% | ● |
| 6 | $617,188 | 3.53 | 19.8 | 96.4% | ● |
| 7 | $743,650 | 3.37 | 18.9 | 96.4% | ● |
| 8 | $896,888 | 3.24 | 18.1 | 96.4% | ● |

Every row is Pareto-optimal on (cost, CO2): denser networks cost strictly more
but cut CO2 and shorten last-mile delivery. The plain-language read the tool
prints:

- **Adding a 4th DC** cuts modeled CO2 by **18.6%** (5.31 → 4.33 t) and lifts
  within-40 km service **78% → 92%**, for **$83,540 (+26.9%)** more total cost.
- The **greenest** design (8 DCs) is **39% below** the cost-optimal CO2 but costs
  **+189%** — the trade-off a planner has to price, not a free win.

The **emission factor is illustrative** (0.10 kg CO2e/tonne-km, 0.10 t/unit) —
a round road-freight placeholder, **not a certified figure**; swap it for an
audited GLEC/DEFRA-style factor before quoting any CO2 number. Only the outbound
(DC → customer) leg is counted, to stay like-for-like with the facility model's
own cost objective. Running with `--deliverables` also writes the full frontier
to `deliverables/co2_sensitivity.csv` and a hand-drawn
`deliverables/co2_cost_frontier.svg` (cost-vs-CO2 scatter, Pareto highlighted).

## Cheap can be fragile: a disruption-resilience (N-1) screen

The cost-optimal design is *lean* — it opens the fewest DCs that cover demand —
and lean networks can be fragile. The `resilience` module runs the classic
**N-1 contingency screen**: knock out each opened DC in turn, re-optimize the
outbound assignment over the *surviving* committed DCs (a transportation LP that
allows unmet demand as slack), and read off the fill rate. Where an outage leaves
demand unserved, it then **re-solves the same facility-location MILP** with the
survivors forced open and the failed DC forced closed, to find the cheapest
standby to activate and restore 100% service. Seed 42:

| Lose | Surviving capacity | Fill rate | Unmet units | Recover by | Recovery premium |
| ---: | ---: | ---: | ---: | :---: | ---: |
| **DC1** | 10,659 | **59.6%** | 7,221 | +DC5 | **$157,016** |
| DC4 | 13,116 | 73.4% | 4,764 | +DC3 | $109,247 |
| DC6 | 15,127 | 84.6% | 2,753 | +DC0 | $84,826 |

The finding is blunt and honest: on seed 42 the cost-optimal 3-DC network is
**not N-1 resilient** — *every* opened DC is critical, because total demand
(17,880 units) exceeds any two survivors' capacity. Losing DC1 alone would drop
service to **59.6%** until a standby is activated. Restoring full service after
the worst loss costs an added **$157,016** (a standby's fixed cost plus the extra
transport). That is the resilience premium a planner weighs against the
$83,550 the lean design *saved* versus the greedy baseline — the flip side of
the same cost-vs-CO2 tension, now cost-vs-robustness. `--deliverables` adds a
two-panel resilience page (fill rate + recovery premium per outage) to the PDF
and a `Resilience` sheet to the workbook.

Honest scope: capacity is the only hard operating limit modeled and demand is
deterministic, so treat these fill rates as a **planning screen**, not a
guaranteed service outcome. Recovery re-optimizes strategically (cheapest network
excluding the failed DC while keeping survivors); a true fast operational failover
would also model switching time and standing standby cost.

## What does service cost? An inventory service-level frontier

The safety-stock section above fixes the service target at 95%. But the target is
itself a priced lever. Base-stock safety stock scales with the standard-normal
quantile `z(service_level)`, and `z` climbs ever more steeply toward 100%, so the
*last* points of service tie up far more inventory than the first. The
`service_frontier` module sweeps the target and, for the *committed* network,
traces safety stock (units) under all three pooling regimes, the dollars that
stock ties up, and the **marginal carrying cost of each extra point of service**.
It re-uses the existing `pooling_analysis` engine unchanged — one call per level —
so at 95% it reproduces the headline pooling numbers exactly. Seed 42:

| Service | z | SS decentralized | SS network | SS centralized | Carry $/yr (network) | Marginal $/pt |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 80.0% | 0.84 | 11,854 | 4,066 | 2,358 | $50,823 | — |
| 85.0% | 1.04 | 14,598 | 5,007 | 2,904 | $62,587 | $2,353 |
| 90.0% | 1.28 | 18,051 | 6,191 | 3,591 | $77,389 | $2,960 |
| **95.0%** | 1.64 | 23,168 | **7,946** | 4,608 | **$99,327** | $4,388 |
| 97.5% | 1.96 | 27,607 | 9,468 | 5,491 | $118,356 | $7,611 |
| 99.0% | 2.33 | 32,767 | 11,238 | 6,518 | $140,481 | $14,750 |

The plain-language read the tool prints:

- **Diminishing returns.** Going 97.5% → 99.0% costs about **$14,750/yr per service
  point** — roughly **6.3x** the cost per point of the first increment (80% → 85%).
  The convex `z(sl)` curve *is* the "last mile of service is the expensive one"
  effect, in dollars.
- **Pooling read as service, not just inventory.** The 23,168 units a decentralized
  network needs for 95% service, pooled into the opened DCs, lift the safety
  factor from z ≈ 1.64 to z ≈ 4.80 — cutting the modeled stockout probability from
  5% to under 0.01%. That is the same square-root law the pooling section reports
  as a −65.7% inventory cut, read the other way round: the freed units buy service.

Honest scope: the dollar figures use an **illustrative** unit value ($50/unit) and
annual carrying rate (25%/yr) — round textbook placeholders, **not** a real cost of
capital; swap them before quoting any number. Safety stock is **modelled, not
measured**, on normal, independent demand, so the pooling gain is an optimistic
bound (real demand is partly correlated). And the "buys service" figure lands deep
in the normal tail (z ≈ 4.8), where the normal model is unreliable — treat it as a
direction, not a service guarantee. `--deliverables` adds a two-panel service-
frontier page to the PDF, a `ServiceFrontier` sheet to the workbook, and writes the
frontier as `deliverables/service_frontier.csv` and a hand-drawn
`deliverables/service_frontier.svg`.

## When does growth force the next DC? A demand-growth expansion plan

Everything above optimizes the network for *today's* demand. But a network is
committed for years while demand moves, so the `growth` module asks the
capacity planner's question: **how much growth can the committed network
absorb, and at what demand level does the next DC start to pay?** It sweeps a
uniform demand multiplier (1.00x to 2.00x in 5% steps) and, at every level,
**re-solves the same facility-location MILP twice**: unconstrained (the network
you would build at that demand) and with today's three DCs pinned open and
every other candidate pinned closed — the same `force_open`/`force_closed`
pins the resilience module uses — so the frozen network's cost and its exact
capacity wall fall out of the same engine. Seed 42 (condensed to the levels
where the design changes):

| Growth | Demand | Optimal DCs | Optimal cost | $/unit | Committed (frozen) |
| ---: | ---: | :--- | ---: | ---: | ---: |
| 1.00x | 17,880 | 3 — DC1, DC4, DC6 | $310,666 | 17.38 | $310,666 |
| 1.05x | 18,774 | 3 — unchanged | $312,435 | 16.64 | $312,435 |
| 1.10x | 19,668 | 3 — swap DC6 → DC0 | $321,694 | 16.36 | — capacity wall — |
| **1.30x** | 23,244 | **4** — DC0, DC1, DC4, DC6 | $399,787 | 17.20 | — |
| **1.65x** | 29,502 | **5** — DC0, DC1, DC3, DC4, DC6 | $514,629 | 17.44 | — |
| **1.95x** | 34,866 | **6** — DC0, DC1, DC3, DC4, DC6, DC7 | $636,613 | 18.26 | — |
| 2.00x | 35,760 | 6 — DC0, DC1, DC2, DC3, DC4, DC6 | $647,359 | 18.10 | — |

The plain-language read the tool prints:

- **The lean network has only +8.8% growth headroom.** The committed DCs hold
  19,451 units of capacity against 17,880 units of demand, so the capacity wall
  sits at exactly 1.088x today's demand — the flip side of the same leanness the
  N-1 screen flags. Up to that wall, freezing today's network costs **nothing
  extra**: capacity, not economics, forces the first change.
- **The first response to growth is a reshuffle, not a new DC.** At 1.10x the
  optimizer swaps DC6 (4,324 capacity) out for DC0 (4,757) and stays at 3 DCs.
  The **4th DC first pays at 1.30x** — exactly when any 3-DC design physically
  caps out (the three largest candidates hold 23,116 units = 1.29x). The 5th
  arrives at 1.65x; the **6th at 1.95x is an economic trigger** — a 5-DC design
  is still feasible there (ceiling 1.99x), but six is already cheaper.
- **Cost per unit is a sawtooth around ~$17.4.** It *falls* as opened DCs fill
  toward capacity and *jumps* when the design changes — economies of fill, not
  of scale (fixed costs and rates are flat by assumption).
- **Two ceilings, two echelons.** The full 8-candidate DC pool caps at 2.74x
  demand (49,035 units), but the plant echelon — not re-solved in this siting
  sweep — totals only 2.10x, so plants would bind first. A real expansion plan
  has to widen both.

Honest scope: growth is modeled as **uniform** (every zone scales by the same
factor, so the demand mix and geography never change — real growth is lumpy and
shifts the map), demand is deterministic, DC capacity is the only hard operating
limit, and fixed costs and transport rates are held flat as volumes grow (no
scale economies, no inflation). Expansion triggers are planning estimates on
synthetic data, not forecasts. `--deliverables` adds a two-panel growth page to
the PDF (cost of growth vs the frozen network + the expansion staircase), a
`Growth` sheet to the workbook, and writes the sweep as
`deliverables/growth_plan.csv` and a hand-drawn
`deliverables/growth_expansion.svg`.

## In which *year* does each DC open? A phased build plan (NPV)

The growth sweep says the 4th DC pays at 1.30x demand. A board does not vote on
a multiplier — it votes on a **year** and a number. So the `phasing` module puts
a calendar and a discount rate on that staircase: demand grows at a constant
annual rate, every year's demand is `(1+g)^t` times today's, and each year is
priced by re-solving the **same** facility MILP with the policy's continuity
constraint expressed through the **same** `force_open`/`force_closed` pins the
resilience and growth modules use. Four policies, one horizon:

| Policy | Continuity constraint | Seed-42 result (10 yr, +6%/yr, 10% discount) |
| --- | --- | ---: |
| Free redesign | none — may close a site it opened | **$2,598,732** NPV, but **7 site closures** |
| **Staged build** | open-only; never close | **$2,750,462** NPV — `yr2 +DC0; yr6 +DC3` |
| Build ahead | staged plan's final design, opened in year 0 | **$3,422,059** NPV |
| No expansion | today's network held forever | **fails in year 2** |

The plain-language read the tool prints:

- **Today's network runs out of capacity in year 2.** At +6%/yr, demand reaches
  1.124x — past the 1.088x wall the growth module computes exactly. Holding it
  unchanged is not a plan, so it is reported as *failing* rather than priced.
- **The build schedule is two openings: DC0 in year 2, DC3 in year 6**, ending on
  5 DCs. DC0 arrives three years *earlier* than the free redesign's economic
  trigger because the staged plan cannot close DC6 to make room — it has to add
  instead of swap.
- **Building ahead costs $671,597 more in NPV (+24.4%).** That is the price of
  readiness, not a mistake the model caught: in this model fixed cost is a
  **recurring operating cost, not capex**, and a DC opens with **no construction
  lead time**, so building early can never pay on cost alone.
- **Never closing a site costs $151,730 NPV (+5.8%)** versus the free-redesign
  lower bound — which buys that saving with 7 site closures over 10 years, at a
  closure cost this model charges nothing for. The free line is a bound, not a
  plan.

Three orderings hold at **every** year by construction, because each policy is
the same MILP under a strictly tighter pin set: `free ≤ staged ≤ build-ahead`
and `staged ≤ frozen` wherever frozen is feasible. The tests assert them year by
year, alongside a solver-free anchor — the capacity-wall year is the first `t`
with `(1+g)^t` past the growth module's wall, and it must equal the year the
frozen policy is *observed* to go infeasible.

Honest scope: the annual growth rate and the discount rate are **illustrative
assumptions, not forecasts**, and both are labelled everywhere they surface.
Costs are charged at the start of each year (year 0 undiscounted). Growth stays
**uniform**, inherited from the growth module. There is no capex, no construction
lead time and no land escalation in this model — any of which could reverse the
early-vs-late answer. Only the DC echelon is re-sited; by year 9 demand is 1.69x
today's against a 2.74x candidate-pool ceiling and a 2.10x plant ceiling.
`--deliverables` adds plate 09 to the PDF, a `BuildSchedule` sheet to the
workbook, and writes `deliverables/build_schedule.csv` and a hand-drawn
`deliverables/build_schedule.svg`.

## How to run it

```bash
pip install -r requirements.txt

python -m supplynet                 # print the analysis report
python -m supplynet --deliverables  # also write the PDF + Excel to deliverables/
python -m supplynet --seed 7        # try a different synthetic instance
python -m supplynet --service-level 0.98   # move the base service target
```

The deliverables step writes an executive PDF (cover with disclaimer and
headline savings, a network map of opened DCs and flows, a cost-breakdown bar,
a safety-stock pooling chart, a cost-vs-CO2 Pareto page, a disruption-resilience
page, an inventory service-level-frontier page, a demand-growth expansion page,
and a phased-build-schedule page) plus an Excel workbook (Summary, Facilities,
Flows, SafetyStock, Customers, CO2Sensitivity, Resilience, ServiceFrontier,
Growth, BuildSchedule, Assignment) and the CO2 frontier, the service frontier,
the growth plan and the build schedule as a CSV and a hand-drawn SVG each.

Run the checks with `python -m ruff check .` and `python -m pytest -q`.

## The methods (and their assumptions)

**1. Facility location — capacitated MILP.** Binary open/close per candidate DC
plus continuous shipment quantities, minimizing fixed opening cost plus outbound
transport, subject to every customer's demand being met and no DC shipping above
its capacity. Solved with OR-Tools CBC. Facility location is NP-hard; CBC solves
*this* instance to proven optimality, which is a statement about the model, not
a promise for arbitrarily large real networks. The **named baseline** is a
greedy heuristic that opens the cheapest-to-open DCs until capacity covers
demand, then assigns each customer to its cheapest open DC. I report the MILP
cost against that baseline so the gap is concrete.

**2. Network flow — min-cost multi-echelon.** Given the opened DCs, route
production plant to DC to customer at least total cost (production + inbound +
outbound), respecting plant supply and DC throughput. I solve it two independent
ways — OR-Tools `SimpleMinCostFlow` (a graph solver) and a transportation LP via
`scipy.optimize.linprog` (HiGHS) — and cross-check them. Because a min-cost-flow
polytope is integral, the two optima should match; on seed 42 they agree to the
cent.

**3. Safety stock — multi-echelon with risk pooling.** Base-stock safety stock
at a stocking point is `z(service_level) * sqrt(lead_time) * sigma_demand`. I use
`scipy.stats.norm.ppf` for the z-score. Pooling independent demand replaces
`sum(sigma_i)` with `sqrt(sum(sigma_i^2))` — the square-root law — so
consolidating stocking locations cuts total safety stock. I report three
scenarios: fully decentralized, pooled by the actually-opened DCs, and fully
centralized. These formulas assume normal, independent demand and fixed lead
times; real demand is neither perfectly normal nor independent, so treat the
figures as estimates.

**4. Disruption resilience — single-DC-outage (N-1) screen.** For each opened DC,
I remove it and re-optimize the outbound assignment over the surviving committed
DCs as a transportation LP (OR-Tools GLOP) with a per-customer slack for unmet
demand, so the model serves as much as surviving capacity allows and the slack
reads out as the lost fill rate. Where survivors fall short, I re-solve the same
capacitated facility MILP with the survivors forced open and the failed DC forced
closed (two backward-compatible pins added to `solve_facility_milp`) to find the
cheapest standby to activate; the added fixed cost plus the transport change is
the recovery premium. This models capacity as the only hard operating limit and
treats demand as deterministic — a planning screen, not a live-failover SLA.

**5. Inventory service-level frontier.** I sweep the target service level and, at
each level, call the *same* `pooling_analysis` engine to size safety stock under
the decentralized, network-pooled and fully-centralized regimes, then convert
units to inventory value and annual carrying cost with an illustrative unit value
and holding rate. The marginal carrying cost of each service point is the discrete
slope of that curve; because `z(sl)` is convex it rises monotonically, so the last
points of service are the priciest. I also invert the square-root law: fixing the
inventory budget to the decentralized safety stock for the base service level, the
pooled safety factor `z` scales by the pooling ratio, so the same units "buy" a
higher service level. That inverted figure lands deep in the normal tail, where
the normal model is unreliable, so it is reported as a direction with that caveat.
Exact as computed; the same normal, independent-demand assumptions apply.

**6. Demand-growth capacity planning.** I scale every zone's demand by a uniform
multiplier and, at each swept level, re-solve the same capacitated facility MILP
unconstrained (the expansion view) and with today's opened DCs pinned open and
all other candidates pinned closed (the frozen view, via the same two pins the
resilience module added). Where the unconstrained optimum's open-DC count steps
up, that level is an expansion trigger. Two anchors need no solver and
cross-check the sweep exactly: the committed capacity wall (committed capacity /
base demand) and the physical ceiling of any k-DC design (the k largest
candidate capacities / base demand) — an expansion trigger below that ceiling is
economic; at it, it is forced. Uniform growth, deterministic demand and flat
costs are stated assumptions, not claims about real demand.

**7. Phased build planning (timing + NPV).** I map the growth staircase onto a
calendar — year `t` carries `(1+g)^t` times today's demand — and price four build
policies by re-solving the same facility MILP each year under a different
continuity constraint, using the same two pins: unconstrained (free redesign),
with everything already open pinned open (staged, open-only), with one fixed
design pinned open and the rest pinned shut (build-ahead and frozen). NPV is the
discounted sum of those annual costs, year 0 undiscounted. Because the feasible
sets nest, `free ≤ staged ≤ build-ahead` and `staged ≤ frozen` hold at every
year by construction rather than by observation, and the capacity-wall year has
a closed form that cross-checks the solver. The growth rate and the discount
rate are labelled illustrative assumptions throughout.

## Honesty notes

- Data is synthetic and seeded. No real customers, costs, or locations.
- "Optimal" refers to the MILP/LP optimum for this synthetic instance under the
  stated model — not a superhuman or real-world-guaranteed result.
- Savings are measured against a **named** greedy baseline, not against "the best
  anyone could do."
- Safety-stock and ROI figures are labelled estimates from textbook models.
- The CO2 numbers use an **illustrative** emission factor (0.10 kg CO2e/tonne-km,
  0.10 t/unit), not a certified one, and count only the outbound leg. Treat them
  as relative comparisons between designs on synthetic data, not absolute
  footprints.
- The resilience screen models **capacity as the only hard limit** and demand as
  deterministic; fill rates are a planning screen, not a guaranteed service level,
  and recovery is a cheapest-redesign estimate, not a timed operational failover.
- The service-level frontier's dollar figures use an **illustrative** unit value
  ($50/unit) and carrying rate (25%/yr), not certified rates. Safety stock is
  modelled, not measured; the "same inventory buys more service" figure is deep in
  the normal tail (z ≈ 4.8), where the model is unreliable — a direction, not an SLA.
- The growth plan models **uniform** growth (every zone scales alike; the mix and
  the map never change) with deterministic demand and flat costs and rates — no
  scale economies, no inflation. The plant echelon is not re-solved in the siting
  sweep; its ceiling is reported separately. Expansion triggers are model-based
  planning estimates on synthetic data, not forecasts.
- The phased build plan's **annual growth rate (6%/yr) and discount rate
  (10%/yr) are illustrative assumptions, not forecasts**, and the horizon is a
  choice. More important: this package models fixed cost as a **recurring
  per-period operating cost, not one-time capex**, and a DC opens with **no
  construction lead time**. Under that cost structure building ahead can never
  pay on cost alone, so the build-ahead premium is the price of readiness rather
  than a finding that early building is wrong — a model carrying capex,
  construction lead times or land escalation could reverse the sign. The
  free-redesign line closes live DCs at zero cost, which is why it is reported
  as a lower bound and its closure count is printed beside it.

## Layout

```
supplynet/
  data.py         seeded synthetic network generator
  facility.py        capacitated facility-location MILP + greedy baseline
  flow.py            min-cost multi-echelon flow (graph solver + LP cross-check)
  safetystock.py     base-stock safety stock + risk pooling
  co2_sensitivity.py CO2-aware variant + cost/CO2/service sweep (Pareto frontier)
  resilience.py      single-DC-outage (N-1) screen + cheapest recovery
  service_frontier.py inventory service-level frontier (safety stock/cost vs service)
  growth.py          demand-growth capacity plan (expansion triggers + headroom)
  phasing.py         phased build plan (when each DC opens + NPV of the timing)
  atlas.py           the "network atlas" design system: materials palette + SVG chrome
  plates.py          matplotlib side of the same plate system (PDF pages)
  pipeline.py        end-to-end orchestration
  exports.py         executive PDF + Excel workbook + four CSV & SVG pairs
  __main__.py        CLI
tests/            93 tests (data, facility, flow, safety stock, co2, resilience,
                  service frontier, growth, phasing, exports)
docs/BUSINESS_CASE.md
```

© 2026 Dimitres Kisimov — all rights reserved; published for portfolio review. See LICENSE. See `docs/BUSINESS_CASE.md` for the framed business case and
`CREDITS.md` for the tools this is built on.
