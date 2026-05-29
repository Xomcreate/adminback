from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models, transaction
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import serializers as drf_serializers

from .models import Investor, Investment, Withdrawal
from .serializers import InvestorSerializer, InvestmentSerializer, WithdrawalSerializer

STOCK_CATEGORIES = [
    "Tesla (TSLA)",
    "Apple (AAPL)",
    "Amazon (AMZN)",
    "McDonald's (MCD)",
    "GameStop (GME)",
    "Coca-Cola (KO)",
    "Meta (META)",
    "Alphabet (GOOG)",
    "Netflix (NFLX)",
    "Intel (INTC)",
]

DAILY_ROI   = 10.0   # flat 10% for all categories
EXPIRY_DAYS = 14


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
    investment.save(update_fields=["approved", "active"])
    return Response({"message": "Investment approved and activated."})


# ─────────────────────────────────────────────
# DASHBOARD STATS
# ─────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dashboard_stats(request):
    expiry_threshold = timezone.now() - timedelta(days=EXPIRY_DAYS)
    Investment.objects.filter(
        active=True, created_at__lte=expiry_threshold
    ).update(active=False)

    total_users       = User.objects.count()
    total_investments = Investment.objects.aggregate(total=models.Sum("amount"))["total"] or 0
    total_withdrawals = Withdrawal.objects.aggregate(total=models.Sum("amount"))["total"] or 0
    blocked_users     = Investor.objects.filter(blocked=True).count()

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
            {"category": c["category"], "total": float(c["total"]), "count": c["count"]}
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

    def get_tier(count):
        if count >= 6:   return "bronze"
        elif count >= 3: return "gold"
        elif count >= 1: return "silver"
        return "none"

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

    investments    = Investment.objects.filter(investor=investor)
    withdrawals    = Withdrawal.objects.filter(investor=investor)
    active_profits = sum(inv.current_profit for inv in investments if inv.active)

    profile_data = InvestorSerializer(investor).data
    profile_data["wallet_balance"] = float(investor.balance)
    profile_data["active_profits"] = float(active_profits)
    profile_data["live_balance"]   = float(investor.balance) + float(active_profits)
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

    active_investments = Investment.objects.filter(investor=investor, active=True)
    active_profits     = sum(inv.current_profit for inv in active_investments)

    data = InvestorSerializer(investor).data
    data["wallet_balance"] = float(investor.balance)
    data["active_profits"] = float(active_profits)
    data["live_balance"]   = float(investor.balance) + float(active_profits)
    data["bonus"]          = float(investor.bonus)
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

    def get_queryset(self):
        try:
            investor = Investor.objects.get(user=self.request.user)
        except Investor.DoesNotExist:
            return Investment.objects.none()

        if investor.role == "admin":
            expiry_threshold = timezone.now() - timedelta(days=EXPIRY_DAYS)
            Investment.objects.filter(
                active=True, created_at__lte=expiry_threshold
            ).update(active=False)
            return Investment.objects.all().order_by("-created_at")

        qs           = Investment.objects.filter(investor=investor).order_by("-created_at")
        active_param = self.request.query_params.get("active")
        if active_param == "true":
            qs = qs.filter(active=True)
        elif active_param == "false":
            qs = qs.filter(active=False)
        return qs

    def perform_create(self, serializer):
        requester = Investor.objects.get(user=self.request.user)

        investor_id = self.request.data.get("investor")
        if investor_id and requester.role == "admin":
            try:
                investor = Investor.objects.get(id=investor_id)
            except Investor.DoesNotExist:
                investor = requester
        else:
            investor = requester

        category = self.request.data.get("category", "Tesla (TSLA)")
        # Validate category
        if category not in STOCK_CATEGORIES:
            category = "Tesla (TSLA)"

        serializer.save(
            investor  = investor,
            active    = False,
            approved  = False,
            daily_roi = DAILY_ROI,   # always 10%
        )


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

        serializer.save(investor=investor)

    def _maybe_deduct_balance(self, instance, new_status):
        if new_status == "Approved" and instance.status == "Pending":
            with transaction.atomic():
                investor = Investor.objects.select_for_update().get(pk=instance.investor.pk)
                if investor.balance < instance.amount:
                    return Response(
                        {"error": (
                            f"Insufficient balance. "
                            f"Investor has ${float(investor.balance):.2f} but "
                            f"withdrawal is ${float(instance.amount):.2f}."
                        )},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                investor.balance -= instance.amount
                investor.save(update_fields=["balance"])

                # Deactivate all active investments for this investor
                # so ROI stops accumulating after withdrawal is approved
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