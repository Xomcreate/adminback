from django.db import models
from django.contrib.auth.models import User


class Investor(models.Model):
    ROLE_CHOICES = (
        ("admin", "Admin"),
        ("user",  "User"),
    )

    user       = models.OneToOneField(User, on_delete=models.CASCADE)
    name       = models.CharField(max_length=100)
    email      = models.EmailField(unique=True)
    phone      = models.CharField(max_length=20, blank=True)
    role       = models.CharField(max_length=10, choices=ROLE_CHOICES, default="user")
    balance    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bonus      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    blocked    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    @property
    def tier(self):
        count = self.investment_set.count()
        if count >= 6:
            return "bronze"
        elif count >= 3:
            return "gold"
        elif count >= 1:
            return "silver"
        return "none"


class Investment(models.Model):
    investor       = models.ForeignKey(Investor, on_delete=models.CASCADE)
    category       = models.CharField(max_length=100, default="General")
    amount         = models.DecimalField(max_digits=12, decimal_places=2)
    daily_roi      = models.DecimalField(max_digits=5, decimal_places=2, default=2.0)
    current_profit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=50, default="BTC")
    payment_proof  = models.ImageField(upload_to="proofs/", blank=True, null=True)
    active         = models.BooleanField(default=False)
    approved       = models.BooleanField(default=False)
    created_at     = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.investor.name} - {self.category} - ${self.amount}"


class Withdrawal(models.Model):
    investor       = models.ForeignKey(Investor, on_delete=models.CASCADE)
    amount         = models.DecimalField(max_digits=12, decimal_places=2)
    wallet_address = models.CharField(max_length=255)
    status         = models.CharField(max_length=20, default="Pending")
    created_at     = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.investor.name} - ${self.amount} - {self.status}"