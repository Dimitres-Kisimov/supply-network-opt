# Business Case: Redesigning a Distributor's DC Network

> All figures below come from a **synthetic, seeded** dataset (seed 42) and are
> **model-based estimates** under stated assumptions. They illustrate the method
> and the shape of the decision, not a real company's finances.

## 1. Situation

A regional distributor supplies 30 customer zones with roughly **17,880 units**
of demand per period from 3 plants. Today its distribution footprint is set by
habit and cheap real estate rather than by analysis. Leadership suspects it is
paying for the wrong distribution centers and holding more inventory than its
service target requires, but has no quantified alternative to point to.

## 2. Problem, quantified

There are 8 candidate DC sites, each with a fixed operating cost and a throughput
capacity. Three coupled decisions drive total landed cost:

1. **Which DCs to open.** A naive "open the cheapest sites until capacity covers
   demand" rule opens 4 DCs and lands at **$394,216** (fixed + transport).
2. **How product flows** plant to DC to customer at least cost.
3. **How much safety stock** each stocking point must hold to hit a 95% service
   level. Holding stock in many places is expensive: a stock-point-per-zone
   design needs about **23,168 units** of safety stock.

Left unoptimized, the network overspends on fixed facility cost and over-stocks
inventory.

## 3. Solution

Three operations-research models, one dataset:

- **Capacitated facility-location MILP** (OR-Tools CBC) chooses which DCs to open
  and how to serve demand at minimum fixed + transport cost.
- **Min-cost multi-echelon flow** (OR-Tools graph solver, cross-checked against a
  `scipy` LP) routes production through the opened DCs to customers.
- **Multi-echelon safety stock** with the square-root risk-pooling law sizes
  inventory at the chosen service level.

## 4. Results and ROI (labelled estimates)

| Lever | Baseline | Optimized | Delta |
| --- | --- | --- | --- |
| DCs opened | 4 | **3** | -1 site |
| Fixed + transport cost | $394,216 | **$310,666** | **-$83,550 (-21.2%)** |
| Safety stock (units, 95% SL) | 23,168 (decentralized) | **7,946** (pooled into 3 DCs) | **-65.7%** |

**ROI framing (estimate).** The facility redesign alone is an estimated
**$83,550 per period** lower on fixed + transport cost versus the greedy
baseline. Separately, pooling safety stock into the three opened DCs cuts modeled
safety stock by **~15,200 units**; at an assumed holding cost the inventory
saving compounds on top of the facility saving. Both numbers are model outputs
under the assumptions in section 6 — they are estimates to be validated against
the distributor's real cost rates before any commitment.

## 5. Stakeholders

- **Supply chain / network design** — owns the DC open/close decision.
- **Finance** — cares about the fixed-cost reduction and inventory carrying cost.
- **Operations / logistics** — executes the plant-to-DC-to-customer flow plan.
- **Sales / customer service** — protected by the explicit service-level target.

## 6. Assumptions (so the numbers are honest)

- Demand is treated as normal and independent across zones; lead times are fixed.
- Costs are per-unit and distance-proportional on a synthetic coordinate grid.
- The MILP optimum is optimal *for this instance and model*, not a guarantee for
  larger real networks (facility location is NP-hard).
- Holding-cost and per-period framing for ROI are illustrative placeholders.
- The build schedule's demand growth (6%/yr) and discount rate (10%/yr) are
  illustrative assumptions, not forecasts. Fixed cost is modelled as a recurring
  operating cost, not capex, and a DC opens with no construction lead time.

## 7. Deliverable

`python -m supplynet --deliverables` produces an executive **PDF** (headline
savings, network map, cost breakdown, pooling chart, cost-vs-CO2 Pareto page,
disruption-resilience page, an inventory service-level-frontier page, a
demand-growth expansion page, and a phased-build-schedule page) and an **Excel
workbook** (Summary, Facilities, Flows, SafetyStock, Customers, CO2Sensitivity,
Resilience, ServiceFrontier, Growth, BuildSchedule, Assignment) that a planner
can hand to finance and operations. The service-level frontier makes the **cost
of the service target** explicit: each extra point of service is priced in annual
inventory carrying cost, and the marginal cost rises convexly toward 100%, so the
last points of service are the most expensive to buy. Those inventory dollars use
illustrative unit-value and carrying-rate placeholders (section 6), to be replaced
with the distributor's own rates before any commitment.

The **build schedule** turns the same models into the question the board votes
on — a year and a number. Today's three DCs run out of capacity in **year 2** at
an assumed +6%/yr growth, so the plan opens DC0 in year 2 and DC3 in year 6 for
**$2.75M NPV** at an assumed 10% discount rate. Opening that final network today
instead would cost **$671,597 more in NPV (+24.4%)**. Both rates are illustrative
assumptions (section 6), and because this model charges fixed cost as a recurring
operating cost with no capex and no construction lead time, that premium prices
**readiness**, not a construction decision — it would have to be re-run against
the distributor's real capex and build lead times before any commitment.
