from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


NETWORK_MAP = {
    "BTC":  "Bitcoin Network",
    "TRX":  "TRC-20 Network",
    "ETH":  "ERC-20 Network",
    "USDT": "TRC-20 / ERC-20",
    "LTC":  "Litecoin Network",
    "XRP":  "XRP Ledger",
}


class Investor(models.Model):
    ROLE_CHOICES = (
        ("admin", "Admin"),
        ("user",  "User"),
    )

    user       = models.OneToOneField(User, on_delete=models.CASCADE)
    name       = models.CharField(max_length=100)
    email      = models.EmailField(unique=True)
    phone      = models.CharField(max_length=20, blank=True)
    country    = models.CharField(max_length=100, blank=True, default="")
    role       = models.CharField(max_length=10, choices=ROLE_CHOICES, default="user")
    balance    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bonus      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    blocked    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    referral_code = models.CharField(max_length=20, unique=True, blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.referral_code:
            self.referral_code = self._generate_referral_code()
        super().save(*args, **kwargs)

    def _generate_referral_code(self):
        import uuid
        return uuid.uuid4().hex[:8].upper()

    @property
    def tier(self):
        count = self.investment_set.count()
        if count >= 6:
            return "diamond"
        elif count >= 3:
            return "gold"
        elif count >= 1:
            return "silver"
        return "none"

    @property
    def kyc_status(self):
        """Returns the latest KYC status for this investor."""
        latest = self.kyc_submissions.order_by("-submitted_at").first()
        if not latest:
            return "unverified"
        return latest.status


class Investment(models.Model):
    TYPE_CHOICES = (
        ("stock", "Stock"),
        ("plan",  "Plan"),
    )
    STATUS_CHOICES = (
        ("Pending",  "Pending"),
        ("Approved", "Approved"),
        ("Declined", "Declined"),
    )

    investor       = models.ForeignKey(Investor, on_delete=models.CASCADE)
    category       = models.CharField(max_length=100, default="")
    plan           = models.CharField(max_length=100, blank=True, default="")
    type           = models.CharField(max_length=10, choices=TYPE_CHOICES, default="stock")
    amount         = models.DecimalField(max_digits=12, decimal_places=2)
    daily_roi      = models.DecimalField(max_digits=5, decimal_places=2, default=10.0)
    current_profit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=50, default="BTC")
    payment_proof  = models.ImageField(upload_to="proofs/", blank=True, null=True)
    active         = models.BooleanField(default=False)
    approved       = models.BooleanField(default=False)
    status         = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Pending")
    created_at     = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        label = self.plan or self.category or "Investment"
        return f"{self.investor.name} - {label} - ${self.amount}"


class Withdrawal(models.Model):
    investor       = models.ForeignKey(Investor, on_delete=models.CASCADE)
    amount         = models.DecimalField(max_digits=12, decimal_places=2)
    wallet_address = models.CharField(max_length=255)
    status         = models.CharField(max_length=20, default="Pending")
    created_at     = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.investor.name} - ${self.amount} - {self.status}"


class Deposit(models.Model):
    STATUS_CHOICES = (
        ("pending",  "Pending"),
        ("approved", "Approved"),
        ("declined", "Declined"),
    )

    investor       = models.ForeignKey(Investor, on_delete=models.CASCADE, related_name="deposits")
    payment_method = models.CharField(max_length=10)
    amount         = models.DecimalField(max_digits=12, decimal_places=2)
    payment_proof  = models.ImageField(upload_to="deposit_proofs/", blank=True, null=True)
    status         = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    created_at     = models.DateTimeField(auto_now_add=True)

    @property
    def network(self):
        return NETWORK_MAP.get(self.payment_method, "Unknown Network")

    def __str__(self):
        return f"{self.investor.name} - {self.payment_method} - ${self.amount} - {self.status}"


class Referral(models.Model):
    STATUS_CHOICES = (
        ("pending",  "Pending"),
        ("active",   "Active"),
        ("inactive", "Inactive"),
        ("expired",  "Expired"),
    )
    COMMISSION_DEFAULT = 50.00

    referrer      = models.ForeignKey(Investor, on_delete=models.CASCADE, related_name="referrals_made")
    referred_user = models.OneToOneField(
        Investor, on_delete=models.SET_NULL, null=True, blank=True, related_name="referral_source"
    )
    referred_name  = models.CharField(max_length=150, blank=True, default="")
    referred_email = models.EmailField(blank=True, default="")
    referrer_name  = models.CharField(max_length=150, blank=True, default="")

    status     = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    approved   = models.BooleanField(default=False)
    commission = models.DecimalField(max_digits=10, decimal_places=2, default=COMMISSION_DEFAULT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"{self.referrer.name} → "
            f"{self.referred_name or self.referred_email or 'Unknown'} "
            f"[{self.status}]"
        )


# ─────────────────────────────────────────────
# KYC SUBMISSION
# ─────────────────────────────────────────────

class KYCSubmission(models.Model):
    DOC_TYPE_CHOICES = (
        ("national_id",      "National ID"),
        ("passport",         "Passport"),
        ("drivers_license",  "Driver's License"),
    )
    STATUS_CHOICES = (
        ("pending",  "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    )

    investor      = models.ForeignKey(Investor, on_delete=models.CASCADE, related_name="kyc_submissions")
    document_type = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES, default="national_id")
    id_front      = models.ImageField(upload_to="kyc/id_front/")
    id_back       = models.ImageField(upload_to="kyc/id_back/")
    selfie        = models.ImageField(upload_to="kyc/selfies/")
    status        = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    submitted_at  = models.DateTimeField(auto_now_add=True)
    reviewed_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.investor.name} — KYC [{self.status}] @ {self.submitted_at:%Y-%m-%d}"

    @property
    def doc_type_display(self):
        return dict(self.DOC_TYPE_CHOICES).get(self.document_type, self.document_type)


# ─────────────────────────────────────────────
# COPY TRADING
# ─────────────────────────────────────────────

COPY_TRADING_PLAN_CHOICES = (
    ("Starter",       "Starter"),
    ("Pro",           "Pro"),
    ("Institutional", "Institutional"),
)
COPY_TRADING_STATUS_CHOICES = (
    ("Pending",  "Pending"),
    ("Approved", "Approved"),
    ("Declined", "Declined"),
    ("Expired",  "Expired"),
)
COPY_TRADING_PLAN_PRICES = {
    "Starter":       49,
    "Pro":           149,
    "Institutional": 499,
}
COPY_TRADING_PLAN_DEPOSITS = {
    "Starter":       500,
    "Pro":           2000,
    "Institutional": 10000,
}

COPY_TRADING_TRADER_DURATIONS = {
    "Alex Mercer":   14,
    "Sofia Chen":    30,
    "Raj Patel":     7,
    "Elena Kovacs":  30,
    "Marcus Webb":   14,
    "Nadia Osei":    21,
}
COPY_TRADING_DEFAULT_DURATION_DAYS = 14


class CopyTradingSubscription(models.Model):
    investor   = models.ForeignKey(Investor, on_delete=models.CASCADE, related_name="copy_trading_subscriptions")
    plan       = models.CharField(max_length=20, choices=COPY_TRADING_PLAN_CHOICES, default="Starter")
    status     = models.CharField(max_length=20, choices=COPY_TRADING_STATUS_CHOICES, default="Pending")
    approved   = models.BooleanField(default=False)
    active     = models.BooleanField(default=False)

    plan_fee       = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    copied_trader  = models.CharField(max_length=100, blank=True, default="")

    duration_days   = models.PositiveIntegerField(default=COPY_TRADING_DEFAULT_DURATION_DAYS)
    copy_started_at = models.DateTimeField(null=True, blank=True)
    copy_ends_at    = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.investor.name} — {self.plan} [{self.status}]"

    def start_copying(self):
        now = timezone.now()
        self.copy_started_at = now
        self.copy_ends_at    = now + timezone.timedelta(days=self.duration_days)
        self.status   = "Approved"
        self.approved = True
        self.active   = True

    def stop_copying(self, status="Declined"):
        self.status   = status
        self.approved = False
        self.active   = False

    @property
    def is_expired(self):
        return bool(
            self.active
            and self.copy_ends_at is not None
            and timezone.now() >= self.copy_ends_at
        )


# ─────────────────────────────────────────────
# BOT SUBSCRIPTIONS
# ─────────────────────────────────────────────

BOT_SUBSCRIPTION_PLAN_CHOICES = (
    ("Starter",       "Starter"),
    ("Pro",           "Pro"),
    ("Institutional", "Institutional"),
)
BOT_SUBSCRIPTION_STATUS_CHOICES = (
    ("Pending",  "Pending"),
    ("Approved", "Approved"),
    ("Declined", "Declined"),
)
BOT_BILLING_PERIOD_CHOICES = (
    ("weekly",  "Weekly"),
    ("monthly", "Monthly"),
    ("yearly",  "Yearly"),
)

BOT_PLAN_PRICES = {
    "weekly":  {"Starter": 15,   "Pro": 40,   "Institutional": 130},
    "monthly": {"Starter": 49,   "Pro": 149,  "Institutional": 499},
    "yearly":  {"Starter": 410,  "Pro": 1250, "Institutional": 4190},
}
BOT_PLAN_DEPOSITS = {
    "Starter":       500,
    "Pro":           2000,
    "Institutional": 10000,
}


class BotSubscription(models.Model):
    investor       = models.ForeignKey(Investor, on_delete=models.CASCADE, related_name="bot_subscriptions")
    plan           = models.CharField(max_length=20, choices=BOT_SUBSCRIPTION_PLAN_CHOICES, default="Starter")
    billing_period = models.CharField(max_length=10, choices=BOT_BILLING_PERIOD_CHOICES, default="monthly")
    status         = models.CharField(max_length=20, choices=BOT_SUBSCRIPTION_STATUS_CHOICES, default="Pending")
    approved       = models.BooleanField(default=False)
    active         = models.BooleanField(default=False)

    plan_fee       = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.investor.name} — {self.plan} / {self.billing_period} [{self.status}]"