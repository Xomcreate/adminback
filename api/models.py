from django.db import models


class Investor(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20)

    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    bonus = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    blocked = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Investment(models.Model):
    investor = models.ForeignKey(
        Investor,
        on_delete=models.CASCADE
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    daily_roi = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=5
    )

    active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.investor.name


class Withdrawal(models.Model):
    investor = models.ForeignKey(
        Investor,
        on_delete=models.CASCADE
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    status = models.CharField(
        max_length=20,
        default="Pending"
    )

    created_at = models.DateTimeField(auto_now_add=True)