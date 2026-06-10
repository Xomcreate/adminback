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
    # Flattened investor fields so both frontend resolvers work without
    # needing to traverse nested objects on every row.
    investor_name  = serializers.SerializerMethodField()
    investor_email = serializers.SerializerMethodField()

    class Meta:
        model  = Investment
        fields = "__all__"
        read_only_fields = [
            "investor",
            "current_profit",
            "daily_roi",
            # NOTE: "active", "approved", "status" are intentionally NOT read-only
            # so that admin PATCH requests can persist approval/decline changes.
        ]

    # ── investor helpers ──────────────────────────────────────────────────────

    def get_investor_name(self, obj):
        inv = obj.investor
        if inv.name and inv.name.strip():
            return inv.name.strip()
        u = getattr(inv, "user", None)
        if u:
            full = f"{u.first_name or ''} {u.last_name or ''}".strip()
            if full:
                return full
            return u.username or u.email or ""
        return "Unknown Investor"

    def get_investor_email(self, obj):
        return obj.investor.email or ""

    # ── validation ────────────────────────────────────────────────────────────

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