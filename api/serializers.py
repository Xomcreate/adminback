from rest_framework import serializers
from .models import Investor, Investment, Deposit, Withdrawal, Referral

# ── Per-plan min/max pulled from InvestmentPlans.jsx ─────────────────────────
# Trial: 500–5000 | Essential: 5000–10000 | Premium: 10000–50000
# Ultimate: 50000–250000 | Royal: 250000–500000 | Diamond: 500000–2000000
# Stocks start as low as $299 (AAPL in PurchaseStocks.jsx)
GLOBAL_MIN_AMOUNT = 299
GLOBAL_MAX_AMOUNT = 2_000_000


class InvestorSerializer(serializers.ModelSerializer):
    tier = serializers.ReadOnlyField()

    class Meta:
        model  = Investor
        fields = "__all__"


class InvestmentSerializer(serializers.ModelSerializer):
    class Meta:
        model            = Investment
        fields           = "__all__"
        read_only_fields = ["investor", "active", "approved", "current_profit", "daily_roi"]

    def validate_amount(self, value):
        if value < GLOBAL_MIN_AMOUNT:
            raise serializers.ValidationError(
                f"Minimum investment amount is ${GLOBAL_MIN_AMOUNT:,}."
            )
        if value > GLOBAL_MAX_AMOUNT:
            raise serializers.ValidationError(
                f"Maximum investment amount is ${GLOBAL_MAX_AMOUNT:,}."
            )
        return value


class DepositSerializer(serializers.ModelSerializer):
    class Meta:
        model            = Deposit
        fields           = "__all__"
        read_only_fields = ["investor", "status"]

    def validate_amount(self, value):
        if value < 500:
            raise serializers.ValidationError("Minimum deposit amount is $500.")
        return value

    def validate_payment_method(self, value):
        valid = {"BTC", "TRX", "ETH", "USDT", "LTC", "XRP"}
        if value.upper() not in valid:
            raise serializers.ValidationError(
                f"Invalid payment method. Choose from: {', '.join(valid)}."
            )
        return value.upper()


class WithdrawalSerializer(serializers.ModelSerializer):
    class Meta:
        model            = Withdrawal
        fields           = "__all__"
        read_only_fields = ["investor"]


class ReferralSerializer(serializers.ModelSerializer):
    referrer_name = serializers.CharField(source="referrer.name", read_only=True)
    referred_name = serializers.CharField(source="referred.name", read_only=True)

    class Meta:
        model  = Referral
        fields = "__all__"