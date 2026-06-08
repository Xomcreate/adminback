from rest_framework import serializers
from .models import Investor, Investment, Withdrawal, Deposit

MIN_AMOUNT = 500
MAX_AMOUNT = 10_000_000


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


class DepositSerializer(serializers.ModelSerializer):
    # Flatten investor info for the admin list view
    user    = serializers.SerializerMethodField()
    email   = serializers.SerializerMethodField()
    network = serializers.ReadOnlyField()

    class Meta:
        model  = Deposit
        fields = [
            "id", "user", "email", "payment_method", "network",
            "amount", "payment_proof", "status", "created_at",
        ]
        read_only_fields = ["status", "created_at"]

    def get_user(self, obj):
        return obj.investor.name

    def get_email(self, obj):
        return obj.investor.email


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(required=True)
    new_password     = serializers.CharField(required=True, min_length=8)


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)