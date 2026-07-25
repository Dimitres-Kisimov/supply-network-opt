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

## How to run it

```bash
pip install -r requirements.txt

python -m supplynet                 # print the analysis report
python -m supplynet --deliverables  # also write the PDF + Excel to deliverables/
python -m supplynet --seed 7        # try a different synthetic instance
```

The deliverables step writes an executive PDF (cover with disclaimer and
headline savings, a network map of opened DCs and flows, a cost-breakdown bar,
and a safety-stock pooling chart) plus an Excel workbook (Summary, Facilities,
Flows, SafetyStock, Customers, Assignment).

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

## Honesty notes

- Data is synthetic and seeded. No real customers, costs, or locations.
- "Optimal" refers to the MILP/LP optimum for this synthetic instance under the
  stated model — not a superhuman or real-world-guaranteed result.
- Savings are measured against a **named** greedy baseline, not against "the best
  anyone could do."
- Safety-stock and ROI figures are labelled estimates from textbook models.

## Layout

```
supplynet/
  data.py         seeded synthetic network generator
  facility.py     capacitated facility-location MILP + greedy baseline
  flow.py         min-cost multi-echelon flow (graph solver + LP cross-check)
  safetystock.py  base-stock safety stock + risk pooling
  pipeline.py     end-to-end orchestration
  exports.py      executive PDF + Excel workbook
  __main__.py     CLI
tests/            19 tests (data, facility, flow, safety stock, exports)
docs/BUSINESS_CASE.md
```

© 2026 Dimitres Kisimov — all rights reserved; published for portfolio review. See LICENSE. See `docs/BUSINESS_CASE.md` for the framed business case and
`CREDITS.md` for the tools this is built on.
