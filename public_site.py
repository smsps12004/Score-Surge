"""Public-facing copy: the welcome section, footer, legal pages and signup question.

Added 22 Oct 2026 for the public launch (marketing review items 2-5 and 8). Kept out
of app.py so the words can be changed without touching app logic, and so
run_checks.py can test them without Streamlit.

Rules this file holds to:
- Nothing here may state a fact the app doesn't back up. The tagline in particular is
  checked against the verified question bank (see TAGLINE below).
- No Navy seal, emblem or logo, and nothing that implies Navy or DoD endorsement.
- Prices are NOT typed here. The welcome section reads them from app.py's
  UPGRADE_INFO so the page can never advertise a price checkout doesn't charge.
"""

SUPPORT_EMAIL = "strategicsailor@gmail.com"
EFFECTIVE_DATE = "22 October 2026"

# The marketing review asked for:
#   "Advancement exam prep for PS, YN and NC. Every practice question shows the exact
#    manual text behind the answer. Built by a retired PS1 with 20 years of service."
# Checked 22 Oct 2026: the verified question bank has 0 YN and 0 NC questions, so
# "exam prep for PS, YN and NC" would sell YN and NC sailors practice questions that
# don't exist yet. Adjusted to what is true today. When YN or NC questions go live,
# change this one line.
TAGLINE = (
    "Advancement exam prep for Navy Personnel Specialists (PS), with YN and NC coming "
    "soon. Every practice question shows the exact manual text behind the answer. "
    "Built by a retired PS1 with 20 years of service."
)

INDEPENDENCE_LINE = (
    "Score Surge is an independent exam-prep tool. Not affiliated with or endorsed by "
    "the U.S. Navy or DoD."
)

TRIAL_LINE = "3-day free trial, no card required"

# "How did you hear about us?" -- optional, stored on profiles.heard_from.
# Values on the left are what's stored (short and stable for counting); the labels
# on the right are what a sailor sees. The database CHECK constraint in
# supabase/migrations/20261022_heard_from.sql must list the same values.
HEARD_FROM_CHOICES = {
    "facebook": "Facebook",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "reddit": "Reddit",
    "discord": "Discord",
    "google": "Google",
    "friend": "A friend or shipmate",
    "other": "Other",
}


def clean_heard_from(value):
    """A stored value, or None. Anything not on the list is dropped, never stored."""
    value = (value or "").strip().lower()
    return value if value in HEARD_FROM_CHOICES else None


# ── Legal pages ──────────────────────────────────────────────────────────────
# Served at ?page=terms / ?page=privacy / ?page=refunds, before the login wall.
# Built from launch/*_DRAFT.md with the internal notes removed and the decisions
# Shawn has since made filled in (refund window, trial terms). Cancellation timing
# ("at the end of the billing period") was read live in the Stripe customer-portal
# settings on 12 Oct 2026. Counsel has not reviewed these yet.

TERMS_MD = f"""
# Terms of Use

**Effective {EFFECTIVE_DATE}**

## Who we are
Score Surge is provided by **Strategic Solutions LLC**, operating under the Strategic
Sailor brand, in Illinois, United States. Contact: {SUPPORT_EMAIL}.

Score Surge is an independent Navy advancement exam-preparation tool: FMS estimates,
study planning, a placement test, practice questions and AI-assisted study help.
**It is not an official Navy or Department of Defense service and is not endorsed by
them.** It does not issue official scores, eligibility decisions, selection results or
billet assignments.

## Accuracy, and how much to rely on it
- AI-generated lessons, explanations and practice answer keys can contain errors,
  omissions or outdated information. Showing a manual excerpt next to an AI answer
  does not prove every statement made from it is correct.
- Labels in the app (for example "Verified source" or "Quoted from") describe the
  specific check that was run. They do not mean the Navy or a human reviewer approved
  the answer.
- FMS results are estimates based on the paygrade and values you enter or upload.
  Automated profile-sheet reading can misread values, so check your numbers. Your
  official profile sheet and your ESO are the authority.
- Confirm eligibility, deadlines and rules in current official publications and with
  your ESO or command career counselor.
- **Score Surge does not guarantee any exam score, advancement, selection or billet.**
  A subscription buys access to the tools described, not a career outcome.

## Your account
- One account is for one sailor, signed in on one device at a time.
- Your account is set up for one rating. You can change it yourself once every 180
  days from My Profile, or email us if you convert sooner.
- Monthly limits apply to AI generations and downloads.
- Keep your password private. Don't try to reach anyone else's data, get around
  payment or usage limits, or disrupt the service.
- Don't enter Social Security numbers, DoD ID numbers, passwords, classified
  information or controlled unclassified information anywhere in the app.

## Free trial
New accounts get a **3-day free trial with full access. No card is required, and the
trial never turns into a charge.** When it ends, your account moves to the free plan
unless you choose to subscribe.

## Subscriptions
- Plans are billed monthly at the price shown in the app and at Stripe checkout, and
  renew automatically until you cancel. You confirm recurring billing before checkout.
- Cancel any time from **My Profile → Manage subscription / cancel**, or by emailing
  {SUPPORT_EMAIL}. Cancellation stops future renewals; you keep paid access through
  the end of the period you've already paid for.
- If we ever change a price, we'll tell existing subscribers by email before the new
  price applies to them.
- Refunds: see our [Refund Policy](?page=refunds).

## Reporting errors
Use the challenge button under a practice question, or email {SUPPORT_EMAIL}.
Automated challenge results are an AI's first opinion, not a final ruling.

## Availability
We work to keep Score Surge running, but it can be interrupted by maintenance or by
outages at the providers it depends on. We don't promise uninterrupted service.

## Disclaimer
To the extent the law allows, Score Surge is provided "as is", without a promise that
AI-generated content will be error-free. Nothing in these terms limits any right you
have that cannot be waived under applicable law.

## Changes
If we change these terms, we'll post the new version here with a new effective date.
For changes that affect billing, we'll email subscribers before they take effect.

## Privacy
See our [Privacy Notice](?page=privacy).
"""

PRIVACY_MD = f"""
# Privacy Notice

**Effective {EFFECTIVE_DATE}**

Score Surge is provided by **Strategic Solutions LLC**, Illinois, United States.
Privacy questions and requests: {SUPPORT_EMAIL}.

## What we collect and why
- **Account:** your email address, login session, plan (trial, free or paid), when
  your trial started, your chosen rating, and which device is signed in. Used to run
  your account and enforce one-device, one-rating access. Stored with Supabase.
- **How you heard about us:** if you answer the optional signup question, we store your
  answer to understand which outreach works.
- **Practice history:** placement and mock-exam results by topic, so the app can show
  your progress and weak spots.
- **Questions generated for you:** practice questions the AI writes for you are saved
  so they can be checked and, if they pass, added to the question bank.
- **Challenges:** if you dispute a question, we keep your explanation, the question
  and an AI's first opinion so the error can be investigated.
- **Usage counts:** how many AI generations and downloads you've used this month.
- **What you type into AI features** (study guide requests, tutor chat, challenge
  explanations) is sent to Anthropic to generate the response.
- **Profile sheets** you upload are read on the application server to fill in the FMS
  calculator. The app doesn't save the file to your account.
- **Payments** are handled by Stripe. Your card details go to Stripe, not to us. We keep
  the link between your account and your Stripe customer record so your plan works.

## Who processes it
Supabase (accounts and database), Anthropic (AI responses), Stripe (payments) and our
hosting provider. We don't sell your information and we don't use advertising
trackers in the app.

## Keeping and deleting it
We keep your account information and practice history while your account exists. To
delete your account, email {SUPPORT_EMAIL} from your account's email address. We'll
delete your account, practice history and challenges, except records we must keep for
tax and accounting (such as payment records at Stripe). Deleted data can remain in
routine backups for a limited time until those backups roll over.

## Please don't enter
Social Security numbers, DoD ID numbers, passwords, classified information or
controlled unclassified information. Share only what your study question needs.

## Changes
If we change this notice, we'll post the new version here with a new effective date.
"""

REFUNDS_MD = f"""
# Refund Policy

**Effective {EFFECTIVE_DATE}**

## The short version
**Ask within 7 days of a charge and we'll refund that charge in full.** This applies to
your latest charge, whether it's your first payment or a monthly renewal.

## Details
- **How to ask:** email {SUPPORT_EMAIL} from the email address on your Score Surge
  account, within 7 days of the charge. No reason needed.
- **What gets refunded:** the full amount of your most recent charge. Earlier charges,
  and requests made after 7 days, are at our discretion.
- **What happens to your plan:** when we refund a charge we also cancel the
  subscription, so it won't renew, and paid access ends.
- **How long it takes:** refunds go back to the card you paid with through Stripe.
  Depending on your bank it usually takes 5–10 business days to show up.
- **Free trial:** the 3-day trial needs no card and is never charged, so there's
  nothing to refund.
- **Duplicate or unauthorized charges:** email us and we'll put it right.

## Canceling without a refund
You can cancel any time from **My Profile → Manage subscription / cancel**. That stops
future renewals, and you keep paid access through the end of the period you've
already paid for.

Nothing in this policy limits any right you have that cannot be waived under
applicable law.
"""

LEGAL_PAGES = {
    "terms": ("Terms of Use", TERMS_MD),
    "privacy": ("Privacy Notice", PRIVACY_MD),
    "refunds": ("Refund Policy", REFUNDS_MD),
}


def legal_links_md(base_url=""):
    """The footer's links. `base_url` makes them absolute when the app knows it."""
    base = (base_url or "").rstrip("/")
    return " · ".join(
        f"[{title}]({base}/?page={slug})" for slug, (title, _) in LEGAL_PAGES.items()
    )


def trial_days_left(trial_start_iso, now):
    """Whole days left in a 3-day trial, never negative. None if the start is unreadable."""
    import datetime
    try:
        started = datetime.datetime.fromisoformat(str(trial_start_iso).replace("Z", "+00:00"))
    except Exception:
        return None
    return max(0, 3 - (now - started).days)


def trial_banner_text(days_left, po_price, chief_price):
    """'Free trial: 2 days left. Plans from $12.99/mo; full access is Chief at $19.99/mo.'

    The review's example read "Keep full access from $12.99/mo", but $12.99 (Petty
    Officer) doesn't include the Mock Exam or Planner -- only Chief ($19.99) is full
    access -- so the line names both rather than promise what $12.99 doesn't buy.
    """
    if days_left is None:
        lead = "Free trial active."
    elif days_left == 0:
        lead = "Free trial: last day."
    else:
        lead = f"Free trial: {days_left} day{'s' if days_left != 1 else ''} left."
    # "$" is escaped: Streamlit's markdown reads the text between two dollar signs
    # as a math formula, which is what this line looked like before (found in the
    # browser 22 Oct 2026).
    return (f"🎖️ **{lead}** Plans from {money_md(po_price)}; keep full access with "
            f"Chief at {money_md(chief_price)}.")


def money_md(text):
    """A price that's safe inside Streamlit markdown ($ would start a math formula)."""
    return str(text).replace("$", "\\$")


def rewrite_relative_links(md, base_url=""):
    """Point the legal pages' '(?page=...)' links at the app's real address."""
    base = (base_url or "").rstrip("/")
    return md.replace("(?page=", f"({base}/?page=")
