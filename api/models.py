from django.db import models
from django.contrib.auth.models import User


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

    # Referral code — auto-generated on save, unique per investor
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
    type           = models.CharField(
                         max_length=10, choices=TYPE_CHOICES, default="stock"
                     )
    amount         = models.DecimalField(max_digits=12, decimal_places=2)
    daily_roi      = models.DecimalField(max_digits=5, decimal_places=2, default=10.0)
    current_profit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=50, default="BTC")
    payment_proof  = models.ImageField(upload_to="proofs/", blank=True, null=True)
    active         = models.BooleanField(default=False)
    approved       = models.BooleanField(default=False)
    status         = models.CharField(
                         max_length=20, choices=STATUS_CHOICES, default="Pending"
                     )
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

    investor       = models.ForeignKey(
                         Investor, on_delete=models.CASCADE, related_name="deposits"
                     )
    payment_method = models.CharField(max_length=10)
    amount         = models.DecimalField(max_digits=12, decimal_places=2)
    payment_proof  = models.ImageField(upload_to="deposit_proofs/", blank=True, null=True)
    status         = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    created_at     = models.DateTimeField(auto_now_add=True)

    @property
    def network(self):
        return NETWORK_MAP.get(self.payment_method, "Unknown Network")

    def __str__(self):
        return (
            f"{self.investor.name} - {self.payment_method} "
            f"- ${self.amount} - {self.status}"
        )


class Referral(models.Model):
    """
    Tracks a referral relationship between two investors.

    - referrer   : the investor who shared their referral link
    - referred_user : the newly registered investor (nullable until they register)
    - referred_email: email captured at sign-up for display even before profile loads
    - status     : pending → active (approved by admin) or inactive (declined)
    - commission : dollar amount credited to the referrer when status → active
    - approved   : mirrors status for quick boolean checks on the frontend
    """

    STATUS_CHOICES = (
        ("pending",  "Pending"),
        ("active",   "Active"),
        ("inactive", "Inactive"),
        ("expired",  "Expired"),
    )

    COMMISSION_DEFAULT = 50.00   # $50 flat commission per approved referral

    referrer      = models.ForeignKey(
                        Investor,
                        on_delete=models.CASCADE,
                        related_name="referrals_made",
                    )
    referred_user = models.OneToOneField(
                        Investor,
                        on_delete=models.SET_NULL,
                        null=True,
                        blank=True,
                        related_name="referral_source",
                    )
    # Denormalised for display even before the referred user activates
    referred_name  = models.CharField(max_length=150, blank=True, default="")
    referred_email = models.EmailField(blank=True, default="")

    # Denormalised referrer name snapshot (admin list display)
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