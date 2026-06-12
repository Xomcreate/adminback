from rest_framework import serializers
from .models import Investor, Investment, Withdrawal, Deposit, Referral

MIN_AMOUNT = 500
MAX_AMOUNT = 10_000_000


class InvestorSerializer(serializers.ModelSerializer):
    tier = serializers.ReadOnlyField()

    class Meta:
        model  = Investor
        fields = "__all__"


class InvestmentSerializer(serializers.ModelSerializer):
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


# ─────────────────────────────────────────────
# REFERRAL SERIALIZER
# ─────────────────────────────────────────────

class ReferralSerializer(serializers.ModelSerializer):
    """
    Full read serializer — exposes nested referrer/referred objects so the
    frontend resolver helpers (resolveUserName, resolveReferrerName, etc.)
    work without changes.
    """

    # Nested snapshots for the frontend resolvers
    referrer      = serializers.SerializerMethodField()
    referred_user = serializers.SerializerMethodField()

    class Meta:
        model  = Referral
        fields = [
            "id",
            "referrer",
            "referrer_name",
            "referred_user",
            "referred_name",
            "referred_email",
            "status",
            "approved",
            "commission",
            "created_at",
        ]
        # FIX: commission is read-only — clients must not be able to set
        # a custom commission amount via the API.
        read_only_fields = [
            "created_at",
            "referrer_name",
            "referred_name",
            "referred_email",
            "commission",       # ← added
        ]

    def get_referrer(self, obj):
        """Return a minimal dict matching what the frontend resolveReferrerName helper expects."""
        r = obj.referrer
        return {
            "id":         r.id,
            "first_name": "",
            "last_name":  "",
            "username":   r.name,
            "email":      r.email,
        }

    def get_referred_user(self, obj):
        """Return a minimal dict matching what the frontend resolveUserName helper expects."""
        if not obj.referred_user:
            return None
        u = obj.referred_user
        return {
            "id":         u.id,
            "first_name": "",
            "last_name":  "",
            "full_name":  u.name,
            "username":   u.name,
            "email":      u.email,
        }


class ReferralStatsSerializer(serializers.Serializer):
    """Used by the /referrals/my-stats/ endpoint."""
    total_referred   = serializers.IntegerField()
    active_contracts = serializers.IntegerField()
    total_earnings   = serializers.DecimalField(max_digits=12, decimal_places=2)
    referral_code    = serializers.CharField()
    referral_link    = serializers.CharField()