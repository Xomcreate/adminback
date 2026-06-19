import logging
from datetime import timedelta

import google.generativeai as genai
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import serializers as drf_serializers

from .models import (
    Investor, Investment, Withdrawal, Deposit, Referral,
    CopyTradingSubscription, COPY_TRADING_PLAN_PRICES, COPY_TRADING_PLAN_DEPOSITS,
    COPY_TRADING_TRADER_DURATIONS, COPY_TRADING_DEFAULT_DURATION_DAYS,
    BotSubscription, BOT_PLAN_PRICES, BOT_PLAN_DEPOSITS,
    KYCSubmission,
    ChatSession, ChatMessage,
)
from .serializers import (
    InvestorSerializer, InvestmentSerializer, WithdrawalSerializer,
    DepositSerializer, ChangePasswordSerializer, ForgotPasswordSerializer,
    ReferralSerializer, ReferralStatsSerializer,
    CopyTradingSubscriptionSerializer,
    BotSubscriptionSerializer,
    KYCSubmissionSerializer,
)

logger = logging.getLogger(__name__)

# ── Gemini setup ──────────────────────────────────────────────────────────────
genai.configure(api_key=settings.GEMINI_API_KEY)

# NOTE: gemini-1.5-flash has been retired. Using gemini-3.5-flash (current stable model).
GEMINI_MODEL_NAME = "gemini-2.5-flash-lite"

CHAT_SYSTEM_PROMPT = """You are a helpful, friendly support agent for IPO Stock — a professional investment platform.

You help users with:
- Investment plans, stock categories, and how the platform works
- Deposits, withdrawals, and wallet balance questions
- KYC verification process and document requirements
- Copy trading and bot subscription plans
- Referral program details
- General account and security questions

Be concise, professional, and warm. If a question is outside your scope (e.g. live balances or specific transaction statuses), politely ask the user to check their dashboard or contact the support team directly.

Do NOT make up specific account numbers, balances, or transaction details.
Keep responses under 120 words unless a detailed explanation is genuinely needed."""

STOCK_CATEGORIES = [
    "Shopify Inc. (SHOP)", "Tesla (TSLA)", "Meta (META)", "Amazon (AMZN)",
    "NVIDIA (NVDA)", "Apple (AAPL)", "Microsoft (MSFT)", "Netflix (NFLX)",
    "McDonald's (MCD)", "GameStop (GME)", "Coca-Cola (KO)", "Alphabet (GOOG)", "Intel (INTC)",
]
PLAN_NAMES = [
    "Trial Plan", "Essential Plan", "Premium Plan",
    "Ultimate Plan", "Royal Plan", "Diamond Plan",
]

DAILY_ROI   = 25.0
EXPIRY_DAYS = 120

FRONTEND_URL = getattr(settings, "FRONTEND_URL", "https://admindashboard-ruddy-beta.vercel.app")


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
    ref_code = request.data.get("ref", "").strip().upper()

    if not username or not email or not password:
        return Response({"error": "All fields are required"}, status=400)
    if User.objects.filter(username=username).exists():
        return Response({"error": "Username already taken"}, status=400)
    if User.objects.filter(email=email).exists():
        return Response({"error": "Email already registered"}, status=400)

    with transaction.atomic():
        user     = User.objects.create_user(username=username, email=email, password=password)
        investor = Investor.objects.create(user=user, name=username, email=email, phone="", role="user")

        if ref_code:
            try:
                referrer_investor = Investor.objects.get(referral_code=ref_code)
                if referrer_investor.user == user:
                    logger.warning(f"[REFERRAL] Self-referral attempt blocked for {username}")
                elif Referral.objects.filter(referred_user=investor).exists():
                    logger.warning(f"[REFERRAL] Duplicate referral blocked for {username}")
                else:
                    Referral.objects.create(
                        referrer=referrer_investor, referred_user=investor,
                        referred_name=username, referred_email=email,
                        referrer_name=referrer_investor.name, status="pending", approved=False,
                    )
                    logger.info(f"[REFERRAL CREATED] {referrer_investor.name} referred {username} ({email})")
            except Investor.DoesNotExist:
                logger.warning(f"[REFERRAL] Invalid ref code used at signup: {ref_code!r}")

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
    generic_response = Response({"message": "If this email exists, a password reset link will be sent."})

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return generic_response

    uid        = urlsafe_base64_encode(force_bytes(user.pk))
    token      = default_token_generator.make_token(user)
    reset_link = f"{FRONTEND_URL}/reset-password/{uid}/{token}/"

    try:
        send_mail(
            subject="Password Reset Request",
            message=(
                f"Hi {user.username},\n\n"
                f"Click the link below to reset your password:\n{reset_link}\n\n"
                f"If you didn't request this, ignore this email."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
        logger.info(f"[FORGOT PASSWORD] Reset email sent to {email}")
    except Exception as e:
        logger.error(f"[FORGOT PASSWORD] Failed to send email to {email}: {e}")

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
        return Response({"error": "uid, token, and new_password are required."}, status=400)
    if len(new_password) < 8:
        return Response({"error": "Password must be at least 8 characters."}, status=400)

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
# ROI TRIGGER
# ─────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([AllowAny])
def trigger_roi(request):
    token = request.data.get("token")
    if not token or token != settings.ROI_SECRET_TOKEN:
        return Response({"error": "Forbidden"}, status=403)
    from .tasks import apply_daily_roi
    result  = apply_daily_roi()
    expired = expire_copy_trading_subscriptions()
    return Response({"result": result, "copy_trading_expired": expired})


# ─────────────────────────────────────────────
# COPY TRADING — AUTO-EXPIRY HELPER
# ─────────────────────────────────────────────

def expire_copy_trading_subscriptions():
    now = timezone.now()
    qs  = CopyTradingSubscription.objects.filter(
        active=True,
        approved=True,
        copy_ends_at__isnull=False,
        copy_ends_at__lte=now,
    )
    count = 0
    for sub in qs:
        with transaction.atomic():
            investor = Investor.objects.select_for_update().get(pk=sub.investor.pk)
            investor.balance += sub.deposit_amount
            investor.save(update_fields=["balance"])
            sub.stop_copying(status="Expired")
            sub.save(update_fields=["status", "approved", "active", "updated_at"])
        logger.info(
            f"[COPY TRADING EXPIRED] #{sub.pk} — {sub.investor.name} "
            f"({sub.copied_trader}) ran for {sub.duration_days} days | "
            f"${float(sub.deposit_amount):.2f} refunded | "
            f"New balance: ${float(investor.balance):.2f}"
        )
        count += 1
    return count


# ─────────────────────────────────────────────
# KYC
# ─────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def kyc_submit(request):
    try:
        investor = Investor.objects.get(user=request.user)
    except Investor.DoesNotExist:
        return Response({"detail": "Investor profile not found."}, status=404)

    latest = investor.kyc_submissions.order_by("-submitted_at").first()
    if latest:
        if latest.status == "approved":
            return Response({"detail": "Your identity is already verified."}, status=400)
        if latest.status == "pending":
            return Response(
                {"detail": "Your submission is already under review. Please wait for a decision."},
                status=400,
            )

    doc_type        = request.data.get("document_type", "national_id")
    valid_doc_types = ["national_id", "passport", "drivers_license"]
    if doc_type not in valid_doc_types:
        return Response(
            {"detail": f"Invalid document_type. Choose from: {', '.join(valid_doc_types)}."},
            status=400,
        )

    id_front = request.FILES.get("id_front")
    id_back  = request.FILES.get("id_back")
    selfie   = request.FILES.get("selfie")

    if not id_front or not id_back or not selfie:
        return Response(
            {"detail": "All three documents are required: id_front, id_back, selfie."},
            status=400,
        )

    MAX_SIZE = 5 * 1024 * 1024
    for label, f in [("id_front", id_front), ("id_back", id_back), ("selfie", selfie)]:
        if f.size > MAX_SIZE:
            return Response({"detail": f"{label} exceeds the 5 MB size limit."}, status=400)

    submission = KYCSubmission.objects.create(
        investor      = investor,
        document_type = doc_type,
        id_front      = id_front,
        id_back       = id_back,
        selfie        = selfie,
        status        = "pending",
    )

    logger.info(
        f"[KYC SUBMITTED] {investor.name} ({investor.email}) "
        f"— doc: {doc_type} — id: #{submission.pk}"
    )
    return Response(
        KYCSubmissionSerializer(submission, context={"request": request}).data,
        status=201,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def kyc_all(request):
    try:
        requester = Investor.objects.get(user=request.user)
    except Investor.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)
    if requester.role != "admin":
        return Response({"detail": "Forbidden."}, status=403)
    submissions = KYCSubmission.objects.select_related("investor").order_by("-submitted_at")
    return Response(
        KYCSubmissionSerializer(submissions, many=True, context={"request": request}).data
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def kyc_approve(request, pk):
    try:
        requester = Investor.objects.get(user=request.user)
    except Investor.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)
    if requester.role != "admin":
        return Response({"detail": "Forbidden."}, status=403)
    try:
        submission = KYCSubmission.objects.select_related("investor").get(pk=pk)
    except KYCSubmission.DoesNotExist:
        return Response({"detail": "KYC submission not found."}, status=404)
    if submission.status == "approved":
        return Response({"detail": "Already approved."}, status=400)
    submission.status      = "approved"
    submission.reviewed_at = timezone.now()
    submission.save(update_fields=["status", "reviewed_at"])
    logger.info(f"[KYC APPROVED] #{submission.pk} — {submission.investor.name} approved by admin {requester.name}")
    return Response(KYCSubmissionSerializer(submission, context={"request": request}).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def kyc_reject(request, pk):
    try:
        requester = Investor.objects.get(user=request.user)
    except Investor.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)
    if requester.role != "admin":
        return Response({"detail": "Forbidden."}, status=403)
    try:
        submission = KYCSubmission.objects.select_related("investor").get(pk=pk)
    except KYCSubmission.DoesNotExist:
        return Response({"detail": "KYC submission not found."}, status=404)
    if submission.status == "rejected":
        return Response({"detail": "Already rejected."}, status=400)
    submission.status      = "rejected"
    submission.reviewed_at = timezone.now()
    submission.save(update_fields=["status", "reviewed_at"])
    logger.info(f"[KYC REJECTED] #{submission.pk} — {submission.investor.name} rejected by admin {requester.name}")
    return Response(KYCSubmissionSerializer(submission, context={"request": request}).data)


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

    raw = request.data.get("profit") or request.data.get("amount", 0)
    try:
        amount = float(raw)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return Response({"error": "Invalid amount. Must be a positive number."}, status=400)

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
        "message":        f"Profit of ${amount:.2f} added. Investment closed. ${float(payout):.2f} credited to wallet.",
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
            active_plans     = models.Count("investment", filter=models.Q(investment__active=True)),
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
    profile_data["kyc_status"]     = investor.kyc_status

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
    data["kyc_status"]       = investor.kyc_status
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
            qs         = Investment.objects.all().order_by("-created_at")
            type_param = self.request.query_params.get("type")
            if type_param in ("stock", "plan"):
                qs = qs.filter(type=type_param)
            return qs

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

        from decimal import Decimal
        try:
            amount = Decimal(str(self.request.data.get("amount", 0)))
        except Exception:
            raise drf_serializers.ValidationError("Invalid investment amount.")

        skip_balance_check = requester.role == "admin" and not investor_id

        if not skip_balance_check:
            with transaction.atomic():
                locked_investor = Investor.objects.select_for_update().get(pk=investor.pk)
                if locked_investor.balance < amount:
                    raise drf_serializers.ValidationError(
                        f"Insufficient wallet balance. "
                        f"Your balance is ${float(locked_investor.balance):,.2f} but "
                        f"this investment requires ${float(amount):,.2f}. "
                        f"Please fund your account first."
                    )
                locked_investor.balance -= amount
                locked_investor.save(update_fields=["balance"])
                logger.info(
                    f"[INVEST] {locked_investor.name} | "
                    f"${float(amount):.2f} deducted. "
                    f"New balance: ${float(locked_investor.balance):.2f}"
                )

        inv_type = self.request.data.get("type", "stock").lower()
        if inv_type not in ("stock", "plan"):
            inv_type = "stock"

        if inv_type == "plan":
            plan_name = self.request.data.get("plan", "").strip()
            if plan_name not in PLAN_NAMES:
                plan_name = plan_name or "Trial Plan"
            serializer.save(
                investor=investor, plan=plan_name, category=plan_name,
                type="plan", active=False, approved=False, status="Pending", daily_roi=DAILY_ROI,
            )
        else:
            category = self.request.data.get("category", "").strip()
            matched  = next((c for c in STOCK_CATEGORIES if c == category), None)
            if not matched:
                matched = next((c for c in STOCK_CATEGORIES if category and category in c), "Tesla (TSLA)")
            serializer.save(
                investor=investor, category=matched, plan="",
                type="stock", active=False, approved=False, status="Pending", daily_roi=DAILY_ROI,
            )

    def partial_update(self, request, *args, **kwargs):
        instance   = self.get_object()
        patch_data = request.data

        new_status = patch_data.get("status")
        if not new_status:
            if patch_data.get("approved") is True:
                new_status = "Approved"
            elif patch_data.get("approved") is False or patch_data.get("active") is False:
                new_status = "Declined"

        mutable = dict(patch_data)
        if new_status == "Approved":
            mutable["approved"] = True
            mutable["active"]   = True
            mutable["status"]   = "Approved"
        elif new_status == "Declined":
            mutable["approved"] = False
            mutable["active"]   = False
            mutable["status"]   = "Declined"

        if new_status == "Declined" and instance.status == "Pending":
            with transaction.atomic():
                investor = Investor.objects.select_for_update().get(pk=instance.investor.pk)
                investor.balance += instance.amount
                investor.save(update_fields=["balance"])
                logger.info(
                    f"[REFUND] {investor.name} | ${float(instance.amount):.2f} refunded "
                    f"— investment #{instance.pk} declined"
                )

        kwargs["partial"] = True
        serializer = self.get_serializer(instance, data=mutable, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        logger.info(
            f"[INVESTMENT UPDATE] #{instance.pk} → status={mutable.get('status')} "
            f"| approved={mutable.get('approved')} | active={mutable.get('active')}"
        )
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
                investor=investor, active=True, approved=True, created_at__gt=lock_threshold,
            ).exists()
            if locked:
                raise drf_serializers.ValidationError(
                    "Withdrawals are locked until your 120-day investment period is complete."
                )

        serializer.save(investor=investor)

    def _maybe_deduct_balance(self, instance, new_status):
        if new_status == "Approved" and instance.status == "Pending":
            with transaction.atomic():
                investor = Investor.objects.select_for_update().get(pk=instance.investor.pk)
                if investor.balance < instance.amount:
                    return Response(
                        {"error": (
                            f"Insufficient balance. Investor has ${float(investor.balance):.2f} "
                            f"but withdrawal is ${float(instance.amount):.2f}."
                        )},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                investor.balance -= instance.amount
                investor.save(update_fields=["balance"])
                Investment.objects.filter(investor=investor, active=True).update(active=False)
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
    queryset           = Deposit.objects.all().order_by("-created_at")
    serializer_class   = DepositSerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

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

        payment_proof = self.request.FILES.get("payment_proof")

        logger.info(
            f"[DEPOSIT CREATE] {investor.name} | {payment_method} | "
            f"${amount:.2f} | proof={'yes' if payment_proof else 'none'}"
        )

        serializer.save(
            investor      = investor,
            status        = "pending",
            payment_proof = payment_proof,
        )

    def partial_update(self, request, *args, **kwargs):
        try:
            requester = Investor.objects.get(user=request.user)
        except Investor.DoesNotExist:
            return Response({"error": "Not found."}, status=404)
        if requester.role != "admin":
            return Response({"error": "Forbidden."}, status=403)

        new_status = request.data.get("status", "").lower()
        if new_status not in ("approved", "declined"):
            return Response({"error": "status must be 'approved' or 'declined'."}, status=400)

        instance = self.get_object()
        if instance.status != "pending":
            return Response({"error": f"Deposit is already {instance.status}."}, status=400)

        if new_status == "approved":
            with transaction.atomic():
                investor = Investor.objects.select_for_update().get(pk=instance.investor.pk)
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

        return Response(DepositSerializer(instance, context={"request": request}).data)

    def update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.partial_update(request, *args, **kwargs)


# ─────────────────────────────────────────────
# REFERRAL VIEWSET
# ─────────────────────────────────────────────

class ReferralViewSet(viewsets.ModelViewSet):
    serializer_class   = ReferralSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            investor = Investor.objects.get(user=self.request.user)
        except Investor.DoesNotExist:
            return Referral.objects.none()
        if investor.role == "admin":
            return Referral.objects.all().order_by("-created_at")
        return Referral.objects.filter(referrer=investor).order_by("-created_at")

    @action(detail=False, methods=["get"], url_path="my-stats", url_name="my-stats")
    def my_stats(self, request):
        try:
            investor = Investor.objects.get(user=request.user)
        except Investor.DoesNotExist:
            return Response({"error": "Profile not found."}, status=404)

        referrals        = Referral.objects.filter(referrer=investor)
        total_referred   = referrals.count()
        active_contracts = referrals.filter(status="active").count()
        total_earnings   = referrals.filter(status="active").aggregate(total=Sum("commission"))["total"] or 0
        referral_link    = f"{FRONTEND_URL}/register?ref={investor.referral_code}"

        return Response({
            "total_referred":   total_referred,
            "active_contracts": active_contracts,
            "total_earnings":   float(total_earnings),
            "referral_code":    investor.referral_code,
            "referral_link":    referral_link,
        })

    def partial_update(self, request, *args, **kwargs):
        try:
            requester = Investor.objects.get(user=request.user)
        except Investor.DoesNotExist:
            return Response({"error": "Not found."}, status=404)
        if requester.role != "admin":
            return Response({"error": "Forbidden."}, status=403)

        instance   = self.get_object()
        new_status = request.data.get("status", "").lower()
        approved   = request.data.get("approved")

        if not new_status:
            if approved is True or approved == "true":
                new_status = "active"
            elif approved is False or approved == "false":
                new_status = "inactive"

        if new_status not in ("active", "inactive", "pending", "expired"):
            return Response({"error": "status must be one of: active, inactive, pending, expired."}, status=400)

        with transaction.atomic():
            if new_status == "active" and instance.status != "active":
                referrer = Investor.objects.select_for_update().get(pk=instance.referrer.pk)
                referrer.balance += instance.commission
                referrer.save(update_fields=["balance"])
                logger.info(
                    f"[REFERRAL APPROVED] {referrer.name} | "
                    f"Commission: ${float(instance.commission):.2f} | "
                    f"New balance: ${float(referrer.balance):.2f}"
                )
            elif new_status == "inactive" and instance.status == "active":
                referrer = Investor.objects.select_for_update().get(pk=instance.referrer.pk)
                referrer.balance = max(referrer.balance - instance.commission, 0)
                referrer.save(update_fields=["balance"])
                logger.info(
                    f"[REFERRAL DECLINED] Commission reversed for {referrer.name} "
                    f"| -${float(instance.commission):.2f}"
                )

            instance.status   = new_status
            instance.approved = (new_status == "active")
            instance.save(update_fields=["status", "approved"])

        return Response(ReferralSerializer(instance).data)

    def update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            requester = Investor.objects.get(user=request.user)
        except Investor.DoesNotExist:
            return Response({"error": "Not found."}, status=404)
        if requester.role != "admin":
            return Response({"error": "Forbidden."}, status=403)
        return super().destroy(request, *args, **kwargs)


# ─────────────────────────────────────────────
# COPY TRADING VIEWSET
# ─────────────────────────────────────────────

class CopyTradingSubscriptionViewSet(viewsets.ModelViewSet):
    serializer_class   = CopyTradingSubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            investor = Investor.objects.get(user=self.request.user)
        except Investor.DoesNotExist:
            return CopyTradingSubscription.objects.none()
        expire_copy_trading_subscriptions()
        if investor.role == "admin":
            return CopyTradingSubscription.objects.all().order_by("-created_at")
        return CopyTradingSubscription.objects.filter(investor=investor).order_by("-created_at")

    def perform_create(self, serializer):
        try:
            investor = Investor.objects.get(user=self.request.user)
        except Investor.DoesNotExist:
            raise drf_serializers.ValidationError("Investor profile not found.")

        plan = self.request.data.get("plan", "").strip()
        if plan not in list(COPY_TRADING_PLAN_PRICES.keys()):
            raise drf_serializers.ValidationError(
                f"Invalid plan. Choose from: {', '.join(COPY_TRADING_PLAN_PRICES.keys())}."
            )

        if investor.role != "admin":
            if CopyTradingSubscription.objects.filter(investor=investor, active=True).exists():
                raise drf_serializers.ValidationError("You already have an active copy trading subscription.")

        copied_trader  = self.request.data.get("copied_trader", "").strip()
        plan_fee       = COPY_TRADING_PLAN_PRICES.get(plan, 0)
        deposit_amount = COPY_TRADING_PLAN_DEPOSITS.get(plan, 0)
        duration_days  = COPY_TRADING_TRADER_DURATIONS.get(copied_trader, COPY_TRADING_DEFAULT_DURATION_DAYS)

        if investor.role != "admin":
            from decimal import Decimal
            deposit_decimal = Decimal(str(deposit_amount))
            if investor.balance < deposit_decimal:
                raise drf_serializers.ValidationError(
                    f"Insufficient wallet balance for the {plan} plan. "
                    f"Your balance is ${float(investor.balance):,.2f} but "
                    f"this plan requires a minimum deposit of ${float(deposit_decimal):,.2f}. "
                    f"Please fund your account first."
                )

        serializer.save(
            investor=investor, plan=plan, plan_fee=plan_fee,
            deposit_amount=deposit_amount, copied_trader=copied_trader,
            duration_days=duration_days,
            status="Pending", approved=False, active=False,
        )
        logger.info(
            f"[COPY TRADING] {investor.name} subscribed to {plan} plan "
            f"(fee={plan_fee}, deposit={deposit_amount}, "
            f"duration={duration_days}d, trader={copied_trader})"
        )

    def partial_update(self, request, *args, **kwargs):
        try:
            requester = Investor.objects.get(user=request.user)
        except Investor.DoesNotExist:
            return Response({"error": "Not found."}, status=404)

        instance = self.get_object()

        if requester.role == "admin":
            new_status = request.data.get("status", "").strip()
            approved   = request.data.get("approved")

            if not new_status:
                if approved is True or approved == "true":
                    new_status = "Approved"
                elif approved is False or approved == "false":
                    new_status = "Declined"

            if new_status not in ("Approved", "Declined", "Pending"):
                return Response({"error": "status must be one of: Approved, Declined, Pending."}, status=400)

            if new_status == "Approved":
                with transaction.atomic():
                    investor = Investor.objects.select_for_update().get(pk=instance.investor.pk)
                    if investor.balance < instance.deposit_amount:
                        return Response(
                            {"error": (
                                f"Cannot approve — {instance.investor.name} has "
                                f"${float(investor.balance):,.2f} but the {instance.plan} plan "
                                f"requires ${float(instance.deposit_amount):,.2f}."
                            )},
                            status=400,
                        )
                    investor.balance -= instance.deposit_amount
                    investor.save(update_fields=["balance"])
                    instance.start_copying()
                    instance.save(update_fields=[
                        "status", "approved", "active",
                        "copy_started_at", "copy_ends_at", "updated_at",
                    ])
                logger.info(
                    f"[COPY TRADING APPROVED] #{instance.pk} for {instance.investor.name} "
                    f"— copying {instance.copied_trader} for {instance.duration_days} days "
                    f"(ends {instance.copy_ends_at}) | "
                    f"${float(instance.deposit_amount):.2f} locked, "
                    f"new balance: ${float(investor.balance):.2f}"
                )
            elif new_status == "Declined":
                with transaction.atomic():
                    if instance.status == "Approved":
                        investor = Investor.objects.select_for_update().get(pk=instance.investor.pk)
                        investor.balance += instance.deposit_amount
                        investor.save(update_fields=["balance"])
                        logger.info(
                            f"[COPY TRADING REFUND] {instance.investor.name} | "
                            f"${float(instance.deposit_amount):.2f} refunded | "
                            f"New balance: ${float(investor.balance):.2f}"
                        )
                    instance.stop_copying(status="Declined")
                    instance.save(update_fields=["status", "approved", "active", "updated_at"])
                logger.info(f"[COPY TRADING DECLINED] #{instance.pk} for {instance.investor.name}")
            else:
                instance.status          = "Pending"
                instance.approved        = False
                instance.active          = False
                instance.copy_started_at = None
                instance.copy_ends_at    = None
                instance.save(update_fields=[
                    "status", "approved", "active",
                    "copy_started_at", "copy_ends_at", "updated_at",
                ])
                logger.info(f"[COPY TRADING RESET] #{instance.pk} for {instance.investor.name}")

            return Response(CopyTradingSubscriptionSerializer(instance).data)

        if instance.investor != requester:
            return Response({"error": "Forbidden."}, status=403)

        new_status = request.data.get("status", "").strip()
        if new_status == "Declined" and (instance.active or instance.status == "Pending"):
            with transaction.atomic():
                if instance.status == "Approved":
                    investor = Investor.objects.select_for_update().get(pk=requester.pk)
                    investor.balance += instance.deposit_amount
                    investor.save(update_fields=["balance"])
                    logger.info(
                        f"[COPY TRADING REFUND] {requester.name} | "
                        f"${float(instance.deposit_amount):.2f} refunded | "
                        f"New balance: ${float(investor.balance):.2f}"
                    )
                instance.stop_copying(status="Declined")
                instance.save(update_fields=["status", "approved", "active", "updated_at"])
            logger.info(
                f"[COPY TRADING STOPPED BY USER] #{instance.pk} — "
                f"{requester.name} stopped copying {instance.copied_trader}"
            )
            return Response(CopyTradingSubscriptionSerializer(instance).data)

        return Response({"error": "Forbidden."}, status=403)

    def update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            requester = Investor.objects.get(user=request.user)
        except Investor.DoesNotExist:
            return Response({"error": "Not found."}, status=404)
        if requester.role != "admin":
            return Response({"error": "Forbidden."}, status=403)
        instance = self.get_object()
        logger.info(f"[COPY TRADING DELETED] #{instance.pk} — {instance.investor.name} / {instance.plan}")
        return super().destroy(request, *args, **kwargs)


# ─────────────────────────────────────────────
# BOT SUBSCRIPTION VIEWSET
# ─────────────────────────────────────────────

class BotSubscriptionViewSet(viewsets.ModelViewSet):
    serializer_class   = BotSubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            investor = Investor.objects.get(user=self.request.user)
        except Investor.DoesNotExist:
            return BotSubscription.objects.none()
        if investor.role == "admin":
            return BotSubscription.objects.all().order_by("-created_at")
        return BotSubscription.objects.filter(investor=investor).order_by("-created_at")

    @action(detail=False, methods=["get"], url_path="my-active", url_name="my-active")
    def my_active(self, request):
        try:
            investor = Investor.objects.get(user=request.user)
        except Investor.DoesNotExist:
            return Response({"error": "Investor profile not found."}, status=404)

        if investor.role == "admin":
            return Response({
                "is_admin":        True,
                "has_active_plan": True,
                "plan":            "Institutional",
                "billing_period":  None,
                "subscription":    None,
                "pending":         None,
            })

        active_sub = (
            BotSubscription.objects
            .filter(investor=investor, approved=True, active=True, status="Approved")
            .order_by("-created_at")
            .first()
        )
        pending_sub = (
            BotSubscription.objects
            .filter(investor=investor, status="Pending", approved=False)
            .order_by("-created_at")
            .first()
        )

        return Response({
            "is_admin":        False,
            "has_active_plan": active_sub is not None,
            "plan":            active_sub.plan if active_sub else None,
            "billing_period":  active_sub.billing_period if active_sub else None,
            "subscription":    BotSubscriptionSerializer(active_sub).data if active_sub else None,
            "pending":         BotSubscriptionSerializer(pending_sub).data if pending_sub else None,
        })

    def perform_create(self, serializer):
        try:
            investor = Investor.objects.get(user=self.request.user)
        except Investor.DoesNotExist:
            raise drf_serializers.ValidationError("Investor profile not found.")

        plan           = self.request.data.get("plan", "").strip()
        billing_period = self.request.data.get("billing_period", "monthly").strip()
        valid_plans    = ["Starter", "Pro", "Institutional"]
        valid_periods  = ["weekly", "monthly", "yearly"]

        if plan not in valid_plans:
            raise drf_serializers.ValidationError(
                f"Invalid plan. Choose from: {', '.join(valid_plans)}."
            )
        if billing_period not in valid_periods:
            raise drf_serializers.ValidationError(
                f"Invalid billing period. Choose from: {', '.join(valid_periods)}."
            )

        if investor.role != "admin":
            if BotSubscription.objects.filter(investor=investor, active=True, approved=True).exists():
                raise drf_serializers.ValidationError(
                    "You already have an active bot subscription. "
                    "Please contact support to upgrade or change your plan."
                )
            existing_pending = BotSubscription.objects.filter(
                investor=investor, status="Pending", approved=False
            ).first()
            if existing_pending:
                raise drf_serializers.ValidationError(
                    f"You already have a pending {existing_pending.plan} plan request "
                    "awaiting admin approval. Please wait for it to be reviewed."
                )

        plan_fee       = BOT_PLAN_PRICES.get(billing_period, {}).get(plan, 0)
        deposit_amount = BOT_PLAN_DEPOSITS.get(plan, 0)

        serializer.save(
            investor       = investor,
            plan           = plan,
            billing_period = billing_period,
            plan_fee       = plan_fee,
            deposit_amount = deposit_amount,
            status         = "Pending",
            approved       = False,
            active         = False,
        )
        logger.info(
            f"[BOT SUBSCRIPTION] {investor.name} requested {plan} / {billing_period} "
            f"(fee={plan_fee}, deposit={deposit_amount})"
        )

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()

        try:
            requester = Investor.objects.get(user=request.user)
        except Investor.DoesNotExist:
            return Response({"error": "Not found."}, status=404)

        if requester.role == "admin":
            new_status = request.data.get("status", "").strip()
            approved   = request.data.get("approved")

            if not new_status:
                if approved is True or approved == "true":
                    new_status = "Approved"
                elif approved is False or approved == "false":
                    new_status = "Declined"

            if new_status not in ("Approved", "Declined", "Pending"):
                return Response(
                    {"error": "status must be one of: Approved, Declined, Pending."},
                    status=400,
                )

            if new_status == "Approved":
                BotSubscription.objects.filter(
                    investor=instance.investor, active=True, approved=True,
                ).exclude(pk=instance.pk).update(active=False, approved=False, status="Declined")
                BotSubscription.objects.filter(
                    investor=instance.investor, status="Pending", approved=False,
                ).exclude(pk=instance.pk).update(status="Declined")

            instance.status   = new_status
            instance.approved = (new_status == "Approved")
            instance.active   = (new_status == "Approved")
            instance.save(update_fields=["status", "approved", "active", "updated_at"])
            logger.info(
                f"[BOT SUBSCRIPTION UPDATE] #{instance.pk} → {new_status} "
                f"for {instance.investor.name}"
            )
            return Response(BotSubscriptionSerializer(instance).data)

        if instance.investor != requester:
            return Response({"error": "Forbidden."}, status=403)

        new_status = request.data.get("status", "").strip()
        if new_status == "Declined" and instance.status == "Pending":
            instance.status   = "Declined"
            instance.approved = False
            instance.active   = False
            instance.save(update_fields=["status", "approved", "active", "updated_at"])
            logger.info(f"[BOT SUBSCRIPTION CANCELLED] #{instance.pk} by user {requester.name}")
            return Response(BotSubscriptionSerializer(instance).data)

        return Response({"error": "Forbidden."}, status=403)

    def update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            requester = Investor.objects.get(user=request.user)
        except Investor.DoesNotExist:
            return Response({"error": "Not found."}, status=404)
        if requester.role != "admin":
            return Response({"error": "Forbidden."}, status=403)
        instance = self.get_object()
        logger.info(
            f"[BOT SUBSCRIPTION DELETED] #{instance.pk} — "
            f"{instance.investor.name} / {instance.plan}"
        )
        return super().destroy(request, *args, **kwargs)


# ─────────────────────────────────────────────
# CHAT  (Gemini-powered)
# ─────────────────────────────────────────────

def _get_or_create_chat_session(request):
    if request.user and request.user.is_authenticated:
        session, _ = ChatSession.objects.get_or_create(user=request.user)
        return session

    session_key = (
        request.data.get("session_key")
        or request.query_params.get("session_key", "")
    ).strip()

    if session_key:
        session, _ = ChatSession.objects.get_or_create(session_key=session_key)
        return session

    return ChatSession.objects.create()


@api_view(["POST"])
@permission_classes([AllowAny])
def chat_message_view(request):
    user_text = (request.data.get("message") or "").strip()
    if not user_text:
        return Response({"error": "message is required."}, status=400)
    if len(user_text) > 1000:
        return Response({"error": "Message too long. Keep it under 1000 characters."}, status=400)

    session = _get_or_create_chat_session(request)

    ChatMessage.objects.create(session=session, role="user", content=user_text)

    all_messages   = list(session.messages.order_by("created_at"))
    prior_messages = all_messages[:-1][-18:]
    gemini_history = [
        {
            "role":  "user" if msg.role == "user" else "model",
            "parts": [msg.content],
        }
        for msg in prior_messages
    ]

    try:
        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL_NAME,
            system_instruction=CHAT_SYSTEM_PROMPT,
        )
        chat       = model.start_chat(history=gemini_history)
        reply_text = chat.send_message(user_text).text.strip()
    except Exception as exc:
        logger.error(f"[CHAT] Gemini error: {exc!r}")
        reply_text = (
            "I'm having a little trouble right now. "
            "Please try again in a moment or reach out to our support team directly."
        )

    ChatMessage.objects.create(session=session, role="assistant", content=reply_text)

    logger.info(
        f"[CHAT] session={session.pk} | "
        f"user={'auth:' + str(request.user.id) if request.user.is_authenticated else 'anon'} | "
        f"msg_len={len(user_text)}"
    )

    return Response({
        "reply":       reply_text,
        "session_key": session.session_key or str(session.pk),
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def chat_history_view(request):
    session  = _get_or_create_chat_session(request)
    messages = session.messages.order_by("created_at")[:30]
    data = [
        {
            "role":       m.role,
            "content":    m.content,
            "created_at": m.created_at.isoformat(),
        }
        for m in messages
    ]
    return Response({
        "messages":    data,
        "session_key": session.session_key or str(session.pk),
    })


@api_view(["DELETE"])
@permission_classes([AllowAny])
def chat_clear_view(request):
    session = _get_or_create_chat_session(request)
    deleted_count, _ = session.messages.all().delete()
    logger.info(f"[CHAT CLEARED] session={session.pk} | {deleted_count} messages deleted")
    return Response({"message": "Chat history cleared."})