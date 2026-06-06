from rest_framework import serializers
from .models import Investor, Investment, Withdrawal

MIN_AMOUNT = 500_000
MAX_AMOUNT = 2_000_000


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
        if value < MIN_AMOUNT:
            raise serializers.ValidationError(
                f"Minimum investment amount is ${MIN_AMOUNT:,}."
            )
        if value > MAX_AMOUNT:
            raise serializers.ValidationError(
                f"Maximum investment amount is ${MAX_AMOUNT:,}."
            )
        return value


class WithdrawalSerializer(serializers.ModelSerializer):
    class Meta:
        model            = Withdrawal
        fields           = "__all__"
        read_only_fields = ["investor"]