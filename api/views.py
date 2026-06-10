import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import models, transaction
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import serializers as drf_serializers

from .models import Investor, Investment, Withdrawal, Deposit
from .serializers import (
    InvestorSerializer, InvestmentSerializer, WithdrawalSerializer,
    DepositSerializer, ChangePasswordSerializer, ForgotPasswordSerializer,
)

logger = logging.getLogger(__name__)

# ── Valid category / plan lists ───────────────────────────────────────────────

STOCK_CATEGORIES = [
    "Shopify Inc. (SHOP)",
    "Tesla (TSLA)",
    "Meta (META)",
    "Amazon (AMZN)",
    "NVIDIA (NVDA)",
    "Apple (AAPL)",
    "Microsoft (MSFT)",
    "Netflix (NFLX)",
    "McDonald's (MCD)",
    "GameStop (GME)",
    "Coca-Cola (KO)",
    "Alphabet (GOOG)",
    "Intel (INTC)",
]

PLAN_NAMES = [
    "Trial Plan",
    "Essential Plan",
    "Premium Plan",
    "Ultimate Plan",
    "Royal Plan",
    "Diamond Plan",
]

DAILY_ROI   = 25.0
EXPIRY_DAYS = 120


def get_tier(investment_count):
    if investment_count >= 6:
        return "diamond"
    elif investment_count >= 3:
        return "gold"
    elif investment_count >= 1:
        return "silver"
    return "none"


# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    username = request.data.get("username")
    email    = request.data.get("email")
    password = request.data.get("password")

    if not username or not email or not password:
        return Response({"error": "All fields are required"}, status=400)
    if User.objects.filter(username=username).exists():
        return Response({"error": "Username already taken"}, status=400)
    if User.objects.filter(email=email).exists():
        return Response({"error": "Email already registered"}, status=400)

    user = User.objects.create_user(username=username, email=email, password=password)
    Investor.objects.create(
        user=user, name=username, email=email, phone="", role="user",
    )
    return Response({"message": "Account created successfully"}, status=201)


# ─────────────────────────────────────────────
# CHANGE PASSWORD
# ─────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password(request):
    serializer = ChangePasswordSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    user = request.user
    if not user.check_password(serializer.validated_data["current_password"]):
        return Response({"detail": "Current password is incorrect."}, status=400)

    user.set_password(serializer.validated_data["new_password"])
    user.save()
    return Response({"message": "Password changed successfully."})


# ─────────────────────────────────────────────
# FORGOT PASSWORD
# ─────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([AllowAny])
def forgot_password(request):
    serializer = ForgotPasswordSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    email            = serializer.validated_data["email"]
    generic_response = Response(
        {"message": "If this email exists, a password reset link will be sent."}
    )

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return generic_response

    uid          = urlsafe_base64_encode(force_bytes(user.pk))
    token        = default_token_generator.make_token(user)
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:5173")
    reset_link   = f"{frontend_url}/reset-password/{uid}/{token}/"

    send_mail(
        subject="Password Reset Request",
        message=(
            f"Hi {user.username},\n\n"
            f"Click the link below to reset your password:\n{reset_link}\n\n"
            f"If you didn't request this, ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=True,
    )
    return generic_response


# ─────────────────────────────────────────────
# RESET PASSWORD CONFIRM
# ─────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([AllowAny])
def reset_password_confirm(request):
    uid          = request.data.get("uid")
    token        = request.data.get("token")
    new_password = request.data.get("new_password", "")

    if not uid or not token or not new_password:
        return Response(
            {"error": "uid, token, and new_password are required."}, status=400
        )
    if len(new_password) < 8:
        return Response(
            {"error": "Password must be at least 8 characters."}, status=400
        )

    try:
        user_pk = force_str(urlsafe_base64_decode(uid))
        user    = User.objects.get(pk=user_pk)
    except (User.DoesNotExist, ValueError, TypeError):
        return Response({"error": "Invalid reset link."}, status=400)

    if not default_token_generator.check_token(user, token):
        return Response({"error": "Reset link is invalid or has expired."}, status=400)

    user.set_password(new_password)
    user.save()
    return Response({"message": "Password reset successfully. You can now log in."})


# ─────────────────────────────────────────────
# FALLBACK WEBHOOK
# ─────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([AllowAny])
def trigger_roi(request):
    token = request.data.get("token")
    if not token or token != settings.ROI_SECRET_TOKEN:
        return Response({"error": "Forbidden"}, status=403)

    from .tasks import apply_daily_roi
    result = apply_daily_roi()
    return Response({"result": result})


# ─────────────────────────────────────────────
# APPROVE INVESTMENT
# ─────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def approve_investment(request, pk):
    try:
        requester = Investor.objects.get(user=request.user)
    except Investor.DoesNotExist:
        return Response({"error": "Not found"}, status=404)

    if requester.role != "admin":
        return Response({"error": "Forbidden"}, status=403)

    try:
        investment = Investment.objects.get(pk=pk)
    except Investment.DoesNotExist:
        return Response({"error": "Investment not found"}, status=404)

    investment.approved = True
    investment.active   = True
    investment.status   = "Approved"
    investment.save(update_fields=["approved", "active", "status"])
    return Response({"message": "Investment approved and activated."})


# ─────────────────────────────────────────────
# ADD MANUAL PROFIT
# ─────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def add_profit(request, pk):
    try:
        requester = Investor.objects.get(user=request.user)
    except Investor.DoesNotExist:
        return Response({"error": "Not found"}, status=404)

    if requester.role != "admin":
        return Response({"error": "Forbidden"}, status=403)

    try:
        investment = Investment.objects.get(pk=pk)
    except Investment.DoesNotExist:
        return Response({"error": "Investment not found"}, status=404)

    # Accept either "profit" (frontend) or "amount" (legacy) key
    raw = request.data.get("profit") or request.data.get("amount", 0)
    try:
        amount = float(raw)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return Response(
            {"error": "Invalid amount. Must be a positive number."}, status=400
        )

    from decimal import Decimal
    amount_decimal = Decimal(str(amount))
    payout = investment.amount + investment.current_profit + amount_decimal

    with transaction.atomic():
        investment.current_profit += amount_decimal
        investment.active = False
        investment.save(update_fields=["current_profit", "active"])

        investor = Investor.objects.select_for_update().get(pk=investment.investor.pk)
        investor.balance += payout
        investor.save(update_fields=["balance"])

    return Response({
        "message":        (
            f"Profit of ${amount:.2f} added. Investment closed. "
            f"${float(payout):.2f} credited to wallet."
        ),
        "current_profit": float(investment.current_profit),
        "payout":         float(payout),
        "new_balance":    float(investor.balance),
    })


# ─────────────────────────────────────────────
# DELETE USER
# ─────────────────────────────────────────────

@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_user(request, pk):
    try:
        requester = Investor.objects.get(user=request.user)
    except Investor.DoesNotExist:
        return Response({"error": "Not found"}, status=404)

    if requester.role != "admin":
        return Response({"error": "Forbidden"}, status=403)

    try:
        user = User.objects.get(pk=pk)
    except User.DoesNotExist:
        return Response({"error": "User not found"}, status=404)

    if user == request.user:
        return Response({"error": "You cannot delete your own account."}, status=400)

    user.delete()
    return Response({"message": "User deleted successfully."})


# ─────────────────────────────────────────────
# DASHBOARD STATS
# ─────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dashboard_stats(request):
    total_users       = User.objects.count()
    total_investments = (
        Investment.objects.aggregate(total=models.Sum("amount"))["total"] or 0
    )
    total_withdrawals = (
        Withdrawal.objects.aggregate(total=models.Sum("amount"))["total"] or 0
    )
    blocked_users = Investor.objects.filter(blocked=True).count()

    investment_by_category = list(
        Investment.objects
        .values("category")
        .annotate(total=models.Sum("amount"), count=models.Count("id"))
        .order_by("-total")
    )

    from django.db.models.functions import TruncMonth
    monthly_investments = list(
        Investment.objects
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(total=models.Sum("amount"))
        .order_by("month")
        .values("month", "total")
    )
    monthly_data = [
        {"month": e["month"].strftime("%b %Y"), "total": float(e["total"])}
        for e in monthly_investments
    ]

    return Response({
        "users":        total_users,
        "investments":  float(total_investments),
        "withdrawals":  float(total_withdrawals),
        "blocked_users": blocked_users,
        "investment_by_category": [
            {
                "category": c["category"],
                "total":    float(c["total"]),
                "count":    c["count"],
            }
            for c in investment_by_category
        ],
        "monthly_investments": monthly_data,
    })


# ─────────────────────────────────────────────
# TOP INVESTORS
# ─────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def top_investors(request):
    investors = (
        Investor.objects
        .filter(role="user", blocked=False)
        .annotate(
            total_invested   = models.Sum("investment__amount"),
            total_profit     = models.Sum("investment__current_profit"),
            active_plans     = models.Count(
                "investment", filter=models.Q(investment__active=True)
            ),
            investment_count = models.Count("investment"),
        )
        .filter(total_invested__isnull=False)
        .order_by("-total_invested")[:10]
    )

    data = []
    for rank, inv in enumerate(investors, start=1):
        count = inv.investment_count or 0
        data.append({
            "rank":             rank,
            "name":             inv.name,
            "email":            inv.email,
            "total_invested":   float(inv.total_invested or 0),
            "total_profit":     float(inv.total_profit   or 0),
            "balance":          float(inv.balance),
            "active_plans":     inv.active_plans,
            "tier":             get_tier(count),
            "investment_count": count,
        })

    return Response(data)


# ─────────────────────────────────────────────
# ALL USERS
# ─────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def all_users(request):
    users = User.objects.all().order_by("-date_joined").values(
        "id", "username", "email", "date_joined", "is_active"
    )
    return Response(list(users))


# ─────────────────────────────────────────────
# USER DASHBOARD
# ─────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def user_dashboard(request):
    if request.user.is_superuser:
        investor, _ = Investor.objects.get_or_create(
            user=request.user,
            defaults={
                "name":  request.user.username,
                "email": request.user.email or f"{request.user.username}@admin.local",
                "role":  "admin",
            }
        )
        if investor.role != "admin":
            investor.role = "admin"
            investor.save()
    else:
        try:
            investor = Investor.objects.get(user=request.user)
        except Investor.DoesNotExist:
            return Response({"error": "Investor profile not found"}, status=404)

    investments   = Investment.objects.filter(investor=investor)
    withdrawals   = Withdrawal.objects.filter(investor=investor)
    total_profits = sum(inv.current_profit for inv in investments)

    profile_data = InvestorSerializer(investor).data
    profile_data["wallet_balance"] = float(investor.balance)
    profile_data["active_profits"] = float(total_profits)
    profile_data["live_balance"]   = float(investor.balance) + float(total_profits)
    profile_data["bonus"]          = float(investor.bonus)

    return Response({
        "profile":     profile_data,
        "investments": InvestmentSerializer(investments, many=True).data,
        "withdrawals": WithdrawalSerializer(withdrawals, many=True).data,
    })


# ─────────────────────────────────────────────
# PROFILE
# ─────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def profile_view(request):
    try:
        investor = Investor.objects.get(user=request.user)
    except Investor.DoesNotExist:
        return Response({"error": "Profile not found"}, status=404)

    all_investments  = Investment.objects.filter(investor=investor)
    total_profits    = sum(inv.current_profit for inv in all_investments)
    investment_count = all_investments.count()

    data = InvestorSerializer(investor).data
    data["wallet_balance"]   = float(investor.balance)
    data["active_profits"]   = float(total_profits)
    data["live_balance"]     = float(investor.balance) + float(total_profits)
    data["bonus"]            = float(investor.bonus)
    data["tier"]             = get_tier(investment_count)
    data["investment_count"] = investment_count
    return Response(data)


# ─────────────────────────────────────────────
# VIEWSETS
# ─────────────────────────────────────────────

class InvestorViewSet(viewsets.ModelViewSet):
    queryset           = Investor.objects.all()
    serializer_class   = InvestorSerializer
    permission_classes = [IsAuthenticated]

    def update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().partial_update(request, *args, **kwargs)


class InvestmentViewSet(viewsets.ModelViewSet):
    queryset           = Investment.objects.all()
    serializer_class   = InvestmentSerializer
    permission_classes = [IsAuthenticated]

    # ── Filtering ─────────────────────────────────────────────────────────────

    def get_queryset(self):
        try:
            investor = Investor.objects.get(user=self.request.user)
        except Investor.DoesNotExist:
            return Investment.objects.none()

        # Admins see everything; filter by ?type= when provided
        if investor.role == "admin":
            qs         = Investment.objects.all().order_by("-created_at")
            type_param = self.request.query_params.get("type")
            if type_param in ("stock", "plan"):
                qs = qs.filter(type=type_param)
            return qs

        # Regular users only see their own investments
        qs           = Investment.objects.filter(investor=investor).order_by("-created_at")
        active_param = self.request.query_params.get("active")
        if active_param == "true":
            qs = qs.filter(active=True)
        elif active_param == "false":
            qs = qs.filter(active=False)
        return qs

    # ── Creation ──────────────────────────────────────────────────────────────

    def perform_create(self, serializer):
        requester = Investor.objects.get(user=self.request.user)

        # Allow admin to invest on behalf of another investor
        investor_id = self.request.data.get("investor")
        if investor_id and requester.role == "admin":
            try:
                investor = Investor.objects.get(id=investor_id)
            except Investor.DoesNotExist:
                investor = requester
        else:
            investor = requester

        # Parse and validate amount early so we can check balance
        from decimal import Decimal
        try:
            amount = Decimal(str(self.request.data.get("amount", 0)))
        except Exception:
            raise drf_serializers.ValidationError("Invalid investment amount.")

        # ── BALANCE CHECK: skip for admins investing on their own account ─────
        # Admins managing the platform don't need a funded wallet.
        # Regular users (and admins investing on behalf of others) must have
        # sufficient balance.
        skip_balance_check = requester.role == "admin" and not investor_id
        if not skip_balance_check:
            # Re-fetch with a lock to prevent race conditions
            with transaction.atomic():
                locked_investor = Investor.objects.select_for_update().get(
                    pk=investor.pk
                )
                if locked_investor.balance < amount:
                    raise drf_serializers.ValidationError(
                        f"Insufficient wallet balance. "
                        f"Your balance is ${float(locked_investor.balance):,.2f} but "
                        f"this investment requires ${float(amount):,.2f}. "
                        f"Please fund your account first."
                    )

                # Deduct the investment amount from the wallet immediately
                locked_investor.balance -= amount
                locked_investor.save(update_fields=["balance"])

        # Determine investment type sent by the frontend
        inv_type = self.request.data.get("type", "stock").lower()
        if inv_type not in ("stock", "plan"):
            inv_type = "stock"

        if inv_type == "plan":
            # ── Plan investment ───────────────────────────────────────────────
            plan_name = self.request.data.get("plan", "").strip()
            if plan_name not in PLAN_NAMES:
                plan_name = plan_name or "Trial Plan"

            # category mirrors plan name for consistency with existing queries
            category = plan_name

            serializer.save(
                investor  = investor,
                plan      = plan_name,
                category  = category,
                type      = "plan",
                active    = False,
                approved  = False,
                status    = "Pending",
                daily_roi = DAILY_ROI,
            )

        else:
            # ── Stock investment ──────────────────────────────────────────────
            category = self.request.data.get("category", "").strip()

            matched = next(
                (c for c in STOCK_CATEGORIES if c == category),
                None,
            )
            if not matched:
                matched = next(
                    (c for c in STOCK_CATEGORIES if category and category in c),
                    "Tesla (TSLA)",
                )

            serializer.save(
                investor  = investor,
                category  = matched,
                plan      = "",
                type      = "stock",
                active    = False,
                approved  = False,
                status    = "Pending",
                daily_roi = DAILY_ROI,
            )

    # ── Approve / Decline via PATCH ───────────────────────────────────────────

    def partial_update(self, request, *args, **kwargs):
        """
        Keeps the `status` field in sync whenever `approved` or `active` is
        patched by the admin frontend so both flags and the status string
        always agree.

        When an investment is Declined after being Pending, the deducted amount
        is refunded back to the investor's wallet.
        """
        instance   = self.get_object()
        patch_data = request.data

        # Derive status from explicit status key or from approved/active flags
        new_status = patch_data.get("status")
        if not new_status:
            if patch_data.get("approved") is True:
                new_status = "Approved"
            elif patch_data.get("approved") is False or patch_data.get("active") is False:
                new_status = patch_data.get("status", "Declined")

        # ── REFUND on Decline: money was already deducted on creation ─────────
        if new_status == "Declined" and instance.status == "Pending":
            with transaction.atomic():
                investor = Investor.objects.select_for_update().get(
                    pk=instance.investor.pk
                )
                investor.balance += instance.amount
                investor.save(update_fields=["balance"])
                logger.info(
                    f"[REFUND] {investor.name} | ${float(instance.amount):.2f} "
                    f"refunded due to declined investment #{instance.pk}"
                )

        # Merge computed status back so the serializer saves it
        mutable = patch_data.copy() if hasattr(patch_data, "copy") else dict(patch_data)
        if new_status:
            mutable["status"] = new_status

        kwargs["partial"] = True
        serializer = self.get_serializer(instance, data=mutable, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

    def update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.partial_update(request, *args, **kwargs)


class WithdrawalViewSet(viewsets.ModelViewSet):
    queryset           = Withdrawal.objects.all()
    serializer_class   = WithdrawalSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            investor = Investor.objects.get(user=self.request.user)
        except Investor.DoesNotExist:
            return Withdrawal.objects.none()

        if investor.role == "admin":
            return Withdrawal.objects.all()
        return Withdrawal.objects.filter(investor=investor)

    def perform_create(self, serializer):
        try:
            requester = Investor.objects.get(user=self.request.user)
        except Investor.DoesNotExist:
            raise drf_serializers.ValidationError("Investor profile not found")

        investor_id = self.request.data.get("investor")
        if investor_id and requester.role == "admin":
            try:
                investor = Investor.objects.get(id=investor_id)
            except Investor.DoesNotExist:
                investor = requester
        else:
            investor = requester

        if requester.role != "admin":
            lock_threshold = timezone.now() - timedelta(days=EXPIRY_DAYS)
            locked = Investment.objects.filter(
                investor=investor,
                active=True,
                approved=True,
                created_at__gt=lock_threshold,
            ).exists()
            if locked:
                raise drf_serializers.ValidationError(
                    "Withdrawals are locked until your 120-day investment period is complete."
                )

        serializer.save(investor=investor)

    def _maybe_deduct_balance(self, instance, new_status):
        if new_status == "Approved" and instance.status == "Pending":
            with transaction.atomic():
                investor = Investor.objects.select_for_update().get(
                    pk=instance.investor.pk
                )
                if investor.balance < instance.amount:
                    return Response(
                        {
                            "error": (
                                f"Insufficient balance. "
                                f"Investor has ${float(investor.balance):.2f} but "
                                f"withdrawal is ${float(instance.amount):.2f}."
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                investor.balance -= instance.amount
                investor.save(update_fields=["balance"])

                Investment.objects.filter(
                    investor=investor, active=True
                ).update(active=False)

        return None

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        err = self._maybe_deduct_balance(instance, request.data.get("status"))
        if err:
            return err
        kwargs["partial"] = True
        return super().partial_update(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        err = self._maybe_deduct_balance(instance, request.data.get("status"))
        if err:
            return err
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)


# ─────────────────────────────────────────────
# DEPOSIT VIEWSET
# ─────────────────────────────────────────────

class DepositViewSet(viewsets.ModelViewSet):
    """
    GET  deposits/        → user sees own deposits; admin sees all
    POST deposits/        → create a new deposit (multipart/form-data)
    PATCH deposits/<id>/  → admin approves or declines { "status": "approved"|"declined" }
    """
    queryset           = Deposit.objects.all().order_by("-created_at")
    serializer_class   = DepositSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            investor = Investor.objects.get(user=self.request.user)
        except Investor.DoesNotExist:
            return Deposit.objects.none()

        if investor.role == "admin":
            return Deposit.objects.all().order_by("-created_at")
        return Deposit.objects.filter(investor=investor).order_by("-created_at")

    def perform_create(self, serializer):
        try:
            investor = Investor.objects.get(user=self.request.user)
        except Investor.DoesNotExist:
            raise drf_serializers.ValidationError("Investor profile not found.")

        payment_method = self.request.data.get("payment_method", "")
        valid_methods  = ["BTC", "TRX", "ETH", "USDT", "LTC", "XRP"]
        if payment_method not in valid_methods:
            raise drf_serializers.ValidationError(
                f"Invalid payment method. Choose from: {', '.join(valid_methods)}."
            )

        try:
            amount = float(self.request.data.get("amount", 0))
            if amount < 500:
                raise ValueError
        except (TypeError, ValueError):
            raise drf_serializers.ValidationError("Minimum deposit amount is $500.")

        serializer.save(investor=investor, status="pending")

    def partial_update(self, request, *args, **kwargs):
        try:
            requester = Investor.objects.get(user=request.user)
        except Investor.DoesNotExist:
            return Response({"error": "Not found."}, status=404)

        if requester.role != "admin":
            return Response({"error": "Forbidden."}, status=403)

        new_status = request.data.get("status", "").lower()
        if new_status not in ("approved", "declined"):
            return Response(
                {"error": "status must be 'approved' or 'declined'."},
                status=400,
            )

        instance = self.get_object()

        if instance.status != "pending":
            return Response(
                {"error": f"Deposit is already {instance.status}."},
                status=400,
            )

        if new_status == "approved":
            with transaction.atomic():
                investor = Investor.objects.select_for_update().get(
                    pk=instance.investor.pk
                )
                investor.balance += instance.amount
                investor.save(update_fields=["balance"])
                instance.status = "approved"
                instance.save(update_fields=["status"])

            logger.info(
                f"[DEPOSIT APPROVED] {instance.investor.name} | "
                f"{instance.payment_method} | ${float(instance.amount):.2f} | "
                f"New balance: ${float(investor.balance):.2f}"
            )
        else:
            instance.status = "declined"
            instance.save(update_fields=["status"])
            logger.info(
                f"[DEPOSIT DECLINED] {instance.investor.name} | "
                f"{instance.payment_method} | ${float(instance.amount):.2f}"
            )

        return Response(DepositSerializer(instance).data)

    def update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.partial_update(request, *args, **kwargs)