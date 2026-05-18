# Architecture & Monetization

---

## 1. Current Stack

```
  Frontend        Next.js 15 + Tailwind + Recharts + Clerk auth
                  Hosted on Vercel (Hobby — needs upgrade to Pro)
                  Repo: sqilled-options-v2 (GitHub)

  Backend         FastAPI (Python)
                  Hosted on DigitalOcean Droplet $6/mo (1 CPU / 1GB RAM)
                  Managed as systemd service: sqilled-api

  Database        DigitalOcean Managed MySQL $15/mo
                  Tables: dim_underlying, dim_option_contract, fact_option_eod
                  ~10M+ rows, indexed on (option_id, trade_date)

  Auth            Clerk (Free tier — 10k MAU)

  Analytics       PostHog (Free tier)

  Payments        Not yet — Stripe needed

  Total cost:     ~$21/mo
```

---

## 2. What Needs to Change Before Monetizing

### 2.1 Vercel — Upgrade to Pro ($20/mo)

**Why required:**
- Hobby plan prohibits commercial use (Vercel ToS)
- Hobby plan has 10s serverless function timeout
- Our simulations take up to 180s — already working around this
- Pro plan: 300s timeout, team members, commercial use allowed

**Action:** Upgrade at vercel.com/account/billing

---

### 2.2 Droplet — Upgrade to $12/mo (2 CPU / 2GB)

**Why:**
- Current: 1 CPU / 1GB RAM
- Grid simulation runs 12 threads simultaneously
- Under concurrent users, 1 CPU causes queuing and timeouts
- 2 CPU handles 3-5 concurrent heavy simulations

**Action:** Resize droplet in DigitalOcean console (takes ~2 min, no data loss)

---

### 2.3 Database Connection Pooling

**Why:**
- Each API request opens a new DB connection
- Under 10+ concurrent users this saturates the DO MySQL connection limit
- Fix: SQLAlchemy connection pool (pool_size=10, max_overflow=5)

**Action:** Update `api/db.py` engine config

---

### 2.4 Uptime Monitoring

**Why:**
- No alerts if the droplet API goes down
- Paid users will churn if the tool is down with no communication

**Action:**
- UptimeRobot free tier — ping `http://147.182.205.5:8000/health` every 5 min
- Alert to Slack webhook on failure (same webhook already in use)
- Add `/health` endpoint to FastAPI (one line)

---

### 2.5 Error Tracking

**Why:**
- No visibility into backend errors (500s, crashes, timeouts)
- Paid users will report bugs we can't reproduce

**Action:**
- Sentry free tier for FastAPI — 5k errors/month free
- `pip install sentry-sdk[fastapi]` + one line init in main.py

---

## 3. Monetization Model

### Tier Design

```
  FREE
  ─────────────────────────────────────────────
  Date range:       Last 2 years only
  Symbols:          SPY only
  Strategies:       CC + CSP only (no wheel, no strangle)
  Grid:             Locked
  Trade log export: Locked
  ─────────────────────────────────────────────

  PRO  — $29/mo or $249/yr ($20.75/mo)
  ─────────────────────────────────────────────
  Date range:       Full history (8+ years)
  Symbols:          SPY, QQQ, IWM, AAPL
  Strategies:       All (CC, CSP, Wheel, Strangle, Iron Condor, Bull Put Spread)
  Grid:             Full access
  Trade log export: CSV download
  IV + VIX regime:  Full access
  Position sizing:  Full access
  ─────────────────────────────────────────────

  RESEARCH  — $79/mo or $699/yr ($58.25/mo)
  ─────────────────────────────────────────────
  Everything in Pro
  Custom date ranges with no limits
  API access (personal use)
  Priority support
  Early access to new features
  ─────────────────────────────────────────────
```

### Revenue math

```
  10 Pro users     × $29   = $290/mo
  50 Pro users     × $29   = $1,450/mo
  100 Pro users    × $29   = $2,900/mo
  10 Research      × $79   = $790/mo
  
  Break even (infra):  2 Pro users
  First $1k/mo target: 35 Pro users
  First $5k/mo target: 100 Pro + 20 Research
```

---

## 4. Stripe Integration

### Setup

1. Create Stripe account at stripe.com
2. Create two products: Pro ($29/mo + $249/yr) and Research ($79/mo + $699/yr)
3. Get API keys (publishable + secret)
4. Add to Vercel environment variables: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`

### Flow

```
  User clicks "Upgrade"
        ↓
  Frontend → POST /api/stripe/checkout
        ↓
  Create Stripe Checkout Session (hosted page)
        ↓
  User pays on Stripe
        ↓
  Stripe webhook → POST /api/stripe/webhook
        ↓
  Update Clerk user metadata: role = "pro" or "research"
        ↓
  User redirected back, features unlocked
```

### Files to create

```
  app/api/stripe/checkout/route.ts    Create checkout session
  app/api/stripe/webhook/route.ts     Handle payment events
  app/pricing/page.tsx                Pricing page
  lib/stripe.ts                       Stripe client init
```

### Webhook events to handle

```
  checkout.session.completed    → set user role to paid tier
  customer.subscription.deleted → downgrade user to free
  invoice.payment_failed        → notify user, keep access for 3 days
```

---

## 5. Clerk Role Gating

### How it works

Clerk supports custom metadata on each user. We store the subscription tier there.

```typescript
  // On successful payment (webhook handler)
  await clerkClient.users.updateUserMetadata(userId, {
    publicMetadata: {
      plan: 'pro',                          // 'free' | 'pro' | 'research'
      stripe_customer_id: customerId,
      stripe_subscription_id: subscriptionId,
    }
  })
```

### Reading the role in API routes

```typescript
  // In any Next.js API route
  const { userId } = auth()
  const user = await clerkClient.users.getUser(userId)
  const plan = user.publicMetadata.plan ?? 'free'
  
  if (plan === 'free' && requestedYears > 2) {
    return NextResponse.json({ error: 'Upgrade to Pro for full history' }, { status: 403 })
  }
```

### Gate logic

```typescript
  const PLAN_LIMITS = {
    free: {
      max_history_years: 2,
      symbols: ['SPY'],
      strategies: ['cc', 'csp'],
      grid_access: false,
      export_access: false,
    },
    pro: {
      max_history_years: 10,
      symbols: ['SPY', 'QQQ', 'IWM', 'AAPL'],
      strategies: ['cc', 'csp', 'wheel', 'strangle', 'iron_condor', 'bull_put_spread'],
      grid_access: true,
      export_access: true,
    },
    research: {
      max_history_years: 999,
      symbols: 'all',
      strategies: 'all',
      grid_access: true,
      export_access: true,
      api_access: true,
    }
  }
```

---

## 6. Frontend Gate Implementation

### Date range enforcement

```typescript
  // In overlay/page.tsx — before firing the simulate request
  const plan = user?.publicMetadata?.plan ?? 'free'
  const minAllowedStart = plan === 'free'
    ? new Date(new Date().setFullYear(new Date().getFullYear() - 2)).toISOString().slice(0, 10)
    : '2005-01-01'

  if (startDate < minAllowedStart && plan === 'free') {
    // Show upgrade prompt instead of running simulation
    setShowUpgradeModal(true)
    return
  }
```

### Strategy gate

```typescript
  // Disable wheel/strangle buttons for free users, show lock icon
  const isLocked = plan === 'free' && ['wheel', 'strangle'].includes(strategy)
  
  <button disabled={isLocked} onClick={...}>
    {isLocked && <Lock className="w-4 h-4 mr-1" />}
    Wheel
  </button>
```

### Grid gate

```typescript
  // Hide "Run Grid" button for free users, show upgrade prompt on click
  {plan === 'free' ? (
    <button onClick={() => setShowUpgradeModal(true)}>
      <Lock className="w-4 h-4" /> Run Grid — Pro Only
    </button>
  ) : (
    <button onClick={handleRunGrid}>Run Grid</button>
  )}
```

---

## 7. Pricing Page

### URL: `/pricing`

### Content structure

```
  Hero: "Backtest options strategies. Make better decisions."
  
  Three columns: Free | Pro | Research
  
  Each column:
  - Price
  - Feature list with checkmarks
  - CTA button ("Get Started Free" / "Upgrade to Pro" / "Get Research Access")
  
  FAQ below:
  - What data is included?
  - Can I cancel anytime?
  - Is there a free trial?
  - What symbols are available?
```

### Free trial consideration

Offer 7-day free trial on Pro. Reduces friction significantly.
Stripe supports trial periods natively (`trial_period_days: 7`).

---

## 8. Upgrade Modal

Show when a free user hits a gate. Keep it simple.

```
  ┌─────────────────────────────────────┐
  │  Unlock Full History                │
  │                                     │
  │  You're on the Free plan. Full      │
  │  history (8+ years) requires Pro.   │
  │                                     │
  │  [Upgrade to Pro — $29/mo]          │
  │  [Maybe later]                      │
  └─────────────────────────────────────┘
```

Trigger points:
- Date range goes beyond 2 years
- Clicks Wheel / Strangle / Iron Condor
- Clicks Run Grid
- Clicks CSV export

---

## 9. Usage Tracking for Conversion

PostHog is already installed. Add these events:

```typescript
  posthog.capture('hit_paywall', {
    gate: 'date_range' | 'strategy' | 'grid' | 'export',
    plan: 'free',
    attempted_value: startDate or strategyName,
  })

  posthog.capture('upgrade_modal_shown', { gate })
  posthog.capture('upgrade_clicked', { gate, plan_selected: 'pro' | 'research' })
  posthog.capture('upgrade_completed', { plan: 'pro', source: gate })
```

This tells you which gates convert best → optimize those first.

---

## 10. Growth Infrastructure

### SEO

- Each strategy result page should be shareable via URL params
  - `/overlay?symbol=SPY&strategy=cc&start=2020-01-01&end=2024-12-31`
  - Pre-populate form from URL → user sees result immediately
- Add meta tags for sharing (og:image, og:description)
- Blog / research section on sqilled.co feeds organic traffic

### Referral

- "Share this backtest" button → generates shareable link
- Referred users see the result without logging in (free preview)
- Prompts signup after viewing

### Email capture

- Free users get monthly email: "SPY covered call update — last month's result"
- Sends the latest month's CC/CSP result automatically
- Keeps users engaged, drives upgrades

---

## 11. Full Infrastructure Cost at Scale

```
  Stage           Users       Monthly cost
  ────────────────────────────────────────
  Now             0-50        $21/mo
  After upgrade   0-50        $55/mo
    Vercel Pro      $20
    DO Droplet $12  $12
    DO MySQL        $15
    Clerk Free      $0
    Sentry Free     $0
    Stripe          2.9% + $0.30 per transaction

  Growing         50-500      $120/mo
    Vercel Pro      $20
    DO Droplet $24  $24 (4 CPU / 8GB)
    DO MySQL        $50 (upgraded plan)
    Clerk Growth    $25 (>10k MAU)
    Sentry Team     $0 (still free)

  Scale           500-2000    $300/mo
    Vercel Pro      $20
    DO Droplet $48  $48 (dedicated CPU)
    DO MySQL        $100
    Redis queue     $15 (for job queuing)
    Clerk           $100
```

At 100 Pro users ($2,900/mo revenue), infrastructure is $120/mo = 95.9% margin.

---

## 12. Migration Path if We Outgrow DigitalOcean

Not needed now. Document for later.

```
  Droplet → multiple workers   Add load balancer + 2 droplets behind it
  MySQL → read replicas        DO supports read replicas natively
  Simulations → job queue      Redis + Celery workers for async simulation
  Frontend → same Vercel       Already scales automatically
```

The current stack handles ~50-100 concurrent users without any changes.
Only scale infrastructure when revenue justifies it.

---

## 13. Action List — Before Launch

```
  REQUIRED (do before charging anyone)
  ☐ Upgrade Vercel to Pro
  ☐ Upgrade droplet to $12 plan
  ☐ Add DB connection pooling
  ☐ Add /health endpoint to FastAPI
  ☐ Set up UptimeRobot monitoring
  ☐ Set up Sentry error tracking
  ☐ Create Stripe account + products
  ☐ Build checkout + webhook routes
  ☐ Build Clerk role update on payment
  ☐ Add gate logic to frontend
  ☐ Build /pricing page
  ☐ Build upgrade modal
  ☐ Add paywall PostHog events
  ☐ Test full payment flow end to end
  ☐ Add Terms of Service + Privacy Policy pages (required by Stripe)

  RECOMMENDED (do soon after launch)
  ☐ 7-day free trial on Pro
  ☐ Shareable backtest URL
  ☐ Monthly email for free users
  ☐ Referral program
```
