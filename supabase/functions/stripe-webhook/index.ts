import Stripe from "npm:stripe@17";
import { createClient } from "npm:@supabase/supabase-js@2";

// Must stay in sync with STRIPE_PRICE_IDS + LEGACY_PRICE_IDS in app.py.
const PRICE_TO_TIER: Record<string, string> = {
  // Current prices.
  price_1TxgMkDP0fFhPzMl2MEk9OZk: "petty_officer", // $12.99/mo
  price_1TxgLnDP0fFhPzMlRxZuLWpU: "chief",         // $19.99/mo

  // Retired prices. Kept so sailors who subscribed before the July 2026 price
  // change still resolve to the right tier on renewal events.
  price_1Tw3DsDP0fFhPzMlLaeh0Ixs: "petty_officer", // $12.00/mo
  price_1Tw3EZDP0fFhPzMlc8E3QcY8: "chief",         // $20.00/mo

  // The archived $7.00/mo Seaman price is deliberately absent: that tier no
  // longer exists, its product is archived, and it has zero subscriptions.
};

// Which subscription statuses still earn access.
//
// "past_due" is deliberately included. When a card fails, Stripe retries it for
// several days before giving up — during that window the sailor has not stopped
// paying, their bank hiccuped. Cutting them off on the first failed charge takes
// access away mid-study from someone who is about to pay successfully. Access
// ends when Stripe actually gives up, which shows as "canceled" or "unpaid".
const ENTITLED_STATUSES = new Set(["active", "trialing", "past_due"]);

const HANDLED_EVENTS = new Set([
  "checkout.session.completed",
  "customer.subscription.updated",
  "customer.subscription.deleted",
]);

const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!);
const webhookSecret = Deno.env.get("STRIPE_WEBHOOK_SECRET")!;

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

function customerIdOf(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && "id" in value) {
    return String((value as { id: unknown }).id);
  }
  return null;
}

/** A paid checkout: grant the tier AND record the Stripe customer id.
 *
 * Matched on email, because at this point the profile has no customer id yet —
 * this is the event that puts one there. Every later subscription event matches
 * on that id instead, which is stable and cannot drift the way an email can.
 *
 * Zero rows matched here is a real failure: money came in and nobody was
 * upgraded. It returns 500 so Stripe retries and the problem is visible in the
 * dashboard instead of showing green while a paying sailor sits locked out.
 */
async function grantFromCheckout(
  email: string,
  customerId: string | null,
  tier: string,
): Promise<Response> {
  const patch: Record<string, string> = { tier };
  if (customerId) patch.stripe_customer_id = customerId;

  const { data, error } = await supabase
    .from("profiles")
    .update(patch)
    .eq("email", email)
    .select("id");

  if (error) {
    console.error("Failed to update profile tier:", error);
    return new Response("Database error", { status: 500 });
  }

  if (!data || data.length === 0) {
    console.error(
      `PAID BUT NOT UPGRADED — no profile matched email ${email} ` +
        `(customer ${customerId ?? "unknown"}, tier ${tier})`,
    );
    return new Response("No matching profile for paid checkout", { status: 500 });
  }

  console.log(`Updated tier to '${tier}' for ${email}`);
  return new Response("OK", { status: 200 });
}

/** A subscription changed or ended: set the tier to whatever they are now owed.
 *
 * Matched on stripe_customer_id only. That is the safety catch: a profile whose
 * tier was set by hand has no customer id, so no Stripe event can ever take it
 * away. Zero rows matched is therefore an expected no-op, not a failure.
 */
async function syncFromSubscription(
  customerId: string,
  tier: string,
): Promise<Response> {
  const { data, error } = await supabase
    .from("profiles")
    .update({ tier })
    .eq("stripe_customer_id", customerId)
    .select("id");

  if (error) {
    console.error("Failed to sync profile tier:", error);
    return new Response("Database error", { status: 500 });
  }

  if (!data || data.length === 0) {
    console.warn(
      `No profile linked to customer ${customerId} — nothing to set to '${tier}'`,
    );
    return new Response("No linked profile", { status: 200 });
  }

  console.log(`Synced tier to '${tier}' for customer ${customerId}`);
  return new Response("OK", { status: 200 });
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  const signature = req.headers.get("stripe-signature");
  if (!signature) {
    return new Response("Missing stripe-signature header", { status: 400 });
  }

  const rawBody = await req.text();

  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(
      rawBody,
      signature,
      webhookSecret,
    );
  } catch (err) {
    console.error("Webhook signature verification failed:", err);
    return new Response("Invalid signature", { status: 400 });
  }

  if (!HANDLED_EVENTS.has(event.type)) {
    return new Response("Event type ignored", { status: 200 });
  }

  // ── Someone paid ───────────────────────────────────────────────────────────
  if (event.type === "checkout.session.completed") {
    const session = await stripe.checkout.sessions.retrieve(
      (event.data.object as Stripe.Checkout.Session).id,
      { expand: ["line_items"] },
    );

    const email =
      session.customer_details?.email ?? session.customer_email ?? null;
    if (!email) {
      console.error("No customer email found on session:", session.id);
      return new Response("No customer email", { status: 400 });
    }

    const priceId = session.line_items?.data?.[0]?.price?.id ?? null;
    if (!priceId) {
      console.error("No price ID found on session:", session.id);
      return new Response("No price ID", { status: 400 });
    }

    const tier = PRICE_TO_TIER[priceId];
    if (!tier) {
      console.warn("Unknown price ID, skipping:", priceId);
      return new Response("Unknown price ID", { status: 200 });
    }

    return await grantFromCheckout(email, customerIdOf(session.customer), tier);
  }

  // ── A subscription changed or ended ────────────────────────────────────────
  const subscription = event.data.object as Stripe.Subscription;
  const customerId = customerIdOf(subscription.customer);
  if (!customerId) {
    console.error("No customer on subscription:", subscription.id);
    return new Response("No customer on subscription", { status: 400 });
  }

  const priceId = subscription.items?.data?.[0]?.price?.id ?? null;
  const paidTier = priceId ? PRICE_TO_TIER[priceId] : undefined;

  // Still entitled? Give them the tier their current price buys. Otherwise the
  // subscription is over — Stripe has stopped trying — and they drop to free.
  //
  // A cancellation scheduled for the end of the period arrives here as an
  // "active" subscription with cancel_at_period_end set, so they correctly keep
  // access until the day it actually ends and Stripe sends the deleted event.
  const entitled = ENTITLED_STATUSES.has(subscription.status);
  const tier = entitled ? (paidTier ?? "free") : "free";

  if (entitled && !paidTier) {
    console.warn(
      `Unknown price ${priceId} on active subscription ${subscription.id} — ` +
        `falling back to free`,
    );
  }

  console.log(
    `Subscription ${subscription.id} is '${subscription.status}' → tier '${tier}'`,
  );

  return await syncFromSubscription(customerId, tier);
});
