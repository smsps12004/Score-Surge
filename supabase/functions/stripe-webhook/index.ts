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

const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!);
const webhookSecret = Deno.env.get("STRIPE_WEBHOOK_SECRET")!;

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

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

  if (event.type !== "checkout.session.completed") {
    return new Response("Event type ignored", { status: 200 });
  }

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

  const { error } = await supabase
    .from("profiles")
    .update({ tier })
    .eq("email", email);

  if (error) {
    console.error("Failed to update profile tier:", error);
    return new Response("Database error", { status: 500 });
  }

  console.log(`Updated tier to '${tier}' for ${email}`);
  return new Response("OK", { status: 200 });
});
