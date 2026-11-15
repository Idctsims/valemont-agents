// How a price reads on /betting (Chat 8 Phase 3.5, Workstream B; the
// approved mock's x / % switch, multiplier by default). No page, no server.
//
// A Kalshi YES price in dollars is also the chance it implies. Read two ways:
//   x   what $1 pays back if it hits: 1 / price ("2.08×"), before Kalshi's fee
//   pct the chance the price implies: price × 100 ("48%")
// A leg's price is shown before fee, and says so. A ticket's total is AFTER
// fee: Kalshi charges one taker fee on the combo's own price
// (docs/betting_science_audit.md §3: 0.07 × P × (1 − P), multiplier 1,
// rounded up to $0.000001 as db/073's kalshi_taker_fee does), never one per leg.

export type PriceUnit = "x" | "pct";

export const UNIT_KEY = "vm-price-unit";

/** The stored choice; anything else (nothing stored, junk, blocked) is the default, x. */
export function parseUnit(raw: string | null | undefined): PriceUnit {
  return raw === "pct" ? "pct" : "x";
}

function usable(p: number | null | undefined): p is number {
  return typeof p === "number" && Number.isFinite(p) && p > 0 && p < 1;
}

/** A multiplier: two decimals under 10×, one from 10×: 2.0833 -> "2.08×", 12.5 -> "12.5×". */
export function multiplier(m: number): string {
  return `${m >= 10 ? m.toFixed(1) : m.toFixed(2)}×`;
}

/** A percent: whole when whole, else one decimal: 0.61 -> "61%", 0.015 -> "1.5%", 0.1946 -> "19.5%". */
export function percent(p: number): string {
  const v = Math.round(p * 1000) / 10;
  return `${Number.isInteger(v) ? v.toFixed(0) : v.toFixed(1)}%`;
}

/** One price (before fee), in the chosen unit; "—" when there is none. */
export function priceText(price: number | null | undefined, unit: PriceUnit): string {
  if (!usable(price)) return "—";
  return unit === "x" ? multiplier(1 / price) : percent(price);
}

/** The price of the side bought: YES at the ask; NO at 1 − the YES bid. */
export function sideAsk(m: { a?: number | null; b?: number | null }, side: "yes" | "no"): number | undefined {
  if (side === "yes") return usable(m.a) ? m.a : undefined;
  if (m.b === null || m.b === undefined || !Number.isFinite(m.b)) return undefined;
  const a = Math.round((1 - m.b) * 10000) / 10000;
  return usable(a) ? a : undefined;
}

/** Kalshi's taker fee per contract at a price (quadratic, multiplier 1), rounded up at $0.000001. */
export function takerFee(p: number): number {
  return Math.ceil(0.07 * p * (1 - p) * 1e6 - 1e-6) / 1e6;
}

export type TicketTotal = {
  /** The combo's price before fee: the product of the legs' prices. */
  price: number;
  /** One taker fee on that price. */
  fee: number;
  /** What a $1 contract costs after fee. */
  cost: number;
  /** What $1 staked pays back if every leg hits, after fee: 1 / cost. */
  pays: number;
};

/** A ticket's total after fee, or null when a leg has no usable price or there are fewer than 2 legs. */
export function ticketTotal(prices: readonly (number | null | undefined)[]): TicketTotal | null {
  if (prices.length < 2 || !prices.every(usable)) return null;
  const price = (prices as number[]).reduce((acc, p) => acc * p, 1);
  if (!(price > 0)) return null;
  const fee = takerFee(price);
  const cost = price + fee;
  return { price, fee, cost, pays: 1 / cost };
}

/** The total in the chosen unit: x is what $1 pays after fee, % what a $1 contract costs after fee. */
export function totalText(t: TicketTotal | null, unit: PriceUnit): string {
  if (!t) return "—";
  return unit === "x" ? multiplier(t.pays) : percent(t.cost);
}
