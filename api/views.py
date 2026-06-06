import logging
from datetime import timedelta

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from .models import Deposit, Investment, Investor, Referral, Withdrawal
from .roi import apply_daily_roi          # roi.py holds the apply_daily_roi() function
from .serializers import (
    DepositSerializer,
    InvestmentSerializer,
    InvestorPublicSerializer,
    InvestorSerializer,
    ReferralSerializer,
    WithdrawalSerializer,
)

logger = logging.getLogger(__name__)

REFERRAL_COMMISSION_RATE = 0.05   # 5% of referred user's first deposit
ROI_TRIGGER_SECRET       = "roi-secret-key-change-in-prod"


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def get_investor_or_404(user):
    """Return the Investor linked to a Django User, or raise a 404 Response."""
    try:
        return Investor.objects.get(user=user)
    except Investor.DoesNotExist:
        return None


def is_admin(user):
    return user.is_staff or (
        hasattr(user, "investor") and user.investor.role == "admin"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AUTH
# ═════════════════════════════════════════════════════════════════════════════

@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    """
    POST /api/register/
    Body: { username, email, password, referral_code? }

    Creates a Django User + linked Investor profile.
    If referral_code is a valid investor ID, links the referral.
    Returns a token so the user is immediately logged in.
    """
    username = request.data.get("username", "").strip()
    email    = request.data.get("email",    "").strip().lower()
    password = request.data.get("password", "")
    ref_code = request.data.get("referral_code", None)

    if not username or not email or not password:
        return Response(
            {"error": "Username, email, and password are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if User.objects.filter(username=username).exists():
        return Response({"error": "Username already taken."}, status=status.HTTP_400_BAD_REQUEST)

    if Investor.objects.filter(email=email).exists():
        return Response({"error": "Email already registered."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        user = User.objects.create_user(username=username, email=email, password=password)
        token, _ = Token.objects.get_or_create(user=user)

        # Resolve referrer
        referrer = None
        if ref_code:
            try:
                referrer = Investor.objects.get(pk=int(ref_code))
            except (Investor.DoesNotExist, ValueError, TypeError):
                pass   # Invalid ref code — silently ignore

        investor = Investor.objects.create(
            user=user,
            name=username,
            email=email,
            role="user",
            referred_by=referrer,
        )

        # Log the referral relationship
        if referrer:
            Referral.objects.get_or_create(referrer=referrer, referred=investor)

    return Response(
        {
            "message": "Account created successfully.",
            "token":   token.key,
            "role":    investor.role,
            "user_id": investor.pk,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    """
    POST /api/login/
    Body: { username, password }

    Returns token + role so the React app can route admin → /dashboard,
    user → /user/dashboard.
    """
    username = request.data.get("username", "").strip()
    password = request.data.get("password", "")

    if not username or not password:
        return Response(
            {"error": "Username and password are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(username=username, password=password)
    if not user:
        return Response({"error": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)

    investor = get_investor_or_404(user)
    if not investor:
        return Response({"error": "Investor profile not found."}, status=status.HTTP_404_NOT_FOUND)

    if investor.blocked:
        return Response(
            {"error": "Your account has been blocked. Contact support."},
            status=status.HTTP_403_FORBIDDEN,
        )

    token, _ = Token.objects.get_or_create(user=user)

    return Response(
        {
            "token":   token.key,
            "role":    investor.role,          # "admin" | "user"
            "user_id": investor.pk,
            "name":    investor.name,
        }
    )


# ═════════════════════════════════════════════════════════════════════════════
# DASHBOARDS
# ═════════════════════════════════════════════════════════════════════════════

@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def user_dashboard(request):
    """
    GET /api/user-dashboard/
    Returns everything the UserDashboard page needs in one call.
    """
    investor = get_investor_or_404(request.user)
    if not investor:
        return Response({"error": "Investor not found."}, status=status.HTTP_404_NOT_FOUND)

    investments = Investment.objects.filter(investor=investor)
    deposits    = Deposit.objects.filter(investor=investor)
    withdrawals = Withdrawal.objects.filter(investor=investor)

    active_investments = investments.filter(active=True, approved=True)
    total_profit = sum(i.current_profit for i in active_investments)

    return Response(
        {
            "name":                investor.name,
            "email":               investor.email,
            "balance":             float(investor.balance),
            "bonus":               float(investor.bonus),
            "tier":                investor.tier,
            "role":                investor.role,
            "total_invested":      float(sum(i.amount for i in investments.filter(approved=True))),
            "total_profit":        float(total_profit),
            "active_investments":  InvestmentSerializer(active_investments, many=True).data,
            "pending_deposits":    DepositSerializer(deposits.filter(status="Pending"), many=True).data,
            "recent_withdrawals":  WithdrawalSerializer(withdrawals.order_by("-created_at")[:5], many=True).data,
            "referral_count":      investor.referrals.count(),
        }
    )


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def dashboard_stats(request):
    """
    GET /api/dashboard-stats/
    Admin-only summary for the admin Dashboard page.
    """
    if not is_admin(request.user):
        return Response({"error": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

    investors   = Investor.objects.filter(role="user")
    investments = Investment.objects.all()
    deposits    = Deposit.objects.all()
    withdrawals = Withdrawal.objects.all()

    return Response(
        {
            "total_users":            investors.count(),
            "active_investments":     investments.filter(active=True, approved=True).count(),
            "pending_investments":    investments.filter(approved=False).count(),
            "pending_deposits":       deposits.filter(status="Pending").count(),
            "pending_withdrawals":    withdrawals.filter(status="Pending").count(),
            "total_deposited":        float(sum(d.amount for d in deposits.filter(status="Approved"))),
            "total_withdrawn":        float(sum(w.amount for w in withdrawals.filter(status="Approved"))),
            "total_profit_paid":      float(sum(i.current_profit for i in investments.filter(active=False, approved=True))),
            "blocked_users":          investors.filter(blocked=True).count(),
        }
    )


@api_view(["GET", "PATCH"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def profile_view(request):
    """
    GET  /api/profile/  — return current user's profile
    PATCH /api/profile/ — update name / phone
    """
    investor = get_investor_or_404(request.user)
    if not investor:
        return Response({"error": "Investor not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(InvestorSerializer(investor).data)

    # PATCH — only allow safe fields
    allowed = {k: v for k, v in request.data.items() if k in ("name", "phone")}
    serializer = InvestorSerializer(investor, data=allowed, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ═════════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT  (admin only)
# ═════════════════════════════════════════════════════════════════════════════

@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def all_users(request):
    """GET /api/users/ — list all investors (admin only)."""
    if not is_admin(request.user):
        return Response({"error": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

    investors = Investor.objects.select_related("user").order_by("-created_at")
    return Response(InvestorPublicSerializer(investors, many=True).data)


@api_view(["DELETE"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def delete_user(request, pk):
    """DELETE /api/users/<pk>/delete/"""
    if not is_admin(request.user):
        return Response({"error": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

    try:
        investor = Investor.objects.get(pk=pk)
    except Investor.DoesNotExist:
        return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)

    investor.user.delete()   # cascade deletes Investor via OneToOneField
    return Response({"message": "User deleted."}, status=status.HTTP_200_OK)


@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def block_user(request, pk):
    """POST /api/users/<pk>/block/"""
    if not is_admin(request.user):
        return Response({"error": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

    try:
        investor = Investor.objects.get(pk=pk)
    except Investor.DoesNotExist:
        return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)

    investor.blocked = True
    investor.save(update_fields=["blocked"])
    return Response({"message": f"{investor.name} has been blocked."})


@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def unblock_user(request, pk):
    """POST /api/users/<pk>/unblock/"""
    if not is_admin(request.user):
        return Response({"error": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

    try:
        investor = Investor.objects.get(pk=pk)
    except Investor.DoesNotExist:
        return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)

    investor.blocked = False
    investor.save(update_fields=["blocked"])
    return Response({"message": f"{investor.name} has been unblocked."})


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def top_investors(request):
    """GET /api/top-investors/ — top 10 by balance (admin only)."""
    if not is_admin(request.user):
        return Response({"error": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

    investors = Investor.objects.filter(role="user").order_by("-balance")[:10]
    return Response(InvestorPublicSerializer(investors, many=True).data)


# ═════════════════════════════════════════════════════════════════════════════
# INVESTMENT ADMIN ACTIONS
# ═════════════════════════════════════════════════════════════════════════════

@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def approve_investment(request, pk):
    """
    POST /api/investments/<pk>/approve/
    Sets investment.approved=True and active=True.
    """
    if not is_admin(request.user):
        return Response({"error": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

    try:
        investment = Investment.objects.select_related("investor").get(pk=pk)
    except Investment.DoesNotExist:
        return Response({"error": "Investment not found."}, status=status.HTTP_404_NOT_FOUND)

    if investment.approved:
        return Response({"message": "Investment already approved."})

    investment.approved = True
    investment.active   = True
    investment.save(update_fields=["approved", "active"])

    logger.info(
        f"[APPROVED] Investment #{pk} | "
        f"{investment.investor.name} | "
        f"${float(investment.amount):.2f} | {investment.category}"
    )
    return Response({"message": "Investment approved and activated."})


@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def add_profit(request, pk):
    """
    POST /api/investments/<pk>/add_profit/
    Body: { amount: float }
    Manually credit extra profit to an investment (admin tool).
    """
    if not is_admin(request.user):
        return Response({"error": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

    try:
        investment = Investment.objects.select_related("investor").get(pk=pk)
    except Investment.DoesNotExist:
        return Response({"error": "Investment not found."}, status=status.HTTP_404_NOT_FOUND)

    try:
        extra = float(request.data.get("amount", 0))
        if extra <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return Response({"error": "Provide a positive amount."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        investment.current_profit += extra
        investment.save(update_fields=["current_profit"])

    return Response(
        {
            "message":         f"Added ${extra:.2f} profit to investment #{pk}.",
            "current_profit":  float(investment.current_profit),
        }
    )


# ═════════════════════════════════════════════════════════════════════════════
# DEPOSIT ADMIN ACTIONS
# ═════════════════════════════════════════════════════════════════════════════

@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def approve_deposit(request, pk):
    """
    POST /api/deposits/<pk>/approve/
    Credits investor.balance and awards referral commission (5%) to referrer
    if this is the investor's first approved deposit.
    """
    if not is_admin(request.user):
        return Response({"error": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

    try:
        deposit = Deposit.objects.select_related("investor").get(pk=pk)
    except Deposit.DoesNotExist:
        return Response({"error": "Deposit not found."}, status=status.HTTP_404_NOT_FOUND)

    if deposit.status == "Approved":
        return Response({"message": "Deposit already approved."})

    with transaction.atomic():
        investor = Investor.objects.select_for_update().get(pk=deposit.investor.pk)

        deposit.status = "Approved"
        deposit.save(update_fields=["status"])

        investor.balance += deposit.amount
        investor.save(update_fields=["balance"])

        # ── Referral commission on first deposit ──────────────────────────────
        first_approved = (
            Deposit.objects.filter(investor=investor, status="Approved").count() == 1
        )
        if first_approved and investor.referred_by:
            commission = deposit.amount * REFERRAL_COMMISSION_RATE
            referrer   = Investor.objects.select_for_update().get(pk=investor.referred_by.pk)
            referrer.balance += commission
            referrer.save(update_fields=["balance"])

            # Update or create the Referral record with commission
            referral, _ = Referral.objects.get_or_create(
                referrer=referrer, referred=investor
            )
            referral.commission += commission
            referral.save(update_fields=["commission"])

            logger.info(
                f"[REFERRAL] ${commission:.2f} commission → {referrer.name} "
                f"(referred {investor.name})"
            )

    logger.info(
        f"[DEPOSIT APPROVED] #{pk} | "
        f"{investor.name} | ${float(deposit.amount):.2f} | {deposit.payment_method}"
    )
    return Response(
        {
            "message":     "Deposit approved and balance credited.",
            "new_balance": float(investor.balance),
        }
    )


@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def reject_deposit(request, pk):
    """POST /api/deposits/<pk>/reject/"""
    if not is_admin(request.user):
        return Response({"error": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

    try:
        deposit = Deposit.objects.get(pk=pk)
    except Deposit.DoesNotExist:
        return Response({"error": "Deposit not found."}, status=status.HTTP_404_NOT_FOUND)

    if deposit.status != "Pending":
        return Response({"message": f"Deposit is already {deposit.status}."})

    deposit.status = "Rejected"
    deposit.save(update_fields=["status"])
    return Response({"message": "Deposit rejected."})


# ═════════════════════════════════════════════════════════════════════════════
# REPORTS
# ═════════════════════════════════════════════════════════════════════════════

@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def referral_stats(request):
    """
    GET /api/referrals/
    - Admin: all referral records
    - User: only their own referrals and earnings
    """
    investor = get_investor_or_404(request.user)
    if not investor:
        return Response({"error": "Investor not found."}, status=status.HTTP_404_NOT_FOUND)

    if is_admin(request.user):
        referrals = Referral.objects.select_related("referrer", "referred").all()
    else:
        referrals = Referral.objects.filter(referrer=investor).select_related("referred")

    total_commission = sum(r.commission for r in referrals)
    active_referred  = sum(
        1 for r in referrals
        if Investment.objects.filter(investor=r.referred, active=True, approved=True).exists()
    )

    return Response(
        {
            "referral_count":    referrals.count(),
            "active_referred":   active_referred,
            "total_commission":  float(total_commission),
            "referrals":         ReferralSerializer(referrals, many=True).data,
            # Used by Referrals.jsx to build the shareable link
            "referral_code":     investor.pk,
        }
    )


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def transactions_list(request):
    """
    GET /api/transactions/
    Returns all deposits + withdrawals for the requesting user (or all, if admin).
    """
    investor = get_investor_or_404(request.user)
    if not investor:
        return Response({"error": "Investor not found."}, status=status.HTTP_404_NOT_FOUND)

    if is_admin(request.user):
        deposits    = Deposit.objects.select_related("investor").order_by("-created_at")
        withdrawals = Withdrawal.objects.select_related("investor").order_by("-created_at")
    else:
        deposits    = Deposit.objects.filter(investor=investor).order_by("-created_at")
        withdrawals = Withdrawal.objects.filter(investor=investor).order_by("-created_at")

    return Response(
        {
            "deposits":    DepositSerializer(deposits, many=True).data,
            "withdrawals": WithdrawalSerializer(withdrawals, many=True).data,
        }
    )


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def profit_history(request):
    """
    GET /api/profit-history/
    Returns investment records with accumulated profit figures.
    """
    investor = get_investor_or_404(request.user)
    if not investor:
        return Response({"error": "Investor not found."}, status=status.HTTP_404_NOT_FOUND)

    if is_admin(request.user):
        investments = Investment.objects.select_related("investor").order_by("-created_at")
    else:
        investments = Investment.objects.filter(investor=investor).order_by("-created_at")

    return Response(InvestmentSerializer(investments, many=True).data)


# ═════════════════════════════════════════════════════════════════════════════
# ROI CRON TRIGGER
# ═════════════════════════════════════════════════════════════════════════════

@api_view(["POST"])
@permission_classes([AllowAny])
def trigger_roi(request):
    """
    POST /api/trigger-roi/
    Header: X-ROI-Secret: <ROI_TRIGGER_SECRET>

    Called by cron-job.org once per day at midnight UTC.
    Returns a summary of investments updated and matured.
    """
    secret = request.headers.get("X-ROI-Secret", "")
    if secret != ROI_TRIGGER_SECRET:
        return Response({"error": "Unauthorized."}, status=status.HTTP_403_FORBIDDEN)

    try:
        result = apply_daily_roi()
        return Response({"message": result})
    except Exception as exc:
        logger.exception("ROI trigger failed")
        return Response(
            {"error": str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# ═════════════════════════════════════════════════════════════════════════════
# VIEWSETS
# ═════════════════════════════════════════════════════════════════════════════

class InvestorViewSet(viewsets.ModelViewSet):
    """
    /api/investors/   — admin sees all; regular user sees only themselves.
    """
    authentication_classes = [TokenAuthentication]
    permission_classes     = [IsAuthenticated]
    serializer_class       = InvestorSerializer

    def get_queryset(self):
        if is_admin(self.request.user):
            return Investor.objects.all().select_related("user")
        return Investor.objects.filter(user=self.request.user)


class InvestmentViewSet(viewsets.ModelViewSet):
    """
    /api/investments/
    POST — user submits a new investment (pending admin approval).
    GET  — admin sees all; user sees their own.
    """
    authentication_classes = [TokenAuthentication]
    permission_classes     = [IsAuthenticated]
    serializer_class       = InvestmentSerializer

    def get_queryset(self):
        if is_admin(self.request.user):
            return Investment.objects.all().select_related("investor")
        investor = get_investor_or_404(self.request.user)
        if not investor:
            return Investment.objects.none()
        return Investment.objects.filter(investor=investor)

    def perform_create(self, serializer):
        investor = get_investor_or_404(self.request.user)
        if not investor:
            from rest_framework.exceptions import NotFound
            raise NotFound("Investor profile not found.")
        # approved=False by default; admin must approve via /investments/<pk>/approve/
        serializer.save(investor=investor)


class DepositViewSet(viewsets.ModelViewSet):
    """
    /api/deposits/
    POST — user submits deposit + payment proof (multipart/form-data).
    GET  — admin sees all; user sees their own.
    """
    authentication_classes = [TokenAuthentication]
    permission_classes     = [IsAuthenticated]
    serializer_class       = DepositSerializer

    def get_queryset(self):
        if is_admin(self.request.user):
            return Deposit.objects.all().select_related("investor")
        investor = get_investor_or_404(self.request.user)
        if not investor:
            return Deposit.objects.none()
        return Deposit.objects.filter(investor=investor)

    def perform_create(self, serializer):
        investor = get_investor_or_404(self.request.user)
        if not investor:
            from rest_framework.exceptions import NotFound
            raise NotFound("Investor profile not found.")
        serializer.save(investor=investor)


class WithdrawalViewSet(viewsets.ModelViewSet):
    """
    /api/withdrawals/
    POST — user requests withdrawal; deducted from balance immediately (optimistic).
           If admin rejects it, admin must manually refund via add_profit or balance patch.
    GET  — admin sees all; user sees their own.
    """
    authentication_classes = [TokenAuthentication]
    permission_classes     = [IsAuthenticated]
    serializer_class       = WithdrawalSerializer

    def get_queryset(self):
        if is_admin(self.request.user):
            return Withdrawal.objects.all().select_related("investor")
        investor = get_investor_or_404(self.request.user)
        if not investor:
            return Withdrawal.objects.none()
        return Withdrawal.objects.filter(investor=investor)

    def perform_create(self, serializer):
        investor = get_investor_or_404(self.request.user)
        if not investor:
            from rest_framework.exceptions import NotFound
            raise NotFound("Investor profile not found.")

        amount = serializer.validated_data["amount"]

        with transaction.atomic():
            inv = Investor.objects.select_for_update().get(pk=investor.pk)
            if inv.balance < amount:
                from rest_framework.exceptions import ValidationError
                raise ValidationError(
                    {"amount": f"Insufficient balance. Available: ${float(inv.balance):.2f}."}
                )
            inv.balance -= amount
            inv.save(update_fields=["balance"])
            serializer.save(investor=inv)